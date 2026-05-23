#!/usr/bin/env python3
"""
Compare weekly replay outputs between two branches.

Compares prediction signals, order intents, execution results,
positions, and ledger state across a set of trade dates.

Usage:
    python scripts/dev/compare_weekly_replay.py \\
        --baseline /path/to/main/project/root \\
        --candidate /path/to/branch/project/root \\
        --trade-dates 2026-05-18,2026-05-19,2026-05-20,2026-05-21,2026-05-22
"""

from __future__ import annotations

import argparse
import csv  # noqa: F401 (kept for potential future use)
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭️  SKIP"


def compare_csv(
    label: str,
    baseline_path: Path,
    candidate_path: Path,
    *,
    tolerances: dict[str, float] | None = None,
    skip_columns: list[str] | None = None,
) -> bool:
    """Compare two CSVs with per-column tolerances."""
    tolerances = tolerances or {}
    skip_columns = skip_columns or []

    if not baseline_path.exists() and not candidate_path.exists():
        print(f"  {SKIP}: {label} — both missing")
        return True
    if not baseline_path.exists():
        print(f"  {FAIL}: {label} — baseline missing: {baseline_path}")
        return False
    if not candidate_path.exists():
        print(f"  {FAIL}: {label} — candidate missing: {candidate_path}")
        return False

    try:
        bdf = pd.read_csv(baseline_path)
        cdf = pd.read_csv(candidate_path)
    except Exception as e:
        print(f"  {FAIL}: {label} — read error: {e}")
        return False

    if bdf.shape != cdf.shape:
        print(f"  {FAIL}: {label} — shape mismatch: baseline {bdf.shape} vs candidate {cdf.shape}")
        return False

    cols_b = set(bdf.columns)
    cols_c = set(cdf.columns)
    if cols_b != cols_c:
        missing = cols_b - cols_c
        extra = cols_c - cols_b
        parts = []
        if missing:
            parts.append(f"missing columns: {sorted(missing)}")
        if extra:
            parts.append(f"extra columns: {sorted(extra)}")
        print(f"  {FAIL}: {label} — column mismatch; {', '.join(parts)}")
        return False

    all_ok = True
    for col in bdf.columns:
        if col in skip_columns:
            continue
        bvals = bdf[col].values
        cvals = cdf[col].values

        # Determine if both are numeric
        if np.issubdtype(bvals.dtype, np.number) and np.issubdtype(cvals.dtype, np.number):
            mismatch = ~np.isclose(bvals, cvals, rtol=1e-9, atol=tolerances.get(col, 1e-9))
        else:
            mismatch = bvals != cvals

        if mismatch.any():
            indices = np.where(mismatch)[0]
            bad_count = len(indices)
            all_ok = False
            if bad_count <= 5:
                for idx in indices[:5]:
                    print(f"    row {idx}: {label}.{col} baseline={bvals[idx]} candidate={cvals[idx]}")
            else:
                for idx in indices[:3]:
                    print(f"    row {idx}: {label}.{col} baseline={bvals[idx]} candidate={cvals[idx]}")
                print(f"    ... and {bad_count - 3} more mismatches")

    if all_ok:
        print(f"  {PASS}: {label} ({len(bdf)} rows)")
    else:
        print(f"  {FAIL}: {label} — see mismatches above")
    return all_ok


def compare_predictions(base_dir: Path, cand_dir: Path, trade_dates: list[str]) -> bool:
    print(f"\n{'='*60}")
    print("📡 Signal Comparison (Predictions)")
    print(f"{'='*60}")

    all_ok = True
    for d in trade_dates:
        bp = base_dir / "experiments" / "alpha_v1_shadow_predictions" / f"predictions_{d}.csv"
        cp = cand_dir / "experiments" / "alpha_v1_shadow_predictions" / f"predictions_{d}.csv"
        if not compare_csv(f"predictions_{d}", bp, cp, tolerances={"score": 1e-6}):
            all_ok = False
    return all_ok


