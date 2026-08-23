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

import gc
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


_WINDOW_CACHE_SCHEMA_VERSION = 4
_WINDOW_CACHE_BUILDER_ID = "lightgbm_single_label_qlib_frame_v4_pit_content_bound"
FEATURE_VISIBILITY_CONTRACT = (
    "actual_feature_date_strictly_before_trade_date_v1"
)


def _prediction_membership_identity(path_value: str) -> tuple[str, str, set[str]]:
    """Validate and fingerprint an exact prediction-date membership snapshot.

    The snapshot is intentionally independent from the historical PIT span
    artifact.  It is an immutable, exact instrument set used only for the
    prediction rows.  A symlink is rejected so the identity cannot drift via
    a moving target such as ``latest``.
    """
    path = Path(path_value).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"prediction_membership_path must be an existing regular file: {path}"
        )
    try:
        snapshot = pd.read_parquet(path)
    except Exception as exc:
        raise ValueError(
            f"Failed to read prediction_membership_path: {path}"
        ) from exc
    if "instrument" not in snapshot.columns:
        raise ValueError(
            f"prediction membership snapshot missing required 'instrument' column: {path}"
        )
    if snapshot.empty:
        raise ValueError(f"prediction membership snapshot is empty: {path}")
    instruments = snapshot["instrument"]
    if instruments.isna().any():
        raise ValueError(
            f"prediction membership snapshot contains null instruments: {path}"
        )
    normalized = instruments.astype(str).str.upper()
    if normalized.duplicated().any():
        raise ValueError(
            f"prediction membership snapshot contains duplicate instruments: {path}"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return str(path), digest, set(normalized)


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
    # When True, rows are restricted to csi800_pit_v2 membership at the row's
    # feature date (trade_date), applied AFTER _load_data so train and predict
    # subsets of the shared frame are filtered identically.  Membership is read
    # from the per-interval artifact spans, never from the qlib registry's
    # collapsed min/max ranges (a stock may leave and re-enter).
    #
    # pit_membership is the LEGACY flag: True → member_as_of on the default
    # artifact (csi800_pit_v2).  For other universes use the new fields:
    #   pit_filter_mode        = "", "member_as_of", "ever_member_as_of"
    #   pit_universe_artifact  = dirname under data/research/universes/
    #   liquidity_exclusion_path = parquet (trade_date, instrument) anti-join
    pit_membership: bool = False
    pit_filter_mode: str = ""
    pit_universe_artifact: str = "csi800_pit_v2"
    liquidity_exclusion_path: str = ""
    # Optional exact current-date snapshot.  Historical training remains
    # member_as_of against ``pit_universe_artifact``; only prediction rows use
    # this exact instrument set.
    prediction_membership_path: str = ""
    # Optional operational registry used only to load the latest prediction
    # feature rows.  The historical PIT-union registry may legitimately end
    # at its last immutable snapshot and must never be extended in place.
    prediction_universe: str = ""

    _qlib_inited: bool = field(default=False, repr=False)
    _pit_store: object | None = field(default=None, repr=False, init=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)
    _call_count: int = field(default=0, repr=False)
    _prediction_membership_sha256: str = field(default="", repr=False, init=False)

    def __post_init__(self) -> None:
        if self.prediction_universe and not self.prediction_membership_path:
            raise ValueError(
                "prediction_universe requires prediction_membership_path"
            )
        if not self.prediction_membership_path:
            return
        if not self._effective_pit_filter_mode():
            raise ValueError(
                "prediction_membership_path requires an enabled PIT filter "
                "(pit_membership=true or pit_filter_mode)"
            )
        normalized, digest, _ = _prediction_membership_identity(
            self.prediction_membership_path
        )
        self.prediction_membership_path = normalized
        self._prediction_membership_sha256 = digest

    # ═══════════════════════════════════════════════════════════════
    # Per-window cache: content identity, not date range alone.
    # ═══════════════════════════════════════════════════════════════

    def _cache_identity(
        self,
        start: str,
        end: str,
        features: list[str],
    ) -> dict[str, object]:
        mode = self._effective_pit_filter_mode()
        membership_hash = ""
        if mode:
            try:
                from qsys.research.pit_universe import PitUniverseStore

                membership_hash = PitUniverseStore(
                    self.pit_universe_artifact
                ).provenance.membership_sha256
            except FileNotFoundError:
                membership_hash = "missing"
        exclusion_hash = ""
        if self.liquidity_exclusion_path:
            exclusion_path = Path(self.liquidity_exclusion_path)
            if exclusion_path.is_file():
                exclusion_hash = hashlib.sha256(
                    exclusion_path.read_bytes()
                ).hexdigest()
            else:
                exclusion_hash = "missing"
        prediction_path = ""
        prediction_hash = ""
        if self.prediction_membership_path:
            prediction_path, prediction_hash, _ = _prediction_membership_identity(
                self.prediction_membership_path
            )
        return {
            "schema_version": _WINDOW_CACHE_SCHEMA_VERSION,
            "builder_id": _WINDOW_CACHE_BUILDER_ID,
            "source_manifest_hash": self.source_manifest_hash,
            "universe": self.universe,
            "feature_list_id": self.feature_list_id,
            "features": features,
            "pit_membership": self.pit_membership,
            "pit_filter_mode": mode,
            "pit_universe_artifact": self.pit_universe_artifact if mode else "",
            "pit_membership_sha256": membership_hash,
            "liquidity_exclusion_path": self.liquidity_exclusion_path,
            "liquidity_exclusion_sha256": exclusion_hash,
            "prediction_membership_path": prediction_path,
            "prediction_membership_sha256": prediction_hash,
            "prediction_universe": self.prediction_universe,
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

    def _effective_pit_filter_mode(self) -> str:
        """Resolve the active filter mode from the new + legacy fields.

        ``pit_filter_mode`` wins; otherwise the legacy ``pit_membership``
        boolean maps to member_as_of.  Empty means no PIT restriction.
        """
        if self.pit_filter_mode:
            return self.pit_filter_mode
        return "member_as_of" if self.pit_membership else ""

    def _apply_pit_membership(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Restrict rows to a PIT membership artifact at each row's feature date.

        Dispatch on ``pit_filter_mode`` (defaults to member_as_of when the
        legacy ``pit_membership`` flag is set, preserving old behaviour):

        - ``member_as_of``: keep rows whose trade_date falls inside a span of
          the artifact (``PitUniverseStore(pit_universe_artifact)``).
        - ``ever_member_as_of``: keep rows whose trade_date >= the earliest
          span effective_from (ever-member monotonic; idempotent, ignores
          effective_to).
        - ``""``: no membership filter.

        After the span filter, if ``liquidity_exclusion_path`` is set, its
        ``(trade_date, instrument)`` rows are anti-joined (U3 diagnostic).

        Membership is read from the per-interval artifact spans, never from
        the qlib registry's collapsed min/max ranges — a stock that left the
        index and re-entered must be excluded during its non-member gap.
        Applied once, right after _load_data, so the train and predict
        subsets of the shared frame see identical rows (PIT semantics apply
        to training data too, per audit Section 17).
        """
        mode = self._effective_pit_filter_mode()
        if mode == "":
            return frame

        if self._pit_store is None:
            from qsys.research.pit_universe import PitUniverseStore
            self._pit_store = PitUniverseStore(self.pit_universe_artifact)

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
        if mode == "ever_member_as_of":
            keep_mask = date_int >= merged["_eff_from"]
        else:  # member_as_of
            keep_mask = (date_int >= merged["_eff_from"]) & (date_int <= merged["_eff_to"])
        keep = merged.loc[keep_mask, frame.columns].drop_duplicates()

        if self.liquidity_exclusion_path and not keep.empty:
            exclusions = pd.read_parquet(self.liquidity_exclusion_path)
            exclusions["trade_date"] = exclusions["trade_date"].astype(str).str[:10]
            exclusions["instrument"] = exclusions["instrument"].astype(str).str.upper()
            key = pd.MultiIndex.from_arrays(
                [keep["trade_date"].astype(str).str[:10], keep["instrument"]]
            )
            excl = pd.MultiIndex.from_arrays(
                [exclusions["trade_date"], exclusions["instrument"]]
            )
            keep = keep[~key.isin(excl)]
            log.info(
                "liquidity exclusion anti-join: %d -> %d rows",
                len(key), len(keep),
            )

        n_dropped = len(frame) - len(keep)
        log.info(
            "pit_membership filter [mode=%s, artifact=%s]: %d -> %d rows "
            "(dropped %d)",
            mode, self.pit_universe_artifact, len(frame), len(keep), n_dropped,
        )
        if keep.empty:
            raise ValueError("pit_membership: no rows remain after membership filter")
        return keep

    def _apply_prediction_membership(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Restrict prediction rows to the exact immutable snapshot set."""
        if not self.prediction_membership_path:  # pragma: no cover - defensive
            return frame
        path, digest, instruments = _prediction_membership_identity(
            self.prediction_membership_path
        )
        if self._prediction_membership_sha256 and digest != self._prediction_membership_sha256:
            raise ValueError(
                "prediction membership snapshot changed after generator initialization: "
                f"{path}"
            )
        keep = frame["instrument"].astype(str).str.upper().isin(instruments)
        result = frame.loc[keep].copy()
        if result.empty:
            raise ValueError(
                "prediction membership snapshot has no matching prediction rows: "
                f"{path}"
            )
        return result

    def _load_prediction_data(
        self,
        start: str,
        end: str,
        clean_features: list[str],
    ) -> pd.DataFrame:
        """Load latest feature rows from the prediction-only registry."""
        if not self.prediction_universe:
            raise ValueError("prediction_universe is not configured")
        from qsys.data.adapter import QlibAdapter

        raw = QlibAdapter().get_features(
            self.prediction_universe,
            clean_features + ["$close"],
            start_time=start,
            end_time=end,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        if "trade_date" not in frame.columns:
            raise ValueError(
                f"prediction_universe {self.prediction_universe!r} returned no feature rows"
            )
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if "instrument" not in frame.columns and "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})
        return frame

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

        from qsys.data.calendar import get_trading_calendar

        window_cal = get_trading_calendar(predict_start, predict_end)
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")
        load_end = train_end if self.prediction_universe else extended_end

        log.info("Loading data [%s, %s]", train_start, load_end)
        frame, clean_features = self._load_data(train_start, load_end)

        # Historical train rows and current prediction rows can use different
        # PIT artifacts.  Preserve the old single-frame behavior when no exact
        # prediction snapshot is supplied.
        if self.prediction_membership_path:
            train_frame = self._apply_pit_membership(frame)
            if self.prediction_universe:
                prediction_frame = self._load_prediction_data(
                    min(feature_dates), max(feature_dates), clean_features,
                )
            else:
                prediction_frame = frame
            prediction_frame = self._apply_prediction_membership(prediction_frame)
        else:
            if self._effective_pit_filter_mode():
                frame = self._apply_pit_membership(frame)
            train_frame = frame
            prediction_frame = frame

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
        train = train_frame[
            (train_frame["trade_date"] >= train_start)
            & (train_frame["trade_date"] <= train_end)
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
        pred = prediction_frame[
            prediction_frame["trade_date"].isin(feature_dates)
        ].copy()
        if pred.empty:
            raise ValueError(f"No feature data for execution window [{predict_start}, {predict_end}]")

        pred["pred"] = predict_model(
            model, center, scale, pred[clean_features].fillna(0.0).astype(np.float32)
        ).values
        # A Booster retains native Dataset handles beyond ordinary DataFrame
        # lifetimes.  Release them as soon as prediction is complete so long
        # rolling runs do not grow until the kernel OOM killer intervenes.
        free_dataset = getattr(model, "free_dataset", None)
        if callable(free_dataset):
            free_dataset()
        del model, center, scale, X_tr, y_tr, train, label_df
        gc.collect()

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
                    "score_model_raw": float(r["pred"]),
                    "score": float(z.iloc[i]) if pd.notna(z.iloc[i]) else 0.0,
                })

        result = pd.DataFrame(rows)
        log.info("Generated %d rows across %d trade dates", len(result), result["trade_date"].nunique())
        del pred, frame, train_frame, prediction_frame, rows
        gc.collect()
        return result
