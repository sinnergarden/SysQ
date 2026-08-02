"""LightGBMBinaryGenerator — binary classification for stop-loss prediction.

Trains one binary classifier per label, outputs probability of class 1
(e.g. probability of max drawdown worse than -5% within the forward window).

Output score is raw probability [0, 1] — no z-score transform, because
probability is already on a comparable scale across dates.
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
    horizon_from_label_id as _horizon_from_label_id,
)
from qsys.signal.alpha_v1.calibrate import ProbabilityCalibrator
from qsys.utils.logger import log


@dataclass
class LightGBMBinaryGenerator:
    """Rolling binary classifier — predicts binary event probability.

    Parameters
    ----------
    label_id:
        Label identifier for the binary label to train against.
        Must already exist in LabelStore.
    universe:
        Stock universe.
    n_estimators:
        Number of boosting rounds.
    lgb_params:
        LightGBM hyperparameters (binary defaults if None).
    feature_list_id:
        Feature list for training data.
    """

    label_id: str = "fwd_maxdd_5d_binary_5pct"
    universe: str = "csi800"
    n_estimators: int = 300
    lgb_params: dict | None = None
    feature_list_id: str | None = None

    _qlib_inited: bool = field(default=False, repr=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter().init_qlib()
            self._qlib_inited = True

    def _load_data(self, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        """Load features from qlib for the given date range."""
        from qsys.feature.registry import FeatureListRegistry

        if self.feature_list_id:
            clean = FeatureListRegistry.load(self.feature_list_id)
        else:
            from qsys.feature.registry import get_feature_fields
            from qsys.strategy.alpha_v1.spec import get_clean_features
            all_feats = get_feature_fields("semantic_all_features")
            clean = get_clean_features(all_feats)
        self._clean_features = clean

        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter()
        raw = adapter.get_features(self.universe, clean + ["$close"],
                                   start_time=start, end_time=end)
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if "instrument" not in frame.columns and "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})
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
        self._ensure_qlib()

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")

        log.info("Loading binary data [%s, %s]", train_start, extended_end)
        frame, clean_features = self._load_data(train_start, extended_end)

        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import train_model, predict_model

        label_df = LabelStore().load_labels(self.label_id)

        # ── Train ──
        log.info("Binary training window: %s -> %s", train_start, train_end)
        # F01 (Option A, strict): align features at date f with the binary label
        # realized from the NEXT trading day onward, matching inference where
        # trade_date = next_td(f) — removes same-day-close lookahead.
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
        y_tr = train.loc[y_valid, "label_value"].astype(int)
        if y_tr.empty:
            raise ValueError(f"No valid training samples for {self.label_id}")
        if y_tr.nunique() < 2:
            raise ValueError(
                f"Binary training requires both classes; got only {y_tr.unique()}"
            )

        # Log class balance
        pos = (y_tr == 1).sum()
        neg = (y_tr == 0).sum()
        log.info("Binary training: pos=%d (%.1f%%), neg=%d (%.1f%%)",
                 pos, 100 * pos / len(y_tr), neg, 100 * neg / len(y_tr))

        model, center, scale = train_model(
            X_tr.loc[y_tr.index], y_tr, "binary",
            n_estimators=self.n_estimators,
            lgb_params=self.lgb_params,
            mode="binary",
        )

        # ── Calibrate: isotonic on trailing portion of training window ──
        # Note: in-sample calibration (calibrator fitted on training predictions).
        # Isotonic preserves ranking (AUC unchanged).  Calibrated probabilities
        # are risk-bucket aids, not strict OOS probabilities.
        from qsys.signal.alpha_v1.labels import robust_zscore_transform as _rzt
        cal_train_dates = sorted(set(
            d for d in frame["trade_date"].unique() if train_start <= d <= train_end
        ))
        n_calib = max(1, int(len(cal_train_dates) * 0.15))
        calib_start = cal_train_dates[-n_calib]
        cal_sub = frame[frame["trade_date"].between(calib_start, cal_train_dates[-1])].copy()
        calibrator = None
        if not cal_sub.empty and len(cal_sub) > 100:
            Xz_c = _rzt(cal_sub[clean_features].fillna(0.0).astype(np.float32), center, scale)
            cal_sub["raw_prob"] = model.predict(Xz_c.values)
            cal_sub["label_date"] = cal_sub["trade_date"].map(next_td)
            cm = cal_sub.merge(
                label_df[["trade_date", "instrument", "label_value"]].rename(
                    columns={"trade_date": "label_date"}),
                on=["label_date", "instrument"], how="inner")
            if not cm.empty and cm["label_value"].nunique() == 2:
                pobj = ProbabilityCalibrator(method="isotonic", use_margin=False)
                pobj.fit(cm["raw_prob"].values, cm["label_value"].values)
                calibrator = pobj
                log.info("Calibrator fitted on %d holdout samples", len(cm))

        # ── Predict calibrated probability — F01 backward-shift ──
        # The configured [predict_start, predict_end] is the EXECUTION window;
        # each execution day d uses features from prev_td(d), so the output
        # stays inside the window and no feature bar at/after trade_date is used.
        from qsys.data.calendar import get_trading_calendar

        window_cal = get_trading_calendar(predict_start, predict_end)
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
        pred = frame[frame["trade_date"].isin(feature_dates)].copy()
        if pred.empty:
            raise ValueError(f"No feature data for execution window [{predict_start}, {predict_end}]")

        pred_prob = predict_model(model, center, scale,
                                  pred[clean_features].fillna(0.0).astype(np.float32), mode="binary")

        if calibrator is not None:
            pred["score"] = calibrator.predict(pred_prob.values)
        else:
            pred["score"] = pred_prob.values

        # Assemble output — calibrated probability as score
        f_to_d = {prev_td.get(d, d): d for d in window_cal}
        rows: list[dict] = []
        for f in feature_dates:
            td = f_to_d.get(f)
            sub = pred[pred["trade_date"] == f]
            if td is None or sub.empty:
                continue
            assert str(f) < td, f"F01 lookahead: feature date {f} >= trade_date {td}"
            for _, r in sub.iterrows():
                rows.append({
                    "trade_date": td,
                    "data_date": str(f),
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(r["score"]) if pd.notna(r["score"]) else 0.0,
                })

        result = pd.DataFrame(rows)
        log.info("Binary generated %d rows across %d trade dates",
                 len(result), result["trade_date"].nunique())
        return result
