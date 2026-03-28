from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.data.storage import StockDataStore
from qsys.feature.groups.microstructure import build_microstructure_features
from qsys.feature.groups.liquidity import build_liquidity_features
from qsys.feature.groups.tradability import build_tradability_features
from qsys.feature.groups.relative_strength import build_relative_strength_features
from qsys.feature.transforms import apply_cross_sectional_standardization
from qsys.utils.logger import log


@click.command()
@click.option("--codes", default="600176.SH,000338.SZ", help="Comma separated stock codes")
@click.option("--start", default="2026-01-01")
@click.option("--end", default="2026-03-20")
@click.option("--output", default="scratch/feature_build_sample.csv")
def main(codes, start, end, output):
    store = StockDataStore()
    frames = []
    for code in [c.strip() for c in codes.split(",") if c.strip()]:
        df = store.load_daily(code)
        if df is None or df.empty:
            continue
        sub = df[(df["trade_date"].astype(str) >= start.replace('-', '')) & (df["trade_date"].astype(str) <= end.replace('-', ''))].copy()
        if sub.empty:
            continue
        frames.append(sub)
    if not frames:
        raise SystemExit("No sample data found")

    full = pd.concat(frames, ignore_index=True)
    full["trade_date"] = pd.to_datetime(full["trade_date"].astype(str))
    full = full.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    out = build_microstructure_features(full)
    out = build_liquidity_features(out)
    out = build_tradability_features(out)
    out = build_relative_strength_features(out)
    out = apply_cross_sectional_standardization(
        out,
        [
            "close_to_open_gap_1d",
            "open_to_close_ret",
            "amount_log",
            "amount_zscore_20",
            "volume_shock_3",
            "volume_shock_5",
            "turnover_acceleration",
            "illiquidity",
        ],
        date_col="trade_date",
    )

    output_path = project_root / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    log.info(f"Feature sample written to {output_path}")


if __name__ == "__main__":
    main()
