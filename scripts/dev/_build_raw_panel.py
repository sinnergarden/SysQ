#!/usr/bin/env python3
"""Build a raw panel from canonical feather files, ready for backfill."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import numpy as np
from qsys.utils.logger import log

REPO = Path(__file__).resolve().parents[2]
CANONICAL_DIR = REPO / "data" / "canonical" / "daily"
OUTPUT_DIR = REPO / "data" / "raw_panels"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# CSI800 stocks from qlib_bin instruments
inst_dir = REPO / "data" / "qlib_bin" / "instruments"
csi800_file = inst_dir / "csi800.txt"
if csi800_file.exists():
    symbols = csi800_file.read_text().strip().splitlines()
    log.info("Loaded %d symbols from %s", len(symbols), csi800_file)
else:
    # Fallback: scan canonical dir
    symbols = [f.stem for f in sorted(CANONICAL_DIR.glob("*.feather"))[:100]]
    log.warning("No csi800.txt found, using first %d symbols", len(symbols))

# Read a sample of symbols to build raw panel
# For backfill we need: trade_date, ts_code, close, open, high, low, volume, amount,
# plus fundamental fields
REQUIRED_FIELDS = [
    "trade_date", "ts_code",
    "close", "open", "high", "low", "volume", "amount", "vwap",
    "high_limit", "low_limit", "factor", "float_shares",
    "pe", "pb", "total_mv", "circ_mv", "roe",
    "grossprofit_margin", "debt_to_assets", "current_ratio",
    "net_income", "revenue", "op_cashflow", "total_assets", "equity",
    "net_inflow", "big_inflow",
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
    "industry",
]

# Scan a subset to estimate size. Use full set for backfill.
# For test purpose, use up to 200 stocks (CSI800 subset)
SAMPLE_SIZE = 200
symbols_to_read = symbols[:SAMPLE_SIZE]

chunks = []
start = time.time()
for i, sym in enumerate(symbols_to_read):
    feather_path = CANONICAL_DIR / f"{sym}.feather"
    if not feather_path.exists():
        continue
    try:
        df = pd.read_feather(feather_path)
        df["ts_code"] = sym
        # Keep only required fields that exist
        keep = [c for c in REQUIRED_FIELDS if c in df.columns]
        chunks.append(df[keep])
    except Exception as e:
        log.warning("Error reading %s: %s", sym, e)

    if (i + 1) % 50 == 0:
        log.info("Read %d/%d symbols...", i + 1, len(symbols_to_read))

if chunks:
    panel = pd.concat(chunks, ignore_index=True)
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])

    out_path = OUTPUT_DIR / f"raw_panel_csi800_{SAMPLE_SIZE}stocks_{panel['trade_date'].min():%Y%m%d}_{panel['trade_date'].max():%Y%m%d}.parquet"
    panel.to_parquet(out_path, index=False)
    elapsed = time.time() - start
    log.info("Written %d rows x %d cols -> %s in %.1fs",
             len(panel), len(panel.columns), out_path, elapsed)
    log.info("Date range: %s to %s", panel["trade_date"].min(), panel["trade_date"].max())
    log.info("Stocks: %d", panel["ts_code"].nunique())
else:
    log.error("No data read!")
