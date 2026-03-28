from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.data.storage import StockDataStore
from qsys.feature.builder import build_phase1_features
from qsys.utils.logger import log


@click.command()
@click.option("--codes", default="600176.SH,000338.SZ,002001.SZ,002493.SZ,300394.SZ")
@click.option("--start", default="2026-01-01")
@click.option("--end", default="2026-03-20")
@click.option("--with_phase2", is_flag=True, help="Enable Phase 2 regime + industry context features")
@click.option("--output", default="experiments/phase1_feature_experiment.csv")
def main(codes, start, end, with_phase2, output):
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
        raise SystemExit("No sample data for experiment")

    full = pd.concat(frames, ignore_index=True)
    full["trade_date"] = pd.to_datetime(full["trade_date"].astype(str))
    full = full.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    flags = {
        "enable_industry_context_features": with_phase2,
        "enable_regime_features": with_phase2,
    }
    feat_df = build_phase1_features(full, flags=flags)

    output_path = project_root / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    feat_df.to_csv(output_path, index=False)
    log.info(f"Phase1 experiment dataset written to {output_path}")
    print(f"rows={len(feat_df)} cols={len(feat_df.columns)} path={output_path}")


if __name__ == "__main__":
    main()
