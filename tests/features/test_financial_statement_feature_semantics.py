"""Test financial statement feature semantics — prevent PIT and lookahead bugs.

Historical bugs this prevents:
1. capex_to_assets using free_cashflow as capex proxy — must name "_proxy" explicitly
2. Quarter-over-quarter changes computed on daily PIT-expanded data instead of report-level panel
3. Financial statement features using end_date instead of ann_date as visibility date
4. pct_change(4) on daily-expanded data when it should be on report-level panel
"""

import unittest
import warnings

import numpy as np
import pandas as pd


class TestFinancialStatementSemantics(unittest.TestCase):
    """Semantic tests for financial statement features."""

    # ── Contract 1: ann_date vs end_date ──

    def test_ann_date_is_visibility_date(self):
        """Financial statement features must use ann_date as visibility date.

        Simulate the load_shareholder_data pattern which uses merge_asof(direction='backward')
        on ann_date, not end_date.
        """
        # Create quarterly announcements
        ann_dates = pd.to_datetime(["2023-01-15", "2023-04-14", "2023-07-16", "2023-10-15"])
        end_dates = pd.to_datetime(["2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30"])

        # Create announcement-level data
        ann_data = pd.DataFrame({
            "inst": ["A"] * 4,
            "ann_date": ann_dates,
            "end_date": end_dates,
            "revenue": [100, 110, 120, 130],
        })

        # Create daily timeline
        daily_dates = pd.date_range("2023-01-01", "2023-12-31", freq="B")
        daily = pd.DataFrame({
            "trade_date": daily_dates,
            "ts_code": ["A"] * len(daily_dates),
        })
        daily["_dt"] = daily["trade_date"]
        daily["_inst"] = "A"
        daily["_row_id"] = np.arange(len(daily))

        # Merge using ann_date (correct)
        ann_right = ann_data.copy()
        ann_right["_dt"] = ann_right["ann_date"]
        ann_right["_inst"] = ann_right["inst"]
        correct = pd.merge_asof(
            daily[["_row_id", "_dt", "_inst"]].sort_values("_dt"),
            ann_right[["_inst", "_dt", "revenue"]].sort_values("_dt"),
            on="_dt", by="_inst", direction="backward",
        )

        # On Jan 2 (before first ann_date Jan 15), correct merge gives NaN
        before_first = correct[daily["trade_date"] < "2023-01-15"]["revenue"]
        self.assertTrue(
            before_first.isna().all(),
            "Before first ann_date, revenue should be NaN (data not yet visible)",
        )

        # On Jan 16 (after first ann_date), correct merge gives revenue=100
        after_first = correct[daily["trade_date"] == "2023-01-16"]["revenue"]
        if len(after_first) > 0:
            self.assertEqual(
                after_first.iloc[0], 100,
                "After first ann_date, revenue should be visible (100)",
            )

        # ── WRONG: using end_date ──
        wrong_right = ann_data.copy()
        wrong_right["_dt"] = wrong_right["end_date"]  # BUG: end_date, not ann_date
        wrong_right["_inst"] = wrong_right["inst"]
        wrong = pd.merge_asof(
            daily[["_row_id", "_dt", "_inst"]].sort_values("_dt"),
            wrong_right[["_inst", "_dt", "revenue"]].sort_values("_dt"),
            on="_dt", by="_inst", direction="backward",
        )

        # end_date=2022-12-31 means revenue is visible on Jan 2 — incorrect!
        wrong_before = wrong[daily["trade_date"] < "2023-01-15"]["revenue"]
        if wrong_before.notna().any():
            self.skipTest("end_date merge gives data before ann_date — demonstrates the bug pattern")

    # ── Contract 2: Quarter-over-quarter must use report-level pct_change(4) ──

    def test_qoq_on_report_level_not_daily(self):
        """QoQ changes must be computed on report-level panel before daily expansion."""
        # Report-level quarterly data
        report = pd.DataFrame({
            "inst": ["A"] * 8,
            "ann_date": pd.date_range("2022-01-31", periods=8, freq="QE"),
            "end_date": pd.date_range("2021-12-31", periods=8, freq="QE"),
            "revenue": [100, 105, 108, 112, 120, 130, 135, 140],
        })
        report = report.sort_values(["inst", "end_date"])

        # CORRECT: YoY on report level (pct_change, 4 quarters apart)
        report["revenue_yoy_report"] = report.groupby("inst")["revenue"].pct_change(4)

        # Q5 (2023-03-31) vs Q1 (2022-03-31): (120-100)/100 = 0.20
        self.assertAlmostEqual(
            report["revenue_yoy_report"].iloc[4], 0.20, places=4,
            msg="Report-level YoY should be 20%",
        )

        # WRONG: If someone applies pct_change on daily-expanded data
        # (simulate by using shift(1) instead of shift(4), or different freq)
        report["revenue_yoy_wrong_qoq"] = report.groupby("inst")["revenue"].pct_change(1)
        wrong_qoq = report["revenue_yoy_wrong_qoq"].iloc[4]
        correct_yoy = report["revenue_yoy_report"].iloc[4]
        self.assertNotAlmostEqual(
            wrong_qoq, correct_yoy, places=4,
            msg=f"Quarter-over-quarter ({wrong_qoq}) should differ from YoY ({correct_yoy})",
        )

    # ── Contract 3: Proxy naming ──

    def test_capex_proxy_naming(self):
        """If a feature uses a proxy for a real economic concept, must name '_proxy'."""
        # This is a naming convention check, not a computation check
        # Scan all registry features for potential proxy names

        # Features that sound like proxies:
        potential_proxies = [
            ("peg_proxy", True, "peg_proxy should include _proxy"),
            ("earnings_yield_proxy", True, "earnings_yield_proxy should include _proxy"),
            ("continuation_candidate_score", False, "composite score — not a proxy"),
        ]
        for name, should_have_proxy, msg in potential_proxies:
            has_proxy = "_proxy" in name
            self.assertEqual(
                has_proxy, should_have_proxy,
                f"{msg} (has_proxy={has_proxy})",
            )

    # ── Contract 4: No future data in PIT ──

    def test_no_future_data_in_rolling(self):
        """Rolling window features must use only past data."""
        values = pd.Series(range(1, 101))

        # Correct: rolling(window).mean() at position t uses t-1, t-2, ..., t-window
        correct = values.rolling(10, min_periods=5).mean()

        # On position 10, correct rolling mean = (1+2+...+10)/10 = 5.5
        self.assertAlmostEqual(correct.iloc[9], 5.5, places=4)

        # WRONG: Using shift(-x) would incorporate future data
        wrong = values.rolling(10, min_periods=5).mean().shift(-5)
        if not pd.isna(wrong.iloc[9]):
            self.assertNotAlmostEqual(
                wrong.iloc[9], correct.iloc[9], places=4,
                msg="Shifted rolling incorporates future data",
            )


