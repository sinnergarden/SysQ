
import sys
from pathlib import Path
# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import qlib
from qlib.data import D
from qsys.config import cfg

# Initialize Qlib
qlib.init(provider_uri=str(cfg.get_path("qlib_bin")))

# Check Raw Data for a sample stock
print("\n--- Checking Raw Data for 600519.SH (Moutai) ---")
try:
    df_raw = D.features(
        instruments=['600519.SH'], 
        fields=['$close', '$volume', '$amount', '$factor'], 
        start_time='2020-01-01', 
        end_time='2020-01-05'
    )
    print(df_raw)
except Exception as e:
    print(f"Failed to fetch raw data: {e}")
