#!/usr/bin/env python3
"""DR=BT equivalence check — executable semantic guard for alpha_v1.

Verifies that BacktestRunner produces identical daily results to DailyRunner
(or to a prior baseline run), or runs a deterministic self-check.

Usage
-----

    # Self-check: run BacktestRunner twice and verify determinism
    python scripts/checks/check_dr_bt_equivalence.py \\
        --strategy alpha_v1 \\
        --start-date 2026-05-16 \\
        --end-date 2026-05-22 \\
        --initial-capital 1000000 \\
        --rebalance-freq weekly \\
        --output-dir /tmp/qsys_dr_bt_check

    # Baseline comparison: compare against a directory of DailyRunner outputs
    python scripts/checks/check_dr_bt_equivalence.py \\
        --strategy alpha_v1 \\
        --start-date 2026-05-16 \\
        --end-date 2026-05-22 \\
        --initial-capital 1000000 \\
        --rebalance-freq weekly \\
        --baseline-dir /path/to/daily_runner_output \\
        --output-dir /tmp/qsys_dr_bt_check
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.backtest.strategy_runner import BacktestRunner
from qsys.strategy.registry import create_strategy
from qsys.strategy.spec import load_strategy_spec


def load_baseline_daily(
    baseline_dir: Path,
) -> dict[str, dict[str, float]]:
    """Load DailyRunner execution_summary.json per date from *baseline_dir*."""
    baseline: dict[str, dict[str, float]] = {}
    for d in sorted(baseline_dir.iterdir()):
        if not d.is_dir():
            continue
        summary_path = d / "execution" / "staging" / "execution_summary.json"
        if not summary_path.exists():
            summary_path = d / "execution" / "execution_summary.json"
        if summary_path.exists():
            data = json.loads(summary_path.read_text())
            baseline[str(data.get("trade_date", d.name))] = data
    return baseline


COMPARED_FIELDS = [
    "total_value_after",
    "cash_after",
    "market_value_after",
    "order_count",
    "filled_count",
]


def compare_baseline(
    bt_daily: list[dict],
    baseline: dict[str, dict],
) -> dict:
    """Compare BacktestRunner output against a baseline.

    Returns a dict with ``status``, ``diffs``, and ``notes``.
    """
    diffs: list[dict] = []
    for bt_day in bt_daily:
        date = bt_day.get("trade_date")
        base_day = baseline.get(date, {})
        if not base_day:
            diffs.append({
                "date": date,
                "field": "_missing",
                "bt_value": None,
                "baseline_value": None,
                "message": f"date {date} missing from baseline",
            })
            continue
        for field in COMPARED_FIELDS:
            bv = bt_day.get(field)
            lv = base_day.get(field)
            if bv is None and lv is None:
                continue
            if bv is None or lv is None:
                diffs.append({
                    "date": date,
                    "field": field,
                    "bt_value": bv,
                    "baseline_value": lv,
                    "message": f"mismatch on {date}.{field}: BT={bv} vs BL={lv}",
                })
            elif isinstance(bv, (int, float)) and isinstance(lv, (int, float)):
                if abs(bv - lv) > 0.01:
                    diffs.append({
                        "date": date,
                        "field": field,
                        "bt_value": bv,
                        "baseline_value": lv,
                        "message": f"mismatch on {date}.{field}: BT={bv} vs BL={lv}",
                    })
            elif bv != lv:
                diffs.append({
                    "date": date,
                    "field": field,
                    "bt_value": bv,
                    "baseline_value": lv,
                    "message": f"mismatch on {date}.{field}: BT={bv} vs BL={lv}",
                })

    status = "pass" if not diffs else "fail"
    return {
        "status": status,
        "compared_fields": list(COMPARED_FIELDS),
        "diffs": diffs,
        "notes": "all compared fields match" if status == "pass" else f"{len(diffs)} diffs found",
    }


def run_backtest(
    strategy_id: str,
    start_date: str,
    end_date: str,
    *,
    initial_capital: float,
    rebalance_freq: str | None = None,
    output_dir: Path,
) -> tuple[BacktestRunner, list[dict]]:
    """Run BacktestRunner once and return (runner, daily_summary)."""
    spec = load_strategy_spec(
        str(PROJECT_ROOT / f"configs/strategies/{strategy_id}.yaml")
    )
    strategy = create_strategy(strategy_id, project_root=PROJECT_ROOT)
    runner = BacktestRunner(
        mode="strict_daily_equivalent",
        artifact_mode="debug",
        execution_price_mode="open",
    )
    result = runner.run_range(
        strategy, spec,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        rebalance_freq=rebalance_freq,
        output_dir=output_dir,
    )
    return runner, result.daily_summary


def check(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy = create_strategy(args.strategy, project_root=PROJECT_ROOT)

    if args.baseline_dir:
        # ── Baseline comparison mode ──────────────────────────────
        baseline = load_baseline_daily(Path(args.baseline_dir))
        _, bt_daily = run_backtest(
            args.strategy, args.start_date, args.end_date,
            initial_capital=args.initial_capital,
            rebalance_freq=args.rebalance_freq,
            output_dir=output_dir / "bt",
        )
        result = compare_baseline(bt_daily, baseline)
    else:
        # ── Self-check mode ───────────────────────────────────────
        with tempfile.TemporaryDirectory() as tmp1:
            run1, daily1 = run_backtest(
                args.strategy, args.start_date, args.end_date,
                initial_capital=args.initial_capital,
                rebalance_freq=args.rebalance_freq,
                output_dir=Path(tmp1) / "bt",
            )
        with tempfile.TemporaryDirectory() as tmp2:
            run2, daily2 = run_backtest(
                args.strategy, args.start_date, args.end_date,
                initial_capital=args.initial_capital,
                rebalance_freq=args.rebalance_freq,
                output_dir=Path(tmp2) / "bt",
            )

        diffs = []
        for d1, d2 in zip(daily1, daily2):
            date = d1.get("trade_date")
            for field in COMPARED_FIELDS:
                v1 = d1.get(field)
                v2 = d2.get(field)
                if v1 is None and v2 is None:
                    continue
                if v1 is None or v2 is None:
                    diffs.append({
                        "date": date,
                        "field": field,
                        "run1_value": v1,
                        "run2_value": v2,
                    })
                elif isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    if abs(v1 - v2) > 0.01:
                        diffs.append({
                            "date": date,
                            "field": field,
                            "run1_value": v1,
                            "run2_value": v2,
                        })
                elif v1 != v2:
                    diffs.append({
                        "date": date,
                        "field": field,
                        "run1_value": v1,
                        "run2_value": v2,
                    })

        status = "pass" if not diffs else "fail"
        result = {
            "status": status,
            "compared_fields": list(COMPARED_FIELDS),
            "diffs": diffs,
            "notes": "deterministic self-check: two BacktestRunner runs match"
            if status == "pass"
            else f"non-deterministic: {len(diffs)} diffs found",
        }

    # ── Write result JSON ────────────────────────────────────────
    check_result = {
        "strategy_id": args.strategy,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_capital": args.initial_capital,
        "rebalance_freq": args.rebalance_freq,
        "mode": "baseline" if args.baseline_dir else "self_check",
        "baseline_dir": str(args.baseline_dir) if args.baseline_dir else None,
        **result,
    }
    result_path = output_dir / "equivalence_check.json"
    result_path.write_text(json.dumps(check_result, indent=2, ensure_ascii=False))
    print(json.dumps(check_result, indent=2, ensure_ascii=False))

    return 0 if result["status"] == "pass" else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DR=BT equivalence check — BacktestRunner vs DailyRunner"
    )
    parser.add_argument("--strategy", default="alpha_v1")
    parser.add_argument("--start-date", default="2026-05-16")
    parser.add_argument("--end-date", default="2026-05-22")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--rebalance-freq", default=None)
    parser.add_argument("--baseline-dir", default=None,
                        help="Path to DailyRunner output directory for comparison")
    parser.add_argument("--output-dir", default="/tmp/qsys_dr_bt_check")
    args = parser.parse_args()

    sys.exit(check(args))


if __name__ == "__main__":
    main()
