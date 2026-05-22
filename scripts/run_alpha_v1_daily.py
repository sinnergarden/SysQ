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

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.shadow_rebalance import run_alpha_v1_shadow_rebalance
from qsys.ops.shadow_rebalance import ShadowRebalanceArtifacts
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


def fetch_single_date_data(trade_date: str):
    """获取单个交易日的特征数据。"""
    adapter = QlibAdapter()
    adapter.init_qlib()

    all_features = FeatureLibrary.get_semantic_all_features_config()
    from qsys.strategy.alpha_v1.spec import get_clean_features
    clean_features = get_clean_features(all_features)

    raw = adapter.get_features(UNIVERSE, all_features + ["$close"],
                                start_time=trade_date, end_time=trade_date)
    if raw.empty:
        return pd.DataFrame(), clean_features
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    return frame, clean_features


def generate_predictions_for_date(models, clean_features, frame, trade_date: str) -> pd.DataFrame:
    """生成单日混合预测。"""
    mask = frame["trade_date"] == trade_date
    today = frame[mask]
    if today.empty:
        raise ValueError(f"交易日 {trade_date} 无数据")

    X = today[clean_features].astype(np.float32).fillna(0.0)
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

    instruments = today["instrument"].values
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
    """Format amount in human-readable Chinese units."""
    if amount >= 1_0000_0000:
        return f"¥{amount/1_0000_0000:.2f}亿"
    if amount >= 1_0000:
        return f"¥{amount/1_0000:.0f}万"
    return f"¥{amount:_.0f}"


def _find_summary_file(trade_date: str) -> Path | None:
    """Search common paths for reconciliation summary.json."""
    for p in [
        PROJECT_ROOT / "daily" / trade_date / "post_close" / "summary.json",
        PROJECT_ROOT / "daily" / trade_date / "post_close" / "reports" / "summary.json",
        PROJECT_ROOT / "runs" / f"postclose_{trade_date}" / "summary.json",
    ]:
        if p.exists():
            return p
    return None


def _build_preopen_message(trade_date: str, artifacts, pred_count: int,
                            top_picks: list[tuple[str, float]],
                            rebalance_skipped: bool = False,
                            pred_path: str | Path | None = None) -> str:
    """构建 preopen Telegram 通知文本。"""
    lines = [
        f"<b>✅ Alpha V1 Pre-open {trade_date}</b>",
        f"Time: {_now_str()}",
        "",
        f"<b>📈 推荐股票</b>",
    ]
    for i, (inst, score) in enumerate(top_picks[:5], 1):
        name = _get_stock_name(inst)
        lines.append(f"  {i}. {inst} {name}  score={score:.4f}")

    if rebalance_skipped:
        lines += [
            "",
            "<b>⏭ 本周已调仓，跳过重复交易</b>",
            f"策略: {ALPHA_V1_CANDIDATE.display_name} | 频率: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq}",
            f"Universe: {UNIVERSE} | 预测: {pred_count}只",
        ]
        return "\n".join(lines)

    # Actual rebalance — show trade details
    lines += [
        "",
        "<b>📋 交易股票</b>",
    ]

    intents_path = getattr(artifacts, "order_intents_path", None)
    if intents_path and Path(intents_path).exists():
        try:
            orders_df = pd.read_csv(intents_path)
            # Merge scores from predictions for score-sorted display
            if pred_path and Path(pred_path).exists():
                scores_df = pd.read_csv(pred_path)[["instrument", "score"]]
                orders_df = orders_df.merge(scores_df, on="instrument", how="left")
                orders_df["score"] = orders_df["score"].fillna(0.0)
            else:
                orders_df["score"] = 0.0

            buys = orders_df[orders_df["side"] == "buy"].sort_values("score", ascending=False)
            sells = orders_df[orders_df["side"] == "sell"].sort_values("score", ascending=False)

            if not buys.empty:
                lines.append(f"  买入 ({len(buys)}):")
                for _, row in buys.iterrows():
                    name = _get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    lines.append(f"    {row['instrument']} {name}  +{_fmt(diff_val)}  score={row['score']:.4f}")
            if not sells.empty:
                lines.append(f"  卖出 ({len(sells)}):")
                for _, row in sells.iterrows():
                    name = _get_stock_name(row["instrument"])
                    diff_val = float(row.get("diff_value", 0))
                    lines.append(f"    {row['instrument']} {name}  -{_fmt(abs(diff_val))}  score={row['score']:.4f}")
        except Exception:
            lines.append(f"  Orders: {artifacts.order_count} ({artifacts.buy_count} buy / {artifacts.sell_count} sell)")
    else:
        lines.append(f"  Orders: {artifacts.order_count} ({artifacts.buy_count} buy / {artifacts.sell_count} sell)")

    lines += [
        "",
        "<b>💰 账户</b>",
    ]
    if artifacts.total_value_after:
        lines.append(f"  Total: {_fmt(artifacts.total_value_after)}  Cash: {_fmt(artifacts.cash_after or 0)}")
    else:
        lines.append(f"  Total: ¥0  Cash: ¥0")
    if artifacts.turnover:
        lines.append(f"  Turnover: {_fmt(artifacts.turnover)}")
    lines.append(f"  Universe: {UNIVERSE} | 预测: {pred_count}只 | 策略: {ALPHA_V1_CANDIDATE.display_name}")

    return "\n".join(lines)


