#!/usr/bin/env python3
"""Replay alpha_v1 shadow rebalance for 5 days (2026-05-18 → 2026-05-22).

Usage:
    python scripts/replay_alpha_v1_5day.py <output_dir> [--base-dir <base_dir>]

Compares output between branches. Trading-critical artifacts should be identical
except for run_id and timestamps.
"""
from __future__ import annotations

import warnings
warnings.warn(
    "DEPRECATED: replay_alpha_v1_5day.py is a one-time comparison tool. "
    "Use UC-7 backtest_from_signal.py for reproducible backtests instead. "
    "Scheduled for removal.",
    DeprecationWarning, stacklevel=2,
)

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.ops.shadow_rebalance import (
    DEFAULT_INITIAL_CAPITAL,
    ShadowRebalanceArtifacts,
    run_shadow_rebalance,
)

DATES = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]
PREDICTIONS_DIR = PROJECT_ROOT / "experiments" / "alpha_v1_shadow_predictions"

# ── Artifact tracking ─────────────────────────────────────────────────────────

ARTIFACT_FILES = [
    "target_weights.csv",
    "order_intents.csv",
    "rebalance_audit.csv",
    "execution_summary.json",
    "account_after.json",
    "positions_after.csv",
]

SHADOW_STATE_FILES = [
    "account.json",
    "positions.csv",
    "ledger.csv",
]


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def df_hash(path: Path) -> str:
    return hashlib.sha256(pd.read_csv(path).to_csv(index=False).encode()).hexdigest()


def format_diff(diffs: list[str]) -> str:
    if not diffs:
        return "  ✅ IDENTICAL"
    return "\n".join(f"  🔴 {d}" for d in diffs)


def make_default_account(base_dir: Path) -> None:
    """Create a fresh initial shadow account."""
    shadow_dir = base_dir / "shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        shadow_dir / "account.json",
        {
            "trade_date": None,
            "cash": DEFAULT_INITIAL_CAPITAL,
            "available_cash": DEFAULT_INITIAL_CAPITAL,
            "market_value": 0.0,
            "total_value": DEFAULT_INITIAL_CAPITAL,
            "last_run_id": None,
            "initial_capital": DEFAULT_INITIAL_CAPITAL,
        },
    )
    cols = ["instrument", "quantity", "sellable_quantity", "cost_price", "last_price", "market_value"]
    pd.DataFrame(columns=cols).to_csv(shadow_dir / "positions.csv", index=False)
    # header-only ledger
    (shadow_dir / "ledger.csv").write_text(
        "run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason\n",
        encoding="utf-8",
    )


def run_one_day(
    base_dir: Path, date: str, run_idx: int, output_dir: Path,
) -> Path:
    """Run alpha_v1 shadow rebalance for a single date. Returns the output dir."""
    run_id = f"replay_alpha_v1_{date}"
    day_out = output_dir / date
    day_out.mkdir(parents=True, exist_ok=True)

    predictions_path = PREDICTIONS_DIR / f"predictions_{date}.csv"
    if not predictions_path.exists():
        print(f"  ⚠️  Predictions not found: {predictions_path}, skipping")
        return day_out

    # Copy predictions to output for reference
    shutil.copy2(predictions_path, day_out / "predictions.csv")

    # Save pre-rebalance shadow state
    shadow_dir = base_dir / "shadow"
    for fname in SHADOW_STATE_FILES:
        src = shadow_dir / fname
        if src.exists():
            shutil.copy2(src, day_out / f"before_{fname}")

    # Run the rebalance
    # Note: main branch has a pre-existing bug (missing ledger_rows_path in
    # ShadowRebalanceArtifacts constructor). All artifact files are written
    # before the constructor call, so we catch the TypeError and continue.
    try:
        artifacts = run_shadow_rebalance(
            base_dir=base_dir,
            run_id=run_id,
            trade_date=date,
            predictions_path=predictions_path,
            output_dir=day_out,
            strategy_id="replay_alpha_v1",
            strategy_version="1.0",
            portfolio_method="rank_weight_buffer",
        )
    except TypeError as exc:
        if "ledger_rows_path" not in str(exc):
            raise
        print(f"  ⚠️  Pre-existing bug on main branch (missing ledger_rows_path). Artifacts still written.")
        artifacts = None

    # Save rebalance outputs to day dir (run_shadow_rebalance already writes there)
    for fname in SHADOW_STATE_FILES:
        src = shadow_dir / fname
        if src.exists():
            shutil.copy2(src, day_out / f"after_{fname}")

    return day_out


