#!/usr/bin/env python3
"""
Alpha V1 每日运营：preopen → inference + plan + notify；postclose → PnL notify；train → 周级别训练。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── 加载 .env（Telegram 凭据）────────────────────────────────────────

_ENV_FILE = Path("/home/liuming/.openclaw/.env")
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

# ── 路径常量 ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.shadow_rebalance import build_alpha_v1_plan
from qsys.ops.shadow_rebalance import execute_alpha_v1_plan
from qsys.ops.shadow_rebalance import ShadowRebalanceArtifacts
from qsys.ops.shadow_rebalance import write_json
from qsys.ops.telegram import send_telegram_message
from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE


# ── 模型加载（复用 run_alpha_v1_shadow_observation.py 逻辑）──────────────

MODEL_DIR = PROJECT_ROOT / "experiments/alpha_v1_models/latest"
UNIVERSE = "csi300"
PREDICTIONS_DIR = PROJECT_ROOT / "experiments/alpha_v1_shadow_predictions"


# ── Stock name lookup ─────────────────────────────────────────────────

_STOCK_NAMES: dict[str, str] = {}

def _get_stock_name(ts_code: str) -> str:
    if not _STOCK_NAMES:
        _load_stock_names()
    return _STOCK_NAMES.get(ts_code, ts_code)

def _load_stock_names() -> None:
    path = PROJECT_ROOT / "data" / "stock_names.csv"
    if path.exists():
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            _STOCK_NAMES[str(row["ts_code"])] = str(row["name"])


# ── Rebalance frequency check ──────────────────────────────────────────

def _should_rebalance(trade_date: str) -> bool:
    """Check if rebalancing should run based on ALPHA_V1_CANDIDATE.portfolio.rebalance_freq."""
    freq = ALPHA_V1_CANDIDATE.portfolio.rebalance_freq
    if freq != "weekly":
        return True  # daily or unknown → always rebalance

    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
    current_iso = trade_dt.isocalendar()

    account_path = PROJECT_ROOT / "shadow" / "account.json"
    if account_path.exists():
        try:
            account = json.loads(account_path.read_text())
            last_trade_date = account.get("trade_date", "")
            if last_trade_date:
                last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
                last_iso = last_dt.isocalendar()
                # Same ISO year + week → already rebalanced this week
                if last_iso[0] == current_iso[0] and last_iso[1] == current_iso[1]:
                    return False
        except (json.JSONDecodeError, OSError):
            pass

    return True


# ── Core pipeline functions ────────────────────────────────────────────

def cs_zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def robust_zscore_transform(X: pd.DataFrame, center: pd.Series, scale: pd.Series) -> pd.DataFrame:
    return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)


def load_model_and_params():
    """加载 alpha_v1 双模型 + 归一化参数 + 特征列表。"""
    models = {}
    for tag in ["5d", "20d"]:
        model_path = MODEL_DIR / f"model_{tag}.txt"
        center_path = MODEL_DIR / f"center_{tag}.json"
        scale_path = MODEL_DIR / f"scale_{tag}.json"
        if not model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        model = lgb.Booster(model_file=str(model_path))
        center = pd.Series(json.loads(center_path.read_text()))
        scale = pd.Series(json.loads(scale_path.read_text()))
        models[tag] = (model, center, scale)

    features_file = MODEL_DIR / "features.json"
    if features_file.exists():
        clean_features = json.loads(features_file.read_text())
    else:
        all_features = FeatureLibrary.get_semantic_all_features_config()
        from qsys.strategy.alpha_v1.spec import get_clean_features
        clean_features = get_clean_features(all_features)

    return models, clean_features


def fetch_latest_data(until_date: str) -> tuple[pd.DataFrame, list[str], str]:
    """获取截至 until_date 的最新交易日数据。

    自动回退到 qlib 中最新可用日期（解决 08:00 今天数据还未同步的问题）。
    返回 (frame, clean_features, actual_data_date)。
    """
    from qlib.data import D
    adapter = QlibAdapter()
    adapter.init_qlib()

    all_features = FeatureLibrary.get_semantic_all_features_config()
    from qsys.strategy.alpha_v1.spec import get_clean_features
    clean_features = get_clean_features(all_features)

    # Find latest available qlib trading day <= until_date
    cal = D.calendar(start_time="2020-01-01", end_time=until_date)
    if cal is None or len(cal) == 0:
        print(f"  ⚠ qlib 日历无 <= {until_date} 的交易日")
        return pd.DataFrame(), clean_features, until_date

    data_date = pd.Timestamp(cal[-1]).strftime("%Y-%m-%d")
    if data_date != until_date:
        print(f"  ⚠ {until_date} 无数据，回退到 {data_date}")

    raw = adapter.get_features(UNIVERSE, all_features + ["$close"],
                                start_time=data_date, end_time=data_date)
    if raw.empty:
        print(f"  ⚠ {data_date} 无特征数据")
        return pd.DataFrame(), clean_features, data_date

    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    return frame, clean_features, data_date


def generate_predictions_for_date(models, clean_features, frame, trade_date: str) -> pd.DataFrame:
    """生成单日混合预测（frame 已限定为单日数据）。"""
    if frame.empty:
        raise ValueError(f"交易日 {trade_date} 无数据")

    X = frame[clean_features].astype(np.float32).fillna(0.0)
    if X.empty or len(X) < 10:
        raise ValueError(f"交易日 {trade_date} 数据不足 ({len(X)} rows)")

    pred_5d_model, center_5d, scale_5d = models["5d"]
    pred_20d_model, center_20d, scale_20d = models["20d"]

    Xz_5d = robust_zscore_transform(X, center_5d, scale_5d)
    Xz_20d = robust_zscore_transform(X, center_20d, scale_20d)

    p5 = pd.Series(pred_5d_model.predict(Xz_5d.values), index=X.index)
    p20 = pd.Series(pred_20d_model.predict(Xz_20d.values), index=X.index)

    z5 = cs_zscore(p5)
    z20 = cs_zscore(p20)
    blended = 0.8 * z5 + 0.2 * z20

    instruments = frame["instrument"].values
    rows = []
    for i, inst in enumerate(instruments):
        rows.append({
            "trade_date": trade_date,
            "instrument": str(inst),
            "score": float(blended.iloc[i]) if pd.notna(blended.iloc[i]) else 0.0,
            "model_name": "alpha_v1_candidate_ensemble",
            "mainline_object_name": "alpha_v1_candidate",
        })
    return pd.DataFrame(rows)


# ── Telegram 通知格式化 ────────────────────────────────────────────────

def _send_notification(text: str) -> None:
    print(f"\n{'─' * 50}")
    print("📱 Telegram 通知:")
    print(text)
    print(f"{'─' * 50}\n")
    result = send_telegram_message(text)
    status = result.get("status", "unknown")
    if status == "skipped":
        print(f"  ⚠ Telegram 未配置: {result.get('message', '')}")
    elif status == "failed":
        print(f"  ❌ Telegram 发送失败: {result.get('error', '')}")
    else:
        print(f"  ✅ Telegram 已发送")


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt(amount: float) -> str:
    """Format amount in thousands (k) with 2 decimal places."""
    return f"¥{amount/1000:.2f}k"


def _load_prev_mtm(trade_date: str) -> dict | None:
    """Load the previous trading day's MTM snapshot."""
    mtm_history = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / "mtm_history.csv"
    if not mtm_history.exists():
        return None
    try:
        hist = pd.read_csv(mtm_history)
        hist = hist.sort_values("trade_date")
        prev = hist[hist["trade_date"] < trade_date]
        if prev.empty:
            return None
        return prev.iloc[-1].to_dict()
    except Exception:
        return None


