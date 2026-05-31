"""DnnMultitaskGenerator — multi-task DNN rolling signal generator.

Per rolling window:
1. Fetch 254 alpha_v1 features from qlib (train + predict range)
2. Compute per-date cross-sectional zscore for each feature → 254 zscores
3. Concat [raw_254 | zscore_254] → 508-dim input
4. Load labels from LabelStore (``label_ids`` param)
5. Train DnnMultitask on train window
6. Predict on predict window → score_5d, score_20d
7. Per-date zscore each → equal-weight blend → primary score
8. Save score_5d, score_20d as extra columns for separate evaluation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _cs_zscore(s: pd.Series) -> pd.Series:
    """Cross-sectional zscore, clip at ±3, handle constant."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def _get_trading_calendar(start: str, end: str) -> list[str]:
    try:
        from qsys.data.calendar import get_trading_calendar
        cal = get_trading_calendar(start, end)
        if cal:
            return cal
    except Exception:
        pass
    dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    cal = []
    while dt <= end_dt:
        if dt.weekday() < 5:
            cal.append(dt.strftime("%Y-%m-%d"))
        dt += timedelta(days=1)
    return cal


def _labels_from_close(
    close_df: pd.DataFrame,
    horizons: list[int] = (5, 20),
) -> dict[int, pd.Series]:
    """Compute forward returns at each horizon, per-instrument.

    Retained for backward compatibility.  Primary path uses LabelStore.
    """
    result = {}
    for h in horizons:
        shifted = close_df.groupby("instrument")["$close"].transform(
            lambda s: s.shift(-h)
        )
        fwd = shifted / close_df["$close"] - 1.0
        result[h] = fwd
    return result


