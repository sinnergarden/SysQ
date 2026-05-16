#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore


PLAN_FIELDS = [
    "symbol",
    "raw_last_date_before",
    "target_start_date",
    "target_end_date",
    "rows_before",
    "rows_after",
    "rows_added",
    "status",
    "error",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _raw_stat(store: StockDataStore, symbol: str) -> tuple[int, str | None]:
    df = store.load_daily(symbol)
    if df is None or df.empty or "trade_date" not in df.columns:
        return 0, None
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    if dates.empty:
        return int(len(df)), None
    return int(len(df)), dates.max().strftime("%Y-%m-%d")


def run_raw_full_update(
    *,
    base_dir: str | Path,
    universe: str = "csi800",
    start_date: str = "2025-01-01",
    end_date: str = "2026-04-30",
    batch_size: int = 50,
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_full_rebuild",
    apply: bool = False,
    max_failure_ratio: float = 0.05,
) -> dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = (base_dir / output_dir).resolve()

    collector = TushareCollector()
    store = StockDataStore()
    symbols = sorted(set(str(symbol) for symbol in collector.get_universe(universe)))
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rows_before, raw_last_before = _raw_stat(store, symbol)
        rows.append(
            {
                "symbol": symbol,
                "raw_last_date_before": raw_last_before,
                "target_start_date": start_date,
                "target_end_date": end_date,
                "rows_before": rows_before,
                "rows_after": rows_before,
                "rows_added": 0,
                "status": "planned",
                "error": "",
            }
        )
    row_map = {row["symbol"]: row for row in rows}
    _write_csv(output_dir / "raw_full_update_plan.csv", rows, PLAN_FIELDS)

    failed_symbols: list[str] = []
    if apply:
        for idx in range(0, len(symbols), batch_size):
            batch = symbols[idx : idx + batch_size]
            try:
                collector._update_batch_by_year(
                    batch,
                    ",".join(batch),
                    start_date.replace("-", ""),
                    end_date.replace("-", ""),
                    include_basic=True,
                    include_limit=True,
                    include_adj=True,
                    include_moneyflow=True,
                )
                for symbol in batch:
                    rows_after, raw_last_after = _raw_stat(store, symbol)
                    row = row_map[symbol]
                    row["rows_after"] = rows_after
                    row["rows_added"] = max(rows_after - int(row["rows_before"]), 0)
                    row["status"] = "success"
                    row["error"] = ""
                    row["raw_last_date_before"] = row["raw_last_date_before"]
                    if raw_last_after is not None:
                        row["raw_last_date_after"] = raw_last_after
            except Exception as exc:
                for symbol in batch:
                    row = row_map[symbol]
                    row["status"] = "error"
                    row["error"] = str(exc)
                    failed_symbols.append(symbol)
                if len(failed_symbols) / len(symbols) > max_failure_ratio:
                    break
    summary = {
        "universe": universe,
        "symbol_count": len(symbols),
        "start_date": start_date,
        "end_date": end_date,
        "apply": apply,
        "success_count": int(sum(1 for row in rows if row["status"] == "success")),
        "error_count": int(sum(1 for row in rows if row["status"] == "error")),
        "planned_count": int(sum(1 for row in rows if row["status"] == "planned")),
        "failure_ratio": float(sum(1 for row in rows if row["status"] == "error") / len(symbols)) if symbols else 0.0,
        "stopped_due_to_failure_threshold": bool(apply and symbols and (sum(1 for row in rows if row["status"] == "error") / len(symbols)) > max_failure_ratio),
    }
    _write_csv(output_dir / "raw_full_update_plan.csv", rows, PLAN_FIELDS)
    _write_json(output_dir / "raw_full_update_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or run a full targeted raw update for a universe.")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT))
    parser.add_argument("--universe", default="csi800")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/csi800_full_rebuild")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-failure-ratio", type=float, default=0.05)
    args = parser.parse_args()

    result = run_raw_full_update(
        base_dir=args.base_dir,
        universe=args.universe,
        start_date=args.start_date,
        end_date=args.end_date,
        batch_size=args.batch_size,
        output_dir=args.output_dir,
        apply=args.apply,
        max_failure_ratio=args.max_failure_ratio,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
