from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.data.storage import StockDataStore
from qsys.feature.builder import build_research_features
from qsys.feature.registry import resolve_feature_selection
from qsys.utils.logger import log


@click.command()
@click.option("--codes", default="600176.SH,000338.SZ", help="Comma separated stock codes")
@click.option("--start", default="2026-01-01")
@click.option("--end", default="2026-03-20")
@click.option("--feature_set", default="short_horizon_state_core_v1", show_default=True, help="正式 feature set 名称；脚本当前主要面向 research derived set")
@click.option("--output", default="scratch/feature_build_sample.csv")
def main(codes, start, end, feature_set, output):
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
    selection = resolve_feature_selection(feature_set=feature_set)
    out = build_research_features(
        full,
        feature_set=feature_set,
        preset="phase1_core",
        select_only=True,
    )

    output_path = project_root / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    log.info(
        f"Resolved feature_set={feature_set} -> feature_ids={len(selection.feature_ids)} "
        f"derived_groups={selection.required_groups}"
    )
    log.info(f"Feature sample written to {output_path}")


if __name__ == "__main__":
    main()
