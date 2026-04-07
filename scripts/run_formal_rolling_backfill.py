#!/usr/bin/env python3
"""Run a formal rolling daily backfill using the existing pre_open / post_close scripts.

Workflow per execution day:
- retrain semantic_all_features model on the trailing 4-year window ending at signal_date
- generate pre_open artifacts with top5 equal-weight plans
- simulate shadow execution for execution_date with fees + slippage
- write a zeroed real account sync artifact
- generate post_close reconciliation artifacts
- refresh aggregate backtest CSV + report artifacts for UI consumption
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qsys.live.account import RealAccount
from qsys.live.ops_paths import build_stage_paths
from qsys.live.simulation import ShadowSimulator
from qsys.reports.backtest import BacktestReport
from qsys.utils.logger import log, log_stage


@dataclass
class BackfillPaths:
    db_path: Path
    daily_root: Path
    experiments_root: Path
    reports_root: Path
    logs_root: Path
    progress_path: Path
    real_sync_root: Path
    master_log_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Formal rolling backfill via daily pre_open/post_close scripts")
    parser.add_argument("--start", default="2025-01-02", help="First execution date")
    parser.add_argument("--end", default=None, help="Last execution date; defaults to latest weekday <= qlib_latest")
    parser.add_argument("--train_years", type=int, default=4, help="Trailing train window in calendar years")
    parser.add_argument("--shadow_cash", type=float, default=500000.0, help="Initial cash for shadow account")
    parser.add_argument("--real_cash", type=float, default=0.0, help="Initial cash for real account")
    parser.add_argument("--top_k", type=int, default=5, help="Top-k equal weight holdings")
    parser.add_argument("--shadow_fee_rate", type=float, default=0.0003, help="Shadow commission rate")
    parser.add_argument("--shadow_tax_rate", type=float, default=0.001, help="Shadow sell tax rate")
    parser.add_argument("--shadow_slippage", type=float, default=0.001, help="Shadow execution slippage")
    parser.add_argument("--min_trade", type=int, default=5000, help="Minimum trade amount")
    parser.add_argument("--feature_set", default="semantic_all_features", help="Training feature set")
    parser.add_argument("--model_name", default="qlib_lgbm", help="Training model name")
    parser.add_argument("--model_path", default="data/models/qlib_lgbm_semantic_all_features", help="Model path passed into daily scripts")
    parser.add_argument("--db_path", default="data/meta/real_account.db", help="Account database path")
    parser.add_argument("--reset", action="store_true", help="Delete overlapping daily artifacts and account rows before replay")
    parser.add_argument("--skip_signal_quality_gate", action="store_true", help="Bypass pre_open gate during replay")
    parser.add_argument("--max_days", type=int, default=None, help="Optional cap for smoke runs")
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> BackfillPaths:
    logs_root = PROJECT_ROOT / "build_logs" / "formal_rolling"
    logs_root.mkdir(parents=True, exist_ok=True)
    real_sync_root = logs_root / "real_sync_zero"
    real_sync_root.mkdir(parents=True, exist_ok=True)
    return BackfillPaths(
        db_path=(PROJECT_ROOT / args.db_path).resolve(),
        daily_root=(PROJECT_ROOT / "daily").resolve(),
        experiments_root=(PROJECT_ROOT / "experiments").resolve(),
        reports_root=(PROJECT_ROOT / "experiments" / "reports").resolve(),
        logs_root=logs_root,
        progress_path=logs_root / "progress.jsonl",
        real_sync_root=real_sync_root,
        master_log_path=logs_root / "formal_rolling.log",
    )


def append_progress(paths: BackfillPaths, event: str, **fields: object) -> None:
    payload = {"ts": pd.Timestamp.utcnow().isoformat(), "event": event, **fields}
    with open(paths.progress_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_cmd(cmd: list[str], *, log_path: Path, cwd: Path = PROJECT_ROOT) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"\n$ {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.run(cmd, cwd=str(cwd), stdout=handle, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def latest_replay_end() -> str:
    try:
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        adapter.init_qlib()
        status = adapter.get_data_status_report()
        latest = status.get("qlib_latest") or status.get("raw_latest")
        if latest:
            return pd.Timestamp(latest).strftime("%Y-%m-%d")
    except Exception as exc:
        log.warning(f"Failed to resolve latest replay end from qlib status: {exc}")
    return pd.Timestamp.today().strftime("%Y-%m-%d")


def previous_business_day(value: str) -> str:
    return (pd.Timestamp(value) - pd.offsets.BDay(1)).strftime("%Y-%m-%d")


def build_execution_days(start: str, end: str, max_days: int | None = None) -> list[str]:
    days = [ts.strftime("%Y-%m-%d") for ts in pd.date_range(start, end, freq="B")]
    if max_days is not None:
        days = days[:max_days]
    return days


def reset_replay_range(paths: BackfillPaths, start_execution_date: str) -> None:
    signal_cutoff = previous_business_day(start_execution_date)
    if paths.daily_root.exists():
        for day_dir in paths.daily_root.iterdir():
            if not day_dir.is_dir():
                continue
            if day_dir.name >= start_execution_date:
                shutil.rmtree(day_dir, ignore_errors=True)
    if paths.db_path.exists():
        conn = sqlite3.connect(paths.db_path)
        try:
            for table in ["balance_history", "position_history", "trade_log"]:
                conn.execute(
                    f"DELETE FROM {table} WHERE account_name IN ('shadow', 'real') AND date >= ?",
                    (signal_cutoff,),
                )
            conn.commit()
        finally:
            conn.close()
    append_progress(paths, "reset_done", start_execution_date=start_execution_date, signal_cutoff=signal_cutoff)


def train_model_for_signal_date(args: argparse.Namespace, signal_date: str, log_path: Path) -> None:
    train_end = pd.Timestamp(signal_date)
    train_start = (train_end - pd.DateOffset(years=args.train_years) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_train.py"),
        "--model",
        args.model_name,
        "--start",
        train_start,
        "--end",
        signal_date,
        "--feature_set",
        args.feature_set,
    ]
    run_cmd(cmd, log_path=log_path)


def run_pre_open(args: argparse.Namespace, paths: BackfillPaths, signal_date: str, execution_date: str, log_path: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_daily_trading.py"),
        "--date",
        signal_date,
        "--execution_date",
        execution_date,
        "--model_path",
        args.model_path,
        "--db_path",
        str(paths.db_path),
        "--shadow_cash",
        str(args.shadow_cash),
        "--real_cash",
        str(args.real_cash),
        "--top_k",
        str(args.top_k),
        "--min_trade",
        str(args.min_trade),
        "--skip_update",
    ]
    if args.skip_signal_quality_gate:
        cmd.append("--skip_signal_quality_gate")
    run_cmd(cmd, log_path=log_path)


def find_shadow_plan(execution_date: str, signal_date: str) -> Path:
    stage_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=PROJECT_ROOT / "daily")
    return stage_paths.plans_dir / f"plan_{signal_date}_shadow.csv"


def simulate_shadow_fill(args: argparse.Namespace, paths: BackfillPaths, execution_date: str, plan_path: Path) -> None:
    simulator = ShadowSimulator(
        account_name="shadow",
        initial_cash=args.shadow_cash,
        db_path=str(paths.db_path),
        fee_rate=args.shadow_fee_rate,
        tax_rate=args.shadow_tax_rate,
        slippage=args.shadow_slippage,
    )
    simulator.simulate_execution(str(plan_path), execution_date)


def write_zero_real_sync(paths: BackfillPaths, execution_date: str) -> Path:
    output = paths.real_sync_root / f"real_sync_zero_{execution_date}.csv"
    frame = pd.DataFrame(
        [
            {
                "symbol": "CASH",
                "amount": 0,
                "price": 0.0,
                "cost_basis": 0.0,
                "cash": 0.0,
                "total_assets": 0.0,
                "side": "hold",
                "filled_amount": 0,
                "filled_price": 0.0,
                "fee": 0.0,
                "tax": 0.0,
                "total_cost": 0.0,
                "order_id": "",
                "note": "formal_rolling_zero_real",
            }
        ]
    )
    frame.to_csv(output, index=False)
    return output


def run_post_close(paths: BackfillPaths, execution_date: str, real_sync_path: Path, log_path: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_post_close.py"),
        "--date",
        execution_date,
        "--execution_date",
        execution_date,
        "--real_sync",
        str(real_sync_path),
        "--db_path",
        str(paths.db_path),
    ]
    run_cmd(cmd, log_path=log_path)


def build_aggregate_outputs(args: argparse.Namespace, paths: BackfillPaths, execution_days: list[str]) -> dict[str, str]:
    account = RealAccount(db_path=str(paths.db_path), account_name="shadow")
    trade_log = account.get_trade_log(account_name="shadow")
    daily_rows: list[dict[str, object]] = []
    trades_rows: list[dict[str, object]] = []

    for execution_date in execution_days:
        state = account.get_state(execution_date, account_name="shadow")
        if not state:
            continue
        day_trades = trade_log[trade_log["date"] == execution_date].copy()
        daily_rows.append(
            {
                "date": execution_date,
                "total_assets": float(state["total_assets"]),
                "cash": float(state["cash"]),
                "position_count": len(state["positions"]),
                "trade_count": int(len(day_trades)),
                "daily_fee": float(day_trades["fee"].sum() + day_trades["tax"].sum()) if not day_trades.empty else 0.0,
                "daily_turnover": float((day_trades["amount"] * day_trades["price"]).sum()) if not day_trades.empty else 0.0,
            }
        )

        if day_trades.empty:
            continue
        signal_date = previous_business_day(execution_date)
        plan_path = find_shadow_plan(execution_date, signal_date)
        target_weight_map: dict[tuple[str, str], float] = {}
        if plan_path.exists():
            try:
                plan_df = pd.read_csv(plan_path)
                if "target_weight" in plan_df.columns:
                    for _, row in plan_df.iterrows():
                        target_weight_map[(str(row.get("symbol")), str(row.get("side", "")).lower())] = float(row.get("target_weight") or 0.0)
            except Exception:
                pass
        for _, row in day_trades.iterrows():
            symbol = str(row["symbol"])
            side = str(row["side"]).lower()
            trades_rows.append(
                {
                    "date": execution_date,
                    "symbol": symbol,
                    "side": side,
                    "target_weight": target_weight_map.get((symbol, side)),
                    "filled_amount": int(row["amount"]),
                    "deal_price": float(row["price"]),
                    "fee": float(row["fee"] + row["tax"]),
                    "status": "filled",
                    "reason": "",
                }
            )

    daily_df = pd.DataFrame(daily_rows)
    trades_df = pd.DataFrame(trades_rows)
    paths.experiments_root.mkdir(parents=True, exist_ok=True)
    paths.reports_root.mkdir(parents=True, exist_ok=True)
    daily_csv = paths.experiments_root / "backtest_result.csv"
    trades_csv = paths.experiments_root / "backtest_trades.csv"
    daily_df.to_csv(daily_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)

    metrics = {}
    report_path = ""
    if not daily_df.empty:
        report = BacktestReport.from_backtest_result(
            daily_df,
            model_path=args.model_path,
            start_date=execution_days[0],
            end_date=execution_days[-1],
            top_k=args.top_k,
            universe="csi300",
            daily_result_path=str(daily_csv),
            notes=[
                "formal rolling replay via pre_open/post_close scripts",
                f"shadow_cash={args.shadow_cash}",
                f"shadow_fee_rate={args.shadow_fee_rate}",
                f"shadow_tax_rate={args.shadow_tax_rate}",
                f"shadow_slippage={args.shadow_slippage}",
            ],
        )
        report.artifacts["trades"] = str(trades_csv)
        report_path = BacktestReport.save(report, output_dir=str(paths.reports_root))
        metrics = report.sections[0].metrics if report.sections else {}
    return {
        "daily_csv": str(daily_csv),
        "trades_csv": str(trades_csv),
        "report_path": report_path,
        "metrics": metrics,
    }


def main() -> None:
    args = parse_args()
    paths = resolve_paths(args)
    end = args.end or latest_replay_end()
    execution_days = build_execution_days(args.start, end, max_days=args.max_days)
    if not execution_days:
        raise SystemExit("No execution days resolved")

    append_progress(paths, "run_started", start=args.start, end=end, days=len(execution_days))
    if args.reset:
        reset_replay_range(paths, execution_days[0])

    start_wall = time.time()
    for idx, execution_date in enumerate(execution_days, start=1):
        signal_date = previous_business_day(execution_date)
        day_log = paths.logs_root / f"{execution_date}.log"
        append_progress(paths, "day_start", idx=idx, total=len(execution_days), signal_date=signal_date, execution_date=execution_date)
        try:
            train_model_for_signal_date(args, signal_date, day_log)
            append_progress(paths, "train_done", execution_date=execution_date, signal_date=signal_date)

            run_pre_open(args, paths, signal_date, execution_date, day_log)
            append_progress(paths, "pre_open_done", execution_date=execution_date, signal_date=signal_date)

            plan_path = find_shadow_plan(execution_date, signal_date)
            if not plan_path.exists():
                raise FileNotFoundError(f"Shadow plan missing: {plan_path}")
            simulate_shadow_fill(args, paths, execution_date, plan_path)
            append_progress(paths, "shadow_fill_done", execution_date=execution_date, plan_path=str(plan_path))

            real_sync_path = write_zero_real_sync(paths, execution_date)
            run_post_close(paths, execution_date, real_sync_path, day_log)
            append_progress(paths, "post_close_done", execution_date=execution_date, real_sync_path=str(real_sync_path))

            aggregate = build_aggregate_outputs(args, paths, execution_days[:idx])
            append_progress(paths, "aggregate_done", execution_date=execution_date, artifacts=aggregate)
            log_stage("formal_rolling", "day_done", execution_date=execution_date, idx=idx, total=len(execution_days))
        except Exception as exc:
            append_progress(paths, "day_failed", execution_date=execution_date, signal_date=signal_date, error=str(exc), log_path=str(day_log))
            raise

    duration = time.time() - start_wall
    aggregate = build_aggregate_outputs(args, paths, execution_days)
    append_progress(paths, "run_completed", duration_seconds=round(duration, 2), artifacts=aggregate)
    log_stage("formal_rolling", "done", days=len(execution_days), duration_seconds=round(duration, 2))


if __name__ == "__main__":
    main()
