#!/usr/bin/env python3
"""Read-only state path audit — check all ledger / shadow / legacy state files.

Purpose:
  Operator / agent can run this to see the current state of all state paths
  in the three-state coexistence (data/trade.db, data/meta/real_account.db,
  shadow/).

No state is modified.  Missing legacy files are reported but do not fail.

Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _exists(path: Path) -> dict:
    """Return file metadata or None."""
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _try_query_trade_db(db_path: Path) -> dict:
    """Read-only inspection of trade.db schema and latest snapshot/fills."""
    import sqlite3

    result: dict = {"found": True, "error": None, "tables": [], "snapshot_count": 0, "latest_snapshot": None}
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cursor.fetchall()]
        result["tables"] = tables

        # Try common table names
        for snap_table in ("snapshots", "position_snapshots", "account_snapshots"):
            if snap_table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM \"{snap_table}\"")
                result["snapshot_count"] = cursor.fetchone()[0]
                cursor.execute(f"SELECT * FROM \"{snap_table}\" ORDER BY rowid DESC LIMIT 1")
                cols = [d[0] for d in cursor.description]
                row = cursor.fetchone()
                if row:
                    result["latest_snapshot"] = dict(zip(cols, [str(v)[:80] for v in row]))
                break

        for fills_table in ("fills", "orders", "executions"):
            if fills_table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM \"{fills_table}\"")
                result[f"{fills_table}_count"] = cursor.fetchone()[0]

        for pos_table in ("positions", "holdings"):
            if pos_table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM \"{pos_table}\"")
                result[f"{pos_table}_count"] = cursor.fetchone()[0]

        conn.close()
    except Exception as e:
        result["error"] = str(e)

    return result


def audit_state_paths(project_root: Path | str) -> dict:
    root = Path(project_root)

    state_paths = {
        "data/trade.db": _exists(root / "data" / "trade.db"),
        "data/meta/real_account.db": _exists(root / "data" / "meta" / "real_account.db"),
        "shadow/account.json": _exists(root / "shadow" / "account.json"),
        "shadow/positions.csv": _exists(root / "shadow" / "positions.csv"),
        "shadow/ledger.csv": _exists(root / "shadow" / "ledger.csv"),
    }

    result: dict = {
        "status": "ok",
        "project_root": str(root.resolve()),
        "state_paths": state_paths,
        "trade_db": None,
        "latest_daily_dir": None,
    }

    if state_paths["data/trade.db"]["exists"]:
        result["trade_db"] = _try_query_trade_db(root / "data" / "trade.db")

    # Latest daily artifact directory
    daily_root = root / "daily"
    if daily_root.exists():
        dirs = sorted([d for d in daily_root.iterdir() if d.is_dir()], reverse=True)
        if dirs:
            latest = dirs[0].name
            pre = list((dirs[0] / "pre_open").iterdir()) if (dirs[0] / "pre_open").exists() else []
            post = list((dirs[0] / "post_close").iterdir()) if (dirs[0] / "post_close").exists() else []
            result["latest_daily_dir"] = {
                "date": latest,
                "pre_open_subdirs": [p.name for p in pre],
                "post_close_subdirs": [p.name for p in post],
            }

    # Summary
    present = [k for k, v in state_paths.items() if v["exists"]]
    missing = [k for k, v in state_paths.items() if not v["exists"]]
    result["summary"] = {
        "present": present,
        "missing": missing,
        "legacy_active": any(k in present for k in
                             ["data/meta/real_account.db", "shadow/account.json",
                              "shadow/positions.csv", "shadow/ledger.csv"]),
        "target_sot_present": "data/trade.db" in present,
    }

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only state path audit")
    parser.add_argument("--json", action="store_true", help="Output JSON (default)")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="Project root path")
    args = parser.parse_args()

    result = audit_state_paths(args.root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
