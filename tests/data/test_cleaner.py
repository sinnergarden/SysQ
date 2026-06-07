"""Tests for qsys.data.cleaner — dirty suffix coalesce and detection."""

from __future__ import annotations

import unittest

import pandas as pd

from qsys.data.cleaner import (
    DIRTY_SUFFIX_RE,
    coalesce_merge_suffix_columns,
    has_dirty_columns,
)


class TestDirtySuffixRegex(unittest.TestCase):
    """DIRTY_SUFFIX_RE must match _x, _y, __src but not normal columns."""

    def test_matches_close__src(self):
        self.assertTrue(DIRTY_SUFFIX_RE.search("close__src"))

    def test_matches_open_x(self):
        self.assertTrue(DIRTY_SUFFIX_RE.search("open_x"))

    def test_matches_high_y(self):
        self.assertTrue(DIRTY_SUFFIX_RE.search("high_y"))

    def test_does_not_match_normal(self):
        self.assertIsNone(DIRTY_SUFFIX_RE.search("close"))
        self.assertIsNone(DIRTY_SUFFIX_RE.search("volume"))
        self.assertIsNone(DIRTY_SUFFIX_RE.search("open_x1"))  # _x mid-string
        self.assertIsNone(DIRTY_SUFFIX_RE.search("buy_x_sm"))  # embedded


class TestHasDirtyColumns(unittest.TestCase):
    def test_false_when_clean(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close": [1.0]})
        self.assertFalse(has_dirty_columns(df))

    def test_true_when_close__src(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close__src": [1.0]})
        self.assertTrue(has_dirty_columns(df))

    def test_true_when_x_suffix(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "open_x": [10.0]})
        self.assertTrue(has_dirty_columns(df))

    def test_true_when_y_suffix(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "amount_y": [100.0]})
        self.assertTrue(has_dirty_columns(df))


class TestCoalesceMergeSuffixColumns(unittest.TestCase):
    def test_coalesces_close_x_close_y_to_close(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close_x": [10.0], "close_y": [11.0]})
        result = coalesce_merge_suffix_columns(df)
        self.assertIn("close", result.columns)
        self.assertNotIn("close_x", result.columns)
        self.assertNotIn("close_y", result.columns)
        self.assertEqual(result.loc[0, "close"], 10.0)  # close_x first

    def test_coalesces_open_x_open_y_to_open(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "open_x": [10.0], "open_y": [11.0]})
        result = coalesce_merge_suffix_columns(df)
        self.assertIn("open", result.columns)
        self.assertNotIn("open_x", result.columns)
        self.assertNotIn("open_y", result.columns)
        self.assertEqual(result.loc[0, "open"], 10.0)

    def test_removes_close__src(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close__src": [1.0]})
        result = coalesce_merge_suffix_columns(df)
        self.assertNotIn("close__src", result.columns)

    def test_combined_dirty_columns_all_removed(self):
        df = pd.DataFrame({
            "trade_date": ["20260101"],
            "close__src": [1.0],
            "open_x": [10.0],
            "open_y": [11.0],
        })
        result = coalesce_merge_suffix_columns(df)
        dirty_remain = [c for c in result.columns if DIRTY_SUFFIX_RE.search(c)]
        self.assertEqual(dirty_remain, [])

    def test_clean_dataframe_passes_through(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close": [1.0], "volume": [100]})
        result = coalesce_merge_suffix_columns(df)
        # All canonical columns are created with NaN fallback if missing; clean columns remain
        self.assertIn("trade_date", result.columns)
        self.assertIn("close", result.columns)
        self.assertIn("volume", result.columns)
        self.assertFalse(has_dirty_columns(result))
