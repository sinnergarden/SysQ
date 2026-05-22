#!/usr/bin/env python3
"""
Alpha V1 每日运营：preopen → inference + plan + notify；postclose → execute + MTM + notify；train → 周级别训练。

生产模式默认写 shadow/account.json / positions.csv / ledger.csv。
--debug-run 不修改 shadow 文件，输出到 --output-dir。
--notify-only 仅从已有产物重建通知，不执行任何交易。
--force-rerun 是危险的生产覆盖，必须配合 --reason。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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

from qlib.data import D as qlib_D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.shadow_rebalance import build_alpha_v1_plan
from qsys.ops.shadow_rebalance import execute_alpha_v1_plan
from qsys.ops.shadow_rebalance import ShadowRebalanceArtifacts
from qsys.ops.shadow_rebalance import write_json
from qsys.ops.telegram import send_telegram_message
from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE


# ── 模型加载 ────────────────────────────────────────────────────────────

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
    freq = ALPHA_V1_CANDIDATE.portfolio.rebalance_freq
    if freq != "weekly":
        return True
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
    adapter = QlibAdapter()
    adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()
    from qsys.strategy.alpha_v1.spec import get_clean_features
    clean_features = get_clean_features(all_features)
    cal = qlib_D.calendar(start_time="2020-01-01", end_time=until_date)
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


# ── 日期工具 ──────────────────────────────────────────────────────────────

def _prev_trading_day(trade_date: str) -> str | None:
    try:
        cal = qlib_D.calendar(start_time="2010-01-01", end_time=trade_date)
        if cal is None or len(cal) < 2:
            return None
        return pd.Timestamp(cal[-2]).strftime("%Y-%m-%d")
    except Exception:
        return None


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
    return f"¥{amount/1000:.2f}k"


# ── Stale data detection（使用 mtm_snapshot.json）─────────────────────

def _load_mtm_snapshot(trade_date: str) -> dict | None:
    path = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
            / trade_date / "mtm" / "mtm_snapshot.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


_STALE_DATA_WARNED: set[str] = set()


def _check_stale_prices(trade_date: str, close_prices: dict[str, float],
                        positions: pd.DataFrame) -> dict:
    """Compare today's close prices with previous MTM snapshot.

    使用前一交易日的 mtm_snapshot.json 中的 details 做对比。
    如果 >85% 的持仓价格未变（容差 < 0.005），硬阻断。

    返回 stale_check dict，包含检查结果元数据。
    """
    prev_date = _prev_trading_day(trade_date)
    result = {
        "trade_date": trade_date,
        "prev_trade_date": prev_date,
        "checked_count": 0,
        "identical_count": 0,
        "identical_ratio": 0.0,
        "threshold": 0.85,
        "status": "skipped",
        "examples": [],
    }
    if prev_date is None:
        return result
    prev_snapshot = _load_mtm_snapshot(prev_date)
    if prev_snapshot is None:
        print(f"  ⚠ 无上一交易日 ({prev_date}) MTM 快照，跳过陈旧检查")
        return result
    prev_details: list = prev_snapshot.get("details", [])
    if not prev_details:
        return result
    prev_close: dict[str, float] = {}
    for entry in prev_details:
        if isinstance(entry, (list, tuple)) and len(entry) >= 5:
            inst = str(entry[0])
            close_val = float(entry[4])
            if close_val > 0:
                prev_close[inst] = close_val
    if not prev_close:
        return result
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
    stale_ratio = identical / checked if checked > 0 else 0.0
    result["checked_count"] = checked
    result["identical_count"] = identical
    result["identical_ratio"] = stale_ratio
    result["examples"] = examples
    if checked == 0:
        result["status"] = "skipped"
        return result
    if stale_ratio > 0.85:
        result["status"] = "blocked"
        lines = [
            f"\n{'=' * 60}",
            f"⛔ CRITICAL: 收盘价数据疑似陈旧/前向填充！",
            f"{'=' * 60}",
            f"交易日: {trade_date}",
            f"上一交易日: {prev_date}",
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
    else:
        result["status"] = "passed"
    return result


def _save_stale_check(trade_date: str, check_result: dict) -> None:
    mtm_dir = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
               / trade_date / "mtm")
    mtm_dir.mkdir(parents=True, exist_ok=True)
    write_json(mtm_dir / "stale_check.json", check_result)


# ── Artifact paths ──────────────────────────────────────────────────────

def _daily_dir(trade_date: str) -> Path:
    return PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date

def _plan_dir(trade_date: str) -> Path:
    return _daily_dir(trade_date) / "plan"

def _exec_dir(trade_date: str) -> Path:
    return _daily_dir(trade_date) / "execution"

def _staging_dir(trade_date: str) -> Path:
    return _exec_dir(trade_date) / "staging"

def _mtm_dir(trade_date: str) -> Path:
    return _daily_dir(trade_date) / "mtm"

def _committed_marker(trade_date: str) -> Path:
    return _exec_dir(trade_date) / "COMMITTED"


# ── 执行状态检查 ─────────────────────────────────────────────────────────

def _is_execution_committed(trade_date: str) -> bool:
    return _committed_marker(trade_date).exists()


def _load_execution_summary(trade_date: str) -> dict | None:
    path = _exec_dir(trade_date) / "execution_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ── MTM（独立进程可重复运行）────────────────────────────────────────────

def _try_mark_to_market(trade_date: str,
                        close_prices_override: dict[str, float] | None = None) -> dict | None:
    pos_path = PROJECT_ROOT / "shadow" / "positions.csv"
    acct_path = PROJECT_ROOT / "shadow" / "account.json"
    if not pos_path.exists() or not acct_path.exists():
        return None
    try:
        positions = pd.read_csv(pos_path)
        if positions.empty:
            return None
        account = json.loads(acct_path.read_text())
        if close_prices_override is not None:
            close_prices = close_prices_override
        else:
            adapter = QlibAdapter()
            adapter.init_qlib()
            instruments = positions["instrument"].tolist()
            market = adapter.get_features(
                instruments, ["$close"],
                start_time=trade_date, end_time=trade_date,
            )
            if market is None or market.empty:
                return None
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
            close_prices = {}
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
        stale_result = _check_stale_prices(trade_date, close_prices, positions)
        _save_stale_check(trade_date, stale_result)
        total_market_value = 0.0
        total_cost = 0.0
        priced_count = 0
        details: list[tuple] = []
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
        prev_date = _prev_trading_day(trade_date)
        if prev_date is not None:
            prev_snap = _load_mtm_snapshot(prev_date)
            if prev_snap is not None:
                daily_pnl = total_value - float(prev_snap["total_value"])
            else:
                daily_pnl = cumulative_pnl
        else:
            daily_pnl = cumulative_pnl
        if priced_count == 0:
            return None
        details.sort(key=lambda x: x[5], reverse=True)
        snapshot = {
            "cash": cash, "market_value": total_market_value,
            "total_value": total_value, "initial_capital": initial_capital,
            "cumulative_pnl": cumulative_pnl,
            "cumulative_pnl_pct": round(cumulative_pnl_pct, 2),
            "daily_pnl": daily_pnl, "priced_count": priced_count,
            "total_positions": len(positions), "details": details,
        }
        _save_mtm_snapshot(trade_date, snapshot)
        return snapshot
    except Exception as e:
        if isinstance(e, SystemExit):
            raise
        print(f"  ⚠ mark-to-market failed: {e}")
        return None


def _save_mtm_snapshot(trade_date: str, snapshot: dict) -> None:
    mtm_dir = _mtm_dir(trade_date)
    mtm_dir.mkdir(parents=True, exist_ok=True)
    write_json(mtm_dir / "mtm_snapshot.json", snapshot)


# ── 存档已有产物（force-rerun 使用）─────────────────────────────────────

def _archive_execution(trade_date: str) -> None:
    exec_dir = _exec_dir(trade_date)
    if not exec_dir.exists():
        return
    archive_dir = _daily_dir(trade_date) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.move(str(exec_dir), str(archive_dir / f"execution_{ts}"))
    print(f"  📦 已有执行产物已存档: archive/execution_{ts}")


# ── 通知构建 ─────────────────────────────────────────────────────────────

def _build_preopen_message(trade_date: str, rebalance_skipped: bool,
                            data_date: str, top_picks: list[tuple[str, float]],
                            pred_count: int,
                            pred_path: str | Path | None = None) -> str:
    lines = [
        f"✅ Alpha V1 Pre-open {trade_date}",
        f"Time: {_now_str()}",
        f"数据参考: {data_date}",
        "", "📈 推荐股票",
    ]
    for i, (inst, score) in enumerate(top_picks[:5], 1):
        name = _get_stock_name(inst)
        lines.append(f"  {i}. {inst} {name}  score={score:.4f}")
    if rebalance_skipped:
        lines += [
            "", "⏭ 本周已调仓，跳过重复交易",
            f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
            f"Universe: {UNIVERSE} | 预测: {pred_count}只",
        ]
    else:
        plan_dir = _plan_dir(trade_date)
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
                        lines.append(f"    {row['instrument']} {name}  +{_fmt(diff_val)}  {qty//100}手  score={row['score']:.4f}")
                if not sells.empty:
                    lines.append(f"  计划卖出 ({len(sells)}):")
                    for _, row in sells.iterrows():
                        name = _get_stock_name(row["instrument"])
                        diff_val = float(row.get("diff_value", 0))
                        qty = int(row.get("requested_qty", 0))
                        lines.append(f"    {row['instrument']} {name}  -{_fmt(abs(diff_val))}  {qty//100}手  score={row['score']:.4f}")
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
                              artifacts: ShadowRebalanceArtifacts | None = None,
                              execution_committed: bool = False,
                              execution_skipped: bool = False,
                              debug_run: bool = False,
                              stale_check: dict | None = None) -> str:
    _get_stock_name("")
    lines = [
        f"📊 Alpha V1 Post-close {trade_date}",
        f"Time: {_now_str()}", "",
    ]
    if debug_run:
        lines.append("🔧 调试模式 — 不修改 shadow 账户")
        lines.append("")
    if execution_committed and not execution_skipped:
        lines += ["✅ 执行状态: 已完成（幂等跳过，未重复执行）", ""]
    elif execution_committed and execution_skipped:
        lines += ["✅ 执行状态: 无计划需执行（跳过）", ""]
    elif debug_run:
        lines += ["🔧 执行状态: 调试模式，未提交 shadow 账户", ""]
    if stale_check:
        sc = stale_check
        status_icon = {"passed": "✅", "blocked": "⛔", "skipped": "⏭"}
        lines.append(
            f"📡 数据陈旧检查: {status_icon.get(sc.get('status', ''), '❓')} "
            f"一致={sc.get('identical_count', 0)}/{sc.get('checked_count', 0)} "
            f"({sc.get('identical_ratio', 0)*100:.0f}%)"
        )
        if sc.get("examples"):
            for ex in sc["examples"]:
                lines.append(f"    {ex}")
        lines.append("")
    if artifacts:
        lines.append(f"🏦 执行摘要（按 {trade_date} 开盘价）")
        lines.append(
            f"  成交额: {_fmt(artifacts.turnover)}  委托: {artifacts.order_count} "
            f"成交: {artifacts.filled_count}  被拒: {artifacts.rejected_count}"
        )
        mv = artifacts.total_value_after - artifacts.cash_after
        lines.append(
            f"  Total: {_fmt(artifacts.total_value_after)}  "
            f"Cash: {_fmt(artifacts.cash_after)}  MV: {_fmt(mv)}"
        )
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
        lines.append("收盘价数据未就绪（数据同步可能未完成）。")
    return "\n".join(lines)


# ── run_meta ──────────────────────────────────────────────────────────────

def _save_run_meta(trade_date: str, mode: str, data_date: str | None = None,
                    debug_run: bool = False, reason: str | None = None,
                    extra: dict | None = None) -> None:
    meta = {
        "trade_date": trade_date,
        "mode": mode, "reference_date": data_date,
        "debug_run": debug_run, "reason": reason,
        "ts": datetime.now().isoformat(),
        **(extra or {}),
    }
    d = _daily_dir(trade_date)
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "run_meta.json", meta)


# ── Mode handlers ──────────────────────────────────────────────────────

def run_preopen(trade_date: str, debug_run: bool = False,
                no_notify: bool = False, reason: str | None = None) -> None:
    t0 = time.time()
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Pre-open — {trade_date}" + (" (DEBUG)" if debug_run else ""))
    print(f"{'=' * 60}")
    print("\n[1/4] Loading model...")
    try:
        models, clean_features = load_model_and_params()
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        if not no_notify:
            _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n模型加载失败: {e}")
        return
    for tag in ["5d", "20d"]:
        print(f"  Model {tag}: {models[tag][0].num_trees()} trees")
    print(f"  Features: {len(clean_features)}")
    print(f"\n[2/4] Fetching data for {trade_date}...")
    try:
        frame, clean_features, data_date = fetch_latest_data(trade_date)
        print(f"  {UNIVERSE}: {len(frame)} rows (data_date={data_date})")
    except Exception as e:
        print(f"  ❌ {e}")
        if not no_notify:
            _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n数据获取失败: {e}")
        return
    if frame.empty:
        print(f"  ⚠ 交易日 {trade_date} 无数据")
        if not no_notify:
            _send_notification(f"⚠ Alpha V1 Pre-open {trade_date}\n交易日无数据，跳过")
        return
    print(f"\n[3/4] Generating predictions...")
    try:
        pred_df = generate_predictions_for_date(models, clean_features, frame, trade_date)
    except Exception as e:
        print(f"  ❌ {e}")
        if not no_notify:
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
    print(f"\n[4/4] Building trading plan...")
    output_dir = _daily_dir(trade_date)
    output_dir.mkdir(parents=True, exist_ok=True)
    _save_run_meta(trade_date, "preopen", data_date=data_date, debug_run=debug_run, reason=reason)
    rebalance_skipped = not _should_rebalance(trade_date)
    if rebalance_skipped:
        print(f"  ⏭ {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week")
        plan_dir = _plan_dir(trade_date)
        plan_dir.mkdir(parents=True, exist_ok=True)
        write_json(plan_dir / "plan_meta.json", {
            "trade_date": trade_date, "reference_date": data_date,
            "status": "skipped",
            "reason": f"{ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week",
            "build_ts": datetime.now().isoformat(),
        })
    else:
        try:
            build_alpha_v1_plan(
                base_dir=".", trade_date=trade_date, reference_date=data_date,
                predictions_path=str(pred_path), output_dir=str(output_dir),
            )
        except Exception as e:
            print(f"  ❌ 建仓计划失败: {e}")
            if not no_notify:
                _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n建仓计划失败: {e}")
            return
    if not no_notify:
        msg = _build_preopen_message(trade_date, rebalance_skipped, data_date,
                                      top_picks, len(pred_df), pred_path)
        _send_notification(msg)
    elapsed = time.time() - t0
    print(f"\n✅ Pre-open {trade_date} completed in {elapsed:.0f}s")


def run_postclose(trade_date: str, debug_run: bool = False,
                  no_notify: bool = False, force_rerun: bool = False,
                  reason: str | None = None) -> None:
    t0 = time.time()
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Post-close — {trade_date}"
          + (" (DEBUG)" if debug_run else "")
          + (" (FORCE-RERUN)" if force_rerun else ""))
    print(f"{'=' * 60}")
    daily_output = _daily_dir(trade_date)
    daily_output.mkdir(parents=True, exist_ok=True)
    _save_run_meta(trade_date, "postclose", debug_run=debug_run, reason=reason,
                   extra={"force_rerun": force_rerun})
    plan_dir = _plan_dir(trade_date)
    has_plan = plan_dir.exists() and (plan_dir / "order_intents.csv").exists()
    has_skip_meta = plan_dir.exists() and (plan_dir / "plan_meta.json").exists()
    has_skip = has_skip_meta and not has_plan
    already_committed = _is_execution_committed(trade_date)
    if already_committed and not force_rerun:
        print(f"  ⏭ 执行已提交（COMMITTED 标记存在），幂等跳过")
        print(f"  💡 如需重新执行请使用 --force-rerun + --reason")
        artifacts = _load_artifacts_for_notification(trade_date)
        mtm = _try_mark_to_market(trade_date)
        if not no_notify:
            msg = _build_postclose_message(
                trade_date, mtm=mtm, artifacts=artifacts,
                execution_committed=True, execution_skipped=has_skip,
                debug_run=debug_run,
            )
            _send_notification(msg)
        elapsed = time.time() - t0
        print(f"\n✅ Post-close {trade_date} (idempotent skip) completed in {elapsed:.0f}s")
        return
    if already_committed and force_rerun:
        if not reason:
            print("  ❌ --force-rerun 必须配合 --reason")
            sys.exit(1)
        print(f"  ⚠ --force-rerun 生效，原因: {reason}")
        _archive_execution(trade_date)
    if not has_plan and not has_skip:
        if not no_notify:
            _send_notification(
                f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
                f"未找到 preopen 计划文件: {plan_dir}\n"
                f"请先运行 preopen。"
            )
        sys.exit(1)
    artifacts = None
    if has_plan:
        print(f"\n[1/3] Validating execution prerequisites...")
        instruments = _load_plan_instruments(plan_dir)
        if instruments:
            try:
                from qsys.ops.shadow_rebalance import _fetch_market_snapshot
                open_prices, _ = _fetch_market_snapshot(trade_date, instruments, price_col="open")
                if not open_prices:
                    raise ValueError("No open prices available")
                print(f"  ✅ Open prices: {len(open_prices)} instruments")
            except Exception as e:
                print(f"  ❌ 开盘价不可用: {e}")
                if not no_notify:
                    _send_notification(
                        f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
                        f"开盘价数据不可用。\n{e}"
                    )
                sys.exit(1)
        staging_exec_dir = _staging_dir(trade_date)
        staging_exec_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[2/3] Executing plan at OPEN price...")
        try:
            artifacts = execute_alpha_v1_plan(
                base_dir=".", plan_dir=str(plan_dir),
                execution_date=trade_date, output_dir=str(staging_exec_dir),
                debug_run=debug_run,
            )
            print(f"  ✅ orders={artifacts.order_count}, "
                  f"total={_fmt(artifacts.total_value_after)}, "
                  f"cash={_fmt(artifacts.cash_after)}, "
                  f"turnover={_fmt(artifacts.turnover)}")
        except Exception as e:
            print(f"  ❌ 执行失败: {e}")
            if not no_notify:
                _send_notification(f"⛔ Alpha V1 Post-close {trade_date} FAILED\n{e}")
            sys.exit(1)
        if not debug_run:
            print(f"\n  Committing to shadow account...")
            _commit_execution(trade_date, staging_exec_dir, artifacts)
            print(f"  ✅ Shadow account updated")
        else:
            print(f"\n  🔧 调试模式 — 不提交 shadow 账户")
    print(f"\n{'[3/3]' if has_plan else '[1/1]'} MTM at CLOSE price...")
    mtm = _try_mark_to_market(trade_date)
    if mtm is None:
        print(f"  ⚠ 收盘价数据未就绪")
        if not no_notify:
            _send_notification(
                f"⛔ Alpha V1 Post-close {trade_date}\n"
                f"收盘价数据未就绪。数据同步可能尚未完成。\n"
                f"请先运行: python scripts/ops/sync_csi800_daily.py --apply"
            )
        sys.exit(1)
    if not no_notify:
        stale_check_path = _mtm_dir(trade_date) / "stale_check.json"
        stale_check = None
        if stale_check_path.exists():
            try:
                stale_check = json.loads(stale_check_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        msg = _build_postclose_message(
            trade_date, mtm=mtm, artifacts=artifacts,
            execution_committed=already_committed or not debug_run,
            execution_skipped=has_skip, debug_run=debug_run,
            stale_check=stale_check,
        )
        _send_notification(msg)
    elapsed = time.time() - t0
    print(f"\n✅ Post-close {trade_date} completed in {elapsed:.0f}s")


def _load_plan_instruments(plan_dir: Path) -> list[str]:
    intents_path = plan_dir / "order_intents.csv"
    if not intents_path.exists():
        return []
    try:
        df = pd.read_csv(intents_path)
        return sorted(set(df["instrument"].astype(str)))
    except Exception:
        return []


def _load_artifacts_for_notification(trade_date: str) -> ShadowRebalanceArtifacts | None:
    exec_dir = _exec_dir(trade_date)
    summary_path = exec_dir / "execution_summary.json"
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    plan_dir = _plan_dir(trade_date)
    return ShadowRebalanceArtifacts(
        trade_date=trade_date,
        run_id=summary.get("run_id", ""),
        status=summary.get("status", "success"),
        strategy_id=summary.get("strategy_id", "alpha_v1"),
        strategy_version=summary.get("strategy_version", ""),
        portfolio_method=summary.get("portfolio_method", "plan_execution"),
        top_n=summary.get("portfolio_params", {}).get("top_n", 20),
        buffer_hold=60, buffer_buy=40, single_stock_cap=0.07,
        turnover_buffer=0.0, price_mode="open", rebalance_mode="plan_execution",
        target_weights_path=str(plan_dir / "target_weights.csv") if (plan_dir / "target_weights.csv").exists() else "",
        order_intents_path=str(plan_dir / "order_intents.csv") if (plan_dir / "order_intents.csv").exists() else "",
        execution_summary_path=str(summary_path),
        account_after_path=str(exec_dir / "account_after.json"),
        positions_after_path=str(exec_dir / "positions_after.csv"),
        shadow_account_path="", shadow_positions_path="", shadow_ledger_path="",
        rebalance_audit_path=str(plan_dir / "rebalance_audit.csv") if (plan_dir / "rebalance_audit.csv").exists() else "",
        order_count=summary.get("order_count", 0),
        buy_count=summary.get("buy_count", 0),
        sell_count=summary.get("sell_count", 0),
        skipped_count=summary.get("skipped_count", 0),
        filled_count=summary.get("filled_count", 0),
        rejected_count=summary.get("rejected_count", 0),
        turnover=summary.get("turnover", 0.0),
        cash_after=summary.get("cash_after", 0.0),
        total_value_after=summary.get("total_value_after", 0.0),
    )


def _commit_execution(trade_date: str, staging_dir: Path,
                       artifacts: ShadowRebalanceArtifacts) -> None:
    exec_dir = _exec_dir(trade_date)
    exec_dir.mkdir(parents=True, exist_ok=True)
    for fname in ["account_after.json", "positions_after.csv", "execution_summary.json"]:
        src = staging_dir / fname
        if src.exists():
            shutil.copy2(str(src), str(exec_dir / fname))
    src_acct = staging_dir / "account_after.json"
    if src_acct.exists():
        shutil.copy2(str(src_acct), str(PROJECT_ROOT / "shadow" / "account.json"))
    src_pos = staging_dir / "positions_after.csv"
    if src_pos.exists():
        shutil.copy2(str(src_pos), str(PROJECT_ROOT / "shadow" / "positions.csv"))
    _committed_marker(trade_date).touch()
    print(f"  ✅ 执行已提交: {exec_dir}")


def run_notify_only(trade_date: str, debug_run: bool = False) -> None:
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Notify-only — {trade_date}")
    print(f"{'=' * 60}")
    artifacts = _load_artifacts_for_notification(trade_date)
    mtm = _try_mark_to_market(trade_date)
    already_committed = _is_execution_committed(trade_date)
    has_skip = _plan_dir(trade_date).exists() and not (
        _plan_dir(trade_date) / "order_intents.csv").exists()
    stale_check_path = _mtm_dir(trade_date) / "stale_check.json"
    stale_check = None
    if stale_check_path.exists():
        try:
            stale_check = json.loads(stale_check_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    msg = _build_postclose_message(
        trade_date, mtm=mtm, artifacts=artifacts,
        execution_committed=already_committed, execution_skipped=has_skip,
        debug_run=debug_run, stale_check=stale_check,
    )
    _send_notification(msg)


def run_train() -> None:
    print(f"\n{'=' * 60}")
    print("Alpha V1 Weekly Training")
    print(f"{'=' * 60}")
    train_script = str(PROJECT_ROOT / "scripts" / "run_alpha_v1_weekly_train.py")
    if not Path(train_script).exists():
        print(f"  ❌ 脚本不存在: {train_script}")
        return
    print(f"  启动: {train_script}")
    result = subprocess.run([sys.executable, train_script], cwd=str(PROJECT_ROOT), capture_output=False)
    if result.returncode != 0:
        print(f"  ❌ 训练失败 (exit {result.returncode})")
    else:
        print(f"  ✅ 训练完成")


# ── CLI ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha V1 每日运营")
    parser.add_argument("--trade-date", help="交易日期 YYYY-MM-DD")
    parser.add_argument("--mode", choices=["preopen", "postclose", "train"], default="preopen")
    parser.add_argument("--debug-run", action="store_true",
                        help="调试模式：不修改 shadow/account.json / positions.csv / ledger.csv")
    parser.add_argument("--output-dir",
                        help="调试模式下输出目录（默认: experiments/alpha_v1_daily/{trade_date}）")
    parser.add_argument("--no-notify", action="store_true", help="跳过 Telegram 通知")
    parser.add_argument("--notify-only", action="store_true",
                        help="仅从已有产物重建并发送通知，不执行任何交易")
    parser.add_argument("--force-rerun", action="store_true",
                        help="危险模式：覆盖已提交的执行产物（必须配合 --reason）")
    parser.add_argument("--reason", help="操作原因说明（--force-rerun 必填）")
    args = parser.parse_args()
    if args.force_rerun and not args.reason:
        parser.error("--force-rerun 必须配合 --reason 提供原因")
    if args.mode == "train":
        if args.force_rerun:
            print("⚠ --force-rerun 对 train 模式无意义，忽略")
        run_train()
        return
    if not args.trade_date:
        parser.error(f"--trade-date 是 {args.mode} 模式的必填参数")
    if args.notify_only:
        run_notify_only(args.trade_date, debug_run=args.debug_run)
        return
    if args.mode == "preopen":
        run_preopen(args.trade_date, debug_run=args.debug_run,
                     no_notify=args.no_notify, reason=args.reason)
    elif args.mode == "postclose":
        run_postclose(args.trade_date, debug_run=args.debug_run,
                       no_notify=args.no_notify,
                       force_rerun=args.force_rerun, reason=args.reason)


if __name__ == "__main__":
    main()
