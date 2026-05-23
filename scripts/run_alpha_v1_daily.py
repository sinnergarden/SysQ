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

# Force output root — set at mode handler entry; all path helpers derive from it.
_FORCE_OUTPUT_ROOT: Path | None = None

# ── Ledger ────────────────────────────────────────────────────────────
LEDGER_DB_PATH = str(PROJECT_ROOT / "data" / "trade.db")


def _shadow_account_id() -> str:
    return ALPHA_V1_CANDIDATE.shadow_account_id


from qlib.data import D as qlib_D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.commit_guard import (
    cleanup_committing,
    committed_marker,
    committing_marker,
    is_execution_committed,
)
from qsys.ops.daily_artifacts import archive_execution, save_run_meta
from qsys.ops.mtm import (
    StaleDataError,
    check_stale_prices,
    fetch_close_prices,
    load_mtm_snapshot,
    save_stale_check,
    try_mark_to_market,
)
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
    # Try ledger first, fall back to shadow/account.json
    last_trade_date: str | None = None
    if Path(LEDGER_DB_PATH).exists():
        try:
            from qsys.ledger.service import LedgerService
            service = LedgerService(LEDGER_DB_PATH)
            last_trade_date = service.get_latest_trade_date(_shadow_account_id())
            service.close()
        except Exception:
            pass
    if not last_trade_date:
        account_path = PROJECT_ROOT / "shadow" / "account.json"
        if account_path.exists():
            try:
                acct_data = json.loads(account_path.read_text())
                last_trade_date = acct_data.get("trade_date", "")
            except (json.JSONDecodeError, OSError):
                pass
    if last_trade_date:
        last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
        last_iso = last_dt.isocalendar()
        if last_iso[0] == current_iso[0] and last_iso[1] == current_iso[1]:
            return False
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


# ── Artifact paths ──────────────────────────────────────────────────────

def _resolve_run_root(trade_date: str, debug_run: bool = False,
                      output_dir: str | None = None) -> Path:
    """Resolve output root for this run. Called once at mode handler entry."""
    if output_dir:
        return Path(output_dir)
    if debug_run:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return PROJECT_ROOT / "experiments" / "debug" / "alpha_v1" / f"{trade_date}_{ts}"
    return PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date


def _plan_dir(run_root: Path) -> Path:
    return run_root / "plan"

def _exec_dir(run_root: Path) -> Path:
    return run_root / "execution"

def _staging_dir(run_root: Path) -> Path:
    return _exec_dir(run_root) / "staging"


# ── 通知构建 ─────────────────────────────────────────────────────────────

def _build_preopen_message(trade_date: str, rebalance_skipped: bool,
                            data_date: str, top_picks: list[tuple[str, float]],
                            pred_count: int,
                            pred_path: str | Path | None = None,
                            run_root: Path | None = None) -> str:
    lines = [
        f"✅ Alpha V1 Pre-open {trade_date}",
        f"Time: {_now_str()}",
        f"数据参考: {data_date}",
        "", "📈 推荐股票",
    ]
    for i, (inst, score) in enumerate(top_picks[:5], 1):
        name = _get_stock_name(inst)
        lines.append(f"  {i}. {inst} {name}  score={score:.4f}")
        # Show existing plan details if available (even on skip re-runs)
    plan_dir = _plan_dir(run_root) if run_root else Path(trade_date) / "plan"
    intents_path = plan_dir / "order_intents.csv"
    has_existing_plan = intents_path.exists()
    if has_existing_plan:
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
                lines.append(f"    {'代码':<12} {'名称':<8} {'买入金额':<12} 手数  score")
                for _, row in buys.iterrows():
                    name = _get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    qty = int(row.get("requested_qty", 0))
                    lines.append(f"    {row['instrument']:<12} {name:<8} +{_fmt(diff_val):<10} {qty//100}手  {row['score']:.4f}")
            if not sells.empty:
                lines.append(f"  计划卖出 ({len(sells)}):")
                lines.append(f"    {'代码':<12} {'名称':<8} {'卖出金额':<12} 手数  score")
                for _, row in sells.iterrows():
                    name = _get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    qty = int(row.get("requested_qty", 0))
                    lines.append(f"    {row['instrument']:<12} {name:<8} -{_fmt(abs(diff_val)):<10} {qty//100}手  {row['score']:.4f}")
            lines.append("")
        except Exception as e:
            lines.append(f"  ⚠ 无法读取交易计划详情: {e}")
    if rebalance_skipped and not has_existing_plan:
        lines += ["", "⏭ 本周已调仓，跳过重复交易"]
    lines += [
        f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
        f"Universe: {UNIVERSE} | 预测: {pred_count}只 | 参考数据: {data_date}",
    ]
    if has_existing_plan:
        lines += ["", "📝 注: 计划不执行交易，待 21:30 数据同步后 postclose 以开盘价执行"]
    return "\n".join(lines)


