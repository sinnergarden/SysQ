#!/usr/bin/env python3
"""Stage-aware daily batch runner — schedule strategies by lifecycle stage.

Dispatches all strategies matching a lifecycle stage through
``run_daily.py``, isolates failures, and writes a machine-readable batch
summary JSON.

Usage::

    # Candidate batch — preopen
    python scripts/run_daily_batch.py \\
        --stage candidate --mode preopen --trade-date 2026-05-22

    # Production batch — postclose
    python scripts/run_daily_batch.py \\
        --stage production --mode postclose --trade-date 2026-05-22

    # Train all candidate strategies
    python scripts/run_daily_batch.py --stage candidate --mode train

    # Dry run — show what would be dispatched
    python scripts/run_daily_batch.py \\
        --stage candidate --mode preopen --trade-date 2026-05-22 --dry-run

    # Debug run — no side effects
    python scripts/run_daily_batch.py \\
        --stage candidate --mode preopen --trade-date 2026-05-22 \\
        --debug-run --no-notify --output-root /tmp/qsys_batch_test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.strategy.spec import (
    load_strategy_specs_for_stage,
    validate_stage,
)
from qsys.strategy.registry import get_strategy_class

BATCH_STAGES = {"candidate", "production"}
SUPPORTED_MODES = {"preopen", "postclose", "train", "notify-only"}
SUMMARY_FILENAME = "batch_summary.json"

# Production safety gate
ALLOW_PRODUCTION_WARNING = (
    "Production batch requires --allow-production. "
    "Production risk controls are not fully implemented."
)


def _summary_filename(stage: str, mode: str) -> str:
    """Return a stage/mode-specific summary filename so preopen/postclose/etc.
    summaries on the same trade_date do not overwrite each other."""
    return f"batch_{stage}_{mode}.json"


# ── Helpers ─────────────────────────────────────────────────────────────


def _resolve_trade_date(trade_date: str | None) -> str:
    """Return *trade_date* or today's date if ``auto`` / ``None``."""
    if not trade_date or trade_date == "auto":
        return datetime.now().strftime("%Y-%m-%d")
    return trade_date


def _is_registered(strategy_id: str) -> bool:
    """Return ``True`` if *strategy_id* has a runtime adapter registered."""
    try:
        get_strategy_class(strategy_id)
        return True
    except ValueError:
        return False


def _build_command(
    strategy_id: str,
    mode: str,
    trade_date: str,
    *,
    debug_run: bool = False,
    no_notify: bool = False,
    output_root: str | None = None,
    triggered_by: str = "manual",
) -> list[str]:
    """Build the subprocess argv for dispatching a single strategy."""
    cmd = [
        sys.executable or "python",
        str(PROJECT_ROOT / "scripts" / "run_daily.py"),
        "--strategy", strategy_id,
    ]

    # notify-only is a flag, not a mode — build a valid run_daily.py command
    if mode == "notify-only":
        cmd.extend(["--mode", "preopen", "--notify-only"])
    else:
        cmd.extend(["--mode", mode])

    if mode != "train":
        cmd.extend(["--trade-date", trade_date])
    if debug_run:
        cmd.append("--debug-run")
    if no_notify:
        cmd.append("--no-notify")
    if output_root:
        out_dir = str(Path(output_root) / trade_date / strategy_id)
        cmd.extend(["--output-dir", out_dir])
    if triggered_by and triggered_by != "manual":
        cmd.extend(["--triggered-by", triggered_by])
    return cmd


def _command_preview(cmd: list[str]) -> str:
    """Return a human-readable preview of the command."""
    return " ".join(cmd)


# ── Core dispatch ───────────────────────────────────────────────────────


