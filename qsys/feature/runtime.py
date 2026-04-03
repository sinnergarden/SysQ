from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore
from qsys.feature.builder import build_research_features
from qsys.feature.registry import resolve_feature_selection


LOOKBACK_DAYS_FOR_DERIVED = 420


def resolve_universe_codes(universe: str | list[str]) -> list[str]:
    if isinstance(universe, (list, tuple, set)):
        return [str(code) for code in universe]
    universe_key = str(universe).strip().lower()
    instrument_file = Path('data/qlib_bin/instruments') / f'{universe_key}.txt'
    if instrument_file.exists():
        return sorted({line.split('\t')[0] for line in instrument_file.read_text(encoding='utf-8').splitlines() if line.strip()})
    if ',' in universe_key:
        return [code.strip() for code in universe.split(',') if code.strip()]
    return [str(universe)]


def load_raw_panel(codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    start_key = pd.Timestamp(start_date).strftime('%Y%m%d')
    end_key = pd.Timestamp(end_date).strftime('%Y%m%d')
    store = StockDataStore()
    frames = []
    for code in codes:
        df = store.load_daily(code)
        if df is None or df.empty:
            continue
        sub = df[(df['trade_date'].astype(str) >= start_key) & (df['trade_date'].astype(str) <= end_key)].copy()
        if not sub.empty:
            frames.append(sub)
    if not frames:
        return pd.DataFrame(columns=['trade_date', 'ts_code'])
    panel = pd.concat(frames, ignore_index=True)
    panel['trade_date'] = pd.to_datetime(panel['trade_date'].astype(str))
    return panel.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)


def build_feature_panel(
    *,
    feature_set: str,
    universe: str | list[str],
    start_date: str,
    end_date: str,
    lookback_days: int = LOOKBACK_DAYS_FOR_DERIVED,
    include_close: bool = False,
) -> tuple[pd.DataFrame, dict]:
    selection = resolve_feature_selection(feature_set=feature_set)
    codes = resolve_universe_codes(universe)
    adapter = QlibAdapter()
    adapter.init_qlib()

    native_df = adapter.get_features(codes, selection.native_qlib_fields, start_time=start_date, end_time=end_date)
    if native_df is None:
        native_df = pd.DataFrame()
    if isinstance(native_df.index, pd.MultiIndex):
        native_df = native_df.reset_index()
    if not native_df.empty:
        native_df = native_df.rename(columns={'datetime': 'trade_date', 'instrument': 'ts_code'})
        native_df['trade_date'] = pd.to_datetime(native_df['trade_date'])

    derived_df = pd.DataFrame(columns=['trade_date', 'ts_code'])
    if selection.derived_columns:
        raw_start = (pd.Timestamp(start_date) - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        raw_panel = load_raw_panel(codes, raw_start, end_date)
        if not raw_panel.empty:
            derived_df = build_research_features(raw_panel, feature_set=feature_set, select_only=True)
            if not derived_df.empty:
                derived_df['trade_date'] = pd.to_datetime(derived_df['trade_date'])
                derived_df = derived_df[
                    (derived_df['trade_date'] >= pd.Timestamp(start_date))
                    & (derived_df['trade_date'] <= pd.Timestamp(end_date))
                ].copy()

    if native_df.empty and derived_df.empty:
        return pd.DataFrame(columns=['trade_date', 'ts_code']), selection.to_dict()
    if native_df.empty:
        panel = derived_df.copy()
    elif derived_df.empty:
        panel = native_df.copy()
    else:
        panel = native_df.merge(derived_df, on=['trade_date', 'ts_code'], how='left')

    if include_close:
        raw_panel = load_raw_panel(codes, start_date, end_date)
        if not raw_panel.empty and 'close' in raw_panel.columns:
            close_frame = raw_panel[['trade_date', 'ts_code', 'close']].drop_duplicates(['trade_date', 'ts_code'])
            panel = panel.merge(close_frame, on=['trade_date', 'ts_code'], how='left')
    panel = panel.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
    return panel, selection.to_dict()