def _build_postclose_message(trade_date: str, mtm: dict | None = None,
                              artifacts: ShadowRebalanceArtifacts | None = None,
                              execution_committed: bool = False,
                              execution_skipped: bool = False,
                              debug_run: bool = False,
                              stale_check: dict | None = None,
                              idempotent_skip: bool = False) -> str:
    _get_stock_name("")
    lines = [
        f"📊 Alpha V1 Post-close {trade_date}",
        f"Time: {_now_str()}", "",
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
        status_icon = {"passed": "✅", "blocked": "⛔", "skipped": "⏭", "skipped_low_overlap": "⏭"}
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
            f"  成交额: {_fmt(artifacts.turnover)}  买入委托: {artifacts.order_count} "
            f"成交: {artifacts.filled_count}  未成交: {artifacts.rejected_count}"
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
        pos_before = mtm.get('positions_before_count', 0)
        pos_after = mtm.get('priced_count', 0)
        if pos_before > 0:
            lines.append(f"  Position: {_fmt(mtm['market_value'])}  Holdings: {pos_after}只（原有{pos_before} + 新增{pos_after - pos_before}）")
        else:
            lines.append(f"  Position: {_fmt(mtm['market_value'])}  Holdings: {pos_after}只")
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


# ── Mode handlers ──────────────────────────────────────────────────────

def run_preopen(trade_date: str, debug_run: bool = False,
                no_notify: bool = False, reason: str | None = None,
                output_dir: str | None = None) -> None:
    t0 = time.time()
    run_root = _resolve_run_root(trade_date, debug_run=debug_run, output_dir=output_dir)
    global _FORCE_OUTPUT_ROOT
    _FORCE_OUTPUT_ROOT = run_root
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
    if debug_run:
        pred_dir = run_root / "predictions"
        pred_dir.mkdir(parents=True, exist_ok=True)
        pred_path = pred_dir / f"predictions_{trade_date}.csv"
    else:
        PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
        pred_path = PREDICTIONS_DIR / f"predictions_{trade_date}.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"  → {len(pred_df)} predictions saved: {pred_path}")
    try:
        from qsys.artifacts.adapters import adapt_predictions
        from qsys.artifacts.writer import write_artifacts, sidecar_path
        arts = list(adapt_predictions(str(pred_path), strategy_id=ALPHA_V1_CANDIDATE.strategy_id))
        if arts:
            write_artifacts(arts, sidecar_path(pred_path))
        print(f"  → ADR-7 signal sidecar written ({len(arts)} rows)")
    except Exception as e:
        print(f"  ⚠ ADR-7 signal sidecar failed: {e}")
    top = pred_df.sort_values("score", ascending=False).head(5)
    top_picks = [(row["instrument"], row["score"]) for _, row in top.iterrows()]
    for inst, score in top_picks:
        print(f"    #{top_picks.index((inst, score)) + 1} {inst}  score={score:.4f}")
    print(f"\n[4/4] Building trading plan...")
    run_root.mkdir(parents=True, exist_ok=True)
    save_run_meta(run_root, trade_date, "preopen", data_date=data_date,
                   debug_run=debug_run, reason=reason)
    rebalance_skipped = not _should_rebalance(trade_date)
    if rebalance_skipped:
        print(f"  ⏭ {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week")
        plan_dir = _plan_dir(run_root)
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
                predictions_path=str(pred_path), output_dir=str(run_root),
                db_path=LEDGER_DB_PATH if not debug_run else None,
            )
            try:
                from qsys.artifacts.adapters import adapt_order_intents
                from qsys.artifacts.writer import write_artifacts, sidecar_path
                oi_path = _plan_dir(run_root) / "order_intents.csv"
                if oi_path.exists():
                    oi_arts = list(adapt_order_intents(str(oi_path), strategy_id=ALPHA_V1_CANDIDATE.strategy_id, account_id=_shadow_account_id()))
                    if oi_arts:
                        write_artifacts(oi_arts, sidecar_path(oi_path))
                    print(f"  → ADR-7 order intent sidecar written ({len(oi_arts)} rows)")
            except Exception as e:
                print(f"  ⚠ ADR-7 order intent sidecar failed: {e}")
        except Exception as e:
            print(f"  ❌ 建仓计划失败: {e}")
            if not no_notify:
                _send_notification(f"❌ Alpha V1 Pre-open {trade_date}\n建仓计划失败: {e}")
            return
    if not no_notify:
        msg = _build_preopen_message(trade_date, rebalance_skipped, data_date,
                                      top_picks, len(pred_df), pred_path,
                                      run_root=run_root)
        _send_notification(msg)
    elapsed = time.time() - t0
    print(f"\n✅ Pre-open {trade_date} completed in {elapsed:.0f}s")


