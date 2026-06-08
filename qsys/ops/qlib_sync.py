"""Qlib sync orchestration for targeted repair and incremental update.

Two entry points (both retain their original signatures for compatibility):

- ``run_targeted_qlib_sync`` — main entry, used by ``shadow_presync``.
  Tries incremental (``dump_update``) first, falls back to
  ``convert_fix_symbols`` (``dump_fix`` per-symbol).

- ``refresh_selected_symbols_from_raw`` — backward-compatible wrapper
  around ``convert_fix_symbols``, used by ``full_universe_backfill``.
  Will be removed in a future cleanup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.utils.json_io import write_csv, write_json


AFFECTED_SYMBOL_COLUMNS = ["symbol", "selected_for_apply"]
QLIB_SYMBOL_SYNC_COLUMNS = [
    "symbol",
    "sync_status",
    "error",
]


def can_run_incremental_qlib_sync(adapter: QlibAdapter, target_date: str | None = None) -> bool:
    """Check if incremental qlib sync (``dump_update``) can be used.

    Incremental works when *target_date* is **newer** than the last qlib
    calendar date (appending new data).  For existing dates, use
    ``dump_fix`` instead.
    """
    convert_fn = getattr(adapter, "convert_incremental", None)
    if not callable(convert_fn):
        return False
    if target_date is None:
        return False
    last_date = adapter.get_last_qlib_date()
    if last_date is None:
        return False
    return pd.Timestamp(target_date) > last_date


# ── Summary helpers ────────────────────────────────────────────────────────


def _summarize_skipped(previous_qlib_last_date: str | None, reason: str, *, symbol_count: int = 0) -> dict[str, Any]:
    return {
        "qlib_update_status": "skipped",
        "convert_mode": "skipped",
        "previous_qlib_last_date": previous_qlib_last_date,
        "post_sync_qlib_last_date": previous_qlib_last_date,
        "affected_symbol_count": symbol_count,
        "symbols_attempted": 0,
        "symbols_synced": 0,
        "symbols_failed": 0,
        "symbols_validated": 0,
        "backup_status": "skipped",
        "rollback_status": "not_needed",
        "reason": reason,
    }


def _summarize_incremental_success(
    previous_qlib_last_date: str | None,
    post_sync: str,
    symbol_count: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "qlib_update_status": "success",
        "convert_mode": "incremental",
        "previous_qlib_last_date": previous_qlib_last_date,
        "post_sync_qlib_last_date": post_sync,
        "affected_symbol_count": symbol_count,
        "symbols_attempted": symbol_count,
        "symbols_synced": symbol_count,
        "symbols_failed": 0,
        "symbols_validated": 0,
        "backup_status": "not_needed",
        "rollback_status": "not_needed",
        "reason": reason,
    }


def _summarize_fix_success(
    previous_qlib_last_date: str | None,
    result: dict,
    symbol_count: int,
    reason: str,
) -> dict[str, Any]:
    post = result.get("post_sync_qlib_last_date", previous_qlib_last_date)
    return {
        "qlib_update_status": "success",
        "convert_mode": "fix_symbols",
        "previous_qlib_last_date": previous_qlib_last_date,
        "post_sync_qlib_last_date": post,
        "affected_symbol_count": symbol_count,
        "symbols_attempted": symbol_count,
        "symbols_synced": symbol_count,
        "symbols_failed": 0,
        "symbols_validated": 0,
        "backup_status": "skipped",
        "rollback_status": "not_needed",
        "reason": reason,
    }


def _summarize_failed(
    previous_qlib_last_date: str | None,
    symbol_count: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "qlib_update_status": "failed",
        "convert_mode": "failed",
        "previous_qlib_last_date": previous_qlib_last_date,
        "post_sync_qlib_last_date": previous_qlib_last_date,
        "affected_symbol_count": symbol_count,
        "symbols_attempted": symbol_count,
        "symbols_synced": 0,
        "symbols_failed": symbol_count,
        "symbols_validated": 0,
        "backup_status": "skipped",
        "rollback_status": "not_needed",
        "reason": reason,
    }


# ── Entry points ───────────────────────────────────────────────────────────


def run_targeted_qlib_sync(
    *,
    adapter: QlibAdapter,
    previous_qlib_last_date: str | None,
    affected_symbols: list[str],
    apply: bool,
    output_dir: Path,
    skip_sync: bool = False,
    base_dir: str | Path | None = None,
    target_date: str | None = None,
    universe: str = "csi300",
) -> tuple[dict[str, Any], Path, Path, Path]:
    """Main qlib sync entry point used by ``shadow_presync``.

    Decision order:
    1. dry-run / skip-sync  →  summary (no mutation)
    2. incremental available → ``convert_incremental(target_date)``
    3. fallback             → ``adapter.convert_fix_symbols(affected_symbols)``

    Returns ``(summary, affected_path, summary_path, symbol_sync_path)``.
    """
    unique_symbols = sorted(set(affected_symbols))
    rows = [{"symbol": symbol, "selected_for_apply": True} for symbol in unique_symbols]
    previous = previous_qlib_last_date

    # ── Short-circuit: no-op paths ──────────────────────────────────────
    if not apply or skip_sync or not unique_symbols:
        reason = "dry-run" if not apply else ("explicitly skipped" if skip_sync else "no affected symbols")
        summary = _summarize_skipped(previous, reason, symbol_count=len(unique_symbols))
        sync_rows: list[dict] = []
        affected_path = write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
        symbol_sync_path = write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
        summary_path = write_json(output_dir / "qlib_sync_summary.json", summary)
        return summary, affected_path, summary_path, symbol_sync_path

    # ── Decide: incremental vs fix-symbols ──────────────────────────────
    fallback_reason = "incremental unavailable"
    if target_date is not None and can_run_incremental_qlib_sync(adapter, target_date=target_date):
        try:
            adapter.convert_incremental(target_date)
            post_sync = pd.Timestamp(target_date).strftime("%Y-%m-%d")
            summary = _summarize_incremental_success(
                previous, post_sync, len(unique_symbols),
                f"incremental qlib sync via dump_update (target={target_date})",
            )
            sync_rows = []
            affected_path = write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
            symbol_sync_path = write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
            summary_path = write_json(output_dir / "qlib_sync_summary.json", summary)
            return summary, affected_path, summary_path, symbol_sync_path
        except Exception as exc:
            fallback_reason = f"incremental failed ({exc})"

    # ── Fix-symbols fallback ─────────────────────────────────────────────
    try:
        result = adapter.convert_fix_symbols(unique_symbols)
        fix_status = result.get("status", "success")
        post = adapter.get_last_qlib_date()
        post_sync_text = post.strftime("%Y-%m-%d") if post is not None else previous
        result["post_sync_qlib_last_date"] = post_sync_text
        if fix_status == "success":
            summary = _summarize_fix_success(
                previous, result, len(unique_symbols),
                f"fix_symbols fallback ({fallback_reason})",
            )
            sync_rows = [{"symbol": s, "sync_status": "success", "error": ""} for s in unique_symbols]
        else:
            summary = _summarize_skipped(
                previous, f"convert_fix_symbols returned status={fix_status}",
                symbol_count=len(unique_symbols),
            )
            summary["convert_mode"] = "fix_symbols"
            sync_rows = [{"symbol": s, "sync_status": fix_status, "error": ""} for s in unique_symbols]
    except Exception as exc:
        summary = _summarize_failed(
            previous, len(unique_symbols),
            f"fix_symbols failed: {exc}",
        )
        sync_rows = [{"symbol": s, "sync_status": "failed", "error": str(exc)} for s in unique_symbols]

    affected_path = write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
    symbol_sync_path = write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
    summary_path = write_json(output_dir / "qlib_sync_summary.json", summary)
    return summary, affected_path, summary_path, symbol_sync_path


def refresh_selected_symbols_from_raw(
    base_dir: str | Path,
    symbols: list[str],
    *,
    universe: str = "csi300",
    target_date: str,
    apply: bool,
    output_dir: Path,
    backup: bool = True,
) -> dict[str, Any]:
    """Targeted per-symbol qlib rebuild.  Backward-compatible wrapper.

    Delegates to ``QlibAdapter.convert_fix_symbols`` and wraps the result
    into the legacy ``{summary: dict, rows: list[dict]}`` shape expected
    by ``full_universe_backfill``.
    """
    adapter = QlibAdapter()
    adapter.init_qlib()
    selected = sorted(set(symbols))

    if not apply:
        return {
            "summary": {
                "qlib_update_status": "skipped",
                "convert_mode": "selected_symbol_refresh",
                "affected_symbol_count": len(selected),
                "symbols_attempted": 0,
                "symbols_synced": 0,
                "symbols_failed": 0,
                "symbols_validated": 0,
                "backup_status": "skipped",
                "rollback_status": "not_needed",
                "reason": "dry-run does not mutate qlib",
            },
            "rows": [{"symbol": s} for s in selected],
        }

    try:
        result = adapter.convert_fix_symbols(selected)
        status = result.get("status", "success")
        if status == "success":
            qlib_status = "success"
            symbols_attempted = len(selected)
            symbols_synced = len(selected)
            symbols_failed = 0
            symbols_validated = len(selected)
            row_sync_status = "success"
        elif status == "skipped":
            qlib_status = "skipped"
            symbols_attempted = 0
            symbols_synced = 0
            symbols_failed = 0
            symbols_validated = 0
            row_sync_status = "skipped"
        else:
            qlib_status = "failed"
            symbols_attempted = len(selected)
            symbols_synced = 0
            symbols_failed = len(selected)
            symbols_validated = 0
            row_sync_status = "failed"
        rows = [
            {
                "symbol": s,
                "sync_status": row_sync_status,
                "validated_on_target_date": row_sync_status == "success",
                "error": "",
            }
            for s in selected
        ]
        return {
            "summary": {
                "qlib_update_status": qlib_status,
                "convert_mode": "selected_symbol_refresh",
                "affected_symbol_count": len(selected),
                "symbols_attempted": symbols_attempted,
                "symbols_synced": symbols_synced,
                "symbols_failed": symbols_failed,
                "symbols_validated": symbols_validated,
                "backup_status": "skipped",
                "rollback_status": "not_needed",
                "reason": f"delegated to convert_fix_symbols (status={status})",
            },
            "rows": rows,
        }
    except Exception as exc:
        return {
            "summary": {
                "qlib_update_status": "failed",
                "convert_mode": "selected_symbol_refresh",
                "affected_symbol_count": len(selected),
                "symbols_attempted": len(selected),
                "symbols_synced": 0,
                "symbols_failed": len(selected),
                "symbols_validated": 0,
                "backup_status": "skipped",
                "rollback_status": "not_needed",
                "reason": str(exc),
            },
            "rows": [{"symbol": s, "sync_status": "failed", "error": str(exc)} for s in selected],
        }
