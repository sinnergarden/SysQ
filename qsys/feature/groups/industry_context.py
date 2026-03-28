from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from qsys.config import cfg


def attach_industry_info(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    meta = Path(cfg.get_path('root')) / 'meta.db'
    with sqlite3.connect(meta) as conn:
        stock_basic = pd.read_sql('select ts_code, industry from stock_basic', conn)
    return out.merge(stock_basic, on='ts_code', how='left')


def build_industry_context_features(df: pd.DataFrame) -> pd.DataFrame:
    out = attach_industry_info(df)
    out['ret_1d_ind_tmp'] = out.groupby('ts_code')['close'].pct_change(1)
    out['ret_3d_ind_tmp'] = out.groupby('ts_code')['close'].pct_change(3)
    out['ret_5d_ind_tmp'] = out.groupby('ts_code')['close'].pct_change(5)

    out['industry_ret_1d'] = out.groupby(['trade_date', 'industry'])['ret_1d_ind_tmp'].transform('mean')
    out['industry_ret_3d'] = out.groupby(['trade_date', 'industry'])['ret_3d_ind_tmp'].transform('mean')
    out['industry_ret_5d'] = out.groupby(['trade_date', 'industry'])['ret_5d_ind_tmp'].transform('mean')
    out['industry_breadth'] = out.groupby(['trade_date', 'industry'])['ret_1d_ind_tmp'].transform(lambda s: (s > 0).mean())
    out['stock_minus_industry_ret'] = out['ret_1d_ind_tmp'] - out['industry_ret_1d']
    out['stock_minus_industry_ret_3d'] = out['ret_3d_ind_tmp'] - out['industry_ret_3d']
    out['stock_minus_industry_ret_5d'] = out['ret_5d_ind_tmp'] - out['industry_ret_5d']
    return out