def run_batch(
    *,
    stage: str,
    mode: str,
    trade_date: str | None = None,
    output_root: str | None = None,
    config_root: str | None = None,
    strategy_filter: list[str] | None = None,
    exclude_filter: list[str] | None = None,
    debug_run: bool = False,
    no_notify: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = True,
    fail_fast: bool = False,
    allow_production: bool = False,
    triggered_by: str = "manual",
) -> dict:
    """Execute a daily batch for all strategies matching *stage*.

    Parameters
    ----------
    stage : str
        Lifecycle stage (``candidate`` or ``production``).
    mode : str
        Operation mode (``preopen``, ``postclose``, ``train``, ``notify-only``).
    trade_date : str or None
        Target date (``YYYY-MM-DD`` or ``auto``).  Defaults to today.
    output_root : str or None
        Root directory for per-strategy output.
    config_root : str or None
        Root directory for strategy YAML configs.  Defaults to
        ``configs/strategies/``.
    strategy_filter : list[str] or None
        Optional list of strategy IDs to include (if given, only these run).
    exclude_filter : list[str] or None
        Optional list of strategy IDs to exclude.
    debug_run : bool
        Pass ``--debug-run`` to each strategy dispatch.
    no_notify : bool
        Pass ``--no-notify`` to each strategy dispatch.
    dry_run : bool
        If ``True``, only print selected strategies and commands — do not
        dispatch.
    continue_on_error : bool
        If ``True``, continue dispatching remaining strategies after a
        failure.  Default ``True``.
    fail_fast : bool
        If ``True``, stop after the first failure.
    allow_production : bool
        If ``True``, allow production-stage dispatch.  Required when
        *stage* is ``production``.

    Returns
    -------
    dict
        Batch summary dict suitable for JSON output.
    """
    started_at = datetime.now()
    trade_date_resolved = _resolve_trade_date(trade_date)

    validate_stage(stage)
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            f"unsupported mode {mode!r}; expected one of {sorted(SUPPORTED_MODES)}"
        )

    if stage not in BATCH_STAGES:
        raise ValueError(
            f"stage {stage!r} is not a daily batch stage; "
            f"expected one of {sorted(BATCH_STAGES)}"
        )

    # Production safety gate
    if stage == "production" and not allow_production:
        print(f"\n  ⛔ {ALLOW_PRODUCTION_WARNING}")
        print()
        finished_at = datetime.now()
        return _build_summary(
            stage=stage, mode=mode, trade_date=trade_date_resolved,
            started_at=started_at, finished_at=finished_at,
            status="blocked", strategy_results=[], output_root=output_root, triggered_by=triggered_by,
        )

    if fail_fast and continue_on_error:
        continue_on_error = False

    # ── Load and filter specs ────────────────────────────────────────
    cfg_root = config_root or str(PROJECT_ROOT / "configs" / "strategies")
    specs = load_strategy_specs_for_stage(
        stage, cfg_root, registry_required=False,
    )

    if strategy_filter:
        filter_set = set(strategy_filter)
        specs = [s for s in specs if s.strategy_id in filter_set]

    if exclude_filter:
        exclude_set = set(exclude_filter)
        specs = [s for s in specs if s.strategy_id not in exclude_set]

    # ── Report selected strategies ────────────────────────────────────
    strategy_results: list[dict[str, Any]] = []

    if not specs:
        print(f"  No {stage}-stage strategies selected for {mode} batch.")
        finished_at = datetime.now()
        return _build_summary(
            stage=stage, mode=mode, trade_date=trade_date_resolved,
            started_at=started_at, finished_at=finished_at,
            status="skipped", strategy_results=strategy_results,
            output_root=output_root, triggered_by=triggered_by,
        )

    print(f"\n{'=' * 60}")
    print(f"  Batch: stage={stage}, mode={mode}, date={trade_date_resolved}")
    print(f"  Strategies ({len(specs)}):")
    for spec in specs:
        reg_status = "✓" if _is_registered(spec.strategy_id) else "✗ (not registered)"
        print(f"    - {spec.strategy_id} ({spec.display_name}) [{reg_status}]")
    print(f"{'=' * 60}\n")

    # ── Dry run ──────────────────────────────────────────────────────
    if dry_run:
        print("  DRY RUN — no dispatch.")
        print()
        for spec in specs:
            cmd = _build_command(
                spec.strategy_id, mode, trade_date_resolved,
                debug_run=debug_run, no_notify=no_notify,
                output_root=output_root, triggered_by=triggered_by,
            )
            preview = _command_preview(cmd)
            print(f"  [{spec.strategy_id}] {preview}")
        print()
        finished_at = datetime.now()
        return _build_summary(
            stage=stage, mode=mode, trade_date=trade_date_resolved,
            started_at=started_at, finished_at=finished_at,
            status="dry_run", strategy_results=[
                {
                    "strategy_id": s.strategy_id,
                    "stage": s.stage,
                    "status": "dry_run",
                    "run_root": None,
                    "duration_sec": 0.0,
                    "command": _command_preview(_build_command(
                        s.strategy_id, mode, trade_date_resolved,
                        debug_run=debug_run, no_notify=no_notify,
                        output_root=output_root, triggered_by=triggered_by,
                    )),
                    "error": None,
                }
                for s in specs
            ],
            output_root=output_root, triggered_by=triggered_by,
        )

    # ── Dispatch ─────────────────────────────────────────────────────
    failed = 0
    for spec in specs:
        cmd = _build_command(
            spec.strategy_id, mode, trade_date_resolved,
            debug_run=debug_run, no_notify=no_notify,
            output_root=output_root, triggered_by=triggered_by,
        )
        preview = _command_preview(cmd)

        if not _is_registered(spec.strategy_id):
            strat_start = time.time()
            strat_result = {
                "strategy_id": spec.strategy_id,
                "stage": spec.stage,
                "status": "skipped",
                "run_root": None,
                "duration_sec": time.time() - strat_start,
                "command": preview,
                "error": f"strategy {spec.strategy_id!r} not registered in runtime registry",
            }
            strategy_results.append(strat_result)
            failed += 1
            if fail_fast:
                break
            continue

        print(f"  [{spec.strategy_id}] {preview}")
        strat_start = time.time()

        try:
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=3600,
            )
            duration = time.time() - strat_start
            run_root = (
                str(Path(output_root) / trade_date_resolved / spec.strategy_id)
                if output_root else None
            )

            if result.returncode == 0:
                strat_result = {
                    "strategy_id": spec.strategy_id,
                    "stage": spec.stage,
                    "status": "success",
                    "run_root": run_root,
                    "duration_sec": round(duration, 3),
                    "command": preview,
                    "error": None,
                }
                print(f"    ✓ {spec.strategy_id} completed ({duration:.1f}s)")
            else:
                stdout = result.stdout[-500:] if result.stdout else ""
                stderr = result.stderr[-500:] if result.stderr else ""
                error_detail = (stderr or stdout).strip()
                strat_result = {
                    "strategy_id": spec.strategy_id,
                    "stage": spec.stage,
                    "status": "failed",
                    "run_root": run_root,
                    "duration_sec": round(duration, 3),
                    "command": preview,
                    "error": error_detail or f"exit code {result.returncode}",
                }
                failed += 1
                print(f"    ✗ {spec.strategy_id} failed ({duration:.1f}s)")

        except subprocess.TimeoutExpired:
            duration = time.time() - strat_start
            strat_result = {
                "strategy_id": spec.strategy_id,
                "stage": spec.stage,
                "status": "failed",
                "run_root": None,
                "duration_sec": round(duration, 3),
                "command": preview,
                "error": "timeout after 3600s",
            }
            failed += 1
            print(f"    ✗ {spec.strategy_id} timed out ({duration:.1f}s)")
        except Exception as exc:
            duration = time.time() - strat_start
            strat_result = {
                "strategy_id": spec.strategy_id,
                "stage": spec.stage,
                "status": "failed",
                "run_root": None,
                "duration_sec": round(duration, 3),
                "command": preview,
                "error": str(exc),
            }
            failed += 1
            print(f"    ✗ {spec.strategy_id} error: {exc}")

        strategy_results.append(strat_result)

        if failed > 0 and fail_fast:
            print(f"\n  ⛔ fail-fast: stopping after {spec.strategy_id} failure.")
            break

    # ── Summary ──────────────────────────────────────────────────────
    finished_at = datetime.now()

    success_count = sum(1 for r in strategy_results if r["status"] == "success")
    failed_count = sum(1 for r in strategy_results if r["status"] == "failed")
    skipped_count = sum(1 for r in strategy_results if r["status"] == "skipped")

    if failed_count == 0 and skipped_count == 0:
        status = "success"
    elif failed_count > 0 and success_count > 0:
        status = "partial_failed"
    elif failed_count > 0 and success_count == 0:
        status = "failed"
    else:
        status = "skipped"

    print(f"\n{'=' * 60}")
    print(f"  Batch complete: {status}")
    print(f"  Selected: {len(strategy_results)} | "
          f"Success: {success_count} | Failed: {failed_count} | Skipped: {skipped_count}")
    print(f"{'=' * 60}\n")

    summary = _build_summary(
        stage=stage, mode=mode, trade_date=trade_date_resolved,
        started_at=started_at, finished_at=finished_at,
        status=status, strategy_results=strategy_results,
        selected_count=len(strategy_results),
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        output_root=output_root, triggered_by=triggered_by,
    )

    # Write summary to disk
    write_summary(summary, output_root, trade_date_resolved, stage, mode)

    return summary


