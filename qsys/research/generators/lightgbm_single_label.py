"""LightGBMSingleLabelGenerator — one label_id -> one LightGBM -> one SignalRun.

Feature cache — per-window write-through
----------------------------------------
First run (write_through=True):
  Window N: qlib adapter -> builder -> frame, clean -> LightGBM training
    └── frame saved to cache/{feature_list_id}/{window_key}.parquet (side effect)

Second run (use_feature_cache=True):
  Window N: read cache/{feature_list_id}/{window_key}.parquet -> LightGBM training
    Builder COMPLETELY skipped. The cache identity binds the source snapshot,
    universe, ordered feature list, date window, schema and builder version.

Guarantee: a cache hit is accepted only for the exact declared input identity.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.research.generators.utils import (
    build_next_trading_date_lookup as _build_next_trading_date_lookup,
    build_prev_trading_date_lookup as _build_prev_trading_date_lookup,
    check_training_label_maturity as _check_training_label_maturity,
    cs_zscore as _cs_zscore,
    horizon_from_label_id as _horizon_from_label_id,
)
from qsys.utils.logger import log


_WINDOW_CACHE_SCHEMA_VERSION = 3
_WINDOW_CACHE_BUILDER_ID = "lightgbm_single_label_qlib_frame_v3"
FEATURE_VISIBILITY_CONTRACT = (
    "actual_feature_date_strictly_before_trade_date_v1"
)


@dataclass
class LightGBMSingleLabelGenerator:
    """Rolling signal generator — trains one LightGBM per label."""

    feature_visibility_contract: str = field(
        default=FEATURE_VISIBILITY_CONTRACT,
        init=False,
    )

    label_id: str = "fwd_ret_5d_xsz_clip3"
    universe: str = "csi300"
    n_estimators: int = 200
    lgb_params: dict | None = None
    feature_list_id: str | None = None
    # ── Feature cache options (opt-in) ──
    use_feature_cache: bool = False
    write_through: bool = False  # save per-window cache on first use
    feature_cache_root: str = "data/feature_cache"
    source_manifest_hash: str = ""
    # ── Point-in-Time universe restriction (opt-in) ──
    # When True, rows are restricted to csi800_pit_v1 membership at the row's
    # feature date (trade_date), applied AFTER _load_data so train and predict
    # subsets of the shared frame are filtered identically.  Membership is read
    # from the per-interval artifact spans, never from the qlib registry's
    # collapsed min/max ranges (a stock may leave and re-enter).
    pit_membership: bool = False

    _qlib_inited: bool = field(default=False, repr=False)
    _pit_store: object | None = field(default=None, repr=False, init=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)
    _call_count: int = field(default=0, repr=False)

    # ═══════════════════════════════════════════════════════════════
    # Per-window cache: content identity, not date range alone.
    # ═══════════════════════════════════════════════════════════════

    def _cache_identity(
        self,
        start: str,
        end: str,
        features: list[str],
    ) -> dict[str, object]:
        return {
            "schema_version": _WINDOW_CACHE_SCHEMA_VERSION,
            "builder_id": _WINDOW_CACHE_BUILDER_ID,
            "source_manifest_hash": self.source_manifest_hash,
            "universe": self.universe,
            "feature_list_id": self.feature_list_id,
            "features": features,
            "pit_membership": self.pit_membership,
            "start": start,
            "end": end,
        }

    def _window_key(self, start: str, end: str, features: list[str]) -> str:
        raw = json.dumps(
            self._cache_identity(start, end, features),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _window_cache_dir(self) -> Path:
        """All windows share one directory."""
        return Path(self.feature_cache_root) / "per_window"

    def _window_cache_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return self._window_cache_dir() / f"{self._window_key(start, end, features)}.parquet"

    def _window_meta_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return Path(str(self._window_cache_path(start, end, features)) + ".meta.json")

    def _window_has_cache(
        self, start: str, end: str, features: list[str]
    ) -> bool:
        path = self._window_cache_path(start, end, features)
        return path.exists() and self._window_meta_path(start, end, features).exists()

    # ═══════════════════════════════════════════════════════════════
    # Data loader
    # ═══════════════════════════════════════════════════════════════

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter().init_qlib()
            self._qlib_inited = True

    def _load_data(self, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        from qsys.feature.registry import FeatureListRegistry

        if self.feature_list_id:
            clean = FeatureListRegistry.load(self.feature_list_id)
        else:
            from qsys.feature.registry import get_feature_fields
            from qsys.strategy.alpha_v1.spec import get_clean_features
            all_feats = get_feature_fields("semantic_all_features")
            clean = get_clean_features(all_feats)
        self._clean_features = clean

        if self.use_feature_cache:
            if not self.feature_list_id:
                raise ValueError("Feature cache requires an explicit feature_list_id")
            if not self.source_manifest_hash.strip():
                raise ValueError(
                    "Feature cache requires a non-empty source_manifest_hash; "
                    "date-only cache reuse is forbidden"
                )

        # ── Cache hit: read per-window parquet → return directly ──
        if self.use_feature_cache and self._window_has_cache(start, end, clean):
            path = self._window_cache_path(start, end, clean)
            meta_path = self._window_meta_path(start, end, clean)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_identity = self._cache_identity(start, end, clean)
            identity_mismatches = [
                key for key, value in expected_identity.items()
                if meta.get(key) != value
            ]
            if identity_mismatches:
                raise ValueError(
                    "Window cache identity mismatch: "
                    + ", ".join(identity_mismatches)
                )

            df = pd.read_parquet(path)
            # trade_date was saved as str; ensure it stays str
            df["trade_date"] = df["trade_date"].astype(str).str[:10]

            needed = {"trade_date", "instrument"} | set(clean)
            missing = needed - set(df.columns)
            if missing:
                raise ValueError(
                    f"Cache missing features needed by '{self.feature_list_id}': {missing}. "
                    "Re-run with write_through=True for this exact feature list."
                )
            df = df[["trade_date", "instrument", *clean]]

            log.info("Cache HIT: %s (%d rows x %d cols, subset=%d feats)",
                     path.name, len(df), len(df.columns), len(clean))
            return df, clean

        # ── Original qlib path (cache miss or disabled) ──
        self._call_count += 1
        log.info("Loading qlib data [%s, %s] (call #%d)", start, end, self._call_count)

        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter()

        # Build features via qlib + phase1 builder
        raw = adapter.get_features(self.universe, clean + ["$close"], start_time=start, end_time=end)
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if "instrument" not in frame.columns and "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})

        # ── Write-through: save this window's frame to per-window cache ──
        if self.use_feature_cache and self.write_through:
            path = self._window_cache_path(start, end, clean)
            meta_path = self._window_meta_path(start, end, clean)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Save parquet
            out = frame.copy()
            out.to_parquet(path, index=False)

            # Save meta
            meta = {
                **self._cache_identity(start, end, clean),
                "window_key": self._window_key(start, end, clean),
                "rows": len(frame),
                "cols": len(frame.columns),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

            log.info("Cache WRITTEN: %s (%d rows x %d cols, %.1f MB)",
                     path.name, len(frame), len(frame.columns), path.stat().st_size / 1024 / 1024)

        return frame, clean

    def _apply_pit_membership(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Restrict rows to csi800 PIT membership at each row's feature date.

        Membership is read from the csi800_pit_v1 artifact spans (per-interval,
        gapped), never from the qlib registry's collapsed min/max ranges — a
        stock that left the index and re-entered must be excluded during its
        non-member gap.  Applied once, right after _load_data, so the train and
        predict subsets of the shared frame see identical rows (PIT semantics
        apply to training data too, per audit Section 17).
        """
        if self._pit_store is None:
            from qsys.research.pit_universe import PitUniverseStore
            self._pit_store = PitUniverseStore()

        spans = self._pit_store.spans[
            ["instrument", "effective_from", "effective_to"]
        ].rename(
            columns={"effective_from": "_eff_from", "effective_to": "_eff_to"}
        )
        spans["_eff_from"] = spans["_eff_from"].astype(int)
        spans["_eff_to"] = spans["_eff_to"].astype(int)

        merged = frame.merge(spans, on="instrument", how="inner")
        if merged.empty:
            raise ValueError(
                "pit_membership: no rows matched any membership span — "
                "check universe registry vs PIT artifact symbol format"
            )
        date_int = (
            merged["trade_date"].astype(str).str.replace("-", "", regex=False).astype(int)
        )
        in_span = (date_int >= merged["_eff_from"]) & (date_int <= merged["_eff_to"])
        keep = merged.loc[in_span, frame.columns].drop_duplicates()

        n_dropped = len(frame) - len(keep)
        log.info(
            "pit_membership filter: %d -> %d rows (dropped %d non-members)",
            len(frame), len(keep), n_dropped,
        )
        if keep.empty:
            raise ValueError("pit_membership: no rows remain after membership filter")
        return keep

    # ═══════════════════════════════════════════════════════════════
    # Training + prediction
    # ═══════════════════════════════════════════════════════════════

    def generate(
        self,
        *,
        train_start: str,
        train_end: str,
        predict_start: str,
        predict_end: str,
        signal_id: str,
        signal_run_id: str,
    ) -> pd.DataFrame:
        self._ensure_qlib()

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        log.info("Loading data [%s, %s]", train_start, extended_end)
        frame, clean_features = self._load_data(train_start, extended_end)

        # PIT restriction: filter once on the shared frame so training and
        # prediction use the same membership semantics (feature-date PIT).
        if self.pit_membership:
            frame = self._apply_pit_membership(frame)

        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import train_model, predict_model

        label_df = LabelStore().load_labels(self.label_id)

        # Train
        log.info("Training window: %s -> %s", train_start, train_end)
        # F01 (Option A, strict): features at date f are paired with the forward
        # return that starts on the NEXT trading day (the actual buy day's
        # close-to-close proxy), matching inference where trade_date = next_td(f).
        # Removes the same-day-close lookahead from research signal generation.
        next_td = _build_next_trading_date_lookup(train_start, train_end)
        # F01/F16: with the label shifted to next_td(f), enforce that no
        # training label extends into the predict window (fail loudly).
        _check_training_label_maturity(
            train_end, predict_start, _horizon_from_label_id(self.label_id),
        )
        train = frame[
            (frame["trade_date"] >= train_start) & (frame["trade_date"] <= train_end)
        ].copy()
        train["label_date"] = train["trade_date"].map(next_td)
        train = train.merge(
            label_df[["trade_date", "instrument", "label_value"]].rename(
                columns={"trade_date": "label_date"}),
            on=["label_date", "instrument"], how="left",
        )

        y_valid = train["label_value"].notna()
        X_tr = train[clean_features].fillna(0.0).astype(np.float32)
        y_tr = train.loc[y_valid, "label_value"].astype(float)
        if y_tr.empty:
            raise ValueError(f"No valid training samples for {self.label_id}")

        model, center, scale = train_model(
            X_tr.loc[y_tr.index], y_tr, "window",
            n_estimators=self.n_estimators, lgb_params=self.lgb_params,
        )

        # Predict — F01 backward-shift: the configured [predict_start,
        # predict_end] is the EXECUTION window.  Each execution day d uses
        # features from the previous trading day prev_td(d) (data_date), so the
        # output stays inside the window and no feature bar at/after trade_date
        # is used (no same-day-close lookahead).
        from qsys.data.calendar import get_trading_calendar

        window_cal = get_trading_calendar(predict_start, predict_end)
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
        pred = frame[frame["trade_date"].isin(feature_dates)].copy()
        if pred.empty:
            raise ValueError(f"No feature data for execution window [{predict_start}, {predict_end}]")

        pred["pred"] = predict_model(
            model, center, scale, pred[clean_features].fillna(0.0).astype(np.float32)
        ).values

        # feature date f -> execution day d (prev_td is a bijection on calendar)
        f_to_d = {prev_td.get(d, d): d for d in window_cal}
        rows: list[dict] = []
        for f in feature_dates:
            td = f_to_d.get(f)
            sub = pred[pred["trade_date"] == f]
            if td is None or sub.empty:
                continue
            assert str(f) < td, f"F01 lookahead: feature date {f} >= trade_date {td}"
            z = _cs_zscore(sub["pred"])
            for i, (_, r) in enumerate(sub.iterrows()):
                rows.append({
                    "trade_date": td,
                    "data_date": str(f),
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(z.iloc[i]) if pd.notna(z.iloc[i]) else 0.0,
                })

        result = pd.DataFrame(rows)
        log.info("Generated %d rows across %d trade dates", len(result), result["trade_date"].nunique())
        return result
