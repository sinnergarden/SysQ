from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import read_instrument_file


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def build_universe_snapshot(*, adapter: QlibAdapter, universe: str, as_of_date: str, output_dir: Path) -> tuple[list[str], dict[str, Any], Path, Path]:
    instrument_path = adapter.qlib_dir / "instruments" / f"{universe}.txt"
    instrument_df = read_instrument_file(instrument_path)
    symbols = sorted(instrument_df["instrument"].astype(str).unique().tolist()) if not instrument_df.empty else []
    rows = [{"symbol": symbol, "universe": universe, "as_of_date": as_of_date} for symbol in symbols]
    summary = {
        "universe": universe,
        "symbol_count": len(symbols),
        "source": "existing_registry",
        "pit_constituent_accurate": False,
        "as_of_date": as_of_date,
    }
    csv_path = _write_csv(output_dir / "universe_snapshot.csv", rows, ["symbol", "universe", "as_of_date"])
    summary_path = _write_json(output_dir / "universe_summary.json", summary)
    return symbols, summary, csv_path, summary_path