def _save_mtm_snapshot(trade_date: str, snapshot: dict) -> None:
    """Save MTM snapshot + append to rolling history."""
    # Per-day snapshot
    snap_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date
    snap_dir.mkdir(parents=True, exist_ok=True)
    with open(snap_dir / "mtm_snapshot.json", "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    # Rolling history
    mtm_history = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / "mtm_history.csv"
    row = {
        "trade_date": trade_date,
        "total_value": snapshot["total_value"],
        "cash": snapshot["cash"],
        "market_value": snapshot["market_value"],
        "cumulative_pnl": snapshot["cumulative_pnl"],
        "cumulative_pnl_pct": snapshot["cumulative_pnl_pct"],
        "daily_pnl": snapshot["daily_pnl"],
        "initial_capital": snapshot["initial_capital"],
    }
    new_row = pd.DataFrame([row])
    if mtm_history.exists():
        existing = pd.read_csv(mtm_history)
        # Remove any existing entry for the same date (idempotent)
        existing = existing[existing["trade_date"] != trade_date]
        combined = pd.concat([existing, new_row], ignore_index=True)
    else:
        combined = new_row
    combined.to_csv(mtm_history, index=False)


_STALE_DATA_WARNED: set[str] = set()  # track which dates we've already hard-blocked


def _check_stale_prices(trade_date: str, close_prices: dict[str, float],
                        positions: pd.DataFrame) -> None:
    """Compare today's close prices with previous MTM snapshot to detect stale/qforward-filled data.

    如果所有持仓的收盘价与上一交易日完全相同（容差 < 0.005），
    说明数据同步可能失败，qlib 没有写入新数据。
    此时发出 CRITICAL 告警并硬阻断（sys.exit(1)）。
    """
    prev = _load_prev_mtm(trade_date)
    if prev is None:
        return  # first ever MTM, nothing to compare

    prev_details: list = prev.get("details", [])
    if not prev_details:
        return

    # Build map: instrument → close_price from previous snapshot
    prev_close: dict[str, float] = {}
    for entry in prev_details:
        # details: (inst, name, qty, cost, close, pnl)
        if isinstance(entry, (list, tuple)) and len(entry) >= 5:
            inst = str(entry[0])
            close_val = float(entry[4])
            if close_val > 0:
                prev_close[inst] = close_val

    if not prev_close:
        return

    # Compare today's close vs yesterday's for intersection of instruments
    checked = 0
    identical = 0
    tol = 0.005
    examples: list[str] = []
    for _, row in positions.iterrows():
        inst = str(row["instrument"])
        if inst in close_prices and inst in prev_close:
            checked += 1
            diff = abs(close_prices[inst] - prev_close[inst])
            if diff < tol:
                identical += 1
                if len(examples) < 3:
                    examples.append(f"{inst}: {prev_close[inst]} → {close_prices[inst]} (no change)")

    if checked == 0:
        return  # no overlapping instruments, can't judge

    stale_ratio = identical / checked
    if stale_ratio > 0.85:
        # — CRITICAL: stale data detected, hard block —
        lines = [
            f"\n{'=' * 60}",
            f"⛔ CRITICAL: 收盘价数据疑似陈旧/前向填充！",
            f"{'=' * 60}",
            f"交易日: {trade_date}",
            f"检查持仓: {checked}只",
            f"价格未变: {identical}只 ({stale_ratio:.0%})",
            f"阈值: > 85% 价格未变 → 判定数据陈旧",
        ]
        for ex in examples:
            lines.append(f"  {ex}")
        lines += [
            "",
            "说明: qlib 数据同步可能失败，当日最新行情未写入。",
            "      忽略此错误直接 MTM 会使用前一天的收盘价，",
            "      导致 PnL 结果完全错误（假 ¥0 日收益）。",
            "",
            "请检查数据同步: python scripts/ops/sync_csi800_daily.py --apply",
            "或检查 qlib 转换日志。",
            "=" * 60,
        ]
        msg = "\n".join(lines)
        print(msg)

        if trade_date not in _STALE_DATA_WARNED:
            _STALE_DATA_WARNED.add(trade_date)
            _send_notification(
                f"⛔ CRITICAL: 收盘价数据陈旧 — {trade_date}\n"
                f"检查持仓: {checked}只，价格未变: {identical}只 ({stale_ratio:.0%})\n"
                f"qlib 数据同步可能未成功写入新数据\n"
                f"请检查: python scripts/ops/sync_csi800_daily.py --apply"
            )

        sys.exit(1)


def _try_mark_to_market(trade_date: str) -> dict | None:
    """从 qlib 获取当日收盘价，重估持仓市值，计算累计和日度 PnL。

    保存 MTM 快照到 experiments/alpha_v1_daily/{trade_date}/mtm_snapshot.json，
    并追加到 experiments/alpha_v1_daily/mtm_history.csv。
    返回 None 表示数据未就绪。
    """
    pos_path = PROJECT_ROOT / "shadow" / "positions.csv"
    acct_path = PROJECT_ROOT / "shadow" / "account.json"
    if not pos_path.exists() or not acct_path.exists():
        return None

    try:
        positions = pd.read_csv(pos_path)
        if positions.empty:
            return None

        account = json.loads(acct_path.read_text())

        # Fetch close prices from qlib
        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter()
        adapter.init_qlib()

        instruments = positions["instrument"].tolist()
        market = adapter.get_features(
            instruments, ["$close"],
            start_time=trade_date, end_time=trade_date,
        )
        if market is None or market.empty:
            return None

        # Normalize multi-index to get (instrument → close)
        if isinstance(market.index, pd.MultiIndex):
            market = market.copy()
            frame = market.reset_index()
            frame = frame[frame.iloc[:, 1].astype(str).str.startswith(trade_date)]
        else:
            frame = market.reset_index()

        if frame.empty:
            return None

        frame = frame.drop_duplicates(subset=["instrument"], keep="last")
        close_col = [c for c in frame.columns if c == "$close"]
        if not close_col:
            return None
        close_col = close_col[0]
        close_prices: dict[str, float] = {}
        for _, r in frame.iterrows():
            inst = str(r["instrument"])
            try:
                val = float(r[close_col])
                if not pd.isna(val) and val > 0:
                    close_prices[inst] = val
            except (ValueError, TypeError):
                pass

        if not close_prices:
            return None

        # ── Stale data check (before using prices) ──
        _check_stale_prices(trade_date, close_prices, positions)

        # Re-price each position
        total_market_value = 0.0
        total_cost = 0.0
        priced_count = 0
        details: list[tuple[str, str, int, float, float, float]] = []  # code, name, qty, cost, close, pnl
        for _, row in positions.iterrows():
            inst = str(row["instrument"])
            qty = int(float(row.get("quantity", 0)))
            if qty <= 0:
                continue
            cost = float(row.get("cost_price", 0))
            close = close_prices.get(inst)
            if close is None:
                continue
            market_val = qty * close
            total_market_value += market_val
            total_cost += qty * cost
            priced_count += 1
            details.append((inst, _get_stock_name(inst), qty, cost, close, market_val - qty * cost))

        cash = float(account.get("cash", 0))
        initial_capital = float(account.get("initial_capital", 1_000_000))
        total_value = cash + total_market_value
        cumulative_pnl = total_value - initial_capital
        cumulative_pnl_pct = cumulative_pnl / initial_capital * 100 if initial_capital > 0 else 0.0

        # Daily PnL = change from previous MTM snapshot
        prev = _load_prev_mtm(trade_date)
        if prev is not None:
            daily_pnl = total_value - float(prev["total_value"])
        else:
            daily_pnl = cumulative_pnl  # first day

        if priced_count == 0:
            return None

        # Sort by position PnL (close - cost) best → worst
        details.sort(key=lambda x: x[5], reverse=True)

        snapshot = {
            "cash": cash,
            "market_value": total_market_value,
            "total_value": total_value,
            "initial_capital": initial_capital,
            "cumulative_pnl": cumulative_pnl,
            "cumulative_pnl_pct": round(cumulative_pnl_pct, 2),
            "daily_pnl": daily_pnl,
            "priced_count": priced_count,
            "total_positions": len(positions),
            "details": details,
        }
        _save_mtm_snapshot(trade_date, snapshot)
        return snapshot
    except Exception as e:
        if isinstance(e, SystemExit):
            raise  # re-raise sys.exit(1) from stale data check
        print(f"  ⚠ mark-to-market failed: {e}")
        return None



def _build_preopen_message(trade_date: str, rebalance_skipped: bool,
                            data_date: str,
                            top_picks: list[tuple[str, float]],
                            pred_count: int,
                            pred_path: str | Path | None = None) -> str:
    """构建 preopen Telegram 通知（仅计划，不执行）。"""
    lines = [
        f"✅ Alpha V1 Pre-open {trade_date}",
        f"Time: {_now_str()}",
        f"数据参考: {data_date}",
        "",
        f"📈 推荐股票",
    ]
    for i, (inst, score) in enumerate(top_picks[:5], 1):
        name = _get_stock_name(inst)
        lines.append(f"  {i}. {inst} {name}  score={score:.4f}")

    if rebalance_skipped:
        lines += [
            "",
            "⏭ 本周已调仓，跳过重复交易",
            f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
            f"Universe: {UNIVERSE} | 预测: {pred_count}只",
        ]
    else:
        # Read plan and show order intents with reference prices
        plan_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date / "plan"
        intents_path = plan_dir / "order_intents.csv"
        if intents_path.exists():
            try:
                orders_df = pd.read_csv(intents_path)
                if pred_path and Path(pred_path).exists():
                    scores_df = pd.read_csv(pred_path)[["instrument", "score"]]
                    orders_df = orders_df.merge(scores_df, on="instrument", how="left")
                    orders_df["score"] = orders_df["score"].fillna(0.0)
                else:
                    orders_df["score"] = 0.0

                buys = orders_df[orders_df["side"] == "buy"].sort_values("score", ascending=False)
                sells = orders_df[orders_df["side"] == "sell"].sort_values("score", ascending=False)

                lines += ["", "📋 计划交易（以 OPEN 价执行）", ""]
                if not buys.empty:
                    lines.append(f"  计划买入 ({len(buys)}):")
                    for _, row in buys.iterrows():
                        name = _get_stock_name(row["instrument"])
                        diff_val = float(row.get("diff_value", 0))
                        qty = int(row.get("requested_qty", 0))
                        shou = qty // 100
                        lines.append(f"    {row['instrument']} {name}  +{_fmt(diff_val)}  {shou}手  score={row['score']:.4f}")
                if not sells.empty:
                    lines.append(f"  计划卖出 ({len(sells)}):")
                    for _, row in sells.iterrows():
                        name = _get_stock_name(row["instrument"])
                        diff_val = float(row.get("diff_value", 0))
                        qty = int(row.get("requested_qty", 0))
                        shou = qty // 100
                        lines.append(f"    {row['instrument']} {name}  -{_fmt(abs(diff_val))}  {shou}手  score={row['score']:.4f}")
                lines.append("")
            except Exception as e:
                lines.append(f"  ⚠ 无法读取交易计划详情: {e}")

        lines += [
            f"📝 注: 计划不执行交易，待 21:30 数据同步后 postclose 以开盘价执行",
            f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
            f"Universe: {UNIVERSE} | 预测: {pred_count}只 | 参考数据: {data_date}",
        ]

    return "\n".join(lines)


def _build_postclose_message(trade_date: str, mtm: dict | None = None,
                              artifacts: ShadowRebalanceArtifacts | None = None) -> str:
    """构建 postclose Telegram 通知文本。

    参数由 run_postclose() 传入，不再内部调用 _try_mark_to_market()。
    """
    _get_stock_name("")  # warm cache

    lines = [
        f"📊 Alpha V1 Post-close {trade_date}",
        f"Time: {_now_str()}",
        "",
    ]

    if artifacts:
        turnover_fmt = _fmt(artifacts.turnover)
        mv = artifacts.total_value_after - artifacts.cash_after
        lines.append(f"🏦 执行摘要（按 {trade_date} 开盘价）")
        lines.append(f"  成交额: {turnover_fmt}  委托: {artifacts.order_count} "
                      f"成交: {artifacts.filled_count}  被拒: {artifacts.rejected_count}")
        lines.append(f"  Total: {_fmt(artifacts.total_value_after)}  "
                      f"Cash: {_fmt(artifacts.cash_after)}  "
                      f"MV: {_fmt(mv)}")
        print(f"    total={artifacts.total_value_after:.2f}, "
              f"cash={artifacts.cash_after:.2f}, "
              f"mv={mv:.2f}")
        lines.append("")

    if mtm:
        cum_pnl_str = f"+{_fmt(mtm['cumulative_pnl'])}" if mtm['cumulative_pnl'] >= 0 else _fmt(mtm['cumulative_pnl'])
        daily_str = f"+{_fmt(mtm['daily_pnl'])}" if mtm['daily_pnl'] >= 0 else _fmt(mtm['daily_pnl'])
        lines.append(f"💰 Mark-to-Market（按 {trade_date} 收盘价）")
        lines.append(f"  累计 PnL: {cum_pnl_str} ({mtm['cumulative_pnl_pct']:+.2f}%)")
        lines.append(f"  当日 PnL: {daily_str}")
        lines.append(f"  Total: {_fmt(mtm['total_value'])}  Cash: {_fmt(mtm['cash'])}")
        lines.append(f"  Position: {_fmt(mtm['market_value'])}  Holdings: {mtm['priced_count']}/{mtm['total_positions']}只")

        top3 = mtm['details'][:3]
        bot3 = mtm['details'][-3:] if len(mtm['details']) >= 3 else mtm['details']
        if top3:
            lines.append("")
            lines.append("📈 当日收益 Top 3")
            for inst, name, qty, cost, close, pnl_val in top3:
                s = f"+{_fmt(pnl_val)}" if pnl_val >= 0 else _fmt(pnl_val)
                lines.append(f"  {inst} {name}  {s}  {qty//100}手  {cost:.2f}→{close:.2f}")
        if bot3 and bot3 != top3:
            lines.append("")
            lines.append("📉 当日收益 Bottom 3")
            for inst, name, qty, cost, close, pnl_val in bot3:
                s = f"+{_fmt(pnl_val)}" if pnl_val >= 0 else _fmt(pnl_val)
                lines.append(f"  {inst} {name}  {s}  {qty//100}手  {cost:.2f}→{close:.2f}")
    else:
        lines.append("⚠ Mark-to-Market 不可用")
        lines.append(f"收盘价数据未就绪（数据同步可能未完成）。")

    return "\n".join(lines)


# ── Mode handlers ──────────────────────────────────────────────────────


def run_preopen(trade_date: str) -> None:
    """Alpha V1 preopen: predict → build plan → notify (不执行交易)."""
    t0 = time.time()
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Pre-open — {trade_date}")
    print(f"{'=' * 60}")

    # 1. Load model
    print("\n[1/4] Loading model...")
    try:
        models, clean_features = load_model_and_params()
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n模型加载失败: {e}")
        return
    for tag in ["5d", "20d"]:
        print(f"  Model {tag}: {models[tag][0].num_trees()} trees")
    print(f"  Features: {len(clean_features)}")

    # 2. Fetch latest available data
    print(f"\n[2/4] Fetching data for {trade_date}...")
    try:
        frame, clean_features, data_date = fetch_latest_data(trade_date)
        print(f"  {UNIVERSE}: {len(frame)} rows (data_date={data_date})")
    except Exception as e:
        print(f"  ❌ {e}")
        _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n数据获取失败: {e}")
        return

    if frame.empty:
        print(f"  ⚠ 交易日 {trade_date} 无数据（qlib 数据未就绪或休市日）")
        _send_notification(
            f"⚠ Alpha V1 Pre-open {trade_date}\n"
            f"交易日无数据，跳过\n"
            f"当前 qlib 最新可用数据可能早于 {trade_date}\n"
            f"请检查数据同步是否完成"
        )
        return

    # 3. Generate predictions
    print(f"\n[3/4] Generating predictions...")
    try:
        pred_df = generate_predictions_for_date(models, clean_features, frame, trade_date)
    except Exception as e:
        print(f"  ❌ {e}")
        _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n预测生成失败: {e}")
        return

    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    pred_path = PREDICTIONS_DIR / f"predictions_{trade_date}.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"  → {len(pred_df)} predictions saved: {pred_path}")

    top = pred_df.sort_values("score", ascending=False).head(5)
    top_picks = [(row["instrument"], row["score"]) for _, row in top.iterrows()]
    for inst, score in top_picks:
        print(f"    #{top_picks.index((inst, score)) + 1} {inst}  score={score:.4f}")

    # 4. Build plan (no execution)
    print(f"\n[4/4] Building trading plan...")
    output_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date
    output_dir.mkdir(parents=True, exist_ok=True)

    rebalance_skipped = not _should_rebalance(trade_date)
    if rebalance_skipped:
        print(f"  ⏭ {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week")
        # Still create plan dir with skip marker so postclose knows preopen ran
        plan_dir = output_dir / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        write_json(plan_dir / "plan_meta.json", {
            "trade_date": trade_date,
            "status": "skipped",
            "reason": f"{ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week",
            "build_ts": datetime.now().isoformat(),
        })
    else:
        try:
            build_alpha_v1_plan(
                base_dir=".",
                trade_date=trade_date,
                reference_date=data_date,
                predictions_path=str(pred_path),
                output_dir=str(output_dir),
            )
        except Exception as e:
            print(f"  ❌ 建仓计划失败: {e}")
            _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n建仓计划失败: {e}")
            return

    # 5. Telegram notify
    msg = _build_preopen_message(
        trade_date, rebalance_skipped, data_date,
        top_picks, len(pred_df), pred_path,
    )
    _send_notification(msg)

    elapsed = time.time() - t0
    print(f"\n✅ Pre-open {trade_date} completed in {elapsed:.0f}s")


def run_postclose(trade_date: str) -> None:
    """Postclose: execute plan at OPEN → MTM at CLOSE → notify."""
    t0 = time.time()
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Post-close — {trade_date}")
    print(f"{'=' * 60}")

    plan_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date / "plan"
    output_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date

    has_plan = plan_dir.exists() and (plan_dir / "order_intents.csv").exists()
    has_skip_meta = plan_dir.exists() and (plan_dir / "plan_meta.json").exists()

    if has_plan:
        print(f"\n[1/2] Executing plan at OPEN price...")
        try:
            artifacts = execute_alpha_v1_plan(
                base_dir=".",
                plan_dir=str(plan_dir),
                execution_date=trade_date,
                output_dir=str(output_dir),
            )
            print(f"  ✅ orders={artifacts.order_count}, "
                  f"total=¥{artifacts.total_value_after:_.0f}, "
                  f"cash=¥{artifacts.cash_after:_.0f}, "
                  f"turnover=¥{artifacts.turnover:_.0f}")

            print(f"\n[2/2] MTM at CLOSE price...")
            mtm = _try_mark_to_market(trade_date)
            if mtm is None:
                raise ValueError(
                    f"⛔ 收盘价数据未就绪！数据同步可能尚未完成。\n"
                    f"请先运行: python scripts/ops/sync_csi800_daily.py --apply"
                )

            msg = _build_postclose_message(trade_date, mtm, artifacts)
            _send_notification(msg)
        except Exception as e:
            print(f"  ❌ {e}")
            _send_notification(f"⛔ Alpha V1 Post-close {trade_date} FAILED\n{e}")
            sys.exit(1)

    elif has_skip_meta:
        mtm = _try_mark_to_market(trade_date)
        msg = _build_postclose_message(trade_date, mtm=mtm)
        _send_notification(msg)
    else:
        _send_notification(
            f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
            f"未找到 preopen 计划文件: {plan_dir}\n"
            f"请先运行 preopen。"
        )
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"\n✅ Post-close {trade_date} completed in {elapsed:.0f}s")


