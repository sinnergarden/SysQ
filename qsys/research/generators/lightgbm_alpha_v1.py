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


def _build_prev_trading_date_lookup(predict_start: str, predict_end: str) -> dict[str, str]:
    try:
        from qsys.data.calendar import get_trading_calendar
        extended_start = (
            datetime.strptime(predict_start, "%Y-%m-%d") - timedelta(days=30)
        ).strftime("%Y-%m-%d")
        cal = get_trading_calendar(extended_start, predict_end)
        if cal:
            lookup: dict[str, str] = {}
            for i, d in enumerate(cal):
                if i > 0:
                    lookup[d] = cal[i - 1]
                else:
                    _dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
                    while _dt.weekday() >= 5:
                        _dt -= timedelta(days=1)
                    lookup[d] = _dt.strftime("%Y-%m-%d")
            return lookup
    except Exception:
        pass
    lookup = {}
    _start_dt = datetime.strptime(predict_start, "%Y-%m-%d")
    _end_dt = datetime.strptime(predict_end, "%Y-%m-%d")
    cur = _start_dt - timedelta(days=60)
    bdays: list[str] = []
    while cur <= _end_dt:
        if cur.weekday() < 5:
            bdays.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    for i, d in enumerate(bdays):
        if i > 0:
            lookup[d] = bdays[i - 1]
        else:
            _dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
            while _dt.weekday() >= 5:
                _dt -= timedelta(days=1)
            lookup[d] = _dt.strftime("%Y-%m-%d")
    return lookup


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
    """

    universe: str = "csi300"
    n_estimators: int = 200
    lgb_params: dict | None = None
    label_ids: tuple[str, ...] = ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3")

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
        # Validate label_ids before any setup: must be exactly {5d, 20d}
        horizons = sorted(_horizon_from_label_id(lid) for lid in self.label_ids)
        if horizons != [5, 20]:
            raise ValueError(
                f"LightGBMAlphaV1Generator currently requires exactly 5d and 20d labels, "
                f"got horizons {horizons} from label_ids {self.label_ids}"
            )

        self._ensure_qlib()

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        print(f"\n  Loading data [{train_start}, {extended_end}] ...")
        frame, clean_features = self._load_data(train_start, extended_end)

        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import train_model, predict_model
        from qsys.signal.alpha_v1.inference import compute_signal

        # Load labels from LabelStore
        store = LabelStore()
        label_dfs = {}
        for lid in self.label_ids:
            label_dfs[lid] = store.load_labels(lid)

        # Train
        print(f"  Training window: {train_start} → {train_end}")
        train = frame[
            (frame["trade_date"] >= train_start) &
            (frame["trade_date"] <= train_end)
        ].copy()

        # Merge labels into training frame
        train_merged = train.copy()
        for lid in self.label_ids:
            sub = label_dfs[lid][["trade_date", "instrument", "label_value"]].rename(
                columns={"label_value": f"label_{lid}"}
            )
            train_merged = train_merged.merge(sub, on=["trade_date", "instrument"], how="left")

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

        # Predict
        pred = frame[
            frame["trade_date"].between(predict_start, predict_end)
        ].copy()
        if pred.empty:
            raise ValueError(f"No data for predict window [{predict_start}, {predict_end}]")

        X_test = pred[clean_features].astype(np.float32).fillna(0.0)
        for tag in ["5d", "20d"]:
            pred[f"pred_{tag}"] = predict_model(*models[tag], X_test).values

        # Assemble output
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        rows: list[dict] = []

        for d in sorted(pred["trade_date"].unique()):
            sub = pred[pred["trade_date"] == d]
            sig = compute_signal(
                pd.Series(sub["pred_5d"].values, index=sub.index),
                pd.Series(sub["pred_20d"].values, index=sub.index),
                sub["instrument"].values, str(d),
            )
            dd = prev_td.get(str(d), str(d))
            for _, r in sig.iterrows():
                rows.append({
                    "trade_date": str(d),
                    "data_date": dd,
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(r["score"]),
                })

        result = pd.DataFrame(rows)
        print(f"  Generated {len(result)} rows across {result['trade_date'].nunique()} trade dates\n")
        return result
