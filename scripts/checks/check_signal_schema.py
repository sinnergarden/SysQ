#!/usr/bin/env python3
"""Validate signal parquet/csv schema.

Required columns: trade_date, data_date, instrument, signal_id,
signal_run_id, score.
Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_COLUMNS = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
OPTIONAL_COLUMNS = {
    "model_id", "model_version", "feature_set_id", "label_id",
    "universe", "score_raw", "score_rank", "score_z",
    "is_valid", "invalid_reason",
}


def _load_dataframe(path: Path) -> tuple | None:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        import pandas as pd
        return pd.read_csv(path), "csv"
    elif suffix == ".parquet":
        try:
            import pandas as pd
            return pd.read_parquet(path), "parquet"
        except ImportError:
            return None
    return None


def check_signal_schema(path: Path) -> dict:
    if path.is_dir():
        files = sorted(path.iterdir())
    else:
        files = [path]

    result = {
        "status": "passed",
        "checked_files": 0,
        "checked_rows": 0,
        "missing_columns": [],
        "parquet_skipped": [],
        "errors": [],
    }

    for f in files:
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix not in (".csv", ".parquet"):
            continue

        loaded = _load_dataframe(f)
        if loaded is None:
            if suffix == ".parquet":
                result["parquet_skipped"].append(str(f))
                result["errors"].append(
                    f"{f}: parquet dependency not available (install pyarrow or fastparquet)"
                )
            else:
                result["errors"].append(f"{f}: unsupported format")
            continue

        df, fmt = loaded
        result["checked_files"] += 1
        result["checked_rows"] += len(df)

        columns = set(df.columns)
        missing = REQUIRED_COLUMNS - columns
        if missing:
            result["missing_columns"].extend(
                f"{f}: missing {m}" for m in sorted(missing)
            )

    if result["missing_columns"]:
        result["status"] = "failed"
    if result["errors"] and not result["missing_columns"]:
        result["status"] = "degraded"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate signal parquet/csv schema"
    )
    parser.add_argument("--path", required=True, help="File or directory to check")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        result = {"status": "failed", "error": f"Path not found: {path}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    result = check_signal_schema(path)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed", "degraded") else 1)


if __name__ == "__main__":
    main()
