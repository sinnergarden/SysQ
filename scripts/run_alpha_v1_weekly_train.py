#!/usr/bin/env python3
"""
Alpha V1 Weekly Model Training
===============================
Trains dual LightGBM models (clean_5d + clean_20d) on a rolling 2-year
window of CSI800 data. Saves model artifacts to disk for daily use by
run_alpha_v1_trading.py. Sends rich Telegram notification with training
metrics including RankIC, tree counts, and top feature importances.

Schedule: systemd timer, Sunday 20:00 or Monday 07:00 (before pre-open).

Usage:
  python scripts/run_alpha_v1_weekly_train.py
  python scripts/run_alpha_v1_weekly_train.py --end-date 2026-05-18

.. warning::
   Historical replay: ``--end-date`` is **required**.  Without it the script
   defaults to ``datetime.now()``, which leaks future data into the training
   set and produces inflated PnL.  Production use (systemd timer) is safe
   because "now" is truly the current date.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from qsys.common.deprecation import print_legacy_entrypoint_warning  # noqa: E402

print_legacy_entrypoint_warning(
    "run_alpha_v1_weekly_train.py",
    "python scripts/run_daily.py --strategy alpha_v1 --mode train",
)

# ── 加载 .env（Telegram 凭据）────────────────────────────────────────

_ENV_FILE = Path("/home/liuming/.openclaw/.env")
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.telegram import send_telegram_message

# ── Constants ──
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
HARMFUL_GROUPS = {"Fundamental", "VolumeAmt", "Valuation", "Margin", "PricePattern"}
MODEL_DIR = Path("experiments/alpha_v1_models")


# ── Helpers ──

def cs_zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def robust_zscore_fit(X: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    center = X.median()
    scale = (X - center).abs().median().replace(0, 1.0)
    return center, scale


def robust_zscore_transform(X: pd.DataFrame, center: pd.Series, scale: pd.Series) -> pd.DataFrame:
    return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)


def make_zs_label(horizon: int):
    def label_fn(df: pd.DataFrame) -> pd.Series:
        g = df.groupby("instrument")["$close"]
        fwd = g.shift(-horizon) / df["$close"] - 1.0
        temp = df.copy()
        temp["_r"] = np.asarray(fwd)
        return temp.groupby("trade_date")["_r"].transform(cs_zscore)
    return label_fn


def make_forward_returns(df: pd.DataFrame, horizons: tuple[int, ...] = (1, 5, 20)) -> None:
    g = df.groupby("instrument")["$close"]
    for h in horizons:
        df[f"fwd_{h}d"] = g.shift(-h) / df["$close"] - 1.0


def get_feature_groups(all_features: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    groups["Size"] = [f for f in all_features if any(k in f for k in ("$total_mv", "$circ_mv", "log_mktcap", "$total_assets", "$equity", "equity "))]
    groups["Valuation"] = [f for f in all_features if any(k in f for k in ("$pe", "$pb", "pe_ttm", "pb_raw", "pcf", "ps_ttm", "operating_cf_to_profit"))]
    groups["Fundamental"] = [f for f in all_features if any(k in f for k in ("roa", "$roe", "net_margin", "$grossprofit_margin", "grossprofit", "$revenue", "$net_income", "$op_cashflow", "revenue_yoy", "profit_yoy", "$debt_to_assets", "$current_ratio"))]
    groups["Margin"] = [f for f in all_features if any(k in f for k in ("lend_volume", "margin_balance", "margin_buy", "margin_repay", "margin_total"))]
    groups["PriceVol"] = [f for f in all_features if "std(" in f.lower() and "close" in f.lower() and "abs" not in f.lower()]
    groups["DollarVol"] = [f for f in all_features if "std(abs" in f.lower() or ("std($" in f.lower() and "volume" in f.lower()) or ("std(" in f.lower() and "abs(" in f.lower())]
    groups["VolumeAmt"] = [f for f in all_features if any(k in f for k in ("turnover_rate", "amount_mean", "vol_mean", "$amount", "$volume", "high_limit", "low_limit", "illiquidity"))]
    groups["Momentum"] = [f for f in all_features if any(k in f for k in ("_ret_", "Slope(", "Rsquare(", "Resi(", "stock_minus_index_ret"))]
    groups["PricePattern"] = [f for f in all_features if any(k in f for k in ("Max(", "Min(", "IdxMax", "IdxMin", "Quantile(", "distance_to", "open_to_close", "close_to_open", "$open/$close", "($close-$open)/$open"))]
    groups["Correlation"] = [f for f in all_features if f.startswith("Corr(")]
    assigned = set()
    for v in groups.values():
        assigned.update(v)
    unassigned = [f for f in all_features if f not in assigned]
    if unassigned:
        groups["Other"] = unassigned
    return {k: v for k, v in groups.items() if len(v) >= 3}


def get_clean_features(all_features: list[str]) -> list[str]:
    groups = get_feature_groups(all_features)
    to_remove = set()
    for grp_name in HARMFUL_GROUPS:
        to_remove.update(groups.get(grp_name, []))
    return [f for f in all_features if f not in to_remove]


def load_data(end_date: str) -> tuple[pd.DataFrame, list[str]]:
    """Load CSI800 data with clean features up to end_date."""
    print(f"[Data] Loading CSI800 data up to {end_date}...")
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()

    raw = adapter.get_features(UNIVERSE, all_features + ["$close"],
                               start_time="2022-01-01", end_time=end_date)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]

    # Ensure $open is available ($open is already in all_features, but some
    # qlib APIs may return it under a different name — make a defensive copy)
    if "$open" not in frame.columns:
        try:
            insts = D.instruments(UNIVERSE)
            open_raw = D.features(insts, ["$open"], start_time="2022-01-01", end_time=end_date)
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
    print(f"  Time: {time.time() - t0:.1f}s")
    make_forward_returns(frame, horizons=[1, 5, 20])
    return frame, clean_features


def train_model(X_train: pd.DataFrame, y_train: pd.Series, tag: str, n_est: int | None = None) -> tuple[lgb.Booster, pd.Series, pd.Series, float | None, list[tuple[str, float]]]:
    """Train a single model. Returns (model, center, scale, ric, top_features)."""
    if n_est is None:
        n_est = N_ESTIMATORS
    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    N = len(Xz)
    vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_train.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_train.iloc[-vs:].values)
    model = lgb.train(LGB_PARAMS, train_data, num_boost_round=n_est,
                      valid_sets=[val_data],
                      callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y_train.notna()
    ric: float | None = None
    if valid.sum() > 0:
        ric = float(pred[valid].corr(y_train[valid], method="spearman"))
        print(f"    [{tag}] Train RankIC={ric:.5f}, trees={model.best_iteration}")

    # Top-20 feature importances by gain
    imp = model.feature_importance(importance_type="gain")
    fi = [(X_train.columns[i], float(imp[i])) for i in np.argsort(imp)[::-1][:20] if imp[i] > 0]
    return model, center, scale, ric, fi


def save_models(models: dict[str, tuple], clean_features: list[str], meta: dict[str, Any]) -> Path:
    """Save model artifacts to disk."""
    version = meta["version"]
    model_dir = MODEL_DIR / version
    model_dir.mkdir(parents=True, exist_ok=True)

    for tag in ["5d", "20d"]:
        model, center, scale, ric, _ = models[tag]
        model.save_model(str(model_dir / f"model_{tag}.txt"))
        (model_dir / f"center_{tag}.json").write_text(
            json.dumps({str(k): float(v) for k, v in center.items()}), encoding="utf-8")
        (model_dir / f"scale_{tag}.json").write_text(
            json.dumps({str(k): float(v) for k, v in scale.items()}), encoding="utf-8")

    # Save feature list
    (model_dir / "features.json").write_text(json.dumps(clean_features, indent=2), encoding="utf-8")

    # Save meta
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update latest symlink
    latest_link = MODEL_DIR / "latest"
    if latest_link.is_symlink():
        latest_link.unlink()
    elif latest_link.exists():
        import shutil
        shutil.rmtree(str(latest_link))
    latest_link.symlink_to(Path(version))

    print(f"  Models saved: {model_dir}/")
    return model_dir


def main() -> None:
    t_start = time.time()
    parser = argparse.ArgumentParser(description="Alpha V1 Weekly Model Training")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date for training data (default: last trading day)")
    parser.add_argument("--version", type=str, default=None,
                        help="Model version tag (default: auto YYYYMMDD)")
    args = parser.parse_args()

    # Resolve end date
    adapter = QlibAdapter()
    adapter.init_qlib()
    calendar = D.calendar()
    if args.end_date:
        end_ts = pd.Timestamp(args.end_date)
    else:
        end_ts = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"))
    cal_dates = sorted(set(pd.Timestamp(d).strftime("%Y-%m-%d") for d in calendar))
    cal_dates = [d for d in cal_dates if d <= end_ts.strftime("%Y-%m-%d")]
    if not cal_dates:
        print("ERROR: No trading days available")
        sys.exit(1)
    end_date = cal_dates[-1]
    version = args.version or end_date.replace("-", "")

    print("=" * 70)
    print("Alpha V1 Weekly Model Training")
    print(f"  End date:   {end_date}")
    print(f"  Version:    {version}")
    print("=" * 70)

    # 1. Load data
    frame, clean_features = load_data(end_date)
    all_dates = sorted(frame["trade_date"].unique())

    # 2. Determine training window (last TRAIN_DAYS trading days)
    end_idx = len(all_dates) - 1
    start_idx = max(0, end_idx - TRAIN_DAYS)
    train_start = all_dates[start_idx]
    train_end = all_dates[end_idx]

    print(f"\n[Training Window] {train_start} ~ {train_end}")
    print(f"  Trading days: {end_idx - start_idx + 1}")

    train_mask = (frame["trade_date"] >= train_start) & (frame["trade_date"] <= train_end)
    train_data = frame[train_mask].copy()
    print(f"  Training rows: {len(train_data)}")

    # 3. Train dual models
    models: dict[str, tuple] = {}
    for tag, h in [("5d", 5), ("20d", 20)]:
        print(f"\n  Training clean_{tag} (horizon={h}d)...")
        t0 = time.time()
        y_train = make_zs_label(h)(train_data)
        X_tr = train_data[clean_features].astype(np.float32).fillna(0.0)
        y_tr = y_train[pd.notna(y_train)]
        valid_rows = y_tr.index
        X_tr_valid = X_tr.loc[valid_rows]
        models[tag] = train_model(X_tr_valid, y_tr, tag)
        print(f"    Time: {time.time() - t0:.1f}s")

    # 4. Save models
    meta = {
        "version": version,
        "end_date": end_date,
        "trained_at": datetime.now().isoformat(),
        "universe": UNIVERSE,
        "train_days": TRAIN_DAYS,
        "train_start": str(train_start),
        "train_end": str(train_end),
        "feature_count": len(clean_features),
        "training_rows": len(train_data),
        "trading_days": end_idx - start_idx + 1,
        "model_params": {k: str(v) if callable(v) else v for k, v in LGB_PARAMS.items()},
    }
    model_dir = save_models(models, clean_features, meta)

    # 5. Build Telegram message
    total_time = time.time() - t_start
    ric_5d = models["5d"][3]
    ric_20d = models["20d"][3]
    tree_5d = models["5d"][0].best_iteration
    tree_20d = models["20d"][0].best_iteration

    # Feature importance top-10
    fi_5d = models["5d"][4]  # list of (feature, gain)
    fi_20d = models["20d"][4]
    fi_lines_5d = "\n".join(f"  #{i+1} {feat} ({g:.2f})" for i, (feat, g) in enumerate(fi_5d[:10]))
    fi_lines_20d = "\n".join(f"  #{i+1} {feat} ({g:.2f})" for i, (feat, g) in enumerate(fi_20d[:10]))

    model_5d_line = f"Clean 5d : trees={tree_5d}"
    if ric_5d is not None:
        model_5d_line += f" | RankIC={ric_5d:.4f}"
    model_20d_line = f"Clean 20d: trees={tree_20d}"
    if ric_20d is not None:
        model_20d_line += f" | RankIC={ric_20d:.4f}"

    msg_lines = [
        "🧠 <b>Alpha V1 Weekly Training</b>",
        f"Version: {version} | End: {end_date}",
        f"⏱ {total_time:.0f}s",
        "",
        "<b>Training Data</b>",
        f"Universe: {UNIVERSE} | Window: {train_start} ~ {train_end}",
        f"Features: {len(clean_features)} | Rows: {len(train_data):,}",
        f"Trading days: {end_idx - start_idx + 1}",
        "",
        "<b>Model Performance</b>",
        model_5d_line,
        model_20d_line,
        "",
        "<b>Top Features (5d)</b>",
        fi_lines_5d,
        "",
        "<b>Top Features (20d)</b>",
        fi_lines_20d,
        "",
        "<b>Artifacts</b>",
        f"Saved: {model_dir}/",
    ]

    msg_text = "\n".join(msg_lines)
    print(f"\n[Telegram]")
    try:
        result = send_telegram_message(msg_text)
        if result.get("status") == "success":
            print("  Telegram sent successfully")
        else:
            print(f"  Telegram skipped: {result.get('error', 'unknown')}")
    except Exception as e:
        print(f"  Telegram failed: {e}")

    # 6. Summary
    total_time = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"Done in {total_time:.0f}s")
    print(f"Models: {model_dir}/")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
