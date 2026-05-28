#!/usr/bin/env python3
"""Reset both shadow accounts to ¥100k initial capital, empty positions.

Clears:
  - Shadow files (account.json, positions.csv) for both strategies
  - All ledger records for shadow_alpha_v1 and shadow_alpha_v2 accounts
  - Run output directories for the replay period (experiments/*_daily/*)

Creates:
  - Fresh account.json with cash=100000, no positions
  - Fresh positions.csv (header only)
  - Ledger accounts with initial 100k capital event
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

ACCOUNTS = {
    "shadow_alpha_v1": {
        "shadow_dir": PROJECT_ROOT / "shadow",
        "account_file": PROJECT_ROOT / "shadow" / "account.json",
        "positions_file": PROJECT_ROOT / "shadow" / "positions.csv",
        "strategy_id": "alpha_v1",
    },
    "shadow_alpha_v2": {
        "shadow_dir": PROJECT_ROOT / "shadow_alpha_v2" / "shadow",
        "account_file": PROJECT_ROOT / "shadow_alpha_v2" / "shadow" / "account.json",
        "positions_file": PROJECT_ROOT / "shadow_alpha_v2" / "shadow" / "positions.csv",
        "strategy_id": "alpha_v2",
    },
}

LEDGER_DB = PROJECT_ROOT / "data" / "trade.db"
INITIAL_CAPITAL = 100_000.0

TRADE_DATES = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25"]

# Tables in deletion order (child → parent to respect FK constraints)
TABLES_BY_ACCOUNT = [
    "fills", "orders", "position_ledger", "positions",
    "portfolio_snapshots", "strategy_runs", "cash_ledger", "accounts",
]


def clear_ledger() -> None:
    if not LEDGER_DB.exists():
        print("  Ledger DB not found — nothing to clear")
        return

    conn = sqlite3.connect(str(LEDGER_DB))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for aid in ACCOUNTS:
            for table in TABLES_BY_ACCOUNT:
                deleted = conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (aid,)).rowcount
                if deleted:
                    print(f"  {table}: {deleted} rows for {aid}")
        conn.commit()
        print(f"  Ledger cleared for {len(ACCOUNTS)} accounts")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def create_fresh_account_in_ledger(account_id: str) -> None:
    conn = sqlite3.connect(str(LEDGER_DB))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        now = datetime.now().isoformat()
        # Create account row
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (account_id, account_type, broker, base_currency, initial_cash,
                created_at, updated_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, "shadow", None, "CNY", INITIAL_CAPITAL,
             now, now, "active"),
        )
        # Insert initial cash event
        conn.execute(
            """INSERT INTO cash_ledger
               (cash_event_id, account_id, run_id, trade_date, event_type,
                amount, currency, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"cash_init_{account_id}", account_id, None, "2026-05-16",
             "deposit", INITIAL_CAPITAL, "CNY", now),
        )
        conn.commit()
        print(f"  Account {account_id} created with ¥{INITIAL_CAPITAL:.0f}")
    except Exception as e:
        print(f"  ERROR creating account {account_id}: {e}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def reset_shadow_files(account_id: str) -> None:
    info = ACCOUNTS[account_id]
    shadow_dir = info["shadow_dir"]
    account_file = info["account_file"]
    positions_file = info["positions_file"]

    shadow_dir.mkdir(parents=True, exist_ok=True)

    account_data = {
        "available_cash": INITIAL_CAPITAL,
        "cash": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "last_run_id": None,
        "market_value": 0.0,
        "total_value": INITIAL_CAPITAL,
        "trade_date": "2026-05-16",
    }
    account_file.write_text(json.dumps(account_data, ensure_ascii=False, indent=2))
    print(f"  {account_file.relative_to(PROJECT_ROOT)} — ¥{INITIAL_CAPITAL:.0f}")

    positions_file.write_text("instrument,quantity,sellable_quantity,cost_price,last_price,market_value\n")
    print(f"  {positions_file.relative_to(PROJECT_ROOT)} — empty")


def clear_run_outputs() -> None:
    for date in TRADE_DATES:
        for sid in ["alpha_v1", "alpha_v2"]:
            run_dir = PROJECT_ROOT / "experiments" / f"{sid}_daily" / date
            if run_dir.exists():
                shutil.rmtree(run_dir)
                print(f"  Removed: experiments/{sid}_daily/{date}")


def main() -> None:
    print("=" * 60)
    print("Reset: clear shadow state + ledger, init ¥100k")
    print("=" * 60)

    print("\n[1/5] Clearing shadow files...")
    for account_id in ACCOUNTS:
        af = ACCOUNTS[account_id]["account_file"]
        pf = ACCOUNTS[account_id]["positions_file"]
        if af.exists():
            af.unlink()
            print(f"  Removed: {af.relative_to(PROJECT_ROOT)}")
        if pf.exists():
            pf.unlink()
            print(f"  Removed: {pf.relative_to(PROJECT_ROOT)}")

    print("\n[2/5] Clearing ledger...")
    clear_ledger()

    print("\n[3/5] Creating fresh accounts in ledger...")
    create_fresh_account_in_ledger("shadow_alpha_v1")
    create_fresh_account_in_ledger("shadow_alpha_v2")

    print("\n[4/5] Creating fresh shadow files...")
    for account_id in ACCOUNTS:
        reset_shadow_files(account_id)

    print("\n[5/5] Clearing run output directories...")
    clear_run_outputs()

    print("\n" + "=" * 60)
    print("Reset complete. ¥100k each, empty positions, ready for replay.")
    print("=" * 60)


if __name__ == "__main__":
    main()
