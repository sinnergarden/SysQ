"""
Rolling weekly retrain backtest using the production model config.

Per window: retrain model → backtest → collect metrics.
Outputs a BacktestReport JSON to experiments/reports/ for the UI.

Usage:
    python scripts/run_prod_rolling_backtest.py
"""
import json
import sys
import tempfile
import time
import shutil
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import pandas as pd
import yaml

from qlib.data import D

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.evaluation.evaluator import calculate_metrics
from qsys.model.zoo.qlib_native import QlibNativeModel
from qsys.reports.backtest import BacktestReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.research.rolling import build_rolling_windows, RollingDefaults, compute_window_metrics
from qsys.strategy.engine import DEFAULT_TOP_K
from qsys.trader.account import Account
from qsys.utils.logger import log

START = "2024-01-01"
END = "2026-05-13"
INITIAL_CASH = 100_000.0
TOP_K = 5
UNIVERSE = "csi300"
TEST_WINDOW_DAYS = 63
STEP_DAYS = 5
LABEL_HORIZON = 5

PRODUCTION_MODEL_DIR = project_root / "data" / "models" / "qlib_lgbm_semantic_all_features"


def load_production_model_config() -> dict:
    meta = yaml.safe_load((PRODUCTION_MODEL_DIR / "meta.yaml").read_text(encoding="utf-8"))
    snapshot = json.loads((PRODUCTION_MODEL_DIR / "config_snapshot.json").read_text(encoding="utf-8"))
    split_spec = snapshot.get("split_spec") or {}
    return {
        "feature_config": meta.get("feature_config", []),
        "model_params": dict(meta.get("params", {})),
        "train_start": split_spec.get("train_start") or snapshot.get("train_start", "2023-05-15"),
        "train_end": split_spec.get("train_end") or snapshot.get("train_end", "2026-05-07"),
    }


def make_model(feature_config: list, model_params: dict) -> QlibNativeModel:
    return QlibNativeModel(
        name="rolling_lgbm",
        model_config={
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": dict(model_params),
        },
        feature_config=feature_config,
        label_config=["(Ref($close, -5) / Ref($close, -1) - 1)"],
    )


def rolling_window_metrics(window_results: list[pd.DataFrame]) -> pd.DataFrame:
    """Build per-window metrics dataframe from all window results."""
    rows = []
    for wr in window_results:
        eq = pd.to_numeric(wr["total_assets"], errors="coerce").dropna()
        total_return = float(eq.iloc[-1] / eq.iloc[0] - 1) if not eq.empty else None
        drawdown = (eq / eq.cummax() - 1).min() if not eq.empty else None
        turnover = (pd.to_numeric(wr.get("daily_turnover", pd.Series(dtype=float)), errors="coerce").sum() /
                    pd.to_numeric(wr.get("total_assets", pd.Series(dtype=float)), errors="coerce").iloc[0]) if not eq.empty else None
        rows.append({
            "window_id": wr.attrs.get("window_id", ""),
            "test_start": wr.attrs.get("test_start", ""),
            "test_end": wr.attrs.get("test_end", ""),
            "total_return": total_return,
            "max_drawdown": float(drawdown) if drawdown is not None else None,
            "turnover": float(turnover) if turnover is not None else None,
            "trade_count": int(pd.to_numeric(wr.get("trade_count", pd.Series(dtype=float)), errors="coerce").sum()),
            "total_fees": float(pd.to_numeric(wr.get("daily_fee", pd.Series(dtype=float)), errors="coerce").sum()),
        })
    return pd.DataFrame(rows)


def compute_monthly_returns(all_daily: pd.DataFrame) -> list[dict]:
    if "date" not in all_daily.columns or "total_assets" not in all_daily.columns:
        return []
    df = all_daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)
    monthly = df.groupby("month").agg(
        start_assets=("total_assets", "first"), end_assets=("total_assets", "last")
    )
    monthly["return"] = monthly["end_assets"] / monthly["start_assets"] - 1
    return [
        {"month": idx, "return": round(float(row["return"]), 6)}
        for idx, row in monthly.iterrows()
    ]