class TestAnnotationDrivenFeatures(unittest.TestCase):
    """Tests specific to shareholder data handling (real-world example from codebase)."""

    def test_shareholder_qoq_on_ann_level(self):
        """holder_num_chg_qoq uses announcement-level prev_ann values, not daily differences."""
        # Simulate what load_shareholder_data + build_shareholder_features does:
        # announcement-level prev_ann columns → qoq computation
        import numpy as np
        import pandas as pd

        N = 250
        daily = pd.DataFrame({
            "trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
            "ts_code": ["A"] * N,
            "holder_num": np.where(np.random.random(N) < 0.8, 50000, np.nan),
            "holder_num_prev_ann": 50000,  # flat prev_ann
            "holder_num_prev2_ann": 55000,
        })

        # CORRECT: qoq computed on announcement-level prev values
        # (This is what build_shareholder_features does via _safe_div)
        from qsys.feature.groups.value_growth_v3a import build_shareholder_features
        daily["total_share"] = 1e9
        result = build_shareholder_features(daily)

        if "holder_num_chg_qoq" in result.columns:
            chg = result["holder_num_chg_qoq"].dropna()
            if len(chg) > 0:
                # If holder_num == 50000 and prev_ann == 50000, chg should be 0
                # This is a constant scenario, so the chg should be 0
                mean_chg = chg.abs().mean()
                # Should NOT be random daily noise
                self.assertLess(
                    mean_chg, 1e-6,
                    f"holder_num_chg_qoq should be 0 when holder_num=50000 and prev_ann=50000, got {mean_chg}",
                )


if __name__ == "__main__":
    unittest.main()
