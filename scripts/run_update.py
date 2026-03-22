"""
Primary incremental data update entrypoint.

Purpose:
- initialize metadata
- update a single code, a universe, or a given trading date range
- serve as the narrow/targeted data refresh tool

Typical usage:
- python scripts/run_update.py --init
- python scripts/run_update.py --universe csi300 --start 20230101
- python scripts/run_update.py --history 000001.SZ --start 20240101

Key args:
- --init: refresh stock list + calendar
- --history: update one code
- --universe: update a universe or comma-separated codes
- --date / --start: date controls (accept YYYYMMDD or YYYY-MM-DD)
"""

import click
import datetime
import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import pandas as pd

from qsys.data.collector import TushareCollector
from qsys.utils.logger import log

def _normalize_date(value):
    if not value:
        return value
    return pd.Timestamp(value).strftime('%Y%m%d')


@click.command()
@click.option('--date', help='Trading date to update (YYYYMMDD). Defaults to today.')
@click.option('--init', is_flag=True, help='Initialize stock list and calendar')
@click.option('--history', help='Fetch history for a specific code')
@click.option('--universe', help='Fetch history for a universe (e.g., csi300, all, or comma-separated codes)')
@click.option('--start', default='20100101', help='Start date for history fetch')
def main(date, init, history, universe, start):
    try:
        collector = TushareCollector()
        start = _normalize_date(start)
        date = _normalize_date(date)

        if init:
            log.info("Initializing metadata...")
            collector.update_stock_list()
            collector.update_calendar()
            log.info("Metadata initialization completed.")
            return

        if history:
            log.info(f"Updating history for {history}")
            collector.update_history(history, start_date=start)
            log.info(f"History update for {history} completed.")
            return

        if universe:
            log.info(f"Updating history for universe: {universe}")
            collector.update_universe_history(universe, start_date=start)
            log.info(f"Universe update for {universe} completed.")
            return

        if not date:
            date = datetime.datetime.now().strftime('%Y%m%d')
            
        log.info(f"Starting data update for {date}")
        collector.update_daily(date)
        log.info("Daily update completed.")
        
    except Exception as e:
        log.error(f"Task failed: {e}")
        # raise e # Optional: raise to see stack trace

if __name__ == '__main__':
    main()