def _build_summary(
    *,
    stage: str,
    mode: str,
    trade_date: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    strategy_results: list[dict],
    selected_count: int | None = None,
    success_count: int | None = None,
    failed_count: int | None = None,
    skipped_count: int | None = None,
    output_root: str | None = None,
) -> dict:
    """Build the batch summary dict."""
    duration = (finished_at - started_at).total_seconds()

    summary: dict[str, Any] = {
        "stage": stage,
        "mode": mode,
        "trade_date": trade_date,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round(duration, 3),
        "status": status,
        "selected_count": selected_count or len(strategy_results),
        "success_count": success_count or 0,
        "failed_count": failed_count or 0,
        "skipped_count": skipped_count or 0,
        "strategies": strategy_results,
    }
    return summary


def write_summary(summary: dict, output_root: str | None, trade_date: str,
                  stage: str, mode: str) -> Path:
    """Write the batch summary JSON to disk.

    The filename includes stage and mode so preopen / postclose / etc.
    summaries on the same trade_date do not overwrite each other.
    """
    if output_root:
        out_dir = Path(output_root) / trade_date
    else:
        out_dir = Path(PROJECT_ROOT) / "runs" / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _summary_filename(stage, mode)
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"  Batch summary → {path}")
    return path


# ── CLI ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage-aware daily batch runner",
    )
    parser.add_argument(
        "--stage", required=True,
        choices=sorted(BATCH_STAGES),
        help="Lifecycle stage to dispatch",
    )
    parser.add_argument(
        "--mode", required=True,
        choices=sorted(SUPPORTED_MODES),
        help="Operation mode",
    )
    parser.add_argument(
        "--trade-date", default="auto",
        help="Trading date (YYYY-MM-DD or 'auto' for today)",
    )
    parser.add_argument(
        "--strategy", dest="strategy_filter", action="append", default=None,
        help="Include specific strategy (repeatable); runs all matching stage if omitted",
    )
    parser.add_argument(
        "--exclude", dest="exclude_filter", action="append", default=None,
        help="Exclude specific strategy (repeatable)",
    )
    parser.add_argument(
        "--output-root", default=None,
        help="Root directory for per-strategy output and batch summary",
    )
    parser.add_argument(
        "--config-root", default=None,
        help="Root directory for strategy YAML configs (default: configs/strategies/)",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true", default=True,
        help="Continue dispatching after a strategy failure (default)",
    )
    parser.add_argument(
        "--fail-fast", action="store_true", default=False,
        help="Stop after the first strategy failure",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show selected strategies and commands without dispatching",
    )
    parser.add_argument(
        "--debug-run", action="store_true", default=False,
        help="Pass --debug-run to each strategy (no side effects)",
    )
    parser.add_argument(
        "--no-notify", action="store_true", default=False,
        help="Pass --no-notify to each strategy",
    )
    parser.add_argument(
        "--allow-production", action="store_true", default=False,
        help="Allow production-stage dispatch (required for --stage production)",
    )
    parser.add_argument(
        "--triggered-by", default="manual",
        help="调用来源标识 (manual / scheduler / systemd / telegram / agent)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.fail_fast and args.continue_on_error:
        args.continue_on_error = False
    return args


def main() -> None:
    args = parse_args()
    summary = run_batch(
        stage=args.stage,
        mode=args.mode,
        trade_date=args.trade_date,
        output_root=args.output_root,
        config_root=args.config_root,
        strategy_filter=args.strategy_filter,
        exclude_filter=args.exclude_filter,
        debug_run=args.debug_run,
        no_notify=args.no_notify,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
        fail_fast=args.fail_fast,
        allow_production=args.allow_production,
        triggered_by=args.triggered_by,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    # Exit code
    if summary["status"] in ("failed", "partial_failed", "blocked"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