def run_train() -> None:
    """训练模式：调用 run_alpha_v1_weekly_train.py。"""
    print(f"\n{'=' * 60}")
    print("Alpha V1 Weekly Training")
    print(f"{'=' * 60}")

    train_script = str(PROJECT_ROOT / "scripts" / "run_alpha_v1_weekly_train.py")
    if not Path(train_script).exists():
        print(f"  ❌ 脚本不存在: {train_script}")
        return

    print(f"  启动: {train_script}")
    result = subprocess.run(
        [sys.executable, train_script],
        cwd=str(PROJECT_ROOT),
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  ❌ 训练失败 (exit {result.returncode})")
    else:
        print(f"  ✅ 训练完成")


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha V1 每日运营")
    parser.add_argument("--trade-date", help="交易日期 YYYY-MM-DD")
    parser.add_argument(
        "--mode", choices=["preopen", "postclose", "train"], default="preopen",
        help="运营模式 (默认: preopen)",
    )
    args = parser.parse_args()

    if args.mode == "train":
        run_train()
    elif args.mode == "preopen":
        if not args.trade_date:
            parser.error("--trade-date 是 preopen 模式的必填参数")
        run_preopen(args.trade_date)
    elif args.mode == "postclose":
        if not args.trade_date:
            parser.error("--trade-date 是 postclose 模式的必填参数")
        run_postclose(args.trade_date)


if __name__ == "__main__":
    main()
