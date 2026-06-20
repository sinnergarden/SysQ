"""LightGBMSingleLabelGenerator — one label_id → one LightGBM → one SignalRun.

Per rolling window:
1. Fetch alpha_v1 features from qlib, clean to ~132
2. Load single label from LabelStore
3. Train one LightGBM model
4. Predict on predict window
5. Output SignalStore-compatible DataFrame (no blend)

This is the recommended base signal generator for supervised research.
Combine multiple base signals via ``signal_combine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from qsys.research.generators.utils import (
    build_prev_trading_date_lookup as _build_prev_trading_date_lookup,
    cs_zscore as _cs_zscore,
)
from qsys.utils.logger import log


@dataclass
class LightGBMSingleLabelGenerator:
    """Rolling signal generator that trains one LightGBM per label.

    Produces a single base ``SignalRun`` per ``label_id``.

    Parameters
    ----------
    label_id:
        LabelStore label ID to train and predict against.
    universe:
        Qlib universe identifier (default "csi300").
    n_estimators:
        LightGBM n_estimators (default 200).
    lgb_params:
        Optional extra LightGBM params.
    """

    label_id: str = "fwd_ret_5d_xsz_clip3"
    universe: str = "csi300"
    n_estimators: int = 200
    lgb_params: dict | None = None
    # ── Feature cache options (opt-in, default off) ─────────────────────
    use_feature_cache: bool = False
    materialize_on_miss: bool = False
    feature_cache_root: str = "data/feature_cache"
    source_manifest_hash: str = ""

    _qlib_inited: bool = field(default=False, repr=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter().init_qlib()
            self._qlib_inited = True

    def _load_data(self, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        from qsys.feature.registry import get_feature_fields, FeatureListRegistry

        if self.feature_list_id:
            clean = FeatureListRegistry.load(self.feature_list_id)
            all_features = clean
        else:
            from qsys.strategy.alpha_v1.spec import get_clean_features
            all_features = get_feature_fields("semantic_all_features")
            clean = get_clean_features(all_features)
        self._clean_features = clean

        # ── Fetach raw panel (needed for both cache path and qlib path) ──
        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter()
        raw = adapter.get_features(
            self.universe, all_features + ["$close"],
            start_time=start, end_time=end,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

        # ── Feature cache path (opt-in) ──
        if self.use_feature_cache and self.feature_list_id:
            from qsys.feature.cache_loader import load_feature_matrix_with_cache

            log.info(
                "Feature cache ENABLED for generator '%s' "
                "(feature_list_id=%s, materialize_on_miss=%s)",
                self.label_id, self.feature_list_id, self.materialize_on_miss,
            )
            feature_df = load_feature_matrix_with_cache(
                frame,
                feature_set_id=self.feature_list_id,
                date_start=start,
                date_end=end,
                universe=self.universe,
                source_manifest_hash=self.source_manifest_hash,
                cache_root=self.feature_cache_root,
                use_feature_cache=True,
                materialize_on_miss=self.materialize_on_miss,
            )
            log.info(
                "Feature cache result: %d rows x %d cols",
                len(feature_df), len(feature_df.columns),
            )
            return feature_df, clean

        # ── Existing qlib path (default) ──
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
        """Train one LightGBM on ``self.label_id``, predict window, output SignalRun.

        Returns
        -------
        pd.DataFrame
            SignalStore-compatible with columns:
            trade_date, data_date, instrument, signal_id, signal_run_id, score.
        """
        self._ensure_qlib()

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        log.info("Loading data [%s, %s]", train_start, extended_end)
        frame, clean_features = self._load_data(train_start, extended_end)

        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import train_model, predict_model

        # Load label
        label_df = LabelStore().load_labels(self.label_id)

        # Train
        log.info("Training window: %s → %s", train_start, train_end)
        train = frame[
            (frame["trade_date"] >= train_start) &
            (frame["trade_date"] <= train_end)
        ].copy().merge(
            label_df[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"], how="left",
        )

        y_valid = train["label_value"].notna()
        X_tr = train[clean_features].astype(np.float32).fillna(0.0)
        y_tr = train.loc[y_valid, "label_value"]
        if y_tr.empty:
            raise ValueError(f"No valid training samples for {self.label_id}")

        model, center, scale = train_model(
            X_tr.loc[y_tr.index], y_tr, "window",
            n_estimators=self.n_estimators,
            lgb_params=self.lgb_params,
        )

        # Predict
        pred = frame[
            frame["trade_date"].between(predict_start, predict_end)
        ].copy()
        if pred.empty:
            raise ValueError(f"No data for predict window [{predict_start}, {predict_end}]")

        pred["pred"] = predict_model(model, center, scale, pred[clean_features].astype(np.float32).fillna(0.0)).values

        # Assemble output
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        rows: list[dict] = []

        for d in sorted(pred["trade_date"].unique()):
            sub = pred[pred["trade_date"] == d]
            dd = prev_td.get(str(d), str(d))
            z = _cs_zscore(sub["pred"])

            for i, (_, r) in enumerate(sub.iterrows()):
                rows.append({
                    "trade_date": str(d),
                    "data_date": dd,
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(z.iloc[i]) if pd.notna(z.iloc[i]) else 0.0,
                })

        result = pd.DataFrame(rows)
        log.info("Generated %d rows across %d trade dates", len(result), result["trade_date"].nunique())
        return result
