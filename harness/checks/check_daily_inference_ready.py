#!/usr/bin/env python3
"""Fail-closed readiness check for daily inference inputs."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.common.config import load_strategy_config
from qsys.signal.model_blend_inference import (
    InferenceContractError,
    load_model_lineage,
    load_open_dates,
    resolve_inference_dates,
    validate_inference_config,
)


def _normalise_date(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _raw_latest(project_root: Path) -> str | None:
    db_path = project_root / "data" / "meta.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT MAX(latest_date) FROM data_latest").fetchone()
        return _normalise_date(row[0] if row else None)
    except sqlite3.Error:
        return None


def _check_legacy_pointer(strategy_id: str, project_root: Path) -> tuple[bool, str]:
    pointer = (
        project_root / "artifacts" / "registry" / "models" / strategy_id / "shadow.json"
    )
    if pointer.exists():
        return True, f"Found explicit shadow pointer: {pointer}"
    legacy = project_root / "models" / "latest_shadow_model.json"
    if strategy_id == "alpha_v1" and legacy.exists():
        return True, f"Found legacy alpha_v1 pointer: {legacy}"
    return False, f"Missing explicit model bundle or pointer: {pointer}"


def check_inference_ready(
    trade_date: str,
    strategy_id: str,
    *,
    execution_date: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> list[tuple[str, bool, str]]:
    """Return ``(name, passed, detail)`` results for one inference request."""

    project_root = Path(project_root)
    results: list[tuple[str, bool, str]] = []
    signal_date = _normalise_date(trade_date)
    results.append(
        (
            "signal_date format",
            signal_date is not None,
            f"valid: {signal_date}" if signal_date else f"invalid: {trade_date}",
        )
    )
    results.append(("strategy_id non-empty", bool(strategy_id), strategy_id or "empty"))
    if not signal_date or not strategy_id:
        return results

    try:
        config = load_strategy_config(strategy_id, project_root)
    except (FileNotFoundError, ValueError) as exc:
        results.append(("strategy config", False, str(exc)))
        return results

    if not isinstance(config.get("inference"), dict):
        ok, detail = _check_legacy_pointer(strategy_id, project_root)
        results.append(("model pointer", ok, detail))
        return results

    try:
        settings = validate_inference_config(strategy_id, config, project_root)
        results.append(
            (
                "pinned model bundle",
                True,
                f"{settings['bundle_id']} ({settings['bundle_hash'][:12]})",
            )
        )
    except InferenceContractError as exc:
        results.append(("pinned model bundle", False, str(exc)))
        return results

    try:
        open_dates = load_open_dates(project_root)
        dates = resolve_inference_dates(
            signal_date,
            execution_date,
            open_dates,
            market_close_cutoff=settings["market_close_cutoff"],
            universe_snapshot_semantics=settings["universe_snapshot_semantics"],
        )
        results.append(
            (
                "date contract",
                True,
                f"signal={dates.signal_date}, execution={dates.execution_date}",
            )
        )
    except InferenceContractError as exc:
        results.append(("date contract", False, str(exc)))
        return results

    try:
        lineage = load_model_lineage(settings, dates.signal_date, open_dates)
        maturity = ", ".join(
            f"{item['tag']}={item['maturity_sessions']} sessions" for item in lineage
        )
        results.append(("model lineage and maturity", True, maturity))
    except InferenceContractError as exc:
        results.append(("model lineage and maturity", False, str(exc)))

    raw_latest = _raw_latest(project_root)
    raw_ok = bool(raw_latest and raw_latest >= dates.signal_date)
    results.append(
        (
            "canonical data freshness",
            raw_ok,
            f"latest={raw_latest}, required>={dates.signal_date}",
        )
    )
    try:
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter(
            qlib_dir=project_root / "data" / "qlib_bin",
            raw_dir=project_root / "data" / "canonical" / "daily",
        )
        adapter.init_qlib()
        qlib_latest = _normalise_date(adapter.get_last_qlib_date())
        qlib_ok = bool(qlib_latest and qlib_latest >= dates.signal_date)
        detail = f"latest={qlib_latest}, required>={dates.signal_date}"
    except Exception as exc:  # noqa: BLE001 - qlib providers raise heterogeneous errors
        qlib_ok = False
        detail = f"cannot inspect qlib data: {exc}"
    results.append(("qlib data freshness", qlib_ok, detail))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check daily inference readiness")
    parser.add_argument(
        "--trade-date",
        required=True,
        help="Signal/data date (YYYY-MM-DD); retained name for compatibility",
    )
    parser.add_argument("--execution-date", help="Expected next open session")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier")
    args = parser.parse_args()

    results = check_inference_ready(
        args.trade_date,
        args.strategy_id,
        execution_date=args.execution_date,
    )
    print(f"Daily inference readiness check: {args.trade_date} / {args.strategy_id}\n")
    all_pass = True
    for name, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
        all_pass = all_pass and ok
    print("\nPASS: All checks passed." if all_pass else "\nFAIL: Some checks failed.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
