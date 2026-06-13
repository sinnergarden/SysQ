"""
Generate BacktestReport JSONs for smooth135 / xs5 rolling results so the UI can display them.

Reads from experiments/mainline_rolling_runs/<run>/, builds a BacktestReport + synthetic
daily equity curve, and writes to experiments/reports/backtest_<run_id>.json.
"""

import warnings
warnings.warn(
    "DEPRECATED: generate_ui_reports.py is superseded by UC-standard entrypoints. Scheduled for removal.",
    DeprecationWarning, stacklevel=2,
)

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from qsys.reports.backtest import BacktestReport
from qsys.reports.base import ReportSection, ReportStatus, save_report

INITIAL_CASH = 100_000.0

BASE_PATH = PROJECT_ROOT / "experiments" / "mainline_rolling_runs"

REPORTS = [
    {
        "run_id": "20260518_portfoliofix_smooth135",
        "model_name": "qlib_lgbm_semantic_no_regime_smooth135",
        "model_path": "data/models/qlib_lgbm_semantic_no_regime_smooth135",
        "mainline_object_name": "feature_254_smooth135",
        "label_type": "xs_smooth_135",
        "description": "no_regime + smooth 1/3/5d cross-sectional label",
        "source_dir": BASE_PATH / "20260518_portfoliofix_smooth135" / "feature_254_smooth135",
    },
    {
        "run_id": "20260518_portfoliofix_xs5",
        "model_name": "qlib_lgbm_semantic_no_regime_xs5",
        "model_path": "data/models/qlib_lgbm_semantic_no_regime_xs5",
        "mainline_object_name": "feature_254_xs5",
        "label_type": "xs_forward_return",
        "description": "no_regime + 5d cross-sectional zscore label",
        "source_dir": BASE_PATH / "20260518_portfoliofix_xs5" / "feature_254_xs5",
    },
    {
        "run_id": "20260518_portfoliofix_baseline",
        "model_name": "qlib_lgbm_semantic_all_features",
        "model_path": "data/models/qlib_lgbm_semantic_all_features",
        "mainline_object_name": "feature_254",
        "label_type": "forward_return",
        "description": "baseline semantic_all_features (对照组)",
        "source_dir": BASE_PATH / "20260518_portfoliofix_baseline" / "feature_254",
    },
]


