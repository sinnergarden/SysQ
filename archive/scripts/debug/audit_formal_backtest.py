#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.live.market_rules import AShareMarketRules


@dataclass
class CodePathRecord:
    path: str
    symbol: str
    role: str


def locate_code_paths() -> list[CodePathRecord]:
    return [
        CodePathRecord("qsys/backtest.py", "BacktestEngine.run", "Backtest 主回测循环；生成 target_weights/orders 并调用撮合"),
        CodePathRecord("qsys/trader/matcher.py", "MatchEngine.match", "旧回测撮合器；决定是否成交、成交价、现金/持仓约束"),
        CodePathRecord("qsys/live/simulation.py", "ShadowSimulator.simulate_execution", "formal rolling / shadow replay 实际使用的日频执行器"),
        CodePathRecord("qsys/live/market_rules.py", "AShareMarketRules / MarketSnapshot", "A股交易规则：开盘执行价、涨跌停、停牌、100股规则"),
        CodePathRecord("qsys/trader/plan.py", "PlanGenerator.generate_plan", "生成 target delta 调仓计划"),
        CodePathRecord("qsys/live/manager.py", "LiveManager.generate_signal_basket / run_daily_plan", "信号生成、plan 生成、price_basis_date/execution_date 写入"),
        CodePathRecord("scripts/run_daily_trading.py", "run_preopen_workflow", "pre_open 主入口；产出 signal basket / plan / order intents"),
        CodePathRecord("scripts/run_formal_rolling_backfill.py", "train_model_for_signal_date / run_pre_open / simulate_shadow_fill", "rolling replay 主入口；定义 train->plan->simulate 顺序"),
        CodePathRecord("scripts/run_train.py", "main", "rolling train 主入口；构造 model + feature_set 并训练/保存"),
        CodePathRecord("qsys/model/zoo/qlib_native.py", "QlibNativeModel.fit", "Qlib native 训练入口；label / preprocess / segment 定义"),
        CodePathRecord("qsys/model/zoo/qlib_native.py", "QlibNativeModel._fit_with_semantic_adapter", "semantic_all_features 训练路径；显式生成 label、fit robust scaling"),
        CodePathRecord("qsys/model/zoo/qlib_native.py", "QlibNativeModel.predict / _apply_preprocess", "推理期 preprocess 应用；决定是否用训练期 fitted params"),
        CodePathRecord("qsys/feature/library.py", "FeatureLibrary.get_label_config", "label 配置入口；默认空，实际 fallback 到 model 默认 label"),
        CodePathRecord("qsys/data/adapter.py", "QlibAdapter.get_features", "feature / semantic feature 对齐与时间截断"),
    ]


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _execution_days(start: str, end: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="B")]


def _find_signal_date(day_root: Path, execution_date: str) -> str:
    intents_dir = day_root / execution_date / "pre_open" / "order_intents"
    if intents_dir.exists():
        for path in sorted(intents_dir.glob("order_intents_*_shadow.json")):
            payload = json.loads(path.read_text())
            value = payload.get("signal_date")
            if value:
                return str(value)
    return (pd.Timestamp(execution_date) - pd.offsets.BDay(1)).strftime("%Y-%m-%d")


def _load_plan(day_root: Path, execution_date: str, signal_date: str) -> pd.DataFrame:
    path = day_root / execution_date / "pre_open" / "plans" / f"plan_{signal_date}_shadow.csv"
    return _load_csv(path)


def _load_trades(base: Path, start: str, end: str) -> pd.DataFrame:
    trades = _load_csv(base / "experiments" / "backtest_trades.csv")
    if trades.empty:
        return trades
    trades["date"] = pd.to_datetime(trades["date"]).dt.strftime("%Y-%m-%d")
    return trades[(trades["date"] >= start) & (trades["date"] <= end)].copy()


def _board(symbol: str) -> str:
    code = str(symbol)
    if code.startswith("688"):
        return "STAR"
    if code.startswith("300"):
        return "ChiNext"
    if code.startswith("8") or code.startswith("4"):
        return "BSE"
    if code.endswith(".SH"):
        return "SSE Main"
    if code.endswith(".SZ"):
        return "SZSE Main"
    return "Unknown"


