
import sys
from pathlib import Path
import pandas as pd

# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.utils.logger import log

def main():
    log.info("Creating csi300.txt instrument file...")
    
    # 1. Get CSI300 codes
    try:
        collector = TushareCollector()
        df_weights = collector.get_index_weights('000300.SH')
        if df_weights.empty:
            # Try 399300.SZ just in case
            df_weights = collector.get_index_weights('399300.SZ')
            
        if df_weights.empty:
            log.error("Failed to fetch CSI300 components.")
            return

        csi300_codes = set(df_weights['con_code'].unique())
        log.info(f"Fetched {len(csi300_codes)} CSI300 components.")
        
    except Exception as e:
        log.error(f"Error fetching CSI300 components: {e}")
        return

    # 2. Read all.txt
    qlib_dir = Path(str(cfg.get_path("qlib_bin")))
    all_txt_path = qlib_dir / "instruments" / "all.txt"
    
    if not all_txt_path.exists():
        log.error(f"all.txt not found at {all_txt_path}")
        return
        
    df_all = pd.read_csv(all_txt_path, sep="\t", names=["symbol", "start_date", "end_date"])
    
    # 3. Filter
    # Tushare codes: 600519.SH
    # Qlib codes in all.txt: SH600519 (Wait, let's check format)
    # The dump_bin.py uses fname_to_code. 
    # Let's check a sample from all.txt
    sample_symbol = df_all.iloc[0]['symbol']
    log.info(f"Sample symbol in all.txt: {sample_symbol}")
    
    # Tushare format is 600519.SH, Qlib usually uses SH600519 or 600519.SH depending on setup.
    # dump_bin.py: code = fname_to_code(str(file_or_data.iloc[0][self.symbol_field_name]).lower())
    # fname_to_code usually converts to SH600519 format.
    
    # Let's try to normalize Tushare codes to match all.txt
    # If all.txt has SH600519, and Tushare has 600519.SH
    
    def to_qlib_code(ts_code):
        # 600519.SH -> SH600519
        code, exchange = ts_code.split('.')
        return f"{exchange}{code}"

    # Check format match
    if sample_symbol[0].isalpha(): # Starts with SH/SZ
        csi300_qlib = {to_qlib_code(c) for c in csi300_codes}
    else:
        csi300_qlib = csi300_codes

    df_csi300 = df_all[df_all['symbol'].isin(csi300_qlib)]
    
    log.info(f"Matched {len(df_csi300)} stocks in all.txt")
    
    if df_csi300.empty:
        log.warning("No matches found! Check symbol format.")
        log.info(f"Tushare sample: {list(csi300_codes)[0]}")
        log.info(f"all.txt sample: {sample_symbol}")
        return

    # 4. Write csi300.txt
    out_path = qlib_dir / "instruments" / "csi300.txt"
    df_csi300.to_csv(out_path, sep="\t", header=False, index=False)
    log.info(f"Written to {out_path}")

if __name__ == "__main__":
    main()
