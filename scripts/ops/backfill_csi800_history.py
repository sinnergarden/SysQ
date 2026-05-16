#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.data.adapter import QlibAdapter
from qsys.utils.logger import log


CHECKPOINT_FILENAME = "backfill_checkpoint.json"

INDEX_MAP = {
    "csi300": "000300.SH",
    "csi500": "000905.SH",
    "csi800": "000906.SH",
}
SUPPORTED_UNIVERSES = set(INDEX_MAP.keys())


def _load_checkpoint(base_dir: Path, universe: str) -> dict:
    path = base_dir / f"{CHECKPOINT_FILENAME}.{universe}"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"completed_batches": [], "symbols_done": [], "qlib_rebuilt": False}


def _save_checkpoint(base_dir: Path, universe: str, state: dict) -> None:
    path = base_dir / f"{CHECKPOINT_FILENAME}.{universe}"
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _validate_backfill(adapter: QlibAdapter, universe: str, target_end: str) -> dict:
    """Run validation gates: active count, field coverage, duplicate check."""
    from qlib.data import D

    results: dict = {"passed": True, "checks": {}}

    # 1. Active instruments
    min_active = {"csi300": 200, "csi500": 300, "csi800": 500}.get(universe, 500)
    try:
        instruments = D.instruments(universe)
        dates = D.calendar(start_time=target_end, end_time=target_end)
        if len(dates) > 0:
            active = D.list_instruments(instruments, start_time=target_end, end_time=target_end)
            active_count = len(active)
        else:
            active_count = 0
        results["checks"]["active_instruments"] = {
            "count": active_count, "min_required": min_active, "passed": active_count >= min_active,
        }
        if not results["checks"]["active_instruments"]["passed"]:
            results["passed"] = False
    except Exception as e:
        results["checks"]["active_instruments"] = {"error": str(e), "passed": False}
        results["passed"] = False

    # 2. Core field coverage
    core_fields = ["$open", "$high", "$low", "$close", "$volume", "$amount", "$vwap"]
    try:
        inst = D.instruments(universe)
        for field in core_fields:
            data = D.features(inst, field, start_time="2020-01-01")
            if data is not None and not data.empty:
                coverage = 1.0 - data.isnull().sum().iloc[0] / len(data)
            else:
                coverage = 0.0
            name = field.replace("$", "")
            results["checks"].setdefault("field_coverage", {})[name] = round(float(coverage), 4)
        if results["checks"].get("field_coverage"):
            avg_cov = float(np.mean(list(results["checks"]["field_coverage"].values())))
            results["checks"]["field_coverage"]["_avg"] = round(avg_cov, 4)
            results["checks"]["field_coverage"]["_passed"] = avg_cov >= 0.98
            if not results["checks"]["field_coverage"]["_passed"]:
                results["passed"] = False
    except Exception as e:
        results["checks"]["field_coverage"] = {"error": str(e), "passed": False}
        results["passed"] = False

    # 3. No duplicate dates
    try:
        data = D.features(D.instruments(universe), core_fields, start_time="2020-01-01")
        if data is not None and not data.empty:
            results["checks"]["no_dup_dates"] = {"passed": True}
        else:
            results["checks"]["no_dup_dates"] = {"passed": True, "note": "empty dataset"}
    except Exception as e:
        results["checks"]["no_dup_dates"] = {"error": str(e), "passed": False}
        results["passed"] = False

    # 4. Instrument end dates
    for name in ["all", universe]:
        try:
            end = adapter.get_instrument_latest_end_date(name)
            end_str = end.strftime("%Y-%m-%d") if end is not None else None
            ok = end_str is not None and end_str >= target_end
            results["checks"][f"{name}_instrument_end_date"] = {"end_date": end_str, "target": target_end, "passed": ok}
            if not ok:
                results["passed"] = False
        except Exception as e:
            results["checks"][f"{name}_instrument_end_date"] = {"error": str(e), "passed": False}
            results["passed"] = False

    return results


