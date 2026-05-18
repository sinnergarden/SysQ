import unittest
from unittest.mock import patch

import pandas as pd

from qsys.data.adapter import QlibAdapter


class TestQlibAdapterMetadataFields(unittest.TestCase):
    def test_get_features_joins_meta_fields_without_requesting_invalid_qlib_expressions(self):
        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2026-05-14"), "AAA"),
                (pd.Timestamp("2026-05-14"), "BBB"),
                (pd.Timestamp("2026-05-15"), "AAA"),
                (pd.Timestamp("2026-05-15"), "BBB"),
            ],
            names=["datetime", "instrument"],
        )
        native = pd.DataFrame(
            {
                "$close": [10.0, 20.0, 11.0, 21.0],
                "$circ_mv": [100.0, 200.0, 120.0, 220.0],
                "$amount": [1000.0, 3000.0, 1500.0, 3500.0],
            },
            index=idx,
        )
        stock_basic = pd.DataFrame(
            [
                {"ts_code": "AAA", "industry": "bank", "market": "main"},
                {"ts_code": "BBB", "industry": "tech", "market": "main"},
            ]
        )

        adapter = QlibAdapter()
        with patch.object(__import__("qsys.data.adapter", fromlist=["DatasetD"]).DatasetD, "dataset", return_value=native, create=True) as mock_dataset, patch(
            "qsys.data.adapter.StockDataStore.get_stock_list", return_value=stock_basic
        ):
            frame = adapter.get_features(
                ["AAA", "BBB"],
                ["industry", "market_cap_bucket", "liquidity_bucket"],
                start_time="2026-05-14",
                end_time="2026-05-15",
            )

        requested_fields = mock_dataset.call_args.args[1]
        self.assertNotIn("industry", requested_fields)
        self.assertIn("$circ_mv", requested_fields)
        self.assertIn("$amount", requested_fields)
        self.assertEqual(list(frame.columns), ["industry", "market_cap_bucket", "liquidity_bucket"])
        self.assertEqual(frame.loc[(pd.Timestamp("2026-05-14"), "AAA"), "industry"], "bank")
        self.assertEqual(frame.loc[(pd.Timestamp("2026-05-14"), "BBB"), "industry"], "tech")
        self.assertTrue(frame["market_cap_bucket"].notna().all())
        self.assertTrue(frame["liquidity_bucket"].notna().all())


if __name__ == "__main__":
    unittest.main()