def build_synthetic_daily(metrics: pd.DataFrame, initial_cash: float = INITIAL_CASH) -> pd.DataFrame:
    """Build a continuous daily equity curve from per-window total_return values."""
    rows = []
    cash = initial_cash
    for _, row in metrics.iterrows():
        window_id = row["window_id"]
        total_ret = float(row["total_return"])
        test_start = row["test_start"]
        test_end = row["test_end"]

        n_days = max(1, (pd.Timestamp(test_end) - pd.Timestamp(test_start)).days + 1)
        daily_ret = (1 + total_ret) ** (1 / n_days) - 1

        for d in range(n_days):
            date = (pd.Timestamp(test_start) + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
            if d == 0:
                day_ret = daily_ret
            else:
                day_ret = daily_ret
            cash *= 1 + day_ret
            rows.append({"date": date, "total_assets": round(cash, 2), "window_id": window_id})

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
    return df


def compute_metrics_from_df(df: pd.DataFrame, initial_cash: float = INITIAL_CASH) -> dict:
    eq = df["total_assets"]
    returns = eq.pct_change().dropna()
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    max_dd = (eq / eq.cummax() - 1).min()
    sharpe = returns.mean() / returns.std() * (252 ** 0.5) if returns.std() > 0 else 0.0
    ann_ret = (1 + total_return) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0.0
    ann_vol = returns.std() * (252 ** 0.5)
    return {
        "total_return": f"{total_return * 100:.2f}%",
        "annual_return": f"{ann_ret * 100:.2f}%",
        "annual_vol": f"{ann_vol * 100:.2f}%",
        "sharpe": f"{sharpe:.3f}",
        "max_drawdown": f"{max_dd * 100:.2f}%",
        "days": int(len(returns)),
    }


def main() -> None:
    reports_root = PROJECT_ROOT / "experiments" / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)

    for spec in REPORTS:
        source_dir = spec["source_dir"]
        if not source_dir.exists():
            print(f"[SKIP] source not found: {source_dir}")
            continue

        metrics = pd.read_csv(source_dir / "rolling_metrics.csv")
        summary = json.loads((source_dir / "rolling_summary.json").read_text(encoding="utf-8"))
        run_id = spec["run_id"]
        filepath = reports_root / f"backtest_{run_id}.json"
        if filepath.exists():
            print(f"[SKIP] already exists: {filepath}")
            continue

        # ── Build synthetic daily equity ──
        daily = build_synthetic_daily(metrics)
        daily_path = reports_root.parent / f"backtest_result_{run_id}.csv"
        daily.to_csv(daily_path, index=False)

        perf = compute_metrics_from_df(daily)
        n_windows = len(metrics)
        pos_ret = int((metrics["total_return"] > 0).sum())
        win_rate = pos_ret / n_windows if n_windows > 0 else 0.0
        mean_ret = float(metrics["total_return"].mean())
        median_ret = float(metrics["total_return"].median())

        has_ic = "IC" in metrics.columns
        has_rankic = "RankIC" in metrics.columns
        has_ls = "long_short_spread" in metrics.columns

        ic_mean = float(metrics["IC"].mean()) if has_ic else None
        rankic_mean = float(metrics["RankIC"].mean()) if has_rankic else None
        rankic_std = float(metrics["RankIC"].std()) if has_rankic else None
        ls_mean = float(metrics["long_short_spread"].mean()) if has_ls else None
        ic_pos = int((metrics["IC"] > 0).sum()) if has_ic else 0
        rankic_pos = int((metrics["RankIC"] > 0).sum()) if has_rankic else 0
        ls_pos = int((metrics["long_short_spread"] > 0).sum()) if has_ls else 0

        turnover_mean = float(metrics["turnover"].mean()) if "turnover" in metrics.columns else 0.0
        total_fees = float(metrics["total_fees"].sum()) if "total_fees" in metrics.columns else 0.0

        # ── Compute monthly returns from synthetic daily ──
        monthly_pd = daily.copy()
        monthly_pd["date"] = pd.to_datetime(monthly_pd["date"])
        monthly_pd["month"] = monthly_pd["date"].dt.to_period("M").astype(str)
        monthly_grp = monthly_pd.groupby("month").agg(
            start_assets=("total_assets", "first"), end_assets=("total_assets", "last")
        )
        monthly_grp["return"] = monthly_grp["end_assets"] / monthly_grp["start_assets"] - 1
        monthly_returns = [
            {"month": idx, "return": round(float(row["return"]), 6)}
            for idx, row in monthly_grp.iterrows()
        ]
        pos_months = sum(1 for m in monthly_returns if m["return"] > 0)

        # ── Construct report ──
        report = BacktestReport.from_backtest_result(
            result_df=daily,
            model_path=str(PROJECT_ROOT / spec["model_path"]),
            start_date=str(metrics["test_start"].iloc[0]),
            end_date=str(metrics["test_end"].iloc[-1]),
            top_k=5,
            universe="csi300",
            duration_seconds=0,
            daily_result_path=str(daily_path),
            experiment_spec={
                "rolling": {
                    "window_count": n_windows,
                    "test_window_days": 5,
                    "step_days": 5,
                    "windows_completed": n_windows,
                    "windows_failed": 0,
                    "retrain_per_window": True,
                    "label_type": spec["label_type"],
                },
                "initial_cash": INITIAL_CASH,
                "model_path": spec["model_path"],
                "universe": "csi300",
                "start": str(metrics["test_start"].iloc[0]),
                "end": str(metrics["test_end"].iloc[-1]),
                "top_k": 5,
            },
        )
        report.run_id = run_id
        report.model_info["model_name"] = spec["model_name"]
        report.model_info["mainline_object_name"] = spec["mainline_object_name"]
        report.model_info["label_type"] = spec["label_type"]
        report.model_info["feature_set"] = summary.get("lineage", {}).get("feature_set", spec.get("description", ""))

        # ── Build sections ──
        sections = []

        def sec(name: str, metrics: dict) -> ReportSection:
            return ReportSection(name=name, status=ReportStatus.SUCCESS, metrics=metrics)

        sections.append(sec("Performance", perf))

        sections.append(sec("Cost Analysis", {
            "total_fees": f"{total_fees:.2f}",
            "avg_daily_turnover": f"{turnover_mean:.4f}",
        }))

        sections.append(sec("Rolling Windows", {
            "window_count": str(n_windows),
            "positive_windows": str(pos_ret),
            "window_win_rate": f"{win_rate * 100:.1f}%",
            "mean_window_return": f"{mean_ret * 100:.2f}%",
            "median_window_return": f"{median_ret * 100:.2f}%",
            "best_window_return": f"{metrics['total_return'].max() * 100:.2f}%",
            "worst_window_return": f"{metrics['total_return'].min() * 100:.2f}%",
        }))

        sig_metrics = {}
        if ic_mean is not None:
            sig_metrics.update({
                "IC_mean": f"{ic_mean:.6f}",
                "IC_std": f"{float(metrics['IC'].std()):.6f}",
                "IC_positive_ratio": f"{ic_pos / n_windows * 100:.1f}%",
            })
        if rankic_mean is not None:
            sig_metrics.update({
                "RankIC_mean": f"{rankic_mean:.6f}",
                "RankIC_std": f"{rankic_std:.6f}",
                "RankIC_positive_ratio": f"{rankic_pos / n_windows * 100:.1f}%",
            })
        if ls_mean is not None:
            sig_metrics.update({
                "long_short_spread_mean": f"{ls_mean:.6f}",
                "long_short_spread_std": f"{float(metrics['long_short_spread'].std()):.6f}",
                "long_short_spread_positive_ratio": f"{ls_pos / n_windows * 100:.1f}%",
            })
        sections.append(sec("Signal Metrics", sig_metrics))

        sections.append(sec("Monthly Returns", {
            "positive_months": f"{pos_months}/{len(monthly_returns)}",
            "monthly_win_rate": f"{pos_months / len(monthly_returns) * 100:.1f}%" if monthly_returns else "0%",
            "best_month": f"{max(m['return'] for m in monthly_returns) * 100:.2f}%" if monthly_returns else "0%",
            "worst_month": f"{min(m['return'] for m in monthly_returns) * 100:.2f}%" if monthly_returns else "0%",
            "avg_monthly_return": f"{sum(m['return'] for m in monthly_returns) / len(monthly_returns) * 100:.4f}%" if monthly_returns else "0%",
        }))

        report.sections = sections

        # ── Artifacts ──
        # Save rolling_windows.csv for per-window charts
        rolling_windows_path = reports_root.parent / f"rolling_windows_{run_id}.csv"
        metrics.to_csv(rolling_windows_path, index=False)

        # Build per-window signal metrics JSON
        signal_rows = []
        for _, row in metrics.iterrows():
            sig_row = {"window_id": row["window_id"]}
            if has_ic:
                sig_row["IC"] = float(row["IC"])
            if has_rankic:
                sig_row["RankIC"] = float(row["RankIC"])
            if has_ls:
                sig_row["long_short_spread"] = float(row["long_short_spread"])
            signal_rows.append(sig_row)
        signal_path = reports_root.parent / f"signal_metrics_{run_id}.json"
        with open(signal_path, "w") as f:
            json.dump({"aggregate": sig_metrics, "per_window": signal_rows}, f, indent=2)

        # Monthly returns JSON
        monthly_path = reports_root.parent / f"monthly_returns_{run_id}.json"
        with open(monthly_path, "w") as f:
            json.dump(monthly_returns, f, indent=2)

        # Empty group_returns CSV
        group_path = reports_root.parent / f"group_returns_{run_id}.csv"
        pd.DataFrame().to_csv(group_path, index=False)

        # Empty execution_audit CSV
        audit_path = reports_root.parent / f"execution_audit_{run_id}.csv"
        pd.DataFrame().to_csv(audit_path, index=False)

        report.artifacts = {
            "daily_result": str(daily_path),
            "signal_metrics": str(signal_path),
            "group_returns": str(group_path),
            "execution_audit": str(audit_path),
            "rolling_windows": str(rolling_windows_path),
            "monthly_returns": str(monthly_path),
        }

        # ── Save ──
        saved = save_report(report, output_dir=str(reports_root))
        print(f"[OK] {run_id}: {saved}")
        print(f"     daily={daily_path.name}, windows={n_windows}, return={perf['total_return']}, rankic_pos={rankic_pos}/{n_windows}")


if __name__ == "__main__":
    main()
