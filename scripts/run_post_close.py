import argparse
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.live.account import RealAccount
from qsys.live.reconciliation import (
    build_reconciliation_result,
    reconciliation_to_markdown,
    sync_real_account_from_csv,
    write_reconciliation_outputs,
)
from qsys.utils.logger import log


def main():
    parser = argparse.ArgumentParser(
        description="Post-close workflow: sync real account CSV and reconcile against shadow account"
    )
    parser.add_argument("--date", required=True, help="Trading date to reconcile (YYYY-MM-DD)")
    parser.add_argument("--real_sync", required=True, help="Broker/account CSV exported after market close")
    parser.add_argument("--db_path", default="data/real_account.db", help="SQLite account database path")
    parser.add_argument(
        "--output_dir",
        default="data/reconciliation",
        help="Directory to write reconciliation CSV outputs",
    )
    parser.add_argument("--real_account_name", default="real", help="Account name for real broker state")
    parser.add_argument("--shadow_account_name", default="shadow", help="Account name for shadow simulation state")
    args = parser.parse_args()

    account = RealAccount(db_path=args.db_path, account_name=args.real_account_name)
    normalized = sync_real_account_from_csv(
        account,
        account_name=args.real_account_name,
        sync_path=args.real_sync,
        date=args.date,
        persist_trade_log=True,
    )

    result = build_reconciliation_result(
        account,
        date=args.date,
        real_account_name=args.real_account_name,
        shadow_account_name=args.shadow_account_name,
    )
    written = write_reconciliation_outputs(
        result,
        args.output_dir,
        date=args.date,
        real_sync_snapshot=normalized,
    )

    print(reconciliation_to_markdown(result))
    for name, path in written.items():
        log.info(f"Wrote {name}: {path}")


if __name__ == "__main__":
    main()
