#!/usr/bin/env python3
"""Validate experiment index directory structure and cross-references.

Checks that an experiment index (under ``data/research/experiments/<id>/``)
has the required index files and that signal eval / backtest entries are
internally consistent.

Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_INDEX_FILES = {
    "signal_run_index.csv",
    "signal_eval_index.csv",
    "backtest_index.csv",
    "manifest.json",
}

SIGNAL_RUN_COLS = {"signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"}
SIGNAL_EVAL_COLS = {"signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"}
BACKTEST_COLS = {"strategy_template_id", "signal_id", "signal_run_id", "start_date", "end_date",
                 "initial_capital", "final_value", "total_return", "trading_day_count"}


def _load_csv(path: Path) -> list[dict] | None:
    try:
        import pandas as pd
        df = pd.read_csv(path)
        return df.to_dict(orient="records")
    except Exception:
        return None


def check_experiment_index(exp_dir: Path, strict: bool = False) -> dict:
    # Always initialise all row containers so cross-reference code never
    # references an undefined variable.
    sri_rows: list[dict] = []
    sei_rows: list[dict] = []
    bti_rows: list[dict] = []

    result = {
        "status": "passed",
        "experiment_id": exp_dir.name,
        "missing_files": [],
        "empty_files": [],
        "signal_run_count": 0,
        "signal_eval_count": 0,
        "backtest_count": 0,
        "signal_run_missing_cols": [],
        "signal_eval_missing_cols": [],
        "backtest_missing_cols": [],
        "warnings": [],
        "errors": [],
    }

    # Check required files exist
    for fname in REQUIRED_INDEX_FILES:
        fpath = exp_dir / fname
        if not fpath.exists():
            result["missing_files"].append(fname)
            continue
        if fpath.stat().st_size == 0:
            result["empty_files"].append(fname)

    # Load manifest
    manifest_path = exp_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            result["experiment_id"] = manifest.get("experiment_id", exp_dir.name)
            if strict and "experiment_id" not in manifest:
                result["errors"].append("strict: manifest missing experiment_id")
        except (json.JSONDecodeError, Exception):
            result["warnings"].append("manifest.json: unreadable or invalid JSON")

    # Check signal_run_index.csv
    sri_path = exp_dir / "signal_run_index.csv"
    if sri_path.exists():
        loaded = _load_csv(sri_path)
        if loaded is None:
            result["errors"].append("signal_run_index.csv: unreadable")
        else:
            sri_rows = loaded
            result["signal_run_count"] = len(sri_rows)
            if sri_rows:
                cols = set(sri_rows[0].keys())
                missing = SIGNAL_RUN_COLS - cols
                if missing:
                    result["signal_run_missing_cols"] = sorted(missing)
                    if strict:
                        result["errors"].append(f"strict: signal_run_index missing cols: {sorted(missing)}")
    else:
        if strict:
            result["errors"].append("strict: signal_run_index.csv missing")

    # Check signal_eval_index.csv
    sei_path = exp_dir / "signal_eval_index.csv"
    if sei_path.exists():
        loaded = _load_csv(sei_path)
        if loaded is None:
            result["errors"].append("signal_eval_index.csv: unreadable")
        else:
            sei_rows = loaded
            result["signal_eval_count"] = len(sei_rows)
            if sei_rows:
                cols = set(sei_rows[0].keys())
                missing = SIGNAL_EVAL_COLS - cols
                if missing:
                    result["signal_eval_missing_cols"] = sorted(missing)
                    if strict:
                        result["errors"].append(f"strict: signal_eval_index missing cols: {sorted(missing)}")
    else:
        if strict:
            result["errors"].append("strict: signal_eval_index.csv missing")

    # Check backtest_index.csv
    bti_path = exp_dir / "backtest_index.csv"
    if bti_path.exists():
        loaded = _load_csv(bti_path)
        if loaded is None:
            result["errors"].append("backtest_index.csv: unreadable")
        else:
            bti_rows = loaded
            result["backtest_count"] = len(bti_rows)
            if bti_rows:
                cols = set(bti_rows[0].keys())
                missing = BACKTEST_COLS - cols
                if missing:
                    result["backtest_missing_cols"] = sorted(missing)
                    if strict:
                        result["errors"].append(f"strict: backtest_index missing cols: {sorted(missing)}")
    else:
        if strict:
            result["errors"].append("strict: backtest_index.csv missing")

    # -- strict-only checks ------------------------------------------------
    if strict:
        # Empty artifact path
        for row in sri_rows:
            if not row.get("path", ""):
                result["errors"].append("strict: signal_run has empty artifact path")
        for row in sei_rows:
            if not row.get("path", ""):
                result["errors"].append("strict: signal_eval has empty artifact path")
        for row in bti_rows:
            if not row.get("path", ""):
                result["errors"].append("strict: backtest has empty artifact path")

        # Zero-row eval or backtest index
        if result["signal_eval_count"] == 0:
            result["errors"].append("strict: signal_eval_index is empty")
        if result["backtest_count"] == 0:
            result["errors"].append("strict: backtest_index is empty")

        # Missing critical metadata in manifest
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                for key in ("experiment_id", "created_at"):
                    if key not in manifest:
                        result["errors"].append(f"strict: manifest missing {key}")
            except Exception:
                result["errors"].append("strict: manifest unreadable for metadata check")
    # ----------------------------------------------------------------------

    # Cross-reference: signal_run_ids in eval should exist in run index
    if sri_path.exists() and sei_path.exists():
        run_ids = {(r.get("signal_id", ""), r.get("signal_run_id", "")) for r in sri_rows}
        for r in sei_rows:
            key = (r.get("signal_id", ""), r.get("signal_run_id", ""))
            if key not in run_ids and key != ("", ""):
                msg = f"signal_eval references signal_run not in index: {key[0]}:{key[1]}"
                if strict:
                    result["errors"].append(msg)
                else:
                    result["warnings"].append(msg)

    # Cross-reference: signal_run_ids in backtest should exist in run index
    if sri_path.exists() and bti_path.exists():
        run_ids = {(r.get("signal_id", ""), r.get("signal_run_id", "")) for r in sri_rows}
        for r in bti_rows:
            key = (r.get("signal_id", ""), r.get("signal_run_id", ""))
            if key not in run_ids and key != ("", ""):
                msg = f"backtest references signal_run not in index: {key[0]}:{key[1]}"
                if strict:
                    result["errors"].append(msg)
                else:
                    result["warnings"].append(msg)

    # Determine overall status
    if result["missing_files"] or result["errors"]:
        result["status"] = "failed"
    elif result["signal_run_missing_cols"] or result["signal_eval_missing_cols"] or result["backtest_missing_cols"]:
        result["status"] = "degraded"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate experiment index directory structure"
    )
    parser.add_argument("--path", required=True, help="Experiment directory path")
    parser.add_argument("--strict", action="store_true", help="Enable strict validation mode")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        result = {"status": "failed", "error": f"Path not found: {path}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)
    if not path.is_dir():
        result = {"status": "failed", "error": f"Not a directory: {path}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    result = check_experiment_index(path, strict=args.strict)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed", "degraded") else 1)


if __name__ == "__main__":
    main()