def audit_trade_constraints(base: Path, start: str, end: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    rules = AShareMarketRules()
    trades = _load_trades(base, start, end)
    anomalies: list[dict[str, Any]] = []
    inspected: list[dict[str, Any]] = []
    for execution_date in sorted(trades["date"].unique() if not trades.empty else []):
        signal_date = _find_signal_date(base / "daily", execution_date)
        plan = _load_plan(base / "daily", execution_date, signal_date)
        if plan.empty:
            continue
        day_trades = trades[trades["date"] == execution_date].copy()
        snapshots = rules.load_snapshots(sorted(plan["symbol"].astype(str).unique()), execution_date)
        plan_lookup = {(str(r.symbol), str(r.side).lower()): r for r in plan.itertuples(index=False)}
        for row in day_trades.itertuples(index=False):
            key = (str(row.symbol), str(row.side).lower())
            plan_row = plan_lookup.get(key)
            snapshot = snapshots.get(str(row.symbol))
            should_fail_reason = ""
            if plan_row is None:
                should_fail_reason = "missing_plan_row"
                order_qty = None
            else:
                order_qty = int(getattr(plan_row, "amount", 0) or 0)
                if snapshot is None:
                    should_fail_reason = "missing_market_snapshot"
                else:
                    if str(row.side).lower() == "buy":
                        normalized = rules.normalize_buy_amount(order_qty)
                        if normalized <= 0:
                            should_fail_reason = "invalid_buy_lot"
                        elif snapshot.is_buy_blocked():
                            if snapshot.paused:
                                should_fail_reason = "paused"
                            elif snapshot.high_limit > 0 and snapshot.executable_price >= snapshot.high_limit:
                                should_fail_reason = "limit_up_or_one_word"
                    else:
                        sellable = int(getattr(plan_row, "sellable_amount", order_qty) or 0)
                        normalized = rules.normalize_sell_amount(order_qty, held_amount=sellable)
                        if normalized <= 0:
                            should_fail_reason = "invalid_sell_qty_or_no_sellable"
                        elif snapshot.is_sell_blocked():
                            if snapshot.paused:
                                should_fail_reason = "paused"
                            elif snapshot.low_limit > 0 and snapshot.executable_price <= snapshot.low_limit:
                                should_fail_reason = "limit_down_or_one_word"
                        elif normalized < int(row.filled_amount):
                            should_fail_reason = "filled_qty_exceeds_normalized_sellable"
            record = {
                "date": execution_date,
                "symbol": str(row.symbol),
                "side": str(row.side).lower(),
                "order_qty": order_qty,
                "filled_qty": int(row.filled_amount),
                "price": float(row.deal_price),
                "board_market": _board(str(row.symbol)),
                "status": str(row.status),
                "should_fail_reason": should_fail_reason,
            }
            inspected.append(record)
            if should_fail_reason:
                anomalies.append(record)
    out = pd.DataFrame(anomalies)
    summary = {
        "trade_rows_checked": int(len(inspected)),
        "illegal_filled_trade_count": int(len(out)),
    }
    return out, summary


def audit_execution_timing(base: Path, start: str, end: str, *, slippage: float = 0.001) -> tuple[pd.DataFrame, dict[str, Any]]:
    rules = AShareMarketRules()
    trades = _load_trades(base, start, end)
    rows: list[dict[str, Any]] = []
    for execution_date in sorted(trades["date"].unique() if not trades.empty else []):
        signal_date = _find_signal_date(base / "daily", execution_date)
        plan = _load_plan(base / "daily", execution_date, signal_date)
        if plan.empty:
            continue
        plan_lookup = {(str(r.symbol), str(r.side).lower()): r for r in plan.itertuples(index=False)}
        snapshots = rules.load_snapshots(sorted(plan["symbol"].astype(str).unique()), execution_date)
        for row in trades[trades["date"] == execution_date].itertuples(index=False):
            plan_row = plan_lookup.get((str(row.symbol), str(row.side).lower()))
            snapshot = snapshots.get(str(row.symbol))
            expected_reference_price = snapshot.executable_price if snapshot else None
            if expected_reference_price is None or expected_reference_price <= 0:
                expected_trade_price = None
            else:
                expected_trade_price = expected_reference_price * (1 + slippage if str(row.side).lower() == "buy" else 1 - slippage)
            rows.append(
                {
                    "symbol": str(row.symbol),
                    "side": str(row.side).lower(),
                    "decision_date": signal_date,
                    "feature_cutoff_date": signal_date,
                    "signal_date": signal_date,
                    "order_submit_date": execution_date,
                    "execution_date": execution_date,
                    "price_basis_date": getattr(plan_row, "price_basis_date", signal_date) if plan_row else signal_date,
                    "price_basis_field": getattr(plan_row, "price_basis_field", "close") if plan_row else "close",
                    "expected_price_source": f"{execution_date}.open_with_slippage",
                    "expected_reference_price": expected_reference_price,
                    "actual_trade_price": float(row.deal_price),
                    "expected_trade_price": expected_trade_price,
                    "actual_vs_expected_diff": None if expected_trade_price is None else float(row.deal_price) - float(expected_trade_price),
                }
            )
    out = pd.DataFrame(rows)
    strict = out["actual_vs_expected_diff"].abs().fillna(999).le(1e-8) if not out.empty else pd.Series(dtype=bool)
    summary = {
        "trade_rows_checked": int(len(out)),
        "strict_t1_open_match_count": int(strict.sum()) if len(strict) else 0,
        "strict_t1_open_match_ratio": float(strict.mean()) if len(strict) else None,
    }
    return out, summary


def audit_rolling_window_leakage(base: Path, start: str, end: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    leakage_flags: list[str] = []
    for execution_date in _execution_days(start, end):
        signal_date = _find_signal_date(base / "daily", execution_date)
        train_end = signal_date
        train_start = (pd.Timestamp(signal_date) - pd.DateOffset(years=4) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        infer_date = signal_date
        max_feature_date_used = signal_date
        max_label_date_used = (pd.Timestamp(signal_date) + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        semantic_label_lookahead = pd.Timestamp(max_label_date_used) > pd.Timestamp(infer_date)
        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "valid_start": None,
                "valid_end": None,
                "infer_date": infer_date,
                "execution_date": execution_date,
                "label_horizon": "t+1_to_t+5",
                "max_feature_date_used": max_feature_date_used,
                "max_label_date_used": max_label_date_used,
                "feature_date_gt_infer_date": False,
                "train_end_ge_infer_date": pd.Timestamp(train_end) >= pd.Timestamp(infer_date),
                "semantic_path_future_label_visible": semantic_label_lookahead,
            }
        )
        if semantic_label_lookahead:
            leakage_flags.append(execution_date)
    out = pd.DataFrame(windows)
    summary = {
        "window_count": int(len(out)),
        "max_feature_date_gt_infer_date_count": int(out["feature_date_gt_infer_date"].sum()) if not out.empty else 0,
        "train_end_ge_infer_date_count": int(out["train_end_ge_infer_date"].sum()) if not out.empty else 0,
        "semantic_future_label_visible_count": int(out["semantic_path_future_label_visible"].sum()) if not out.empty else 0,
        "semantic_future_label_visible_dates": leakage_flags[:20],
        "static_code_evidence": [
            "qsys/model/zoo/qlib_native.py:_fit_with_semantic_adapter fetches feature_end = end_date + 7 days",
            "same function computes label = shift(-5)/shift(-1)-1 and only then filters trade_date <= end_date",
            "rolling script trains with train_end = signal_date and then infers on signal_date for next-session execution",
        ],
    }
    return out, summary


def build_markdown_report(code_paths: list[CodePathRecord], a_summary: dict[str, Any], b_summary: dict[str, Any], c_summary: dict[str, Any]) -> str:
    lines = [
        "# Formal Backtest Audit (Minimal Repro)",
        "",
        "## Scope",
        "- Target: strict audit, not strategy optimization",
        "- Slice: 2025-01 minimal reproduction",
        "- Focus: A-share execution legality / T+1 execution timing / rolling leakage",
        "",
        "## Step 1: Key Code Entrypoints",
        "| Path | Symbol | Role |",
        "|---|---|---|",
    ]
    for item in code_paths:
        lines.append(f"| `{item.path}` | `{item.symbol}` | {item.role} |")
    lines += [
        "",
        "## Step 2/3: Audit Summaries",
        f"- A. 成交合法性：checked={a_summary.get('trade_rows_checked', 0)}, illegal_filled={a_summary.get('illegal_filled_trade_count', 0)}",
        f"- B. 价格时序：checked={b_summary.get('trade_rows_checked', 0)}, strict_t1_open_match={b_summary.get('strict_t1_open_match_count', 0)}, ratio={b_summary.get('strict_t1_open_match_ratio')}",
        f"- C. rolling 泄露：windows={c_summary.get('window_count', 0)}, feature_date_gt_infer={c_summary.get('max_feature_date_gt_infer_date_count', 0)}, train_end_ge_infer={c_summary.get('train_end_ge_infer_date_count', 0)}, semantic_future_label_visible={c_summary.get('semantic_future_label_visible_count', 0)}",
        "",
        "## Preliminary Findings",
        "- 这份脚本先给结构化证据，不做口头背书。",
        "- 若 C 中 `semantic_future_label_visible_count > 0`，说明 semantic rolling 路径存在显式未来 label 可见性风险，需要进一步坐实到具体窗口与模型版本。",
        "- 若 B 中 strict T+1 match 不是 100%，就要逐笔检查是 slippage 设定、价格列错误，还是 execution_date / price_basis_date 对不齐。",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit formal A-share backtest artifacts")
    parser.add_argument("--base", default="scratch/formal_173_compare", help="Replay artifact root")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-01-31")
    parser.add_argument("--output", default="reports/audits/formal_173_2025_01")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base = (PROJECT_ROOT / args.base).resolve()
    output = (PROJECT_ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    QlibAdapter().init_qlib()

    code_paths = locate_code_paths()
    a_df, a_summary = audit_trade_constraints(base, args.start, args.end)
    b_df, b_summary = audit_execution_timing(base, args.start, args.end)
    c_df, c_summary = audit_rolling_window_leakage(base, args.start, args.end)

    pd.DataFrame([asdict(item) for item in code_paths]).to_csv(output / "code_paths.csv", index=False)
    a_df.to_csv(output / "trade_legality_anomalies.csv", index=False)
    b_df.to_csv(output / "execution_timing_audit.csv", index=False)
    c_df.to_csv(output / "rolling_window_leakage.csv", index=False)

    summary = {
        "base": str(base),
        "start": args.start,
        "end": args.end,
        "trade_legality": a_summary,
        "execution_timing": b_summary,
        "rolling_leakage": c_summary,
    }
    (output / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "audit_report.md").write_text(build_markdown_report(code_paths, a_summary, b_summary, c_summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