def main():
    start_time = time.time()

    # ── Load config ──
    prod_cfg = load_production_model_config()
    log.info("Production config: %d features, train %s → %s",
             len(prod_cfg["feature_config"]), prod_cfg["train_start"], prod_cfg["train_end"])

    # ── Windows ──
    windows = build_rolling_windows(
        start=START, end=END,
        test_window_days=TEST_WINDOW_DAYS, step_days=STEP_DAYS,
    )
    log.info("Rolling windows: %d (step=%dd, window=%dd)", len(windows), STEP_DAYS, TEST_WINDOW_DAYS)

    # ── Init Qlib once ──
    adapter = QlibAdapter()
    adapter.init_qlib()
    instruments = D.instruments(UNIVERSE)

    # ── Per-window loop ──
    window_results: list[pd.DataFrame] = []
    signal_metrics_list: list[dict] = []
    engine_instances: list[BacktestEngine] = []
    errors: list[str] = []
    tmp_dirs: list[Path] = []

    for i, window in enumerate(windows):
        win_start = time.time()
        try:
            log.info("[%d/%d] %s: test %s → %s",
                     i + 1, len(windows), window.window_id,
                     window.test_start, window.test_end)

            # ── 1. Train ──
            model = make_model(prod_cfg["feature_config"], prod_cfg["model_params"])
            model.fit(
                universe=UNIVERSE,
                start_date=prod_cfg["train_start"],
                end_date=window.test_start,
                infer_date=window.test_start,
                label_horizon=LABEL_HORIZON,
            )

            # ── 2. Save to tmp ──
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"rolling_{window.window_id}_"))
            tmp_dirs.append(tmp_dir)
            model.save(str(tmp_dir))

            # ── 3. Backtest ──
            account = Account(init_cash=INITIAL_CASH)
            engine = BacktestEngine(
                model_path=str(tmp_dir),
                universe=UNIVERSE,
                start_date=window.test_start,
                end_date=window.test_end,
                account=account,
                top_k=TOP_K,
            )
            result = engine.run()
            if result.empty:
                raise ValueError("Empty backtest result")

            # ── 4. Attach metadata ──
            result.attrs["window_id"] = window.window_id
            result.attrs["test_start"] = window.test_start
            result.attrs["test_end"] = window.test_end
            window_results.append(result)
            signal_metrics_list.append(engine.last_signal_metrics or {})
            engine_instances.append(engine)

            elapsed = time.time() - win_start
            final_assets = result.iloc[-1]["total_assets"]
            ret = (final_assets / INITIAL_CASH - 1) * 100
            fees = result["daily_fee"].sum() if "daily_fee" in result.columns else 0
            log.info("[%d/%d] done: return=%.2f%%, fees=%.0f, assets=%.0f (%.1fs)",
                     i + 1, len(windows), ret, fees, final_assets, elapsed)

        except Exception as exc:
            log.error("[%d/%d] %s FAILED: %s", i + 1, len(windows), window.window_id, exc)
            errors.append(f"{window.window_id} ({window.test_start}): {exc}")
            continue

    # ── Cleanup temp dirs ──
    for d in tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)

    if not window_results:
        log.error("All windows failed. No results.")
        return

    # ── Aggregate ──
    duration = time.time() - start_time
    win_metrics = rolling_window_metrics(window_results)

    # Concatenated daily equity curve
    all_daily = pd.concat(window_results, ignore_index=True)
    all_daily = all_daily.sort_values("date").drop_duplicates(subset=["date"])

    # Aggregate portfolio metrics
    agg_metrics = calculate_metrics(
        pd.to_numeric(all_daily["total_assets"], errors="coerce").pct_change().dropna()
    )

    # Monthly returns
    monthly_returns = compute_monthly_returns(all_daily)

    # Aggregate signal metrics
    agg_signal = {}
    if signal_metrics_list:
        for key in ["IC", "RankIC", "long_short_spread"]:
            vals = [m.get(key) for m in signal_metrics_list if m.get(key) is not None]
            if vals:
                agg_signal[key] = {
                    "mean": sum(vals) / len(vals),
                    "std": (pd.Series(vals).std()),
                    "positive_ratio": sum(1 for v in vals if v > 0) / len(vals),
                    "values": vals,
                }

    # ── Save artifacts ──
    root_path = Path(cfg.get_path("root"))
    save_dir = root_path / "experiments"
    save_dir.mkdir(parents=True, exist_ok=True)

    daily_path = save_dir / "backtest_result_rolling.csv"
    all_daily.to_csv(daily_path, index=False)

    windows_path = save_dir / "rolling_windows.csv"
    win_metrics.to_csv(windows_path, index=False)

    # ── Build BacktestReport ──
    experiment_spec = {
        "rolling": {
            "window_count": len(window_results),
            "test_window_days": TEST_WINDOW_DAYS,
            "step_days": STEP_DAYS,
            "windows_completed": len(window_results),
            "windows_failed": len(errors),
            "retrain_per_window": True,
            "total_duration_seconds": round(duration, 1),
        },
        "initial_cash": INITIAL_CASH,
        "model_path": str(PRODUCTION_MODEL_DIR),
        "universe": UNIVERSE,
        "start": START,
        "end": END,
        "top_k": TOP_K,
    }

    report = BacktestReport.from_backtest_result(
        result_df=all_daily,
        model_path=str(PRODUCTION_MODEL_DIR),
        start_date=START,
        end_date=END,
        top_k=TOP_K,
        universe=UNIVERSE,
        duration_seconds=duration,
        daily_result_path=str(daily_path),
        experiment_spec=experiment_spec,
    )

    # ── Extend report with rich metrics ──
    perf_metrics = {}
    for section in report.sections:
        if section.name == "Performance":
            perf_metrics = dict(section.metrics)
            break

    # Add cost breakdown
    total_fees = float(pd.to_numeric(all_daily.get("daily_fee", pd.Series(dtype=float)), errors="coerce").sum())
    total_turnover = float(pd.to_numeric(all_daily.get("daily_turnover", pd.Series(dtype=float)), errors="coerce").sum())

    from qsys.reports.base import ReportStatus
    report.add_section(
        name="Cost Analysis",
        status=ReportStatus.SUCCESS,
        metrics={
            "total_fees": f"{total_fees:.2f}",
            "total_turnover": f"{total_turnover:.2f}",
            "fee_ratio": f"{total_fees / total_turnover * 100:.4f}%" if total_turnover > 0 else "0%",
            "fees_as_pct_of_initial": f"{total_fees / INITIAL_CASH * 100:.2f}%",
            "avg_daily_fee": f"{total_fees / len(all_daily):.2f}" if len(all_daily) > 0 else "0",
        },
    )

    # Add rolling window performance
    win_rets = pd.to_numeric(win_metrics["total_return"], errors="coerce")
    report.add_section(
        name="Rolling Windows",
        status=ReportStatus.SUCCESS,
        metrics={
            "window_count": str(len(win_metrics)),
            "positive_windows": str(int((win_rets > 0).sum())),
            "window_win_rate": f"{(win_rets > 0).mean() * 100:.1f}%",
            "mean_window_return": f"{win_rets.mean() * 100:.2f}%",
            "median_window_return": f"{win_rets.median() * 100:.2f}%",
            "best_window_return": f"{win_rets.max() * 100:.2f}%",
            "worst_window_return": f"{win_rets.min() * 100:.2f}%",
        },
    )

    # Add signal metrics
    if agg_signal:
        sig_metrics = {}
        for key, data in agg_signal.items():
            sig_metrics[f"{key}_mean"] = f"{data['mean']:.6f}"
            sig_metrics[f"{key}_std"] = f"{data['std']:.6f}"
            sig_metrics[f"{key}_positive_ratio"] = f"{data['positive_ratio']:.1%}"
        report.add_section(
            name="Signal Metrics",
            status=ReportStatus.SUCCESS,
            metrics=sig_metrics,
        )

    # Add monthly returns
    pos_months = 0
    if monthly_returns:
        pos_months = sum(1 for m in monthly_returns if m["return"] > 0)
        report.add_section(
            name="Monthly Returns",
            status=ReportStatus.SUCCESS,
            metrics={
                "positive_months": f"{pos_months}/{len(monthly_returns)}",
                "monthly_win_rate": f"{pos_months / len(monthly_returns) * 100:.1f}%",
                "best_month": f"{max(m['return'] for m in monthly_returns) * 100:.2f}%",
                "worst_month": f"{min(m['return'] for m in monthly_returns) * 100:.2f}%",
                "avg_monthly_return": f"{sum(m['return'] for m in monthly_returns) / len(monthly_returns) * 100:.4f}%",
            },
        )

    # ── Save artifacts ──
    unified_paths = unified_run_artifacts(save_dir)
    metrics_payload = dict(perf_metrics) if perf_metrics else {}

    report.artifacts["signal_metrics"] = write_json(
        unified_paths["signal_metrics"],
        {"aggregate": agg_signal, "per_window": signal_metrics_list if len(signal_metrics_list) <= 40 else f"{len(signal_metrics_list)} windows logged"},
    )
    report.artifacts["group_returns"] = write_csv(unified_paths["group_returns"], [])
    report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
    report.artifacts["metrics"] = write_json(unified_paths["metrics"], metrics_payload)
    report.artifacts["rolling_windows"] = write_csv(unified_paths.get("rolling_windows", str(save_dir / "rolling_windows.csv")), win_metrics.to_dict(orient="records"))

    # Also store monthly/window data for UI consumption
    if monthly_returns:
        report.artifacts["monthly_returns"] = write_json(
            str(save_dir / "monthly_returns.json"), monthly_returns
        )

    report_path = BacktestReport.save(report)
    log.info("Report saved: %s", report_path)

    # ── Console summary ──
    print("\n" + "=" * 65)
    print("ROLLING RETRAIN BACKTEST — SUMMARY")
    print("=" * 65)
    print(f"  Windows:  {len(window_results)} completed")
    if errors:
        print(f"  Failed:   {len(errors)} — {'; '.join(errors[:3])}")
    print(f"  Duration: {duration:.0f}s ({duration/60:.1f}min)")
    print(f"\n  Aggregate:")
    print(f"    Total return:  {perf_metrics.get('total_return', '?')}")
    print(f"    Sharpe:        {perf_metrics.get('sharpe', '?')}")
    print(f"    Max drawdown:  {perf_metrics.get('max_drawdown', '?')}")
    print(f"    Total fees:    {total_fees:.2f}")
    print(f"\n  Rolling windows:")
    win_ret = pd.to_numeric(win_metrics["total_return"], errors="coerce")
    print(f"    Mean return:  {win_ret.mean() * 100:.2f}%")
    print(f"    Win rate:     {(win_ret > 0).sum()}/{len(win_ret)} = {(win_ret > 0).mean() * 100:.0f}%")
    if agg_signal:
        ric = agg_signal.get("RankIC", {})
        if ric:
            print(f"    Mean RankIC:  {ric.get('mean', '?'):.6f}  (hit={(ric.get('positive_ratio', 0)*100):.0f}%)")
    print(f"\n  Monthly win rate: {pos_months}/{len(monthly_returns)} = {pos_months/len(monthly_returns)*100:.0f}%")
    print("=" * 65)


if __name__ == "__main__":
    main()
