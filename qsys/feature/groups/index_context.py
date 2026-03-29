from __future__ import annotations

from pathlib import Path
import pandas as pd

INDEX_CODE_MAP = {
    'sse': '000001.SH',
    'hs300': '000300.SH',
    'zz500': '000905.SH',
    'zz1000': '000852.SH',
    'cyb': '399006.SZ',
    'kc50': '000688.SH',
}


def load_index_daily(index_name: str = 'hs300', root: str = '/home/liuming/.openclaw/workspace/SysQ/data/raw/index') -> pd.DataFrame:
    code = INDEX_CODE_MAP[index_name]
    path = Path(root) / f'{code}.csv'
    df = pd.read_csv(path)
    df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str))
    return df[['trade_date', 'close']].rename(columns={'close': f'{index_name}_close'})


def attach_index_context(df: pd.DataFrame, index_name: str = 'hs300') -> pd.DataFrame:
    out = df.copy()
    idx = load_index_daily(index_name=index_name)
    out = out.merge(idx, on='trade_date', how='left')
    out['index_close'] = out[f'{index_name}_close']
    return out
