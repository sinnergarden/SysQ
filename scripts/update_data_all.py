
import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("DataUpdate")

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.collector import TushareCollector
from qsys.data.adapter import QlibAdapter

def main():
    log.info("Starting Full Market Data Update (All A-Shares)...")
    
    # 1. Fetch Raw Data
    try:
        collector = TushareCollector()
        log.info("Fetching history for ALL stocks from 2010-01-01 to today...")
        # universe='all' triggers fetching the full list
        collector.update_universe_history(universe='all', start_date='20100101')
    except Exception as e:
        log.error(f"Data collection failed: {e}")
        return

    # 2. Sync to Qlib Bin (Incremental)
    try:
        adapter = QlibAdapter()
        log.info("Syncing new data to Qlib bin (Incremental)...")
        adapter.check_and_update(force=False)
    except Exception as e:
        log.error(f"Qlib sync failed: {e}")
        return

    log.info("✅ Full market data update completed successfully!")

if __name__ == "__main__":
    main()
