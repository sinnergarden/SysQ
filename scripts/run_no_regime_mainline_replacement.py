#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qlib.data import D

from qsys.backtest import BacktestEngine
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.rolling import RollingDefaults, RollingWindow, build_rolling_summary, compute_window_metrics

BASE_MODEL_DIR = PROJECT_ROOT / "data/models/qlib_lgbm_semantic_all_features"
DEFAULT_START = "2024-01-02"
DEFAULT_TOP_K = 5
DEFAULT_TRAIN_YEARS = 4
DEFAULT_WEEKLY_STEP = 5
DEFAULT_LABEL_HORIZON = 5


@dataclass(frozen=True)
class VariantSpec:
    mainline_object_name: str
    model_name: str
    feature_set: str
    bundle_id: str
    feature_config: list[str]
    label_col: str
    label_type: str
    description: str


VARIANT_ORDER = [
    "feature_254",
    "feature_254_xs5",
    "feature_254_smooth135",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train no-regime mainline replacements and run weekly rolling backtests")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None)
    parser.add_argument("--top_k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--train_years", type=int, default=DEFAULT_TRAIN_YEARS)
    parser.add_argument("--step_trading_days", type=int, default=DEFAULT_WEEKLY_STEP)
    parser.add_argument("--label_horizon", type=int, default=DEFAULT_LABEL_HORIZON)
    parser.add_argument("--output_dir", default="experiments/mainline_rolling")
    parser.add_argument("--report_path", default="scratch/mainline_replacement_rolling_report_20260518.md")
    parser.add_argument("--variant", action="append", dest="variants", default=None, help="Repeatable mainline object selector")
    parser.add_argument("--resume", action="store_true", help="Resume from existing rolling_metrics.csv if present")
    return parser.parse_args()


def load_base_meta() -> dict:
    return yaml.safe_load((BASE_MODEL_DIR / "meta.yaml").read_text(encoding="utf-8")) or {}


def latest_trading_day() -> str:
    adapter = QlibAdapter()
    adapter.init_qlib()
    status = adapter.get_data_status_report()
    latest = status.get("qlib_latest") or status.get("raw_latest")
    if not latest:
        raise RuntimeError("Failed to resolve qlib_latest/raw_latest")
    return pd.Timestamp(latest).strftime("%Y-%m-%d")


def robust_zscore_fit(X: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    center = X.median()
    scale = (X - center).abs().median().replace(0, 1.0)
    Xn = ((X - center) / scale).clip(-3, 3).fillna(0.0)
    return Xn, center, scale


def robust_zscore_apply(X: pd.DataFrame, center: pd.Series, scale: pd.Series) -> pd.DataFrame:
    center = center.reindex(X.columns).fillna(0.0)
    scale = scale.reindex(X.columns).replace(0, 1.0).fillna(1.0)
    return ((X - center) / scale).clip(-3, 3).fillna(0.0)


def xs_zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def build_variants(selected: list[str] | None = None) -> list[VariantSpec]:
    variants = [
        VariantSpec(
            mainline_object_name="feature_254",
            model_name=MAINLINE_OBJECTS["feature_254"].model_name,
            feature_set="semantic_all_features",
            bundle_id=MAINLINE_OBJECTS["feature_254"].bundle_id,
            feature_config=FeatureLibrary.get_semantic_all_features_config(),
            label_col="label_prod_5",
            label_type="forward_return",
            description="historical non-norm semantic baseline",
        ),
        VariantSpec(
            mainline_object_name="feature_254_xs5",
            model_name=MAINLINE_OBJECTS["feature_254_xs5"].model_name,
            feature_set="semantic_no_regime_xs5",
            bundle_id=MAINLINE_OBJECTS["feature_254_xs5"].bundle_id,
            feature_config=FeatureLibrary.get_semantic_no_regime_config(),
            label_col="label_xs5",
            label_type="xs_forward_return",
            description="no-regime semantic with 5d cross-sectional zscore label",
        ),
        VariantSpec(
            mainline_object_name="feature_254_smooth135",
            model_name=MAINLINE_OBJECTS["feature_254_smooth135"].model_name,
            feature_set="semantic_no_regime_smooth135",
            bundle_id=MAINLINE_OBJECTS["feature_254_smooth135"].bundle_id,
            feature_config=FeatureLibrary.get_semantic_no_regime_config(),
            label_col="label_smooth_135",
            label_type="xs_smooth_135",
            description="no-regime semantic with 1/3/5d smoothed cross-sectional label",
        ),
    ]
    if not selected:
        return variants
    selected_set = set(selected)
    return [variant for variant in variants if variant.mainline_object_name in selected_set]


def add_labels(frame: pd.DataFrame) -> pd.DataFrame:
    g = frame.groupby("instrument")["$close"]
    out = frame.copy()
    out["label_prod_5"] = g.shift(-5) / g.shift(-1) - 1.0
    out["label_fwd_1"] = g.shift(-1) / out["$close"] - 1.0
    out["label_fwd_3"] = g.shift(-3) / out["$close"] - 1.0
    out["label_fwd_5"] = g.shift(-5) / out["$close"] - 1.0
    out["label_xs5"] = out.groupby("trade_date")["label_fwd_5"].transform(xs_zscore)
    out["label_smooth_135"] = (
        0.2 * out.groupby("trade_date")["label_fwd_1"].transform(xs_zscore)
        + 0.3 * out.groupby("trade_date")["label_fwd_3"].transform(xs_zscore)
        + 0.5 * out.groupby("trade_date")["label_fwd_5"].transform(xs_zscore)
    ).clip(-3, 3)
    return out


def fetch_frame(start: str, end: str, all_features: list[str]) -> pd.DataFrame:
    adapter = QlibAdapter()
    adapter.init_qlib()
    raw = adapter.get_features(
        instruments=D.instruments("csi300"),
        fields=sorted(set(all_features + ["$close"])),
        start_time=start,
        end_time=end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    return frame


def previous_trading_day(value: str, n: int) -> str:
    ts = pd.Timestamp(value)
    calendar = D.calendar(start_time=ts - pd.Timedelta(days=max(40, n * 6)), end_time=ts)
    candidates = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) < ts]
    if len(candidates) < n:
        raise ValueError(f"Not enough prior trading days before {value} for horizon={n}")
    return candidates[-n].strftime("%Y-%m-%d")


def build_weekly_windows(start: str, end: str, step_trading_days: int, train_years: int, label_horizon: int) -> list[RollingWindow]:
    calendar = [pd.Timestamp(x) for x in D.calendar(start_time=pd.Timestamp(start), end_time=pd.Timestamp(end))]
    windows: list[RollingWindow] = []
    for idx in range(0, len(calendar), step_trading_days):
        test_days = calendar[idx : idx + step_trading_days]
        if not test_days:
            continue
        test_start = test_days[0].strftime("%Y-%m-%d")
        test_end = test_days[-1].strftime("%Y-%m-%d")
        train_end = previous_trading_day(test_start, label_horizon)
        train_start = (pd.Timestamp(train_end) - pd.DateOffset(years=train_years) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        windows.append(
            RollingWindow(
                window_id=f"week_{len(windows) + 1:03d}",
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return windows


def train_model(frame: pd.DataFrame, variant: VariantSpec, window: RollingWindow, params: dict) -> tuple[lgb.Booster, pd.Series, pd.Series, dict]:
    train = frame[(frame["trade_date"] >= pd.Timestamp(window.train_start)) & (frame["trade_date"] <= pd.Timestamp(window.train_end))].copy()
    train = train.dropna(subset=[variant.label_col])
    X = train[variant.feature_config].astype(float)
    y = train[variant.label_col].astype(float)
    Xn, center, scale = robust_zscore_fit(X)
    Xn.columns = [f"f_{i:04d}" for i in range(Xn.shape[1])]
    reg = lgb.LGBMRegressor(**params)
    reg.fit(Xn, y)
    pred = pd.Series(reg.predict(Xn), index=train.index)
    aligned = pd.DataFrame({"score": pred, "label": y}, index=train.index)
    training = {
        "sample_count": int(len(aligned)),
        "feature_count": int(len(variant.feature_config)),
        "rank_ic": float(aligned["score"].corr(aligned["label"], method="spearman")) if len(aligned) else float("nan"),
        "mse": float(((aligned["score"] - aligned["label"]) ** 2).mean()) if len(aligned) else float("nan"),
        "score_std": float(aligned["score"].std()) if len(aligned) else float("nan"),
        "nonzero_split_features": int(np.sum(reg.booster_.feature_importance(importance_type="split") > 0)),
        "train_start": window.train_start,
        "train_end_effective": window.train_end,
        "label_type": variant.label_type,
        "label_name": variant.label_col,
        "is_label_mature_at_infer_time": True,
        "infer_date": window.test_start,
    }
    return reg.booster_, center, scale, training


def predict_scores(frame: pd.DataFrame, variant: VariantSpec, window: RollingWindow, booster: lgb.Booster, center: pd.Series, scale: pd.Series) -> pd.Series:
    eval_frame = frame[(frame["trade_date"] >= pd.Timestamp(window.test_start)) & (frame["trade_date"] <= pd.Timestamp(window.test_end))].copy()
    pieces: list[pd.Series] = []
    for dt, day in eval_frame.groupby("trade_date"):
        X = day.set_index("instrument")[variant.feature_config].astype(float)
        Xn = robust_zscore_apply(X, center, scale)
        Xn.columns = [f"f_{i:04d}" for i in range(Xn.shape[1])]
        scores = pd.Series(booster.predict(Xn), index=X.index)
        idx = pd.MultiIndex.from_arrays([[dt] * len(scores), scores.index], names=["datetime", "instrument"])
        pieces.append(pd.Series(scores.values, index=idx))
    if not pieces:
        return pd.Series(dtype=float)
    return pd.concat(pieces).sort_index()


def save_model_artifact(target_dir: Path, variant: VariantSpec, booster: lgb.Booster, center: pd.Series, scale: pd.Series, training: dict, infer_date: str) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(booster, target_dir / "model.pkl")
    meta = {
        "name": variant.model_name,
        "params": load_base_meta().get("params", {}),
        "feature_config": variant.feature_config,
        "feature_set": variant.feature_set,
        "preprocess_params": {
            "method": "qlib_robust_zscore",
            "center": {k: float(v) for k, v in center.items()},
            "scale": {k: float(v) for k, v in scale.items()},
            "clip_outlier": True,
            "fillna": 0.0,
        },
        "training_summary": training,
    }
    (target_dir / "meta.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    config_snapshot = {
        "model_name": variant.model_name,
        "model_type": "qlib_lgbm",
        "input_mode": "feature_set",
        "feature_set": variant.feature_set,
        "bundle_id": variant.bundle_id,
        "mainline_object_name": variant.mainline_object_name,
        "legacy_feature_set_alias": variant.feature_set,
        "factor_variants": [],
        "bundle_source": None,
        "bundle_resolution_status": "research_variant_manual",
        "object_layer_status": "research_variant_manual",
        "universe": "csi300",
        "train_start": training["train_start"],
        "train_end": infer_date,
        "infer_date": infer_date,
        "label_spec": {
            "label_type": variant.label_type,
            "label_horizon": DEFAULT_LABEL_HORIZON,
        },
        "split_spec": {
            "train_start": training["train_start"],
            "train_end_requested": infer_date,
            "train_end_effective": training["train_end_effective"],
            "infer_date": infer_date,
            "universe": "csi300",
            "is_label_mature_at_infer_time": True,
        },
        "model_spec": {
            "model_type": "qlib_lgbm",
            "model_name": variant.model_name,
            "training_mode": "manual_semantic_no_regime_research",
            "mlflow_root": None,
        },
        "strategy_spec": {},
    }
    (target_dir / "config_snapshot.json").write_text(json.dumps(config_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def run_variant_rolling(frame: pd.DataFrame, variant: VariantSpec, windows: list[RollingWindow], params: dict, top_k: int, out_root: Path, *, resume: bool = False) -> tuple[dict, pd.DataFrame]:
    object_dir = out_root / variant.mainline_object_name
    object_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([w.to_dict() for w in windows]).to_csv(object_dir / "rolling_windows.csv", index=False)

    metrics_path = object_dir / "rolling_metrics.csv"
    metric_rows: list[dict] = []
    completed_window_ids: set[str] = set()
    if resume and metrics_path.exists():
        existing = pd.read_csv(metrics_path)
        if not existing.empty:
            metric_rows = existing.to_dict(orient="records")
            completed_window_ids = {str(v) for v in existing.get("window_id", pd.Series(dtype=str)).dropna().tolist()}
    last_artifact: tuple[lgb.Booster, pd.Series, pd.Series, dict, str] | None = None
    for window in windows:
        if window.window_id in completed_window_ids:
            continue
        booster, center, scale, training = train_model(frame, variant, window, params)
        scores = predict_scores(frame, variant, window, booster, center, scale)
        engine = BacktestEngine(
            start_date=window.test_start,
            end_date=window.test_end,
            daily_predictions=scores,
            top_k=top_k,
            label_horizon="1d_fixed_in_v1_impl1",
        )
        daily_result = engine.run()
        summary = engine.last_summary or {}
        signal = engine.last_signal_metrics or {}
        metric_row = compute_window_metrics(
            spec=MAINLINE_OBJECTS[variant.mainline_object_name],
            window=window,
            daily_result=daily_result,
            signal_metrics=signal,
        )
        metric_row.update(
            {
                "train_rank_ic": training["rank_ic"],
                "nonzero_split_features": training["nonzero_split_features"],
            }
        )
        metric_rows.append(metric_row)
        last_artifact = (booster, center, scale, training, window.test_end)
        pd.DataFrame(metric_rows).to_csv(metrics_path, index=False)

    metrics_frame = pd.DataFrame(metric_rows)
    metrics_frame.to_csv(metrics_path, index=False)
    defaults = RollingDefaults(
        universe="csi300",
        top_k=top_k,
        strategy_type="rank_topk",
        label_horizon="1d_fixed_in_v1_impl1",
        test_window_days=DEFAULT_WEEKLY_STEP,
        step_days=DEFAULT_WEEKLY_STEP,
    )
    summary = build_rolling_summary(metrics_frame, defaults)
    summary.update(
        {
            "model_path": f"data/models/{variant.model_name}",
            "lineage": {
                "feature_set": variant.feature_set,
                "bundle_id": variant.bundle_id,
                "mainline_object_name": variant.mainline_object_name,
                "label_type": variant.label_type,
            },
            "leakage_audit": {
                "label_horizon": DEFAULT_LABEL_HORIZON,
                "all_windows_mature": True,
                "train_end_rule": "previous_trading_day(test_start, 5)",
            },
        }
    )
    (object_dir / "rolling_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if last_artifact is not None and variant.mainline_object_name != "feature_254":
        booster, center, scale, training, infer_date = last_artifact
        save_model_artifact(PROJECT_ROOT / "data" / "models" / variant.model_name, variant, booster, center, scale, training, infer_date)

    return summary, metrics_frame


def build_markdown_report(summaries: list[dict], report_path: Path, windows: list[RollingWindow], end: str) -> None:
    lines = [
        "# Mainline replacement rolling report (2026-05-18)",
        "",
        f"- range: `{windows[0].test_start}` ~ `{end}`",
        f"- cadence: weekly retrain / weekly test (`{DEFAULT_WEEKLY_STEP}` trading days)",
        "- leakage guard: train_end_effective = previous_trading_day(test_start, 5)",
        "",
        "## Summary",
        "",
        "| object | return_mean | rankic_mean | turnover_mean | mdd_worst | windows |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {name} | {ret:.2%} | {ric:.4f} | {to:.4f} | {mdd:.2%} | {cnt} |".format(
                name=row.get("mainline_object_name"),
                ret=float(row.get("rolling_total_return_mean") or 0.0),
                ric=float(row.get("rolling_rankic_mean") or 0.0),
                to=float(row.get("rolling_turnover_mean") or 0.0),
                mdd=float(row.get("rolling_max_drawdown_worst") or 0.0),
                cnt=int(row.get("rolling_window_count") or 0),
            )
        )
    lines += [
        "",
        "## Reading",
        "",
        "- `feature_254`: 旧 non-norm semantic 主线，对照组。",
        "- `feature_254_xs5`: no_regime + 5d 横截面 zscore label。",
        "- `feature_254_smooth135`: no_regime + 1/3/5 横截面 smooth label。",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    adapter = QlibAdapter()
    adapter.init_qlib()

    end = args.end or latest_trading_day()
    windows = build_weekly_windows(args.start, end, args.step_trading_days, args.train_years, args.label_horizon)
    if not windows:
        raise RuntimeError("No rolling windows built")

    variants = build_variants(args.variants)
    all_features = sorted({field for variant in variants for field in variant.feature_config})
    data_start = min(w.train_start for w in windows if w.train_start)
    frame = add_labels(fetch_frame(data_start, end, all_features))

    base_meta = load_base_meta()
    params = dict(base_meta.get("params") or {})
    params.setdefault("n_estimators", 200)

    out_root = (PROJECT_ROOT / args.output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    summaries: list[dict] = []
    for variant in variants:
        summary, _ = run_variant_rolling(frame, variant, windows, params, args.top_k, out_root, resume=args.resume)
        summaries.append(summary)

    comparison = pd.DataFrame(summaries)[[
        "mainline_object_name",
        "bundle_id",
        "legacy_feature_set_alias",
        "rolling_window_count",
        "rolling_total_return_mean",
        "rolling_total_return_median",
        "rolling_rankic_mean",
        "rolling_rankic_std",
        "rolling_max_drawdown_worst",
        "rolling_turnover_mean",
        "rolling_empty_portfolio_ratio_mean",
    ]]
    comparison.to_csv(out_root / "comparison_summary.csv", index=False)
    markdown_lines = [
        "# Mainline rolling comparison",
        "",
        "| object | return_mean | return_median | rankic_mean | rankic_std | mdd_worst | turnover_mean | windows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.to_dict(orient="records"):
        markdown_lines.append(
            "| {name} | {ret:.2%} | {ret_med:.2%} | {ric:.4f} | {ric_std:.4f} | {mdd:.2%} | {turnover:.4f} | {cnt} |".format(
                name=row["mainline_object_name"],
                ret=float(row["rolling_total_return_mean"] or 0.0),
                ret_med=float(row["rolling_total_return_median"] or 0.0),
                ric=float(row["rolling_rankic_mean"] or 0.0),
                ric_std=float(row["rolling_rankic_std"] or 0.0),
                mdd=float(row["rolling_max_drawdown_worst"] or 0.0),
                turnover=float(row["rolling_turnover_mean"] or 0.0),
                cnt=int(row["rolling_window_count"] or 0),
            )
        )
    (out_root / "comparison_summary.md").write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    build_markdown_report(summaries, (PROJECT_ROOT / args.report_path).resolve(), windows, end)

    print(json.dumps({
        "status": "ok",
        "range": [args.start, end],
        "windows": len(windows),
        "variants": [v.mainline_object_name for v in variants],
        "output_dir": str(out_root),
        "report_path": str((PROJECT_ROOT / args.report_path).resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