@dataclass
class DnnMultitaskGenerator:
    """Rolling signal generator using shared-bottom multi-task DNN.

    Labels are loaded from LabelStore via ``label_ids``.
    ``_labels_from_close`` is no longer used as the primary path.

    Parameters
    ----------
    project_root: Path | None
        For path resolution.  Auto-detected if None.
    dnn_kwargs: dict
        Passed to ``DnnMultitask(feature_names=..., **dnn_kwargs)``.
    universe: str, default "csi300"
        Qlib universe identifier.
    label_ids: tuple[str, ...]
        LabelStore label IDs to use as training targets.
    """

    project_root: Path | None = None
    dnn_kwargs: dict | None = None
    universe: str = "csi300"
    feature_set: list[str] | None = None
    label_ids: tuple[str, ...] = ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3")

    _feature_set: list[str] = field(default_factory=list, repr=False)
    _qlib_inited: bool = field(default=False, repr=False)

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter().init_qlib()
            self._qlib_inited = True

    def _resolve_features(self) -> list[str]:
        if self.feature_set is not None:
            return self.feature_set
        if not self._feature_set:
            from qsys.feature.library import FeatureLibrary
            self._feature_set = FeatureLibrary.get_semantic_all_features_config()
        return self._feature_set

    def _build_features(
        self, features: list[str], start: str, end: str
    ) -> pd.DataFrame:
        """Fetch features from qlib, return 508-dim frame.

        Returns DataFrame with columns:
          - 254 abs:   ``<feature_name>`` — global robust-scaled
          - 254 norm:  ``<feature_name>__z`` — per-date cs_zscore
          - ``instrument``, ``trade_date``, ``$close``
        """
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        all_cols = features + ["$close"]
        raw = adapter.get_features(
            self.universe, all_cols,
            start_time=start, end_time=end,
        )
        if raw.empty:
            raise ValueError(f"No feature data for [{start}, {end}]")

        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

        # abs channel: global robust scaling
        vals = frame[features].astype(np.float32)
        med = vals.median()
        mad = (vals - med).abs().median().replace(0, 1.0)
        abs_scaled = ((vals - med) / mad).clip(-10, 10).fillna(0.0)

        # norm channel: daily cs_zscore
        normed = frame.groupby("trade_date")[features].transform(
            lambda g: _cs_zscore(g.astype(float))
        )

        for feat in features:
            frame[feat] = abs_scaled[feat].values
            frame[f"{feat}__z"] = normed[feat].values

        return frame

    def _prepare_training_data(
        self, frame: pd.DataFrame, features: list[str], train_end: str
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Build 508-dim X and label arrays for dates <= train_end.

        Labels are loaded from LabelStore, not computed inline.
        """
        from qsys.label.store import LabelStore

        feat_cols = features
        z_cols = [f"{f}__z" for f in features]
        x_cols = feat_cols + z_cols

        # Load labels from LabelStore
        store = LabelStore()
        label_dfs = {}
        for lid in self.label_ids:
            ldf = store.load_labels(lid)
            label_dfs[lid] = ldf

        # Restrict to training window
        train = frame[frame["trade_date"] <= train_end].copy()
        if train.empty:
            raise ValueError(f"No training data <= {train_end}")

        # Merge each label on (trade_date, instrument)
        for lid in self.label_ids:
            sub = label_dfs[lid][["trade_date", "instrument", "label_value"]].rename(
                columns={"label_value": f"label_{lid}"}
            )
            train = train.merge(sub, on=["trade_date", "instrument"], how="left")

        # Drop rows where any label is NaN
        label_cols = [f"label_{lid}" for lid in self.label_ids]
        valid = train[label_cols].notna().all(axis=1)
        train = train[valid]

        if train.empty:
            raise ValueError(f"No valid training samples after label merge <= {train_end}")

        X = train[x_cols].astype(np.float32).fillna(0.0).values
        y_arrays = [train[col].astype(np.float32).values for col in label_cols]
        return X, *y_arrays

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
        """Generate blended signal for the given window."""
        self._ensure_qlib()
        features = self._resolve_features()

        # Fetch features for the entire window (train + predict)
        # Extended end is still needed for label merge (forward-looking features)
        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        print(f"\n  Fetching features [{train_start}, {extended_end}] ...")
        frame = self._build_features(features, train_start, extended_end)

        # Train
        print(f"  Training window: {train_start} → {train_end}")
        X_train, y5, y20 = self._prepare_training_data(frame, features, train_end)

        from qsys.model.zoo.dnn_multitask import DnnMultitask

        kwargs = dict(self.dnn_kwargs or {})
        kwargs.setdefault("epochs", 50)
        model = DnnMultitask(
            input_dim=len(features) * 2,
            **kwargs,
        )
        model._feature_names = features
        model.fit(X_train, y5, y20)

        # Predict on predict window
        pred_frame = frame[
            frame["trade_date"].between(predict_start, predict_end)
        ].copy()
        if pred_frame.empty:
            raise ValueError(f"No data for predict window [{predict_start}, {predict_end}]")

        feat_cols = features
        z_cols = [f"{f}__z" for f in features]
        x_cols = feat_cols + z_cols
        X_pred = pred_frame[x_cols].astype(np.float32).fillna(0.0).values
        pred_5d, pred_20d = model.predict(X_pred)

        pred_frame["pred_5d"] = pred_5d
        pred_frame["pred_20d"] = pred_20d

        # Per-date zscore predictions → blend
        cal = _get_trading_calendar(train_start, predict_end)
        prev_td = {}
        for i, d in enumerate(cal):
            if i > 0:
                prev_td[d] = cal[i - 1]
        for d in sorted(pred_frame["trade_date"].unique()):
            if d not in prev_td:
                dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
                while dt.weekday() >= 5:
                    dt -= timedelta(days=1)
                prev_td[d] = dt.strftime("%Y-%m-%d")

        pred_frame["score_5d"] = pred_frame.groupby("trade_date")["pred_5d"].transform(_cs_zscore)
        pred_frame["score_20d"] = pred_frame.groupby("trade_date")["pred_20d"].transform(_cs_zscore)
        pred_frame["score"] = (pred_frame["score_5d"] + pred_frame["score_20d"]) / 2.0

        # Assemble output
        rows = []
        for _, row in pred_frame.iterrows():
            td = str(row["trade_date"])
            rows.append({
                "trade_date": td,
                "data_date": prev_td.get(td, td),
                "instrument": str(row["instrument"]),
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "score": float(row["score"]),
                "score_5d": float(row["score_5d"]),
                "score_20d": float(row["score_20d"]),
            })

        result = pd.DataFrame(rows)
        print(f"  Generated {len(result)} rows across {result['trade_date'].nunique()} trade dates\n")
        return result