def run_replay(base_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Run 5-day alpha_v1 replay. Returns summary dict keyed by date."""
    make_default_account(base_dir)
    results: dict[str, Any] = {}

    for idx, date in enumerate(DATES):
        print(f"\n{'='*60}")
        print(f"Day {idx+1}/{len(DATES)}: {date}")
        print(f"{'='*60}")
        day_out = run_one_day(base_dir, date, idx, output_dir)
        results[date] = {"output_dir": str(day_out)}
        # Verify artifacts exist
        for fname in ARTIFACT_FILES:
            path = day_out / fname
            results[date][fname] = {
                "exists": path.exists(),
                "sha256": file_sha256(path) if path.exists() else None,
            }
            status = "✓" if path.exists() else "✗"
            print(f"  {status} {fname}")

    return results


def compare_runs(
    baseline_dir: Path, target_dir: Path, label: str = "",
) -> list[str]:
    """Compare two replay output trees. Returns list of diffs."""
    diffs: list[str] = []
    for date in DATES:
        base_day = baseline_dir / date
        target_day = target_dir / date

        if not base_day.exists():
            diffs.append(f"{date}: baseline day dir missing")
            continue
        if not target_day.exists():
            diffs.append(f"{date}: target day dir missing")
            continue

        for fname in ARTIFACT_FILES + [f"before_{s}" for s in SHADOW_STATE_FILES] + [f"after_{s}" for s in SHADOW_STATE_FILES]:
            base_path = base_day / fname
            target_path = target_day / fname

            if not base_path.exists() and not target_path.exists():
                continue
            if not base_path.exists():
                diffs.append(f"{date}/{fname}: missing from baseline")
                continue
            if not target_path.exists():
                diffs.append(f"{date}/{fname}: missing from target")
                continue

            base_hash = file_sha256(base_path) if base_path.suffix != ".csv" else df_hash(base_path)
            target_hash = file_sha256(target_path) if target_path.suffix != ".csv" else df_hash(target_path)

            if base_hash != target_hash:
                if fname == "execution_summary.json":
                    # Deep compare JSON
                    bj = json.loads(base_path.read_text())
                    tj = json.loads(target_path.read_text())
                    differing_keys = []
                    for k in sorted(set(list(bj.keys()) + list(tj.keys()))):
                        bv = bj.get(k)
                        tv = tj.get(k)
                        if bv != tv:
                            differing_keys.append(f"    {k}: baseline={bv!r} target={tv!r}")
                    diffs.append(f"{date}/{fname}: content differs")
                    for dk in differing_keys:
                        diffs.append(dk)
                else:
                    diffs.append(f"{date}/{fname}: content differs (hash mismatch)")

    return diffs


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Replay alpha_v1 5-day shadow rebalance")
    parser.add_argument("output_dir", type=Path, help="Where to write replay outputs")
    parser.add_argument("--base-dir", type=Path, default=PROJECT_ROOT, help="Project root (default: auto-detect)")
    parser.add_argument("--compare", type=Path, default=None, help="Compare against a previous replay output dir")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp)
        results = run_replay(base_dir, args.output_dir)

    # Write summary
    summary_path = args.output_dir / "replay_summary.json"
    _write_json(summary_path, {
        "dates": DATES,
        "branch": subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=PROJECT_ROOT).decode().strip(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT).decode().strip(),
        "timestamp": datetime.now().isoformat(),
        "results": results,
    })
    print(f"\nSummary written: {summary_path}")

    if args.compare:
        print(f"\n{'='*60}")
        print("Comparing against baseline:", args.compare)
        print(f"{'='*60}")
        diffs = compare_runs(args.compare, args.output_dir)
        if diffs:
            print("\nDIFFERENCES FOUND:")
            for d in diffs:
                print(d)
        else:
            print("\n✅ ALL ARTIFACTS IDENTICAL")
        diff_path = args.output_dir / "replay_diffs.txt"
        diff_path.write_text("\n".join(diffs) + "\n" if diffs else "ALL IDENTICAL\n")
        print(f"Diff log: {diff_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
