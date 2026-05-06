from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.data.collector import TushareCollector
from qsys.ops.instrument_coverage import read_calendar_summary, read_instrument_file

BOOTSTRAP_SUPPORTED_UNIVERSES = {"csi300", "csi500", "csi800"}


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


def _bootstrap_universe_registry_rows(*, adapter: QlibAdapter, universe: str, as_of_date: str) -> tuple[list[str], list[dict[str, str]]]:
    collector = TushareCollector()
    symbols = sorted(set(str(symbol) for symbol in collector.get_universe(universe)))
    calendar_summary = read_calendar_summary(adapter)
    calendar_first_date = calendar_summary.get("calendar_first_date") or pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    calendar_last_date = calendar_summary.get("calendar_last_date") or pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    end_date = min(pd.Timestamp(as_of_date), pd.Timestamp(calendar_last_date)).strftime("%Y-%m-%d")
    rows = [
        {
            "instrument": symbol,
            "start_date": calendar_first_date,
            "end_date": end_date,
        }
        for symbol in symbols
    ]
    return symbols, rows


def _write_instrument_registry(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["instrument", "start_date", "end_date"], delimiter="\t")
        for row in rows:
            writer.writerow(row)
    return path


def build_universe_snapshot(
    *,
    adapter: QlibAdapter,
    universe: str,
    as_of_date: str,
    output_dir: Path,
    apply: bool = False,
) -> tuple[list[str], dict[str, Any], Path, Path]:
    instrument_path = adapter.qlib_dir / "instruments" / f"{universe}.txt"
    instrument_df = read_instrument_file(instrument_path)
    source = "existing_registry"
    registry_written = False
    bootstrap_supported = universe in BOOTSTRAP_SUPPORTED_UNIVERSES

    if not instrument_df.empty:
        symbols = sorted(instrument_df["instrument"].astype(str).unique().tolist())
        rows = [{"symbol": symbol, "universe": universe, "as_of_date": as_of_date} for symbol in symbols]
    elif bootstrap_supported:
        symbols, bootstrap_rows = _bootstrap_universe_registry_rows(adapter=adapter, universe=universe, as_of_date=as_of_date)
        rows = [{"symbol": symbol, "universe": universe, "as_of_date": as_of_date} for symbol in symbols]
        source = "bootstrap_preview"
        if apply:
            _write_instrument_registry(instrument_path, bootstrap_rows)
            registry_written = True
            source = "bootstrapped_registry"
    else:
        symbols = []
        rows = []
        source = "missing_registry"

    summary = {
        "universe": universe,
        "symbol_count": len(symbols),
        "source": source,
        "pit_constituent_accurate": False,
        "as_of_date": as_of_date,
        "instrument_path": str(instrument_path),
        "instrument_file_exists_before": bool(not instrument_df.empty),
        "bootstrap_supported": bootstrap_supported,
        "instrument_file_written": registry_written,
    }
    csv_path = _write_csv(output_dir / "universe_snapshot.csv", rows, ["symbol", "universe", "as_of_date"])
    summary_path = _write_json(output_dir / "universe_summary.json", summary)
    return symbols, summary, csv_path, summary_path
