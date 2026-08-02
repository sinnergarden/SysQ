#!/usr/bin/env python3
"""Generic daily ops entrypoint — strategy-agnostic.

Usage::

    python scripts/run_daily.py --strategy alpha_v1 --mode preopen \\
        --trade-date 2026-05-22

    python scripts/run_daily.py --strategy alpha_v1 --mode postclose \\
        --trade-date 2026-05-22

    python scripts/run_daily.py --strategy alpha_v1 --mode train

    python scripts/run_daily.py --strategy alpha_v1 --notify-only \\
        --trade-date 2026-05-22
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

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

from qsys.common.config import load_strategy_config
from qsys.ops.daily_runner import DailyRunner
from qsys.ops.attempts import (
    build_attempt_id,
    make_active_attempt_payload,
    next_attempt_seq,
    read_active_attempt,
    resolve_promotion_snapshot,
    snapshot_promotion_pointer,
    write_active_attempt,
)
from qsys.ops.run_context import DailyRunContext, resolve_run_root
from qsys.ops.promotion_resolver import resolve_shadow_promotion
from qsys.strategy.registry import create_strategy


# ── CLI ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic daily ops — strategy-agnostic entrypoint"
    )
    parser.add_argument("--strategy", required=True, help="策略 ID (如 alpha_v1)")
    parser.add_argument(
        "--mode",
        choices=["preopen", "postclose", "train"],
        default="preopen",
        help="运行模式",
    )
    parser.add_argument("--trade-date", help="交易日期 YYYY-MM-DD 或 auto（默认 auto）")
    parser.add_argument(
        "--debug-run",
        action="store_true",
        help="调试模式：不修改 shadow/account.json / positions.csv / ledger.csv",
    )
    parser.add_argument(
        "--output-dir",
        help="调试模式下输出目录（默认: experiments/{strategy_id}_daily/{trade_date}）",
    )
    parser.add_argument("--no-notify", action="store_true", help="跳过 Telegram 通知")
    parser.add_argument(
        "--notify-only",
        action="store_true",
        help="仅从已有产物重建并发送通知，不执行任何交易",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="危险模式：覆盖已提交的执行产物（必须配合 --reason）",
    )
    parser.add_argument("--reason", help="操作原因说明（--force-rerun 必填）")
    parser.add_argument(
        "--train-end-date",
        help="训练数据截止日期 YYYY-MM-DD（仅 train 模式）",
    )
    parser.add_argument(
        "--run-mode", choices=["shadow", "production"], default="shadow",
        help="运行模式 (shadow=已promote候选, production=未实现)",
    )
    parser.add_argument(
        "--promotion-pointer", default=None,
        help="promotion pointer 路径（默认: data/research/promotions/shadow.yaml）",
    )
    parser.add_argument(
        "--triggered-by", default="manual",
        help="调用来源标识 (manual / scheduler / systemd / telegram / agent)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.run_mode == "production":
        parser.error(
            "Production run mode (--run-mode production) is not implemented. "
            "Only --run-mode shadow is supported."
        )
    if args.force_rerun and not args.reason:
        parser.error("--force-rerun 必须配合 --reason 提供原因")
    if args.mode == "train":
        if args.force_rerun:
            print("⚠ --force-rerun 对 train 模式无意义，忽略")
    elif not args.trade_date or args.trade_date == "auto":
        from datetime import datetime
        args.trade_date = datetime.now().strftime("%Y-%m-%d")
    return args


# ── Mode handlers ──────────────────────────────────────────────────────


def run_daily_main(argv: list[str] | None = None) -> None:
    """Orchestrate a daily run from parsed CLI arguments."""
    args = parse_args(argv)

    strategy_id = args.strategy
    config = load_strategy_config(strategy_id, PROJECT_ROOT)

    # Inject training end_date into config so it flows through to the trainer
    if args.train_end_date:
        config.setdefault("training", {})["end_date"] = args.train_end_date

    strategy = create_strategy(strategy_id, config, project_root=PROJECT_ROOT)

    runner = DailyRunner()

    # ── Resolve shadow promotion pointer — per mode ─────────────────
    # preopen / train: reads args.promotion_pointer (global shadow.yaml).
    # postclose: reads only active_attempt.json + promotion_snapshot.yaml (below).
    promotion_lineage: dict[str, str | None] = {}
    if args.run_mode == "shadow" and args.mode in ("preopen", "train"):
        raw_pointer = args.promotion_pointer or "data/research/promotions/shadow.yaml"
        pointer_path = Path(raw_pointer)
        if not pointer_path.is_absolute() and not pointer_path.exists():
            pointer_path = PROJECT_ROOT / raw_pointer
        try:
            promotion_lineage = resolve_shadow_promotion(pointer_path)
        except FileNotFoundError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"  ❌ Shadow promotion pointer validation failed: {e}", file=sys.stderr)
            sys.exit(1)

    # ── Train — no trade-date required ────────────────────────────────
    if args.mode == "train":
        trade_date = args.trade_date or datetime.now().strftime("%Y-%m-%d")
        if args.output_dir:
            train_run_root = Path(args.output_dir)
        elif args.debug_run:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            train_run_root = (
                PROJECT_ROOT / "experiments" / "debug"
                / strategy.strategy_id / f"train_{trade_date}_{ts}"
            )
        else:
            train_run_root = (
                PROJECT_ROOT / "experiments"
                / f"{strategy.strategy_id}_train" / trade_date
            )

        ctx = DailyRunContext(
            trade_date=trade_date,
            mode="train",
            run_root=train_run_root,
            project_root=PROJECT_ROOT,
            strategy_id=strategy.strategy_id,
            account_id=strategy.account_id,
            run_mode=args.run_mode,
            debug_run=args.debug_run,
            no_notify=args.no_notify,
            force_rerun=False,
            reason=args.reason,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            triggered_by=args.triggered_by,
            # Promotion lineage
            candidate_id=promotion_lineage.get("candidate_id"),
            candidate_path=promotion_lineage.get("candidate_path"),
            signal_id=promotion_lineage.get("signal_id"),
            signal_run_id=promotion_lineage.get("signal_run_id"),
            strategy_config_id=promotion_lineage.get("strategy_config_id"),
            strategy_template_id=promotion_lineage.get("strategy_template_id"),
            strategy_run_id=promotion_lineage.get("strategy_run_id"),
            backtest_id=promotion_lineage.get("backtest_id"),
            promotion_pointer_path=promotion_lineage.get("promotion_pointer_path"),
            promoted_at=promotion_lineage.get("promoted_at"),
            promoted_by=promotion_lineage.get("promoted_by"),
        )
        runner.run_train(ctx, strategy)
        return

    # ── Modes requiring trade-date ────────────────────────────────────
    trade_date = args.trade_date  # guaranteed non-None by parse_args above

    run_root = resolve_run_root(
        PROJECT_ROOT,
        strategy.strategy_id,
        trade_date,
        debug_run=args.debug_run,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )

    # ── Attempt / snapshot ────────────────────────────────────────────
    attempt_id: str | None = None
    attempt_seq: int | None = None
    supersedes_attempt_id: str | None = None
    active_attempt_val = False
    promotion_snapshot_path_val: str | None = None

    if args.mode == "preopen":
        existing = read_active_attempt(run_root) if not args.debug_run else None
        attempt_seq = next_attempt_seq(run_root)

        if existing and not args.debug_run:
            if not args.force_rerun:
                print(
                    f"⛔ Active attempt {existing.get('attempt_id', '?')} already exists.",
                    file=sys.stderr,
                )
                print(
                    "   Use --force-rerun --reason to replace the active attempt.",
                    file=sys.stderr,
                )
                sys.exit(1)
            supersedes_attempt_id = existing["attempt_id"]

        # Promotion snapshot written before runner for plan audit trail
        if args.debug_run:
            attempt_id = build_attempt_id(
                args.mode, args.run_mode, trade_date, strategy.strategy_id, attempt_seq,
            )
            active_attempt_val = False
            snap_path = snapshot_promotion_pointer(run_root, promotion_lineage)
            promotion_snapshot_path_val = str(snap_path)
            print(f"  🔧 Debug attempt: {attempt_id} (not active)")
        else:
            attempt_id = build_attempt_id(
                args.mode, args.run_mode, trade_date, strategy.strategy_id, attempt_seq,
            )
            active_attempt_val = True
            snap_path = snapshot_promotion_pointer(run_root, promotion_lineage)
            promotion_snapshot_path_val = str(snap_path)
            print(f"  📝 Preopen attempt: {attempt_id}")

    elif args.mode == "postclose":
        active = read_active_attempt(run_root)
        if not active:
            print(
                "⛔ No active preopen attempt found. Run preopen first.",
                file=sys.stderr,
            )
            sys.exit(1)
        attempt_id = active.get("attempt_id")
        attempt_seq = active.get("attempt_seq")
        promotion_snapshot_path_val = str(run_root / "promotion_snapshot.yaml")

        # postclose must NOT read global shadow.yaml — use frozen snapshot
        snap = resolve_promotion_snapshot(run_root)
        if not snap:
            print(
                "⛔ Active preopen attempt has no promotion_snapshot.yaml. "
                "Re-run preopen to create it.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Apply snapshot lineage fields
        snapshot_path = snap.get("promotion_pointer_path")
        for field in (
            "candidate_id", "candidate_path", "signal_id", "signal_run_id",
            "strategy_config_id", "strategy_template_id", "strategy_run_id",
            "backtest_id", "promoted_at", "promoted_by",
        ):
            val = snap.get(field)
            if val is not None:
                promotion_lineage[field] = val  # type: ignore[literal-required]
        if snapshot_path is not None:
            promotion_lineage["promotion_pointer_path"] = snapshot_path
        print("  📋 Lineage from promotion snapshot (frozen at preopen)")

    # ── Build DailyRunContext ────────────────────────────────────────
    if args.mode == "preopen":
        default_ledger_status = "not_applicable"
    elif args.debug_run:
        default_ledger_status = "not_applicable"
    else:
        default_ledger_status = "pending"

    ctx = DailyRunContext(
        trade_date=trade_date,
        mode=args.mode,
        run_root=run_root,
        project_root=PROJECT_ROOT,
        strategy_id=strategy.strategy_id,
        account_id=strategy.account_id,
        run_mode=args.run_mode,
        debug_run=args.debug_run,
        no_notify=args.no_notify,
        force_rerun=args.force_rerun,
        reason=args.reason,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        triggered_by=args.triggered_by,
        # Promotion lineage
        candidate_id=promotion_lineage.get("candidate_id"),
        candidate_path=promotion_lineage.get("candidate_path"),
        signal_id=promotion_lineage.get("signal_id"),
        signal_run_id=promotion_lineage.get("signal_run_id"),
        strategy_config_id=promotion_lineage.get("strategy_config_id"),
        strategy_template_id=promotion_lineage.get("strategy_template_id"),
        strategy_run_id=promotion_lineage.get("strategy_run_id"),
        backtest_id=promotion_lineage.get("backtest_id"),
        promotion_pointer_path=promotion_lineage.get("promotion_pointer_path"),
        promoted_at=promotion_lineage.get("promoted_at"),
        promoted_by=promotion_lineage.get("promoted_by"),
        # Attempt fields
        attempt_id=attempt_id,
        attempt_seq=attempt_seq,
        supersedes_attempt_id=supersedes_attempt_id,
        rerun_reason=args.reason,
        active_attempt=active_attempt_val,
        # Promotion snapshot
        promotion_snapshot_path=promotion_snapshot_path_val,
        # Ledger boundary
        ledger_commit_status=default_ledger_status,
        # F04: record the actual ledger run id (matches
        # write_execution_to_ledger's default f"{trade_date}.{strategy_id}.shadow")
        # so reruns/reversal can target the exact run.
        ledger_run_id=f"{trade_date}.{strategy.strategy_id}.shadow",
    )

    # ── Notify-only ──────────────────────────────────────────────────
    if args.notify_only:
        runner.run_notify_only(ctx, strategy)
        return

    # ── Preopen / Postclose ──────────────────────────────────────────
    if args.mode == "preopen":
        runner.run_preopen(ctx, strategy)
        # Only persist active pointer AFTER a successful preopen
        # (manifest written = preopen completed without early return).
        if not args.debug_run and args.run_mode == "shadow":
            if (run_root / "daily_manifest.json").exists():
                active_payload = make_active_attempt_payload(
                    ctx.attempt_id, ctx.attempt_seq, args.mode, args.run_mode,
                    trade_date, strategy.strategy_id,
                    supersedes_attempt_id=ctx.supersedes_attempt_id,
                    rerun_reason=args.reason,
                )
                write_active_attempt(run_root, active_payload)
                print(f"  📝 Active attempt: {ctx.attempt_id}")
    elif args.mode == "postclose":
        runner.run_postclose(ctx, strategy)


# ── Structured dispatch for batch runner ────────────────────────────────


def run_daily_for_strategy(
    *,
    strategy_id: str,
    mode: str,
    trade_date: str | None = None,
    debug_run: bool = False,
    no_notify: bool = False,
    output_dir: str | None = None,
    force_rerun: bool = False,
    reason: str | None = None,
    notify_only: bool = False,
    train_end_date: str | None = None,
    run_mode: str = "shadow",
    promotion_pointer: str | None = None,
) -> dict:
    """Dispatch DailyRunner for a single strategy, returning a status dict.

    Structured wrapper around ``run_daily_main`` for use by the batch runner.
    Returns a dict suitable for batch summary output.
    """
    argv = ["--strategy", strategy_id, "--mode", mode]
    if trade_date:
        argv.extend(["--trade-date", trade_date])
    if debug_run:
        argv.append("--debug-run")
    if no_notify:
        argv.append("--no-notify")
    if output_dir:
        argv.extend(["--output-dir", output_dir])
    if force_rerun:
        argv.append("--force-rerun")
    if reason:
        argv.extend(["--reason", reason])
    if notify_only:
        argv.append("--notify-only")
    if train_end_date:
        argv.extend(["--train-end-date", train_end_date])
    if run_mode:
        argv.extend(["--run-mode", run_mode])
    if promotion_pointer:
        argv.extend(["--promotion-pointer", promotion_pointer])

    try:
        run_daily_main(argv)
        return {"strategy_id": strategy_id, "status": "success", "error": None}
    except Exception as exc:
        return {"strategy_id": strategy_id, "status": "failed", "error": str(exc)}


def main() -> None:
    run_daily_main()


if __name__ == "__main__":
    main()