def _build_postclose_message(trade_date: str) -> str:
    """构建 postclose Telegram 通知文本。"""
    _get_stock_name("")  # warm cache

    lines = [
        f"<b>📊 Alpha V1 Post-close {trade_date}</b>",
        f"Time: {_now_str()}",
        "",
    ]

    summary_path = _find_summary_file(trade_date)
    if summary_path:
        try:
            data = json.loads(summary_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = None
    else:
        data = None

    if data:
        reconcil = data.get("reconciliation", {})
        if reconcil:
            lines.append("<b>Reconciliation</b>")
            for metric in ("total_assets", "cash"):
                row = reconcil.get(metric, {})
                real = row.get("real", "N/A")
                shadow = row.get("shadow", "N/A")
                if isinstance(real, float):
                    lines.append(f"  {metric}: {_fmt(real)} → shadow {_fmt(shadow)}")
                else:
                    lines.append(f"  {metric}: real={real}  shadow={shadow}")

        snapshots = data.get("account_snapshots", {})
        if snapshots:
            lines.append("")
            lines.append("<b>Account</b>")
            for name, snap in snapshots.items():
                if snap:
                    cash = snap.get("cash", 0)
                    total = snap.get("total_assets", 0)
                    pos = snap.get("position_count", 0)
                    lines.append(f"  {name}: {_fmt(total)}  cash={_fmt(cash)}  positions={pos}")

        gaps = data.get("position_gap_count", 0)
        if gaps:
            lines.append("")
            lines.append(f"Position gaps: {gaps}")
    else:
        # Fallback: shadow execution summary
        shadow_path = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date / "execution_summary.json"
        if shadow_path.exists():
            try:
                shadow = json.loads(shadow_path.read_text())
                lines.append("<b>Shadow Account（影子账户，无券商数据）</b>")
                before = shadow.get("total_value_before", 0)
                after = shadow.get("total_value_after", 0)
                pnl = after - before
                pnl_str = f"+{_fmt(pnl)}" if pnl >= 0 else _fmt(pnl)
                lines.append(f"  PnL: {pnl_str}")
                lines.append(f"  Total: {_fmt(after)}  Cash: {_fmt(shadow.get('cash_after', 0))}")
                lines.append(f"  Turnover: {_fmt(shadow.get('turnover', 0))}")
                bc = shadow.get("buy_count", 0)
                sc = shadow.get("sell_count", 0)
                if bc or sc:
                    lines.append(f"  Orders: {bc}买入 / {sc}卖出")
                lines.append("")
                lines.append("<i>⚠ 无券商 reconciliation 数据（--real_sync 未指定）</i>")
            except (json.JSONDecodeError, OSError) as e:
                lines.append(f"⚠ 无法解析 shadow execution 数据: {e}")
        else:
            lines.append("⚠ Reconciliation 数据不存在")
            lines.append("盘前未运行或数据未就绪，无 shadow 账户记录")

    return "\n".join(lines)


# ── Mode handlers ──────────────────────────────────────────────────────


def run_preopen(trade_date: str) -> None:
    """Alpha V1 preopen: inference → rebalance → Telegram notify."""
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
        _send_notification(f"<b>❌ Alpha V1 Pre-open {trade_date}</b>\n模型加载失败: {e}")
        return
    for tag in ["5d", "20d"]:
        print(f"  Model {tag}: {models[tag][0].num_trees()} trees")
    print(f"  Features: {len(clean_features)}")

    # 2. Fetch data
    print(f"\n[2/4] Fetching data for {trade_date}...")
    try:
        frame, clean_features = fetch_single_date_data(trade_date)
        print(f"  {UNIVERSE}: {len(frame)} rows")
    except Exception as e:
        print(f"  ❌ {e}")
        _send_notification(f"<b>❌ Alpha V1 Pre-open {trade_date}</b>\n数据获取失败: {e}")
        return

    if frame.empty:
        print(f"  ⚠ 交易日 {trade_date} 无数据（qlib 数据未就绪或休市日）")
        _send_notification(
            f"<b>⚠ Alpha V1 Pre-open {trade_date}</b>\n"
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
        _send_notification(f"<b>❌ Alpha V1 Pre-open {trade_date}</b>\n预测生成失败: {e}")
        return

    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    pred_path = PREDICTIONS_DIR / f"predictions_{trade_date}.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"  → {len(pred_df)} predictions saved: {pred_path}")

    # Top picks
    top = pred_df.sort_values("score", ascending=False).head(5)
    top_picks = [(row["instrument"], row["score"]) for _, row in top.iterrows()]
    for inst, score in top_picks:
        print(f"    #{top_picks.index((inst, score)) + 1} {inst}  score={score:.4f}")

    # 4. Check rebalance schedule
    print(f"\n[4/4] Checking rebalance schedule...")
    output_dir = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date
    output_dir.mkdir(parents=True, exist_ok=True)

    rebalance_skipped = False
    if not _should_rebalance(trade_date):
        print(f"  ⏭ Skipping: {ALPHA_V1_CANDIDATE.portfolio.rebalance_freq} policy, already ran this week")
        rebalance_skipped = True
        artifacts = ShadowRebalanceArtifacts(
            trade_date=trade_date,
            run_id=f"alpha_v1_preopen_{trade_date}",
            status="skipped",
            strategy_id="alpha_v1",
            strategy_version=ALPHA_V1_CANDIDATE.version,
            portfolio_method="rank_weight_buffer",
            top_n=ALPHA_V1_CANDIDATE.portfolio.top_n,
            buffer_hold=ALPHA_V1_CANDIDATE.portfolio.buffer_hold,
            buffer_buy=ALPHA_V1_CANDIDATE.portfolio.buffer_buy,
            single_stock_cap=ALPHA_V1_CANDIDATE.portfolio.single_stock_cap,
            turnover_buffer=0.0,
            price_mode="shadow_mark_price",
            rebalance_mode=ALPHA_V1_CANDIDATE.portfolio.rebalance_freq,
            target_weights_path="",
            order_intents_path=str(pred_path),
            execution_summary_path="",
            account_after_path="",
            positions_after_path="",
            shadow_account_path="",
            shadow_positions_path="",
            shadow_ledger_path="",
            rebalance_audit_path="",
            order_count=0, buy_count=0, sell_count=0, skipped_count=0,
            filled_count=0, rejected_count=0, turnover=0.0,
            cash_after=0.0, total_value_after=0.0,
        )
    else:
        print(f"  Running shadow rebalance...")
        try:
            artifacts = run_alpha_v1_shadow_rebalance(
                base_dir=".",
                run_id=f"alpha_v1_preopen_{trade_date}",
                trade_date=trade_date,
                predictions_path=str(pred_path),
                output_dir=str(output_dir),
            )
            print(f"  ✅ orders={artifacts.order_count}, "
                  f"value=¥{artifacts.total_value_after:_.0f}, "
                  f"cash=¥{artifacts.cash_after:_.0f}, "
                  f"turnover=¥{artifacts.turnover:_.0f}")
        except Exception as e:
            print(f"  ❌ rebalance 失败: {e}")
            _send_notification(f"<b>❌ Alpha V1 Pre-open {trade_date}</b>\nRebalance 失败: {e}")
            return

    # 5. Telegram notify
    msg = _build_preopen_message(trade_date, artifacts, len(pred_df), top_picks, rebalance_skipped, pred_path)
    _send_notification(msg)

    elapsed = time.time() - t0
    print(f"\n✅ Pre-open {trade_date} completed in {elapsed:.0f}s")


def run_postclose(trade_date: str) -> None:
    """Postclose: read reconciliation → Telegram notify."""
    t0 = time.time()
    print(f"\n{'=' * 60}")
    print(f"Alpha V1 Post-close — {trade_date}")
    print(f"{'=' * 60}")

    msg = _build_postclose_message(trade_date)
    _send_notification(msg)

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
