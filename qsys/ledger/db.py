"""Database connection management for the ledger system."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from qsys.ledger.schema import ensure_schema, record_migration


def create_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a WAL-mode SQLite connection and ensure schema exists."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    record_migration(conn, "001_initial_schema")
    return conn
