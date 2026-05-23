"""Weekly replay comparison helpers for the alpha_v1 pipeline.

Extracted from scripts/dev/compare_weekly_replay.py for Phase 1.5
boundary refactor.
"""

from __future__ import annotations

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
        print(
            f"  {FAIL}: {label} — shape mismatch: baseline {bdf.shape} vs candidate {cdf.shape}"
        )
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

        if np.issubdtype(bvals.dtype, np.number) and np.issubdtype(
            cvals.dtype, np.number
        ):
            mismatch = ~np.isclose(
                bvals, cvals, rtol=1e-9, atol=tolerances.get(col, 1e-9)
            )
        else:
            mismatch = bvals != cvals

        if mismatch.any():
            indices = np.where(mismatch)[0]
            bad_count = len(indices)
            all_ok = False
            if bad_count <= 5:
                for idx in indices[:5]:
                    print(
                        f"    row {idx}: {label}.{col} baseline={bvals[idx]} candidate={cvals[idx]}"
                    )
            else:
                for idx in indices[:3]:
                    print(
                        f"    row {idx}: {label}.{col} baseline={bvals[idx]} candidate={cvals[idx]}"
                    )
                print(f"    ... and {bad_count - 3} more mismatches")

    if all_ok:
        print(f"  {PASS}: {label} ({len(bdf)} rows)")
    else:
        print(f"  {FAIL}: {label} — see mismatches above")
    return all_ok


def compare_predictions(
    base_dir: Path, cand_dir: Path, trade_dates: list[str]
) -> bool:
    """Compare prediction CSVs for a list of trade dates."""
    print(f"\n{'=' * 60}")
    print("📡 Signal Comparison (Predictions)")
    print(f"{'=' * 60}")

    all_ok = True
    for d in trade_dates:
        bp = (
            base_dir
            / "experiments"
            / "alpha_v1_shadow_predictions"
            / f"predictions_{d}.csv"
        )
        cp = (
            cand_dir
            / "experiments"
            / "alpha_v1_shadow_predictions"
            / f"predictions_{d}.csv"
        )
        if not compare_csv(f"predictions_{d}", bp, cp, tolerances={"score": 1e-6}):
            all_ok = False
    return all_ok


def compare_plans(
    base_dir: Path, cand_dir: Path, trade_dates: list[str]
) -> bool:
    """Compare order intent / target weight CSVs for a list of trade dates."""
    print(f"\n{'=' * 60}")
    print("📋 Order Intent Comparison")
    print(f"{'=' * 60}")

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


def compare_executions(
    base_dir: Path, cand_dir: Path, trade_dates: list[str]
) -> bool:
    """Compare execution / position CSVs for a list of trade dates."""
    print(f"\n{'=' * 60}")
    print("⚙️  Execution Comparison")
    print(f"{'=' * 60}")

    all_ok = True
    for d in trade_dates:
        exec_rel = f"experiments/alpha_v1_daily/{d}/execution"
        for fname in (
            "ledger_rows.csv",
            "positions_before.csv",
            "positions_after.csv",
        ):
            bp = base_dir / exec_rel / fname
            cp = cand_dir / exec_rel / fname
            if not compare_csv(
                f"{d}/execution/{fname}",
                bp,
                cp,
                tolerances={
                    "price": 1e-4,
                    "amount": 1e-4,
                    "market_value": 1e-4,
                    "cost_price": 1e-4,
                },
                skip_columns=["created_at"],
            ):
                all_ok = False
    return all_ok


def compare_artifacts(
    base_dir: Path, cand_dir: Path, trade_dates: list[str]
) -> bool:
    """Compare ADR-007 sidecar artifacts if present."""
    print(f"\n{'=' * 60}")
    print("📦 ADR-007 Sidecar Comparison")
    print(f"{'=' * 60}")

    all_ok = True
    for d in trade_dates:
        daily_rel = f"experiments/alpha_v1_daily/{d}"
        for fname in (
            "signal-artifact.csv",
            "order-intent-artifact.csv",
            "execution-artifact.csv",
            "portfolio-snapshot.csv",
        ):
            bp = base_dir / daily_rel / fname
            cp = cand_dir / daily_rel / fname
            if not bp.exists() and not cp.exists():
                continue
            if not compare_csv(f"{d}/{fname}", bp, cp):
                all_ok = False
    return all_ok
