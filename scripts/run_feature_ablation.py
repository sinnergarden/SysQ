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


ABLATIONS = {
    "baseline": {
        "enable_microstructure_features": False,
        "enable_liquidity_features": False,
        "enable_tradability_features": False,
        "enable_relative_strength_features": False,
        "enable_industry_context_features": False,
        "enable_regime_features": False,
    },
    "phase1_all": {
        "enable_industry_context_features": False,
        "enable_regime_features": False,
    },
    "phase1_phase2": {
        "enable_industry_context_features": True,
        "enable_regime_features": True,
    },
    "phase1_phase2_phase3": {
        "enable_industry_context_features": True,
        "enable_regime_features": True,
        "enable_fundamental_context_features": True,
    },
    "no_tradability": {"enable_tradability_features": False},
    "no_regime": {"enable_regime_features": False, "enable_industry_context_features": False},
    "no_fundamental": {"enable_fundamental_context_features": False},
}


@click.command()
@click.option("--codes", default="600176.SH,000338.SZ,002001.SZ,002493.SZ,300394.SZ")
@click.option("--start", default="2026-01-01")
@click.option("--end", default="2026-03-20")
@click.option("--output", default="experiments/phase1_ablation_summary.csv")
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
        raise SystemExit("No sample data for ablation")

    full = pd.concat(frames, ignore_index=True)
    full["trade_date"] = pd.to_datetime(full["trade_date"].astype(str))
    full = full.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    rows = []
    for name, flags in ABLATIONS.items():
        feat_df = build_phase1_features(full, flags=flags)
        feature_cols = [c for c in feat_df.columns if c not in full.columns]
        rows.append({
            "experiment": name,
            "rows": len(feat_df),
            "feature_count": len(feature_cols),
            "sample_features": ", ".join(feature_cols[:12]),
        })

    summary = pd.DataFrame(rows)
    output_path = project_root / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_path, index=False)
    log.info(f"Phase1 ablation summary written to {output_path}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