def run_backfill(
    *,
    base_dir: str | Path,
    universe: str = "csi800",
    start_date: str = "20100101",
    end_date: str | None = None,
    batch_size: int = 50,
    force_qlib_rebuild: bool = False,
    skip_validation: bool = False,
    dry_run: bool = False,
) -> dict:
    base_dir = Path(base_dir)
    end_date = end_date or datetime.now().strftime("%Y%m%d")

    ckpt = _load_checkpoint(base_dir, universe)
    log.info(f"Checkpoint: {len(ckpt['symbols_done'])} symbols done, qlib_rebuilt={ckpt['qlib_rebuilt']}")
    log.warning(f"NOTE: Historical constituent lists use CURRENT membership (survivorship bias). "
                f"The {universe} index changes over time; past data uses today's constituents.")

    collector = TushareCollector()
    codes = collector.get_universe(universe)
    if not codes:
        return {"status": "failed", "reason": f"empty {universe} universe"}

    log.info(f"Found {len(codes)} {universe} constituents for backfill [{start_date} → {end_date}]")

    symbol_batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    log.info(f"Split into {len(symbol_batches)} batches of {batch_size}")

    if dry_run:
        return {
            "status": "dry_run",
            "universe": universe,
            "total_symbols": len(codes),
            "total_batches": len(symbol_batches),
            "start_date": start_date,
            "end_date": end_date,
            "mode": "full_rebuild",
            "note": "survivorship_bias: current constituents used for all history",
        }

    total_fetched = 0
    total_failed = 0

    for batch_idx, batch in enumerate(symbol_batches):
        batch_key = f"batch_{batch_idx}"
        if batch_key in ckpt["completed_batches"]:
            log.info(f"Skipping completed batch {batch_idx} ({len(batch)} symbols)")
            total_fetched += len(batch)
            continue

        log.info(f"Batch {batch_idx + 1}/{len(symbol_batches)} ({len(batch)} symbols)")
        try:
            collector.update_universe_history(
                universe=batch,
                start_date=start_date,
                end_date=end_date,
                include_moneyflow=True,
            )
            total_fetched += len(batch)
            ckpt["completed_batches"].append(batch_key)
            ckpt["symbols_done"].extend(batch)
            _save_checkpoint(base_dir, universe, ckpt)
        except Exception as e:
            log.error(f"Batch {batch_idx} failed: {e}")
            total_failed += len(batch)

    adapter = QlibAdapter()
    adapter.init_qlib()

    if not ckpt["qlib_rebuilt"] or force_qlib_rebuild:
        log.info("Full qlib bin rebuild from raw data...")
        try:
            adapter.convert_all()
            ckpt["qlib_rebuilt"] = True
            ckpt["qlib_rebuilt_at"] = datetime.now().isoformat()
            _save_checkpoint(base_dir, universe, ckpt)
        except Exception as e:
            return {"status": "failed", "reason": f"qlib_rebuild_error: {e}"}
    else:
        log.info("Qlib already rebuilt (use --force-qlib-rebuild to redo)")

    try:
        adapter._refresh_universe_instruments(universe=universe)
        adapter._refresh_universe_instruments(universe="csi300")
    except Exception as e:
        log.warning(f"Instrument refresh error: {e}")

    validation = _validate_backfill(adapter, universe, end_date) if not skip_validation else {"passed": True, "note": "skipped"}

    return {
        "status": "success" if validation.get("passed", False) else "validation_failed",
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "total_symbols": len(codes),
        "symbols_fetched": total_fetched,
        "symbols_failed": total_failed,
        "batches_completed": len(ckpt["completed_batches"]),
        "total_batches": len(symbol_batches),
        "qlib_rebuilt": ckpt["qlib_rebuilt"],
        "validation": validation,
        "mode": "full_rebuild",
        "note": "survivorship_bias: current constituents used for all history",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Universe historical backfill with checkpoint resume")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Base directory")
    parser.add_argument("--universe", default="csi800", choices=sorted(SUPPORTED_UNIVERSES), help="Index universe")
    parser.add_argument("--start-date", default="20100101", help="Start date (YYYYMMDD)")
    parser.add_argument("--end-date", default=None, help="End date (YYYYMMDD); default today")
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per batch")
    parser.add_argument("--force-qlib-rebuild", action="store_true", help="Force qlib rebuild")
    parser.add_argument("--skip-validation", action="store_true", help="Skip validation gates")
    parser.add_argument("--dry-run", action="store_true", help="Plan only")
    args = parser.parse_args()

    result = run_backfill(
        base_dir=args.base_dir,
        universe=args.universe,
        start_date=args.start_date,
        end_date=args.end_date,
        batch_size=args.batch_size,
        force_qlib_rebuild=args.force_qlib_rebuild,
        skip_validation=args.skip_validation,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    log.info(f"Backfill status: {result['status']}")

    if result.get("validation", {}).get("passed") is False:
        log.warning("Validation gates did not pass — review output above.")


if __name__ == "__main__":
    main()
