"""Shared notification formatting for all strategies.

Extracted from ``alpha_v1/adapter.py`` and ``alpha_v2/adapter.py`` so that every
strategy produces identical Telegram message formatting.  Strategy adapters
call these functions from their ``build_preopen_message`` /
``build_postclose_message`` methods, supplying strategy-specific values
(display_name, universe, etc.) as plain parameters.

No dependency on strategy internals or ``DailyRunContext`` — only plain data
types (str, dict, Path, DataFrame).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def fmt_amount(amount: float) -> str:
    return f"¥{amount / 1000:.2f}k"


def format_preopen_message(
    *,
    display_name: str,
    trade_date: str,
    predictions_df: pd.DataFrame,
    plan_dir: Path | None,
    rebalance_skipped: bool,
    universe: str,
    prediction_count: int,
    rebalance_freq: str,
    get_stock_name: Callable[[str], str],
) -> str:
    """Build a formatted pre-open Telegram message.

    Parameters
    ----------
    display_name : str
        Strategy display name (e.g. "Alpha V1", "Alpha V2 Smoke").
    trade_date : str
        Trading date (YYYY-MM-DD).
    predictions_df : pd.DataFrame
        Must contain ``instrument`` and ``score`` columns.
    plan_dir : Path or None
        Directory containing ``order_intents.csv``, or None if unavailable.
    rebalance_skipped : bool
        Whether the weekly rebalance was skipped.
    universe : str
        Universe name (e.g. "csi300").
    prediction_count : int
        Total number of predicted instruments.
    rebalance_freq : str
        Rebalance frequency string (e.g. "weekly").
    get_stock_name : Callable
        ``str -> str`` mapping instrument codes to display names.
    """
    predictions = pd.DataFrame(predictions_df)
    top = predictions.sort_values("score", ascending=False).head(5)

    lines = [
        f"✅ {display_name} Pre-open {trade_date}",
        f"Time: {now_str()}",
        "", "📈 推荐股票",
    ]
    for i, (_, row) in enumerate(top.iterrows(), 1):
        name = get_stock_name(row["instrument"])
        lines.append(f"  {i}. {row['instrument']} {name}  score={row['score']:.4f}")

    # Show existing plan details if available
    has_existing_plan = plan_dir is not None and (plan_dir / "order_intents.csv").exists()
    if has_existing_plan:
        try:
            orders_df = pd.read_csv(plan_dir / "order_intents.csv")
            scores_df = predictions[["instrument", "score"]]
            orders_df = orders_df.merge(scores_df, on="instrument", how="left")
            orders_df["score"] = orders_df["score"].fillna(0.0)
            buys = orders_df[orders_df["side"] == "buy"].sort_values("score", ascending=False)
            sells = orders_df[orders_df["side"] == "sell"].sort_values("score", ascending=False)
            lines += ["", "📋 计划交易（以 OPEN 价执行）", ""]
            if not buys.empty:
                lines.append(f"  计划买入 ({len(buys)}):")
                lines.append(f"    {'代码':<12} {'名称':<8} {'买入金额':<12} 手数  score")
                for _, row in buys.iterrows():
                    name = get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    qty = int(row.get("requested_qty", 0))
                    lines.append(
                        f"    {row['instrument']:<12} {name:<8} "
                        f"+{fmt_amount(diff_val):<10} {qty // 100}手  {row['score']:.4f}"
                    )
            if not sells.empty:
                lines.append(f"  计划卖出 ({len(sells)}):")
                lines.append(f"    {'代码':<12} {'名称':<8} {'卖出金额':<12} 手数  score")
                for _, row in sells.iterrows():
                    name = get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    qty = int(row.get("requested_qty", 0))
                    lines.append(
                        f"    {row['instrument']:<12} {name:<8} "
                        f"-{fmt_amount(abs(diff_val)):<10} {qty // 100}手  {row['score']:.4f}"
                    )
                lines.append("")
        except Exception as e:
            lines.append(f"  ⚠ 无法读取交易计划详情: {e}")

    if rebalance_skipped and not has_existing_plan:
        lines += ["", "⏭ 本周已调仓，跳过重复交易"]

    lines += [
        "",
        f"策略: {display_name} | 频率: {rebalance_freq}",
        f"Universe: {universe} | 预测: {prediction_count}只",
    ]
    if has_existing_plan:
        lines += [
            "",
            "📝 注: 计划不执行交易，待 21:30 数据同步后 postclose 以开盘价执行",
        ]
    return "\n".join(lines)


def format_postclose_message(
    *,
    display_name: str,
    trade_date: str,
    debug_run: bool = False,
    execution_committed: bool = False,
    execution_skipped: bool = False,
    idempotent_skip: bool = False,
    stale_check: dict | None = None,
    artifacts: Any = None,
    mtm: dict | None = None,
    get_stock_name: Callable[[str], str] | None = None,
) -> str:
    """Build a formatted post-close Telegram message.

    Parameters
    ----------
    display_name : str
        Strategy display name.
    trade_date : str
        Trading date (YYYY-MM-DD).
    debug_run : bool
        Whether this is a debug run.
    execution_committed : bool
        Whether execution was committed.
    execution_skipped : bool
        Whether execution was skipped (no plan).
    idempotent_skip : bool
        Whether execution was already committed from a previous run.
    stale_check : dict or None
        Stale data check result (keys: status, identical_count, checked_count,
        identical_ratio, examples).
    artifacts : Any or None
        Execution result object with attributes: turnover, order_count,
        filled_count, rejected_count, cash_after, total_value_after.
    mtm : dict or None
        MTM snapshot (keys: cumulative_pnl, cumulative_pnl_pct, daily_pnl,
        total_value, cash, market_value, priced_count, positions_before_count,
        details).
    get_stock_name : Callable or None
        ``str -> str`` for instrument name resolution; can be None if mtm
        details already contain names in index position 1.
    """
    lines = [
        f"📊 {display_name} Post-close {trade_date}",
        f"Time: {now_str()}", "",
    ]
    if debug_run:
        lines.append("🔧 调试模式 — 不修改 shadow 账户")
        lines.append("")
    if execution_committed and not execution_skipped:
        if idempotent_skip:
            lines += ["✅ 执行状态: 已完成（执行记录已存在）", ""]
        else:
            lines += ["✅ 执行状态: 已完成", ""]
    elif execution_committed and execution_skipped:
        lines += ["✅ 执行状态: 无计划需执行", ""]
    elif debug_run:
        lines += ["🔧 执行状态: 调试模式，未提交 shadow 账户", ""]

    if stale_check:
        sc = stale_check
        status_icon = {
            "passed": "✅", "blocked": "⛔",
            "skipped": "⏭", "skipped_low_overlap": "⏭",
        }
        lines.append(
            f"📡 数据陈旧检查: {status_icon.get(sc.get('status', ''), '❓')} "
            f"一致={sc.get('identical_count', 0)}/{sc.get('checked_count', 0)} "
            f"({sc.get('identical_ratio', 0) * 100:.0f}%)"
        )
        if sc.get("examples"):
            for ex in sc["examples"]:
                lines.append(f"    {ex}")
        lines.append("")

    if artifacts:
        lines.append(f"🏦 执行摘要（按 {trade_date} 开盘价）")
        lines.append(
            f"  成交额: {fmt_amount(artifacts.turnover)}  买入委托: {artifacts.order_count} "
            f"成交: {artifacts.filled_count}  未成交: {artifacts.rejected_count}"
        )
        mv = artifacts.total_value_after - artifacts.cash_after
        lines.append(
            f"  Total: {fmt_amount(artifacts.total_value_after)}  "
            f"Cash: {fmt_amount(artifacts.cash_after)}  MV: {fmt_amount(mv)}"
        )
        lines.append("")

    if mtm:
        cum_pnl_str = (
            f"+{fmt_amount(mtm['cumulative_pnl'])}"
            if mtm["cumulative_pnl"] >= 0
            else fmt_amount(mtm["cumulative_pnl"])
        )
        daily_str = (
            f"+{fmt_amount(mtm['daily_pnl'])}"
            if mtm["daily_pnl"] >= 0
            else fmt_amount(mtm["daily_pnl"])
        )
        data_date = mtm.get("data_date", trade_date) if mtm else trade_date
        if data_date != trade_date:
            lines.append(f"💰 Mark-to-Market（按 {data_date} 收盘价，数据日期）")
        else:
            lines.append(f"💰 Mark-to-Market（按 {trade_date} 收盘价）")
        lines.append(f"  累计 PnL: {cum_pnl_str} ({mtm['cumulative_pnl_pct']:+.2f}%)")
        lines.append(f"  当日 PnL: {daily_str}")
        lines.append(f"  Total: {fmt_amount(mtm['total_value'])}  Cash: {fmt_amount(mtm['cash'])}")
        pos_before = mtm.get("positions_before_count", 0)
        pos_after = mtm.get("priced_count", 0)
        if pos_before > 0:
            lines.append(
                f"  Position: {fmt_amount(mtm['market_value'])}  "
                f"Holdings: {pos_after}只（原有{pos_before} + 新增{pos_after - pos_before}）"
            )
        else:
            lines.append(
                f"  Position: {fmt_amount(mtm['market_value'])}  Holdings: {pos_after}只"
            )
        details = mtm.get("details", [])
        top3 = details[:3]
        bot3 = details[-3:] if len(details) >= 3 else details
        if top3:
            lines.append("")
            lines.append("📈 当日收益 Top 3")
            for entry in top3:
                inst, name, qty, cost, close, pnl_val = entry[:6]
                s = f"+{fmt_amount(pnl_val)}" if pnl_val >= 0 else fmt_amount(pnl_val)
                lines.append(f"  {inst} {name}  {s}  {qty // 100}手  {cost:.2f}→{close:.2f}")
        if bot3 and bot3 != top3:
            lines.append("")
            lines.append("📉 当日收益 Bottom 3")
            for entry in bot3:
                inst, name, qty, cost, close, pnl_val = entry[:6]
                s = f"+{fmt_amount(pnl_val)}" if pnl_val >= 0 else fmt_amount(pnl_val)
                lines.append(f"  {inst} {name}  {s}  {qty // 100}手  {cost:.2f}→{close:.2f}")
    else:
        lines.append("⚠ Mark-to-Market 不可用")
        lines.append("收盘价数据未就绪（数据同步可能未完成）。")

    return "\n".join(lines)