def run_postclose(trade_date: str, debug_run: bool = False,
                  no_notify: bool = False, force_rerun: bool = False,
                  reason: str | None = None,
                  output_dir: str | None = None) -> None:
    t0 = time.time()
    run_root = _resolve_run_root(trade_date, debug_run=debug_run, output_dir=output_dir)
    global _FORCE_OUTPUT_ROOT
    _FORCE_OUTPUT_ROOT = run_root
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Post-close — {trade_date}"
          + (" (DEBUG)" if debug_run else "")
          + (" (FORCE-RERUN)" if force_rerun else ""))
    print(f"{'=' * 60}")
    run_root.mkdir(parents=True, exist_ok=True)
    save_run_meta(run_root, trade_date, "postclose", debug_run=debug_run, reason=reason,
                   extra={"force_rerun": force_rerun})
    # In debug mode: read plan from production path if not in debug path
    plan_dir = _plan_dir(run_root)
    if debug_run and not (plan_dir / "order_intents.csv").exists():
        prod_root = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date
        prod_plan = _plan_dir(prod_root)
        if (prod_plan / "order_intents.csv").exists():
            plan_dir = prod_plan
            print(f"  ℹ 使用生产路径计划: {plan_dir}")
    has_plan = (plan_dir / "order_intents.csv").exists()
    has_skip_meta = (plan_dir / "plan_meta.json").exists()
    has_skip = has_skip_meta and not has_plan
    already_committed = is_execution_committed(run_root)

    # ── COMMITTING crash recovery check ──
    committing_path = committing_marker(run_root)
    if committing_path.exists() and not already_committed:
        msg = (f"⛔ COMMITTING 标记存在（无 COMMITTED）！\n"
               f"上次提交中崩溃，execution/ 目录可能不完整。\n"
               f"请人工检查后手动删除 COMMITTING 文件重试。")
        print(f"\n{'=' * 60}")
        print(msg)
        print(f"{'=' * 60}")
        sys.exit(1)

    # ── Idempotent skip ──
    if already_committed and not force_rerun:
        print(f"  ⏭ 执行已提交（COMMITTED 标记存在），跳过")
        print(f"  💡 如需重新执行请使用 --force-rerun + --reason")
        artifacts = _load_artifacts_for_notification(trade_date, run_root)
        mtm = load_mtm_snapshot(run_root / "mtm" / "mtm_snapshot.json")
        if not no_notify:
            msg = _build_postclose_message(
                trade_date, mtm=mtm, artifacts=artifacts,
                execution_committed=True, execution_skipped=has_skip,
                debug_run=debug_run, idempotent_skip=True,
            )
            _send_notification(msg)
        elapsed = time.time() - t0
        print(f"\n✅ Post-close {trade_date} (已提交，跳过) completed in {elapsed:.0f}s")
        return

    # ── Force-rerun: restore before-state, then archive ──
    if already_committed and force_rerun:
        if not reason:
            print("  ❌ --force-rerun 必须配合 --reason")
            sys.exit(1)
        print(f"  ⚠ --force-rerun 生效，原因: {reason}")
        exec_before = _exec_dir(run_root) / "account_before.json"
        pos_before = _exec_dir(run_root) / "positions_before.csv"
        has_before_state = exec_before.exists() and pos_before.exists()
        if has_before_state:
            shutil.copy2(str(exec_before), str(PROJECT_ROOT / "shadow" / "account.json"))
            shutil.copy2(str(pos_before), str(PROJECT_ROOT / "shadow" / "positions.csv"))
            print(f"  🔄 Shadow 已恢复至执行前状态")
        else:
            msg = (f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
                   f"--force-rerun 需要 execution/account_before.json 和 "
                   f"positions_before.csv 才能重放交易。\n"
                   f"文件不存在，阻断执行。")
            print(f"\n{msg}")
            if not no_notify:
                _send_notification(msg)
            sys.exit(1)
        archive_execution(run_root)

    # ── Plan check ──
    if not has_plan and not has_skip:
        msg = (f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
               f"未找到 preopen 计划文件: {plan_dir}\n"
               f"请先运行 preopen。")
        print(f"\n{msg}")
        if not no_notify:
            _send_notification(msg)
        sys.exit(1)

    # ── Execution ──
    artifacts = None
    staging_exec_dir = _staging_dir(run_root)
    if has_plan:
        print(f"\n[1/4] Validating execution prerequisites...")
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

        # Stale close-price check BEFORE execution
        print(f"\n[2/4] Stale close-price check...")
        # Build union of current positions and plan instruments
        all_instruments: set[str] = set()
        if Path(LEDGER_DB_PATH).exists():
            try:
                from qsys.ledger.service import LedgerService
                svc = LedgerService(LEDGER_DB_PATH)
                for p in svc.get_positions(_shadow_account_id()):
                    if int(p["quantity"]) > 0:
                        all_instruments.add(p["symbol"])
                svc.close()
            except Exception:
                pass
        shadow_pos = PROJECT_ROOT / "shadow" / "positions.csv"
        if shadow_pos.exists():
            pos_df = pd.read_csv(shadow_pos)
            if not pos_df.empty:
                all_instruments.update(pos_df["instrument"].tolist())
        intents_path = plan_dir / "order_intents.csv"
        if intents_path.exists():
            intents_df = pd.read_csv(intents_path)
            all_instruments.update(intents_df["instrument"].tolist())
        if all_instruments:
            close_prices = fetch_close_prices(trade_date, sorted(all_instruments))
            if close_prices:
                stale_positions = pd.DataFrame(
                    {"instrument": list(all_instruments), "quantity": 0})
                try:
                    stale_result = check_stale_prices(
                        trade_date, close_prices, stale_positions,
                        project_root=PROJECT_ROOT,
                    )
                    save_stale_check(run_root, stale_result)
                    print(f"  ✅ Stale check: {stale_result['status']} "
                          f"({stale_result['identical_count']}/{stale_result['checked_count']} identical)")
                except StaleDataError as e:
                    save_stale_check(run_root, e.stale_check)
                    print(f"  ❌ {e}")
                    if not no_notify:
                        _send_notification(
                            f"⛔ Alpha V1 Post-close {trade_date} BLOCKED\n"
                            f"收盘价数据陈旧，阻断执行。\n"
                            f"一致={e.stale_check.get('identical_count', 0)}/"
                            f"{e.stale_check.get('checked_count', 0)} "
                            f"({e.stale_check.get('identical_ratio', 0)*100:.0f}%)\n"
                            f"请运行数据同步后重试。"
                        )
                    sys.exit(1)

        # ── Write COMMITTING before ledger write (crash-safe boundary) ──
        if not debug_run:
            committing_path = committing_marker(run_root)
            if committing_path.exists():
                print(f"  ❌ COMMITTING 标记已存在，疑似半提交状态。请人工检查。")
                sys.exit(1)
            committing_path.parent.mkdir(parents=True, exist_ok=True)
            committing_path.write_text("")
            print(f"  📝 COMMITTING marker written — ledger write protected")

        try:
            artifacts = execute_alpha_v1_plan(
                base_dir=".", plan_dir=str(plan_dir),
                execution_date=trade_date, output_dir=str(staging_exec_dir),
                debug_run=debug_run,
                db_path=LEDGER_DB_PATH if not debug_run else None,
            )
            print(f"  ✅ orders={artifacts.order_count}, "
                  f"total={_fmt(artifacts.total_value_after)}, "
                  f"cash={_fmt(artifacts.cash_after)}, "
                  f"turnover={_fmt(artifacts.turnover)}")
        except Exception as e:
            # Clean up COMMITTING on failure so retry is possible
            if not debug_run:
                cleanup_committing(run_root)
            print(f"  ❌ 执行失败: {e}")
            if not no_notify:
                _send_notification(f"⛔ Alpha V1 Post-close {trade_date} FAILED\n{e}")
            sys.exit(1)

        if not debug_run:
            print(f"  Committing artifacts...")
            _commit_execution(run_root, staging_exec_dir,
                              db_path=LEDGER_DB_PATH if not debug_run else None,
                              strategy_id=ALPHA_V1_CANDIDATE.strategy_id,
                              trade_date=trade_date)
            print(f"  ✅ Execution committed")
            try:
                from qsys.artifacts.adapters import adapt_executions, build_run_manifest, read_execution_summary
                from qsys.artifacts.writer import write_artifact, write_artifacts, sidecar_path
                exec_dir = _exec_dir(run_root)
                lr_csv = exec_dir / "ledger_rows.csv"
                if lr_csv.exists():
                    ex_arts = list(adapt_executions(str(lr_csv), strategy_id=ALPHA_V1_CANDIDATE.strategy_id, account_id=_shadow_account_id()))
                    if ex_arts:
                        write_artifacts(ex_arts, sidecar_path(lr_csv))
                    print(f"  → ADR-7 execution sidecar written ({len(ex_arts)} rows)")
                summary = read_execution_summary(exec_dir / "execution_summary.json")
                manifest = build_run_manifest(
                    run_id=summary.get("run_id", f"alpha_v1_execute_{trade_date}"),
                    trade_date=trade_date, stage="postclose",
                    strategy_id=ALPHA_V1_CANDIDATE.strategy_id,
                    account_id=_shadow_account_id(),
                    status="completed",
                    output_artifacts=[
                        {"path": sidecar_path(lr_csv).name, "type": "ExecutionArtifact"},
                        {"path": "manifest.adr7.json", "type": "RunManifest"},
                    ],
                )
                write_artifact(manifest, exec_dir / "manifest.adr7.json")
                print(f"  → ADR-7 run manifest written")
            except Exception as e:
                print(f"  ⚠ ADR-7 execution sidecar failed: {e}")
        else:
            print(f"  🔧 调试模式 — 不提交 shadow 账户")

    # ── MTM at CLOSE price ──
    print(f"\n{'[4/4]' if has_plan else '[1/1]'} MTM at CLOSE price...")
    if debug_run and has_plan:
        # Debug mode: read from staging artifacts, not production shadow/
        staging_acct = staging_exec_dir / "account_after.json"
        staging_pos = staging_exec_dir / "positions_after.csv"
        mtm = try_mark_to_market(
            trade_date, output_dir=run_root,
            account_path=staging_acct if staging_acct.exists() else None,
            positions_path=staging_pos if staging_pos.exists() else None,
            project_root=PROJECT_ROOT,
            shadow_account_id=_shadow_account_id(),
            get_stock_name_fn=_get_stock_name,
        )
    else:
        mtm = try_mark_to_market(trade_date, output_dir=run_root,
                                   db_path=LEDGER_DB_PATH if not debug_run else None,
                                   project_root=PROJECT_ROOT,
                                   shadow_account_id=_shadow_account_id(),
                                   get_stock_name_fn=_get_stock_name)

    if mtm is None:
        print(f"  ⚠ 收盘价数据未就绪")
        if not no_notify:
            _send_notification(
                f"⛔ Alpha V1 Post-close {trade_date}\n"
                f"收盘价数据未就绪。数据同步可能尚未完成。\n"
                f"请先运行: python scripts/ops/sync_csi800_daily.py --apply"
            )
        sys.exit(1)

    try:
        from qsys.artifacts.adapters import adapt_portfolio_snapshot
        from qsys.artifacts.writer import write_artifact
        exec_dir = _exec_dir(run_root)
        exec_summary = {}
        es_path = exec_dir / "execution_summary.json"
        if es_path.exists():
            exec_summary = json.loads(es_path.read_text()) or {}
        mtm_path = run_root / "mtm" / "mtm_snapshot.json"
        snapshot = adapt_portfolio_snapshot(
            mtm_path, trade_date=trade_date,
            account_id=_shadow_account_id(),
            strategy_id=ALPHA_V1_CANDIDATE.strategy_id,
            turnover=exec_summary.get("turnover", 0.0),
        )
        if snapshot:
            write_artifact(snapshot, mtm_path.with_name(mtm_path.stem + ".adr7.json"))
            print(f"  → ADR-7 portfolio snapshot sidecar written")
    except Exception as e:
        print(f"  ⚠ ADR-7 portfolio snapshot sidecar failed: {e}")

    # ── Notify ──
    if not no_notify:
        stale_check_path = run_root / "mtm" / "stale_check.json"
        stale_check = None
        if stale_check_path.exists():
            try:
                stale_check = json.loads(stale_check_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        msg = _build_postclose_message(
            trade_date, mtm=mtm, artifacts=artifacts,
            execution_committed=not debug_run,
            execution_skipped=has_skip, debug_run=debug_run,
            stale_check=stale_check,
        )
        _send_notification(msg)

    elapsed = time.time() - t0
    print(f"\n✅ Post-close {trade_date} completed in {elapsed:.0f}s")


# ── Helpers (kept in script — pipeline-specific) ─────────────────────

def _load_plan_instruments(plan_dir: Path) -> list[str]:
    intents_path = plan_dir / "order_intents.csv"
    if not intents_path.exists():
        return []
    try:
        df = pd.read_csv(intents_path)
        return sorted(set(df["instrument"].astype(str)))
    except Exception:
        return []


def _load_artifacts_for_notification(trade_date: str, run_root: Path) -> ShadowRebalanceArtifacts | None:
    exec_dir = _exec_dir(run_root)
    summary_path = exec_dir / "execution_summary.json"
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    plan_dir = _plan_dir(run_root)
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
        ledger_rows_path="",
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


def _commit_execution(run_root: Path, staging_dir: Path,
                      db_path: str | None = None,
                      strategy_id: str | None = None,
                      trade_date: str | None = None) -> None:
    exec_dir = _exec_dir(run_root)
    exec_dir.mkdir(parents=True, exist_ok=True)
    # COMMITTING should already exist (written before execute_alpha_v1_plan)
    committing_path = committing_marker(run_root)
    if not committing_path.exists():
        print(f"  ❌ COMMITTING 标记不存在，疑似提交顺序错误。")
        sys.exit(1)

    ledger_written = False
    try:
        # 1. Write SQLite ledger from staging payload (before artifact copy,
        #    so that COMMITTING protects against inconsistent retry)
        if db_path and strategy_id and trade_date:
            from qsys.ops.shadow_rebalance import _write_execution_to_ledger
            payload_path = staging_dir / "ledger_payload.json"
            if payload_path.exists():
                payload = json.loads(payload_path.read_text())
                positions_df = pd.DataFrame()
                pos_csv = staging_dir / "positions_after.csv"
                if pos_csv.exists():
                    positions_df = pd.read_csv(pos_csv)

                # Read cash/market/total from execution_summary for snapshot accuracy
                summary_path = staging_dir / "execution_summary.json"
                if summary_path.exists():
                    summary = json.loads(summary_path.read_text())
                    cash_after = summary.get("cash_after", 0.0)
                    market_value_after = summary.get("market_value_after", 0.0)
                    total_value_after = summary.get("total_value_after", 0.0)
                else:
                    cash_after = market_value_after = total_value_after = 0.0

                _write_execution_to_ledger(
                    db_path=db_path,
                    execution_date=trade_date,
                    strategy_id=strategy_id,
                    orders=payload["orders"],
                    ledger_rows=[],
                    results=payload["results"],
                    close_prices=payload["close_prices"],
                    cash_after=cash_after,
                    market_value_after=market_value_after,
                    total_value_after=total_value_after,
                    positions_after=positions_df,
                    initial_capital=payload.get("initial_capital", 1_000_000.0),
                )
                ledger_written = True
            else:
                print(f"  ⚠ ledger_payload.json 不存在于 {staging_dir}，跳过 ledger 写入")

        # 2. Copy staging artifacts to execution/ (including before-state for force-rerun)
        for fname in ["account_after.json", "positions_after.csv", "execution_summary.json",
                       "account_before.json", "positions_before.csv", "ledger_rows.csv",
                       "ledger_payload.json"]:
            src = staging_dir / fname
            if src.exists():
                shutil.copy2(str(src), str(exec_dir / fname))

        # 3. Rename COMMITTING → COMMITTED (atomic)
        committing_path.rename(committed_marker(run_root))
        print(f"  ✅ 执行已提交 (COMMITTED): {exec_dir}")

    except BaseException:
        if ledger_written:
            # Ledger was written but artifact commit failed.
            # PRESERVE COMMITTING to prevent retry with inconsistent state.
            print(f"  ❌ Ledger written but artifact commit failed — COMMITTING preserved")
            print(f"  💡 Manual recovery: fix issue, delete COMMITTING, verify execution/ dir")
        else:
            # Ledger failed — clean COMMITTING so retry is possible
            cleanup_committing(run_root)
        raise


def run_notify_only(trade_date: str, output_dir: str | None = None) -> None:
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Notify-only — {trade_date}")
    print(f"{'=' * 60}")
    run_root = _resolve_run_root(trade_date, output_dir=output_dir)
    artifacts = _load_artifacts_for_notification(trade_date, run_root)
    # Read-only: load existing MTM snapshot from run_root, never recalculate
    mtm = load_mtm_snapshot(run_root / "mtm" / "mtm_snapshot.json")
    already_committed = is_execution_committed(run_root)
    has_skip = _plan_dir(run_root).exists() and not (
        _plan_dir(run_root) / "order_intents.csv").exists()
    stale_check_path = run_root / "mtm" / "stale_check.json"
    stale_check = None
    if stale_check_path.exists():
        try:
            stale_check = json.loads(stale_check_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    msg = _build_postclose_message(
        trade_date, mtm=mtm, artifacts=artifacts,
        execution_committed=already_committed, execution_skipped=has_skip,
        debug_run=False, stale_check=stale_check,
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
        run_notify_only(args.trade_date, output_dir=args.output_dir)
        return
    if args.mode == "preopen":
        run_preopen(args.trade_date, debug_run=args.debug_run,
                     no_notify=args.no_notify, reason=args.reason,
                     output_dir=args.output_dir)
    elif args.mode == "postclose":
        run_postclose(args.trade_date, debug_run=args.debug_run,
                       no_notify=args.no_notify,
                       force_rerun=args.force_rerun, reason=args.reason,
                       output_dir=args.output_dir)


if __name__ == "__main__":
    main()
