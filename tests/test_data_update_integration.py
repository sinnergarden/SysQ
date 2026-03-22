import sys
import unittest
import tempfile
from pathlib import Path
import pandas as pd
import numpy as np
from typing import cast

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore


class DummyPro:
    def __init__(self, calls):
        self.calls = calls

    def _date_range(self, start_date, end_date):
        dates = pd.date_range(start=pd.to_datetime(start_date), end=pd.to_datetime(end_date), freq="D")
        return [d.strftime("%Y%m%d") for d in dates]

    def daily(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("daily", start_date, end_date))
        trade_dates = self._date_range(start_date, end_date)
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": trade_dates,
            "open": np.arange(n) + 10.0,
            "high": np.arange(n) + 11.0,
            "low": np.arange(n) + 9.0,
            "close": np.arange(n) + 10.5,
            "vol": np.arange(n) + 100.0
        })

    def adj_factor(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("adj_factor", start_date, end_date))
        trade_dates = self._date_range(start_date, end_date)
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": trade_dates,
            "adj_factor": np.ones(n)
        })

    def daily_basic(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("daily_basic", start_date, end_date))
        trade_dates = self._date_range(start_date, end_date)
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": trade_dates,
            "turnover_rate": np.zeros(n),
            "pe": np.zeros(n),
            "pb": np.zeros(n),
            "circ_mv": np.zeros(n)
        })

    def stk_limit(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("stk_limit", start_date, end_date))
        trade_dates = self._date_range(start_date, end_date)
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": trade_dates,
            "up_limit": np.zeros(n),
            "down_limit": np.zeros(n)
        })

    def moneyflow(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("moneyflow", start_date, end_date))
        trade_dates = self._date_range(start_date, end_date)
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [ts_code] * n,
            "trade_date": trade_dates,
            "buy_sm_amount": np.zeros(n),
            "buy_md_amount": np.zeros(n),
            "buy_lg_amount": np.zeros(n),
            "buy_elg_amount": np.zeros(n),
            "sell_sm_amount": np.zeros(n),
            "sell_md_amount": np.zeros(n),
            "sell_lg_amount": np.zeros(n),
            "sell_elg_amount": np.zeros(n),
            "net_mf_amount": np.zeros(n),
            "net_mf_vol": np.zeros(n)
        })

    def income(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("income", start_date, end_date))
        return pd.DataFrame({
            "ts_code": [ts_code],
            "end_date": [end_date],
            "n_income": [0.0],
            "revenue": [0.0]
        })

    def balancesheet(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("balancesheet", start_date, end_date))
        return pd.DataFrame({
            "ts_code": [ts_code],
            "end_date": [end_date],
            "total_hldr_eqy_exc_min_int": [0.0],
            "total_assets": [0.0]
        })

    def cashflow(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("cashflow", start_date, end_date))
        return pd.DataFrame({
            "ts_code": [ts_code],
            "end_date": [end_date],
            "n_cashflow_act": [0.0]
        })

    def fina_indicator(self, ts_code, start_date, end_date, fields=None):
        self.calls.append(("fina_indicator", start_date, end_date))
        return pd.DataFrame({
            "ts_code": [ts_code],
            "ann_date": [end_date],
            "end_date": [end_date],
            "roe": [0.0],
            "roe_ttm": [0.0],
            "grossprofit_margin": [0.0],
            "debt_to_assets": [0.0],
            "current_ratio": [0.0],
            "q_dt_profit": [0.0],
            "q_gr_yoy": [0.0]
        })


class TestDataUpdateIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_dirs = cfg.dirs.copy()
        cfg.dirs = {
            "root": self.root,
            "raw": self.root / "raw",
            "raw_daily": self.root / "raw" / "daily",
            "meta": self.root / "meta",
            "db": self.root,
            "qlib_bin": self.root / "qlib_bin",
            "feature": self.root / "feature",
            "clean": self.root / "clean"
        }
        for path in cfg.dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        self.store = StockDataStore()

    def tearDown(self):
        cfg.dirs = self.original_dirs
        self.temp_dir.cleanup()

    def _seed_existing(self, code, start_date, end_date):
        dates = pd.date_range(start=pd.to_datetime(start_date), end=pd.to_datetime(end_date), freq="D")
        trade_dates = [d.strftime("%Y%m%d") for d in dates]
        df = pd.DataFrame({
            "ts_code": [code] * len(trade_dates),
            "trade_date": trade_dates,
            "open": np.arange(len(trade_dates)) + 10.0,
            "high": np.arange(len(trade_dates)) + 11.0,
            "low": np.arange(len(trade_dates)) + 9.0,
            "close": np.arange(len(trade_dates)) + 10.5,
            "vol": np.arange(len(trade_dates)) + 100.0,
            "adj_factor": np.ones(len(trade_dates))
        })
        self.store.save_daily(df, code)

    def test_incremental_skip_when_up_to_date(self):
        code = "000001.SZ"
        self._seed_existing(code, "20230101", "20230103")
        calls = []
        collector = TushareCollector()
        collector.__dict__["pro"] = DummyPro(calls)
        collector._fetch_with_retry = lambda api_func, **kwargs: api_func(**kwargs)
        collector.update_history(code, start_date="20230101", end_date="20230103", incremental=True)
        self.assertEqual(len(calls), 0)

    def test_incremental_fetches_missing_range_only(self):
        code = "000001.SZ"
        self._seed_existing(code, "20230101", "20230103")
        calls = []
        collector = TushareCollector()
        collector.__dict__["pro"] = DummyPro(calls)
        collector._fetch_with_retry = lambda api_func, **kwargs: api_func(**kwargs)
        collector.update_history(code, start_date="20230101", end_date="20230105", incremental=True)
        daily_calls = [c for c in calls if c[0] == "daily"]
        self.assertTrue(daily_calls)
        self.assertEqual(daily_calls[0][1], "20230104")
        updated = self.store.load_daily(code)
        if updated is None:
            self.fail("No data saved after incremental update")
        updated_df = cast(pd.DataFrame, updated)
        self.assertEqual(updated_df["trade_date"].max(), "20230105")

    def test_filters_non_open_days(self):
        cal = pd.DataFrame({
            "exchange": ["SSE"] * 3,
            "cal_date": ["20230101", "20230102", "20230103"],
            "is_open": [1, 0, 1]
        })
        self.store.save_meta_calendar(cal)
        code = "000001.SZ"
        calls = []
        collector = TushareCollector()
        collector.__dict__["pro"] = DummyPro(calls)
        collector._fetch_with_retry = lambda api_func, **kwargs: api_func(**kwargs)
        collector.update_history(code, start_date="20230101", end_date="20230103", incremental=False)
        updated = self.store.load_daily(code)
        if updated is None:
            self.fail("No data saved after update")
        updated_df = cast(pd.DataFrame, updated)
        self.assertNotIn("20230102", updated_df["trade_date"].tolist())

    def test_adapter_get_data_status_report(self):
        """Test the new status report method returns raw vs qlib info"""
        # Seed some raw data
        self._seed_existing("000001.SZ", "20230101", "20230105")
        self._seed_existing("000002.SZ", "20230101", "20230105")
        
        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter()
        
        # Test the report generation
        # Note: without qlib initialized, we can still check raw data
        report = adapter.get_data_status_report()
        
        self.assertIn('raw_latest', report)
        self.assertIn('qlib_latest', report)
        self.assertIn('target_signal_date', report)
        self.assertIn('aligned', report)
        
        # Raw should have data
        self.assertEqual(report['raw_latest'], "2023-01-05")
