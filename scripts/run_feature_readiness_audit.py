from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click

from qsys.data.storage import StockDataStore
from qsys.feature.builder import build_research_features
from qsys.feature.registry import resolve_feature_selection


@click.command()
@click.option('--codes', default='000001.SZ,000002.SZ,000333.SZ,000338.SZ,000625.SZ,000977.SZ,002027.SZ,002049.SZ,002241.SZ,002415.SZ,002466.SZ,002600.SZ,002709.SZ,300033.SZ,300433.SZ,300750.SZ,600019.SH,600104.SH,600176.SH,600519.SH,600584.SH,600958.SH,600999.SH,601009.SH,601100.SH,601318.SH,601336.SH,601390.SH,601628.SH,601658.SH')
@click.option('--start', default='20200101')
@click.option('--end', default='20260320')
@click.option('--feature_set', default='research_semantic_default_v1', show_default=True, help='推荐使用语义化 feature set 名称')
@click.option('--output', default='experiments/feature_readiness_audit.csv')
def main(codes, start, end, feature_set, output):
    store = StockDataStore()
    frames = []
    for code in [c.strip() for c in codes.split(',') if c.strip()]:
        df = store.load_daily(code)
        if df is None or df.empty:
            continue
        sub = df[(df['trade_date'].astype(str) >= start) & (df['trade_date'].astype(str) <= end)].copy()
        if not sub.empty:
            frames.append(sub)
    if not frames:
        raise SystemExit('no sample data')
    full = pd.concat(frames, ignore_index=True)
    full['trade_date'] = pd.to_datetime(full['trade_date'].astype(str))
    selection = resolve_feature_selection(feature_set=feature_set)
    feat = build_research_features(full, feature_set=feature_set, flags={
        'enable_microstructure_features': True,
        'enable_liquidity_features': True,
        'enable_tradability_features': True,
        'enable_relative_strength_features': True,
        'enable_industry_context_features': True,
        'enable_regime_features': True,
        'enable_fundamental_context_features': True,
    }, select_only=True)
    base_cols = set(full.columns)
    feature_cols = [c for c in feat.columns if c not in base_cols]
    rows = []
    for c in feature_cols:
        s = pd.to_numeric(feat[c], errors='coerce')
        rows.append({
            'feature': c,
            'missing_ratio': float(s.isna().mean()),
            'nunique': int(s.nunique(dropna=True)),
            'constant': int(s.nunique(dropna=True) <= 1),
            'status': 'ready' if float(s.isna().mean()) < 0.1 and int(s.nunique(dropna=True)) > 1 else ('blocked' if float(s.isna().mean()) >= 0.5 or int(s.nunique(dropna=True)) <= 1 else 'warning'),
        })
    out = pd.DataFrame(rows).sort_values(['status', 'missing_ratio', 'constant'])
    out_path = project_root / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f'feature_set={feature_set} feature_ids={len(selection.feature_ids)} groups={selection.required_groups}')
    print(out.to_string(index=False))
    print(f'written {out_path}')


if __name__ == '__main__':
    main()
