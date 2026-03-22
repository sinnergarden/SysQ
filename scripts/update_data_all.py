
import sys
import logging
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("DataUpdate")

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.collector import TushareCollector
from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore

def print_status_report(adapter: QlibAdapter):
    """Print a clear status report of raw vs qlib data alignment"""
    report = adapter.get_data_status_report()
    
    log.info("=" * 50)
    log.info("DATA STATUS REPORT")
    log.info("=" * 50)
    log.info(f"  Raw latest date:      {report.get('raw_latest', 'N/A')}")
    log.info(f"  Qlib latest date:     {report.get('qlib_latest', 'N/A')}")
    log.info(f"  Target signal date:   {report.get('target_signal_date', 'N/A')}")
    
    gap = report.get('gap_days')
    aligned = report.get('aligned', False)
    if gap is not None:
        log.info(f"  Gap (days):           {gap}")
        if aligned:
            log.info("  Status: ✅ ALIGNED")
        else:
            log.info("  Status: ⚠️  MISMATCH - Qlib needs update")
    else:
        log.info("  Status: ⚠️  Cannot determine gap")
    log.info("=" * 50)

def main():
    log.info("Starting Full Market Data Update (All A-Shares)...")
    
    # Get initial status
    adapter = QlibAdapter()
    log.info("Initial status:")
    print_status_report(adapter)
    
    # 1. Fetch Raw Data
    try:
        collector = TushareCollector()
        log.info("Fetching history for ALL stocks from 2010-01-01 to today...")
        # universe='all' triggers fetching the full list
        collector.update_universe_history(universe='all', start_date='20100101')
    except Exception as e:
        log.error(f"Data collection failed: {e}")
        return

    # 2. Sync to Qlib Bin (Incremental) - with explicit refresh
    try:
        adapter = QlibAdapter()
        log.info("Syncing new data to Qlib bin (Incremental)...")
        # This now explicitly checks raw vs qlib and updates
        adapter.refresh_qlib_date()
    except Exception as e:
        log.error(f"Qlib sync failed: {e}")
        return

    # 3. Final status report
    log.info("\nFinal status after update:")
    print_status_report(adapter)

    log.info("✅ Full market data update completed successfully!")

if __name__ == "__main__":
    main()