def compare_plans(base_dir: Path, cand_dir: Path, trade_dates: list[str]) -> bool:
    print(f"\n{'='*60}")
    print("📋 Order Intent Comparison")
    print(f"{'='*60}")

    all_ok = True
    for d in trade_dates:
        plan_rel = f"experiments/alpha_v1_daily/{d}/plan"
        for fname in ("order_intents.csv", "target_weights.csv", "rebalance_audit.csv"):
            bp = base_dir / plan_rel / fname
            cp = cand_dir / plan_rel / fname
            tols = {
                "target_weight": 1e-9,
                "target_value": 1e-4,
                "diff_value": 1e-4,
            }
            if not compare_csv(f"{d}/plan/{fname}", bp, cp, tolerances=tols):
                all_ok = False
    return all_ok


def compare_executions(base_dir: Path, cand_dir: Path, trade_dates: list[str]) -> bool:
    print(f"\n{'='*60}")
    print("⚙️  Execution Comparison")
    print(f"{'='*60}")

    all_ok = True
    for d in trade_dates:
        exec_rel = f"experiments/alpha_v1_daily/{d}/execution"
        for fname in ("ledger_rows.csv", "positions_before.csv", "positions_after.csv"):
            bp = base_dir / exec_rel / fname
            cp = cand_dir / exec_rel / fname
            if not compare_csv(
                f"{d}/execution/{fname}", bp, cp,
                tolerances={"price": 1e-4, "amount": 1e-4, "market_value": 1e-4, "cost_price": 1e-4},
                skip_columns=["created_at"],
            ):
                all_ok = False
    return all_ok


def compare_artifacts(base_dir: Path, cand_dir: Path, trade_dates: list[str]) -> bool:
    """Compare ADR-007 sidecar artifacts if present."""
    print(f"\n{'='*60}")
    print("📦 ADR-007 Sidecar Comparison")
    print(f"{'='*60}")

    all_ok = True
    for d in trade_dates:
        daily_rel = f"experiments/alpha_v1_daily/{d}"
        for fname in ("signal-artifact.csv", "order-intent-artifact.csv",
                       "execution-artifact.csv", "portfolio-snapshot.csv"):
            bp = base_dir / daily_rel / fname
            cp = cand_dir / daily_rel / fname
            if not bp.exists() and not cp.exists():
                continue
            if not compare_csv(f"{d}/{fname}", bp, cp):
                all_ok = False
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare weekly replay outputs between branches")
    parser.add_argument("--baseline", required=True, help="Path to main branch project root")
    parser.add_argument("--candidate", required=True, help="Path to PR branch project root")
    parser.add_argument("--trade-dates", required=True, help="Comma-separated trade dates, e.g. 2026-05-18,2026-05-19,...")
    args = parser.parse_args()

    base_dir = Path(args.baseline)
    cand_dir = Path(args.candidate)

    trade_dates = [d.strip() for d in args.trade_dates.split(",") if d.strip()]
    print(f"Trade dates: {trade_dates}")
    print(f"Baseline: {base_dir}")
    print(f"Candidate: {cand_dir}")

    results: list[tuple[str, bool]] = []

    ok = compare_predictions(base_dir, cand_dir, trade_dates)
    results.append(("Predictions", ok))

    ok = compare_plans(base_dir, cand_dir, trade_dates)
    results.append(("Order Intents", ok))

    ok = compare_executions(base_dir, cand_dir, trade_dates)
    results.append(("Executions", ok))

    ok = compare_artifacts(base_dir, cand_dir, trade_dates)
    results.append(("ADR-007 Sidecars", ok))

    # Summary
    print(f"\n{'='*60}")
    print("📊 SUMMARY")
    print(f"{'='*60}")
    all_pass = True
    for category, ok in results:
        status = PASS if ok else FAIL
        print(f"  {status}: {category}")
        if not ok:
            all_pass = False

    if all_pass:
        print(f"\n{PASS}: All comparisons passed — replay matches baseline.\n")
        sys.exit(0)
    else:
        print(f"\n{FAIL}: One or more comparisons failed — semantics may have changed.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
