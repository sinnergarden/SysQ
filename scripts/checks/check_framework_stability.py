#!/usr/bin/env python3
"""One-command framework stability check.

Aggregates the most important semantic guard checks for SysQ framework
stability into a single runnable command.  Writes a JSON result file.

Usage::

    # Quick check (no DR=BT, no batch dry-run)
    python scripts/checks/check_framework_stability.py --quick

    # Full check
    python scripts/checks/check_framework_stability.py --full

    # Customised
    python scripts/checks/check_framework_stability.py \\
        --skip-dr-bt --skip-batch-dry-run \\
        --strategy alpha_v1 \\
        --start-date 2026-05-16 --end-date 2026-05-22 \\
        --output-dir /tmp/qsys_framework_stability
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.strategy.spec import load_strategy_specs
from qsys.strategy.validators import validate_strategy_spec


# ── Check result ─────────────────────────────────────────────────────────────


class CheckResult:
    """Mutable accumulator for a single check."""

    def __init__(self, name: str, required: bool = True) -> None:
        self.name = name
        self.required = required
        self.status: str = "pending"
        self.command: str = ""
        self.duration_sec: float = 0.0
        self.error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "required": self.required,
            "command": self.command,
            "duration_sec": round(self.duration_sec, 3),
            "error": self.error,
        }


# ── Check runners ────────────────────────────────────────────────────────────


def _run_pytest(
    result: CheckResult,
    *args: str,
    timeout: int = 120,
) -> None:
    """Run ``pytest -q`` with extra *args* and populate *result*."""
    cmd = [sys.executable or "python", "-m", "pytest", "-q", *args]
    result.command = " ".join(cmd)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result.duration_sec = time.time() - start
        if proc.returncode == 0:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error = (proc.stderr or proc.stdout)[-500:].strip()
    except subprocess.TimeoutExpired:
        result.duration_sec = time.time() - start
        result.status = "fail"
        result.error = f"timeout after {timeout}s"
    except Exception as exc:
        result.duration_sec = time.time() - start
        result.status = "fail"
        result.error = str(exc)


def _run_script(
    result: CheckResult,
    script_path: str,
    *args: str,
    timeout: int = 300,
) -> None:
    """Run a script with *args* and populate *result*."""
    cmd = [sys.executable or "python", str(PROJECT_ROOT / script_path), *args]
    result.command = " ".join(cmd)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result.duration_sec = time.time() - start
        if proc.returncode == 0:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error = (proc.stderr or proc.stdout)[-500:].strip()
    except subprocess.TimeoutExpired:
        result.duration_sec = time.time() - start
        result.status = "fail"
        result.error = f"timeout after {timeout}s"
    except Exception as exc:
        result.duration_sec = time.time() - start
        result.status = "fail"
        result.error = str(exc)


def _check_spec_validators(result: CheckResult) -> None:
    """Validate all strategy YAML configs against their stage."""
    result.command = "validate_strategy_spec(spec) for each config"
    start = time.time()
    try:
        config_root = PROJECT_ROOT / "configs" / "strategies"
        specs = load_strategy_specs(config_root)
        all_errors: list[str] = []
        for spec in specs:
            errors = validate_strategy_spec(spec, strict=False)
            for err in errors:
                all_errors.append(f"[{spec.strategy_id}] {err}")
        result.duration_sec = time.time() - start
        if all_errors:
            result.status = "fail"
            result.error = "; ".join(all_errors[:5])
        else:
            result.status = "pass"
    except Exception as exc:
        result.duration_sec = time.time() - start
        result.status = "fail"
        result.error = str(exc)


# ── Aggregator ───────────────────────────────────────────────────────────────


def run_checks(
    *,
    quick: bool = False,
    skip_dr_bt: bool = False,
    skip_batch_dry_run: bool = False,
    strategy: str = "alpha_v1",
    start_date: str = "2026-05-16",
    end_date: str = "2026-05-22",
    initial_capital: int = 1_000_000,
    rebalance_freq: str = "weekly",
    output_dir: str | None = None,
) -> list[CheckResult]:
    """Run the configured set of stability checks."""
    checks: list[CheckResult] = []

    # ── 1. Calendar tests ──────────────────────────────────────────────
    cal = CheckResult("calendar_tests", required=True)
    _run_pytest(cal, "tests/data/test_calendar.py")
    checks.append(cal)

    # ── 2. Strategy calendar contract ──────────────────────────────────
    scc = CheckResult("strategy_calendar_contract", required=True)
    _run_pytest(scc, "tests/contracts/test_strategy_calendar_contract.py")
    checks.append(scc)

    # ── 3. StrategySpec tests ──────────────────────────────────────────
    spec_tests = CheckResult("strategy_spec_tests", required=True)
    _run_pytest(spec_tests, "tests/strategy/test_strategy_spec.py")
    checks.append(spec_tests)

    # ── 4. StrategySpec validator over existing configs ────────────────
    validators = CheckResult("strategy_spec_validators", required=False)
    _check_spec_validators(validators)
    checks.append(validators)

    # ── 5. Batch runner tests ──────────────────────────────────────────
    batch_tests = CheckResult("batch_runner_tests", required=True)
    _run_pytest(batch_tests, "tests/scripts/test_run_daily_batch.py")
    checks.append(batch_tests)

    # ── 6. Batch dry-run ───────────────────────────────────────────────
    if not skip_batch_dry_run and not quick:
        bdr = CheckResult("batch_dry_run", required=False)
        _run_script(
            bdr, "scripts/run_daily_batch.py",
            "--stage", "candidate",
            "--mode", "preopen",
            "--trade-date", end_date,
            "--dry-run",
            timeout=60,
        )
        checks.append(bdr)

    # ── 7. DR=BT equivalence ───────────────────────────────────────────
    if not skip_dr_bt and not quick:
        drbt = CheckResult("dr_bt_equivalence", required=False)
        out = output_dir or "/tmp/qsys_framework_stability"
        _run_script(
            drbt, "scripts/checks/check_dr_bt_equivalence.py",
            "--strategy", strategy,
            "--start-date", start_date,
            "--end-date", end_date,
            "--initial-capital", str(initial_capital),
            "--rebalance-freq", rebalance_freq,
            "--output-dir", out,
            timeout=600,
        )
        checks.append(drbt)

    return checks


# ── Summary ──────────────────────────────────────────────────────────────────


def print_summary(checks: list[CheckResult]) -> None:
    """Print a human-readable summary of check results."""
    print()
    print("=" * 60)
    print("  Framework Stability Check — Summary")
    print("=" * 60)
    print()

    required_pass = True
    for c in checks:
        status_symbol = "✓" if c.status == "pass" else "✗" if c.status == "fail" else "≈"
        req = "REQUIRED" if c.required else "optional"
        print(f"  {status_symbol} [{req}] {c.name}: {c.status.upper()} ({c.duration_sec:.1f}s)")
        if c.error and c.status == "fail":
            for line in c.error.split("; "):
                print(f"       {line}")

    print()
    print("-" * 60)

    passed = sum(1 for c in checks if c.status == "pass")
    failed = sum(1 for c in checks if c.status == "fail")
    skipped = sum(1 for c in checks if c.status != "pass" and c.status != "fail")

    for c in checks:
        if c.required and c.status == "fail":
            required_pass = False

    print(f"  Passed: {passed}  Failed: {failed}  Skipped/Other: {skipped}")
    if required_pass:
        print(f"  Overall: ✅ PASS (required checks)")
    else:
        print(f"  Overall: ❌ FAIL (some required checks failed)")
    print("=" * 60)
    print()


def build_report(
    checks: list[CheckResult],
    started_at: datetime,
) -> dict[str, Any]:
    """Build the JSON-serialisable report dict."""
    finished_at = datetime.now()
    duration = (finished_at - started_at).total_seconds()

    statuses = {c.status for c in checks}
    required_failures = [c for c in checks if c.required and c.status == "fail"]

    if required_failures:
        overall = "fail"
    elif "fail" in statuses:
        overall = "partial"
    elif all(c.status in ("pass", "skipped") for c in checks):
        overall = "pass"
    else:
        overall = "partial"

    return {
        "status": overall,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round(duration, 3),
        "checks": [c.to_dict() for c in checks],
    }


def write_report(report: dict[str, Any], output_dir: str | None) -> Path:
    """Write the JSON report to disk."""
    if output_dir:
        out = Path(output_dir)
    else:
        out = Path("/tmp/qsys_framework_stability")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "framework_stability_check.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"  Report → {path}")
    return path


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-command framework stability check",
    )
    parser.add_argument(
        "--quick", action="store_true", default=False,
        help="Run only fast local tests (skip DR=BT and batch dry-run)",
    )
    parser.add_argument(
        "--full", action="store_true", default=False,
        help="Run all checks including DR=BT and batch dry-run",
    )
    parser.add_argument(
        "--skip-dr-bt", action="store_true", default=False,
        help="Skip DR=BT equivalence check",
    )
    parser.add_argument(
        "--skip-batch-dry-run", action="store_true", default=False,
        help="Skip batch dry-run check",
    )
    parser.add_argument(
        "--strategy", default="alpha_v1",
        help="Strategy ID for DR=BT check (default: alpha_v1)",
    )
    parser.add_argument(
        "--start-date", default="2026-05-16",
        help="Start date for DR=BT check (default: 2026-05-16)",
    )
    parser.add_argument(
        "--end-date", default="2026-05-22",
        help="End date for DR=BT check (default: 2026-05-22)",
    )
    parser.add_argument(
        "--initial-capital", type=int, default=1_000_000,
        help="Initial capital for DR=BT check (default: 1000000)",
    )
    parser.add_argument(
        "--rebalance-freq", default="weekly",
        help="Rebalance frequency for DR=BT (default: weekly)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory for report JSON (default: /tmp/qsys_framework_stability)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    # --full implies not --quick
    if args.full:
        args.quick = False
    return args


def main() -> None:
    args = parse_args()
    started_at = datetime.now()

    print()
    print("=" * 60)
    print("  SysQ Framework Stability Check")
    print(f"  Started: {started_at.isoformat()}")
    print(f"  Mode: {'quick' if args.quick else 'full'}")
    print("=" * 60)
    print()

    checks = run_checks(
        quick=args.quick,
        skip_dr_bt=args.skip_dr_bt,
        skip_batch_dry_run=args.skip_batch_dry_run,
        strategy=args.strategy,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        rebalance_freq=args.rebalance_freq,
        output_dir=args.output_dir,
    )

    report = build_report(checks, started_at)
    print_summary(checks)
    write_report(report, args.output_dir)

    # Exit code: 0 if all required checks pass, non-zero otherwise
    required_failures = [c for c in checks if c.required and c.status == "fail"]
    if required_failures:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
