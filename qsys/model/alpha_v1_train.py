"""Alpha V1 training — importable functions, not subprocess.

Extracted from ``scripts/run_alpha_v1_weekly_train.py`` so the rolling
backtest runner can train models programmatically without shelling out.

Usage::

    from qsys.model.alpha_v1_train import train_alpha_v1

    model_dir = train_alpha_v1(end_date="2023-12-29")
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from qlib.data import D  # noqa: E402
from qsys.data.adapter import QlibAdapter  # noqa: E402
from qsys.feature.library import FeatureLibrary  # noqa: E402
from qsys.strategy.alpha_v1.spec import (  # noqa: E402
    HARMFUL_GROUPS,
    get_clean_features,
    get_feature_groups,
)

# ── Constants (mirrors the training script) ───────────────────────────────

UNIVERSE = "csi800"
TRAIN_DAYS = 504
LGB_PARAMS: dict[str, Any] = {
    "objective": "regression", "metric": "mse",
    "colsample_bytree": 0.8879, "learning_rate": 0.0421,
    "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
    "max_depth": 8, "num_leaves": 210, "num_threads": 8,
    "verbosity": -1, "seed": 42,
}
N_ESTIMATORS = 200

MODEL_DIR_ROOT = Path("experiments/alpha_v1_models")


# ── Pre-loading (call once, reuse across windows) ─────────────────────────


def preload_training_data(end_date: str) -> tuple[pd.DataFrame, list[str]]:
    """Load CSI800 features up to *end_date* — one qlib call for all windows.

    Returns (frame, clean_features).  Saves ~51s per training window.
    """
    print(f"[Preload] Loading CSI800 up to {end_date} ...")
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()

    raw = adapter.get_features(
        UNIVERSE, all_features + ["$close"],
        start_time="2013-01-01", end_time=end_date,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]

    # Ensure $open
    if "$open" not in frame.columns:
        try:
            insts = D.instruments(UNIVERSE)
            open_raw = D.features(insts, ["$open"], start_time="2013-01-01", end_time=end_date)
            open_df = open_raw.reset_index().rename(columns={"datetime": "trade_date"})
            open_df = open_df[["trade_date", "instrument", "$open"]].dropna(subset=["$open"])
            open_df = open_df.drop_duplicates(subset=["trade_date", "instrument"])
            frame = frame.merge(open_df, on=["trade_date", "instrument"], how="left")
        except Exception:
            pass
    if "$open" not in frame.columns:
        frame["$open"] = frame["$close"]

    if "$amount" in frame.columns and "$volume" in frame.columns:
        vol_safe = frame["$volume"].replace(0, np.nan)
        frame["$vwap"] = frame["$amount"] / vol_safe
    else:
        frame["$vwap"] = frame["$close"]

    frame = frame.sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    clean_features = get_clean_features(all_features)
    print(f"  {len(frame)} rows, {frame['trade_date'].nunique()}d, clean_features={len(clean_features)}")
    print(f"  Preload Time: {time.time() - t0:.1f}s")
    return frame, clean_features


# ── Helpers ───────────────────────────────────────────────────────────────


def cs_zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def make_forward_returns(df: pd.DataFrame, horizons: tuple[int, ...] = (1, 5, 20)) -> None:
    g = df.groupby("instrument")["$close"]
    for h in horizons:
        df[f"fwd_{h}d"] = g.shift(-h) / df["$close"] - 1.0


def make_zs_label(horizon: int):
    def label_fn(df: pd.DataFrame) -> pd.Series:
        g = df.groupby("instrument")["$close"]
        fwd = g.shift(-horizon) / df["$close"] - 1.0
        temp = df.copy()
        temp["_r"] = np.asarray(fwd)
        return temp.groupby("trade_date")["_r"].transform(cs_zscore)
    return label_fn


def robust_zscore_fit(X: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    center = X.median()
    scale = (X - center).abs().median().replace(0, 1.0)
    return center, scale


def robust_zscore_transform(X: pd.DataFrame, center: pd.Series, scale: pd.Series) -> pd.DataFrame:
    return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)


# ── Core training ────────────────────────────────────────────────────────


def train_model(
    X_train: pd.DataFrame, y_train: pd.Series, tag: str,
    n_est: int | None = None,
) -> tuple[lgb.Booster, pd.Series, pd.Series, float | None]:
    """Train a single LightGBM model. Returns (model, center, scale, rank_ic)."""
    if n_est is None:
        n_est = N_ESTIMATORS
    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    N = len(Xz)
    vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_train.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_train.iloc[-vs:].values)
    model = lgb.train(
        LGB_PARAMS, train_data, num_boost_round=n_est,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
    )
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y_train.notna()
    ric: float | None = None
    if valid.sum() > 0:
        ric = float(pred[valid].corr(y_train[valid], method="spearman"))
        print(f"    [{tag}] Train RankIC={ric:.5f}, trees={model.best_iteration}")
    return model, center, scale, ric


def train_alpha_v1(
    frame: pd.DataFrame,
    clean_features: list[str],
    *,
    model_dir: str | Path,
    train_start: str | None = None,
    train_end: str,
    version: str | None = None,
) -> Path:
    """Train alpha_v1 dual models on *frame* and save artifacts to *model_dir*.

    Parameters
    ----------
    frame : pd.DataFrame
        Pre-loaded CSI800 data (from ``preload_training_data``) containing
        ``trade_date``, ``instrument``, ``$close``, and ``clean_features``.
    clean_features : list[str]
        Feature column names to use for training.
    model_dir : str or Path
        Directory to save model artifacts.
    train_start : str, optional
        Override the training start date.  If omitted, uses the last
        ``TRAIN_DAYS`` trading days up to *train_end*.
    train_end : str
        Last date (inclusive) of the training window.
    version : str, optional
        Model version string.  Defaults to *train_end* without dashes.

    Returns
    -------
    Path to the model directory.
    """
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    all_dates = sorted(frame["trade_date"].unique())
    # Normalise to string for comparison
    all_dates_str = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in all_dates]

    end_idx = len([d for d in all_dates_str if d <= train_end]) - 1
    if end_idx < 0:
        raise ValueError(f"train_end {train_end} not found in frame dates")

    if train_start:
        start_idx = len([d for d in all_dates_str if d < train_start])
    else:
        start_idx = max(0, end_idx - TRAIN_DAYS)
    train_start_date = all_dates_str[start_idx]
    train_end_date = all_dates_str[end_idx]

    trade_date_col = frame["trade_date"]
    if hasattr(trade_date_col.iloc[0], "strftime"):
        trade_date_str = trade_date_col.dt.strftime("%Y-%m-%d")
    else:
        trade_date_str = trade_date_col.astype(str)
    train_mask = (trade_date_str >= train_start_date) & (trade_date_str <= train_end_date)
    train_data = frame[train_mask].copy()
    print(f"  Training window: {train_start_date} ~ {train_end_date} ({len(train_data)} rows)")

    if version is None:
        version = train_end_date.replace("-", "")

    # Compute forward returns if not already present
    if "fwd_5d" not in train_data.columns:
        make_forward_returns(train_data)

    models: dict[str, tuple] = {}
    for tag, h in [("5d", 5), ("20d", 20)]:
        print(f"  Training clean_{tag} (horizon={h}d)...")
        t0 = time.time()
        y_train = make_zs_label(h)(train_data)
        X_tr = train_data[clean_features].astype(np.float32).fillna(0.0)
        y_tr = y_train[pd.notna(y_train)]
        valid_rows = y_tr.index
        X_tr_valid = X_tr.loc[valid_rows]
        model, center, scale, ric = train_model(X_tr_valid, y_tr, tag)
        models[tag] = (model, center, scale, ric)
        print(f"    Time: {time.time() - t0:.1f}s")

    # Save artifacts
    for tag in ["5d", "20d"]:
        m, center, scale, _ = models[tag]
        m.save_model(str(model_dir / f"model_{tag}.txt"))
        (model_dir / f"center_{tag}.json").write_text(
            json.dumps({str(k): float(v) for k, v in center.items()}), encoding="utf-8")
        (model_dir / f"scale_{tag}.json").write_text(
            json.dumps({str(k): float(v) for k, v in scale.items()}), encoding="utf-8")

    (model_dir / "features.json").write_text(json.dumps(clean_features, indent=2), encoding="utf-8")

    n_trading_days = end_idx - start_idx + 1
    meta = {
        "version": version,
        "end_date": train_end_date,
        "trained_at": pd.Timestamp.now().isoformat(),
        "universe": UNIVERSE,
        "train_days": TRAIN_DAYS,
        "train_start": str(train_start_date),
        "train_end": str(train_end_date),
        "feature_count": len(clean_features),
        "training_rows": len(train_data),
        "trading_days": n_trading_days,
        "model_params": {k: str(v) if callable(v) else v for k, v in LGB_PARAMS.items()},
    }
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  Models saved: {model_dir}/")
    return model_dir
