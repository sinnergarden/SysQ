#!/usr/bin/env python3
"""DuckDB query helper for research experiment result tables.

Usage::

    # Default summary: join evals + backtests, ordered by total_return desc
    python scripts/research/query_experiment_duckdb.py --experiment-dir data/research/experiments/real_cross_matrix_smoke

    # Custom SQL
    python scripts/research/query_experiment_duckdb.py --experiment-dir <dir> --sql "SELECT * FROM matrix_jobs"

The helper loads all experiment CSVs as DuckDB views and runs the query.

No persistent catalog.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEFAULT_SQL = """
SELECT
    e.signal_id,
    e.signal_run_id,
    e.label_id,
    e.rank_icir,
    b.strategy_template_id,
    b.total_return,
    b.final_value,
    b.trading_day_count
FROM signal_eval_index e
LEFT JOIN backtest_index b
    ON e.signal_id = b.signal_id
    AND e.signal_run_id = b.signal_run_id
ORDER BY b.total_return DESC NULLS LAST
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Query experiment results with DuckDB")
    parser.add_argument("--experiment-dir", required=True, help="Path to experiment directory")
    parser.add_argument("--sql", default=None, help="Custom SQL query (default: join evals + backtests)")
    args = parser.parse_args()

    exp_dir = Path(args.experiment_dir)
    if not exp_dir.exists():
        print(f"Experiment directory not found: {exp_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        import duckdb
    except ImportError:
        print("duckdb not installed. Install with: pip install duckdb", file=sys.stderr)
        sys.exit(1)

    con = duckdb.connect(":memory:")

    # Register CSV files as views
    csv_patterns = [
        "signal_run_index.csv",
        "signal_eval_index.csv",
        "backtest_index.csv",
        "matrix_jobs.csv",
        "cross_signal_index.csv",
    ]
    loaded = []
    for name in csv_patterns:
        path = exp_dir / name
        if path.exists():
            con.execute(f"CREATE VIEW {name.replace('.csv', '')} AS SELECT * FROM read_csv_auto('{path}')")
            loaded.append(name)

    if not loaded:
        print(f"No CSV files found in {exp_dir}", file=sys.stderr)
        sys.exit(1)

    sql = args.sql.strip() if args.sql else _DEFAULT_SQL
    try:
        result = con.execute(sql)
        rows = result.fetchall()
        desc = result.description
        if desc:
            widths = [len(c[0]) for c in desc]
            for row in rows:
                for i, val in enumerate(row):
                    widths[i] = max(widths[i], len(str(val)) if val is not None else 4)
            header = " | ".join(c[0].ljust(widths[i]) for i, c in enumerate(desc))
            sep = "-+-".join("-" * widths[i] for i in range(len(desc)))
            print(header)
            print(sep)
            for row in rows:
                vals = []
                for i, val in enumerate(row):
                    s = str(val) if val is not None else "NULL"
                    vals.append(s.ljust(widths[i]))
                print(" | ".join(vals))
        print(f"\n({len(rows)} rows)")
    except Exception as e:
        print(f"Query failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
