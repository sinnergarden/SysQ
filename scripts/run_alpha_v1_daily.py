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

def run_preopen(trade_date: str, debug_run: bool = False,
                no_notify: bool = False, reason: str | None = None,
                output_dir: str | None = None) -> None:
    ctx = _build_context(trade_date, "preopen", debug_run=debug_run,
                         no_notify=no_notify, reason=reason,
                         output_dir=output_dir)
    runner = DailyRunner()
    strategy = AlphaV1StrategyAdapter()
    runner.run_preopen(ctx, strategy)


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
