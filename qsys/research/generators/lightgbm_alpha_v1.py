"""LightGBMAlphaV1Generator — rolling retrain LightGBM per window.

Per rolling window:
1. Fetch 254 alpha_v1 features from qlib, clean to ~132
2. Load labels from LabelStore
3. Train LightGBM for each label horizon
4. Predict on predict window
5. Blend predictions via compute_signal (0.8×5d + 0.2×20d)
6. Output SignalStore-compatible DataFrame

Labels are read from LabelStore, not computed via ``make_zs_label``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.research.generators.utils import (
    build_next_trading_date_lookup as _build_next_trading_date_lookup,
    build_prev_trading_date_lookup as _build_prev_trading_date_lookup,
    check_training_label_maturity as _check_training_label_maturity,
    cs_zscore as _cs_zscore,
)


def _horizon_from_label_id(label_id: str) -> int:
    """Extract horizon integer from a label ID like ``fwd_ret_5d_xsz_clip3``."""
    # Expected format: fwd_ret_{N}d_*
    parts = label_id.split("_")
    for i, p in enumerate(parts):
        if p == "ret" and i + 1 < len(parts):
            cand = parts[i + 1]
            if cand.endswith("d") and cand[:-1].isdigit():
                return int(cand[:-1])
    raise ValueError(f"Cannot extract horizon from label_id: {label_id}")



@dataclass
class LightGBMAlphaV1Generator:
    """Rolling signal generator that retrains LightGBM per window.

    Labels are loaded from LabelStore.  ``make_zs_label`` is
    no longer used as the primary path.

    .. note::

       **Legacy compatibility**: this generator blends multiple label
       predictions (e.g. 5d + 20d) into a **single** ``score`` column.
       This predates the Generator→Combine separation.  For new research,
       train **one model per label** so each label produces its own
       ``SignalRun``, then combine via ``signal_combine.py`` (combine
       layer).  ``blend_weights`` only exists for this legacy path.

    Parameters
    ----------
    blend_weights:
        Weight per label horizon for the blended score (legacy alpha_v1
        compatibility).  Each horizon's prediction is cross-sectionally
        z-scored then multiplied by its weight and summed into a single
        score.  Default ``{"5d": 0.8, "20d": 0.2}`` preserves the
        legacy ``compute_signal(blend_5d=0.8, blend_20d=0.2)`` behaviour.
    """

    universe: str = "csi300"
    n_estimators: int = 200
    lgb_params: dict | None = None
    label_ids: tuple[str, ...] = ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3")
    blend_weights: dict[str, float] = field(default_factory=lambda: {"5d": 0.8, "20d": 0.2})

    _qlib_inited: bool = field(default=False, repr=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter().init_qlib()
            self._qlib_inited = True

    def _load_data(self, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        from qsys.data.adapter import QlibAdapter
        from qsys.feature.registry import get_feature_fields
        from qsys.strategy.alpha_v1.spec import get_clean_features

        all_features = get_feature_fields("semantic_all_features")
        clean = get_clean_features(all_features)
        self._clean_features = clean

        adapter = QlibAdapter()
        raw = adapter.get_features(
            self.universe, all_features + ["$close"],
            start_time=start, end_time=end,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        return frame, clean

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
        # Legacy blended generator: only 5d + 20d supported.
        # Multi-label support -> MultiLabelLightGBMGenerator (separate PR),
        # where each label produces an independent SignalRun.
        horizons = sorted(_horizon_from_label_id(lid) for lid in self.label_ids)
        if horizons != [5, 20]:
            raise ValueError(
                f"LightGBMAlphaV1Generator is a legacy blended generator "
                f"that requires exactly (5d, 20d) label horizons, "
                f"got {horizons}. "
                f"Arbitrary labels will be supported by "
                f"MultiLabelLightGBMGenerator (one SignalRun per label)."
            )
        if abs(self.blend_weights.get("5d", 0.8) + self.blend_weights.get("20d", 0.2)) < 1e-12:
            raise ValueError("blend_weights 5d + 20d must not sum to zero")

        self._ensure_qlib()

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        print(f"\n  Loading data [{train_start}, {extended_end}] ...")
        frame, clean_features = self._load_data(train_start, extended_end)

        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import train_model, predict_model

        # Load labels from LabelStore
        store = LabelStore()
        label_dfs = {}
        for lid in self.label_ids:
            label_dfs[lid] = store.load_labels(lid)

        # Train
        print(f"  Training window: {train_start} → {train_end}")
        # F01 (Option A, strict): pair features at date f with labels realized
        # from the NEXT trading day onward, matching inference where
        # trade_date = next_td(f) — removes same-day-close lookahead.
        next_td = _build_next_trading_date_lookup(train_start, train_end)
        # F01/F16: with labels shifted to next_td(f), enforce that no training
        # label extends into the predict window (max horizon across labels).
        _max_h = max(_horizon_from_label_id(lid) for lid in self.label_ids)
        _check_training_label_maturity(train_end, predict_start, _max_h)
        train = frame[
            (frame["trade_date"] >= train_start) &
            (frame["trade_date"] <= train_end)
        ].copy()
        train["label_date"] = train["trade_date"].map(next_td)

        # Merge labels into training frame (aligned to the next trading day)
        train_merged = train.copy()
        for lid in self.label_ids:
            sub = label_dfs[lid][["trade_date", "instrument", "label_value"]].rename(
                columns={"trade_date": "label_date", "label_value": f"label_{lid}"}
            )
            train_merged = train_merged.merge(sub, on=["label_date", "instrument"], how="left")

        models = {}
        for lid in self.label_ids:
            h = _horizon_from_label_id(lid)
            tag = f"{h}d"
            y_col = f"label_{lid}"

            y_valid = train_merged[y_col].notna()
            X_tr = train_merged[clean_features].astype(np.float32).fillna(0.0)
            y_tr = train_merged.loc[y_valid, y_col]

            if y_tr.empty:
                raise ValueError(f"No valid training samples for {lid}")

            models[tag] = train_model(
                X_tr.loc[y_tr.index], y_tr, f"window_{tag}",
                n_estimators=self.n_estimators,
                lgb_params=self.lgb_params,
            )

        # Predict — F01 backward-shift: the configured [predict_start,
        # predict_end] is the EXECUTION window; each execution day d uses
        # features from prev_td(d), so the output stays inside the window and
        # no feature bar at/after trade_date is used.
        from qsys.data.calendar import get_trading_calendar

        window_cal = get_trading_calendar(predict_start, predict_end)
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
        pred = frame[frame["trade_date"].isin(feature_dates)].copy()
        if pred.empty:
            raise ValueError(f"No feature data for execution window [{predict_start}, {predict_end}]")

        X_test = pred[clean_features].astype(np.float32).fillna(0.0)
        for tag in ["5d", "20d"]:
            pred[f"pred_{tag}"] = predict_model(*models[tag], X_test).values

        # Assemble output — feature date f -> execution day d (bijection).
        f_to_d = {prev_td.get(d, d): d for d in window_cal}
        rows: list[dict] = []

        for f in feature_dates:
            td = f_to_d.get(f)
            sub = pred[pred["trade_date"] == f]
            if td is None or sub.empty:
                continue
            assert str(f) < td, f"F01 lookahead: feature date {f} >= trade_date {td}"

            # Inline blend: cross-sectional zscore per horizon, weighted sum
            z5 = _cs_zscore(sub["pred_5d"])
            z20 = _cs_zscore(sub["pred_20d"])
            w5 = self.blend_weights.get("5d", 0.8)
            w20 = self.blend_weights.get("20d", 0.2)
            blended = w5 * z5.values + w20 * z20.values

            for i, (_, r) in enumerate(sub.iterrows()):
                rows.append({
                    "trade_date": td,
                    "data_date": str(f),
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(blended[i]) if pd.notna(blended[i]) else 0.0,
                })

        result = pd.DataFrame(rows)
        print(f"  Generated {len(result)} rows across {result['trade_date'].nunique()} trade dates\n")
        return result
