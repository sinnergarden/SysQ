"""
Universe bootstrap utility for index-based universes (csi300, csi500, csi800, etc.).

Purpose:
- build or refresh universe instrument files from index constituents
- can be used standalone for initialization or repair

Usage:
  python scripts/create_instrument_universe.py --universe csi800
  python scripts/create_instrument_universe.py --universe csi300 --output csi300.txt
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.utils.logger import log

INDEX_MAP = {
    "csi300": "000300.SH",
    "csi500": "000905.SH",
    "csi800": "000906.SH",
}

SUPPORTED_UNIVERSES = set(INDEX_MAP.keys())


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate universe instrument file from index constituents")
    parser.add_argument("--universe", default="csi300", choices=sorted(SUPPORTED_UNIVERSES), help="Index universe")
    parser.add_argument("--output", default=None, help="Output filename (default: {universe}.txt in qlib_bin/instruments/)")
    args = parser.parse_args()

    universe = args.universe.lower()
    log.info(f"Creating {universe}.txt instrument file...")

    # 1. Fetch index constituents
    try:
        collector = TushareCollector()
        df_weights = collector.get_index_weights(INDEX_MAP[universe])
        if df_weights.empty:
            log.error(f"Failed to fetch {universe} components from {INDEX_MAP[universe]}.")
            return

        codes = set(df_weights["con_code"].dropna())
        log.info(f"Fetched {len(codes)} {universe} components.")
    except Exception as e:
        log.error(f"Error fetching {universe} components: {e}")
        return

    # 2. Read all.txt
    qlib_dir = Path(str(cfg.get_path("qlib_bin")))
    all_txt_path = qlib_dir / "instruments" / "all.txt"

    if not all_txt_path.exists():
        log.error(f"all.txt not found at {all_txt_path}")
        return

    df_all = pd.read_csv(all_txt_path, sep="\t", names=["symbol", "start_date", "end_date"])

    # 3. Match against all.txt
    code_set = set(codes)
    df_universe = df_all[df_all["symbol"].isin(code_set)]
    log.info(f"Matched {len(df_universe)} stocks in all.txt for {universe}")

    if df_universe.empty:
        log.warning(f"No matches found for {universe}! Check symbol format.")
        log.info(f"Tushare sample: {list(codes)[0] if codes else '(empty)'}")
        log.info(f"all.txt sample: {df_all.iloc[0]['symbol'] if not df_all.empty else '(empty)'}")
        return

    # 4. Write output
    out_filename = args.output or f"{universe}.txt"
    out_path = qlib_dir / "instruments" / out_filename
    df_universe.to_csv(out_path, sep="\t", header=False, index=False)
    log.info(f"Written {len(df_universe)} symbols to {out_path}")


if __name__ == "__main__":
    main()
