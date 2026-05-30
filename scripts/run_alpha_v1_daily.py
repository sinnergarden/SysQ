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
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qsys.common.deprecation import print_legacy_entrypoint_warning  # noqa: E402

print_legacy_entrypoint_warning(
    "run_alpha_v1_daily.py",
    "python scripts/run_daily.py --strategy alpha_v1 --mode <mode>",
)

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

from qsys.ops.daily_runner import DailyRunner
from qsys.ops.run_context import DailyRunContext, resolve_run_root
from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter


# ── Context builder ──────────────────────────────────────────────────────

def _build_context(
    trade_date: str,
    mode: str,
    *,
    debug_run: bool = False,
    no_notify: bool = False,
    force_rerun: bool = False,
    reason: str | None = None,
    output_dir: str | None = None,
    notify_only: bool = False,
) -> DailyRunContext:
    adapter = AlphaV1StrategyAdapter()
    run_root = resolve_run_root(
        PROJECT_ROOT,
        adapter.strategy_id,
        trade_date,
        debug_run=debug_run,
        output_dir=Path(output_dir) if output_dir else None,
    )
    return DailyRunContext(
        trade_date=trade_date,
        mode=mode,
        run_root=run_root,
        project_root=PROJECT_ROOT,
        strategy_id=adapter.strategy_id,
        account_id=adapter.account_id,
        debug_run=debug_run,
        no_notify=no_notify,
        force_rerun=force_rerun,
        reason=reason,
        output_dir=Path(output_dir) if output_dir else None,
    )


# ── Mode handlers ──────────────────────────────────────────────────────

def _save_signal_basket_from_adapter(
    trade_date: str, strategy: AlphaV1StrategyAdapter,
) -> None:
    """Read adapter-correct predictions and save as signal_basket to daily/.

    This overwrites the broken signal_basket written by ``run_daily_trading.py``
    (which uses a different model chain — Qlib .pkl with wrong scores).

    The adapter predictions have correct scores (same as notification source).
    """
    pred_dir = strategy._predictions_dir
    pred_path = pred_dir / f"predictions_{trade_date}.csv"
    if not pred_path.exists():
        print(f"  ⚠ 未找到 adapter predictions: {pred_path}")
        return

    preds = pd.read_csv(pred_path)
    if preds.empty:
        print(f"  ⚠ adapter predictions 为空")
        return

    signal_date = str(preds["trade_date"].iloc[0])

    # Fetch prices for the signal_date using qlib (already initialized by run_preopen)
    from qsys.data.adapter import QlibAdapter
    instruments = preds["instrument"].unique().tolist()
    prices = QlibAdapter().get_features(
        instruments, ["$close", "$factor"],
        start_time=signal_date, end_time=signal_date,
    )
    price_by_sym: dict[str, float] = {}
    if prices is not None and not prices.empty:
        norm = prices.copy()
        if isinstance(norm.index, pd.MultiIndex):
            norm.index = norm.index.get_level_values(-1)
        norm = norm.groupby(level=0).last()
        price_by_sym = norm["$close"].to_dict()

    has_price = preds["instrument"].map(
        lambda sym, lookup=price_by_sym: sym in lookup
        and lookup[sym] is not None and float(lookup[sym]) > 0
    )
    valid = preds[has_price].copy()
    if valid.empty:
        print(f"  ⚠ 所有 adapter predictions 缺少有效价格")
        return

    valid["price"] = valid["instrument"].map(price_by_sym)
    valid["score_rank"] = valid["score"].rank(ascending=False).astype(int)

    basket = pd.DataFrame({
        "symbol": valid["instrument"],
        "score": valid["score"].astype(float),
        "score_rank": valid["score_rank"],
        "weight": 0.0,
        "price": valid["price"].astype(float),
        "signal_date": signal_date,
        "execution_date": trade_date,
        "price_basis_date": signal_date,
        "price_basis_field": "close",
        "price_basis_label": f"close@{signal_date} -> next-session signal basket",
        "model_name": str(preds.iloc[0].get("model_name", "")),
        "model_path": str(pred_dir),
        "universe": "csi300",
    }).sort_values("score_rank").reset_index(drop=True)

    from qsys.live.signal_monitoring import save_signal_basket
    signal_dir = PROJECT_ROOT / "daily" / trade_date / "pre_open" / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)
    save_signal_basket(basket, output_dir=signal_dir, signal_date=signal_date)
    print(f"  ✅ signal_basket 已修复 (adapter predictions, {len(basket)}只, signal_date={signal_date})")


def run_preopen(trade_date: str, debug_run: bool = False,
                no_notify: bool = False, reason: str | None = None,
                output_dir: str | None = None) -> None:
    ctx = _build_context(trade_date, "preopen", debug_run=debug_run,
                         no_notify=no_notify, reason=reason,
                         output_dir=output_dir)
    runner = DailyRunner()
    strategy = AlphaV1StrategyAdapter()
    runner.run_preopen(ctx, strategy)
    # Fix signal_basket artifact — overwrite broken run_daily_trading.py output
    if not debug_run:
        _save_signal_basket_from_adapter(trade_date, strategy)


def run_postclose(trade_date: str, debug_run: bool = False,
                  no_notify: bool = False, force_rerun: bool = False,
                  reason: str | None = None,
                  output_dir: str | None = None) -> None:
    ctx = _build_context(trade_date, "postclose", debug_run=debug_run,
                         no_notify=no_notify, force_rerun=force_rerun,
                         reason=reason, output_dir=output_dir)
    runner = DailyRunner()
    strategy = AlphaV1StrategyAdapter()
    runner.run_postclose(ctx, strategy)


def run_notify_only(trade_date: str, output_dir: str | None = None) -> None:
    ctx = _build_context(trade_date, "postclose", output_dir=output_dir,
                         notify_only=True)
    runner = DailyRunner()
    strategy = AlphaV1StrategyAdapter()
    runner.run_notify_only(ctx, strategy)


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
