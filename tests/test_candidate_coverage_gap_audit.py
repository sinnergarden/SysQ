import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qsys.ops.candidate_coverage_gap import (
    CORE_FIELDS,
    build_candidate_gap_audit,
    classify_gap_reason,
    write_candidate_gap_artifacts,
)


class TestCandidateCoverageGapAudit(unittest.TestCase):
    def test_gap_reason_classification(self):
        date = pd.Timestamp("2025-01-03")
        self.assertEqual(
            classify_gap_reason(
                instrument_active=True,
                listed_before_date=True,
                instrument_start_date=pd.Timestamp("2025-01-02"),
                raw_first_date=pd.Timestamp("2025-01-02"),
                raw_last_date=pd.Timestamp("2025-01-03"),
                raw_available=False,
                raw_value_non_null=False,
                qlib_available=False,
                qlib_value_non_null=False,
                paused_value=None,
                date=date,
            ),
            "raw_missing",
        )
        self.assertEqual(
            classify_gap_reason(
                instrument_active=True,
                listed_before_date=True,
                instrument_start_date=pd.Timestamp("2025-01-02"),
                raw_first_date=pd.Timestamp("2025-01-02"),
                raw_last_date=pd.Timestamp("2025-01-03"),
                raw_available=True,
                raw_value_non_null=True,
                qlib_available=False,
                qlib_value_non_null=False,
                paused_value=None,
                date=date,
            ),
            "qlib_missing",
        )
        self.assertEqual(
            classify_gap_reason(
                instrument_active=True,
                listed_before_date=True,
                instrument_start_date=pd.Timestamp("2025-01-02"),
                raw_first_date=pd.Timestamp("2025-01-02"),
                raw_last_date=pd.Timestamp("2025-01-03"),
                raw_available=True,
                raw_value_non_null=True,
                qlib_available=True,
                qlib_value_non_null=False,
                paused_value=None,
                date=date,
            ),
            "qlib_field_nan",
        )
        self.assertEqual(
            classify_gap_reason(
                instrument_active=True,
                listed_before_date=False,
                instrument_start_date=pd.Timestamp("2025-01-03"),
                raw_first_date=pd.Timestamp("2025-01-03"),
                raw_last_date=pd.Timestamp("2025-01-03"),
                raw_available=False,
                raw_value_non_null=False,
                qlib_available=False,
                qlib_value_non_null=False,
                paused_value=None,
                date=pd.Timestamp("2025-01-02"),
            ),
            "pre_listing_date",
        )
        self.assertEqual(
            classify_gap_reason(
                instrument_active=False,
                listed_before_date=True,
                instrument_start_date=pd.Timestamp("2025-01-02"),
                raw_first_date=pd.Timestamp("2025-01-02"),
                raw_last_date=pd.Timestamp("2025-01-03"),
                raw_available=False,
                raw_value_non_null=False,
                qlib_available=False,
                qlib_value_non_null=False,
                paused_value=None,
                date=date,
            ),
            "not_in_instrument_active_range",
        )

    def test_denominator_comparison_and_recommendation(self):
        calendar_dates = [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")]
        instrument_df = pd.DataFrame(
            [{"instrument": "000001.SZ", "start_date": "2025-01-02", "end_date": "2025-01-03"}]
        )
        raw_frames = {
            "000001.SZ": pd.DataFrame(
                {
                    "trade_date": [pd.Timestamp("2025-01-03")],
                    "$open": [1.0],
                    "$high": [1.0],
                    "$low": [1.0],
                    "$close": [1.0],
                    "$volume": [1.0],
                    "$amount": [1.0],
                    "paused": [0],
                }
            )
        }
        qlib_frame = pd.DataFrame(
            {
                "instrument": ["000001.SZ", "000001.SZ"],
                "datetime": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
                "$open": [float("nan"), 1.0],
                "$high": [float("nan"), 1.0],
                "$low": [float("nan"), 1.0],
                "$close": [float("nan"), 1.0],
                "$volume": [float("nan"), 1.0],
                "$amount": [float("nan"), 1.0],
            }
        )
        audit = build_candidate_gap_audit(
            calendar_dates=calendar_dates,
            instrument_df=instrument_df,
            raw_frames=raw_frames,
            qlib_frame=qlib_frame,
            validation_summary={"core_market_field_coverage_min": 0.5},
            candidate_qlib_path="candidate",
            start_date="2025-01-02",
            end_date="2025-01-03",
        )
        summary = audit["summary"]
        recommendation = audit["recommendation"]
        self.assertAlmostEqual(summary["naive_core_market_field_coverage"], 0.5)
        self.assertAlmostEqual(summary["eligible_core_market_field_coverage"], 1.0)
        self.assertEqual(summary["excluded_static_universe_cells"], 6)
        self.assertEqual(summary["true_missing_cells"], 0)
        self.assertEqual(summary["eligible_non_null_cells"], 6)
        self.assertEqual(recommendation["root_cause"], "validator_denominator_too_strict")
        self.assertFalse(recommendation["safe_to_switch_candidate"])

    def test_true_missing_remains_no_go(self):
        calendar_dates = [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")]
        instrument_df = pd.DataFrame(
            [{"instrument": "000001.SZ", "start_date": "2025-01-02", "end_date": "2025-01-03"}]
        )
        raw_frames = {
            "000001.SZ": pd.DataFrame(
                {
                    "trade_date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
                    "$open": [1.0, 1.0],
                    "$high": [1.0, 1.0],
                    "$low": [1.0, 1.0],
                    "$close": [1.0, 1.0],
                    "$volume": [1.0, 1.0],
                    "$amount": [1.0, 1.0],
                    "paused": [0, 0],
                }
            )
        }
        qlib_frame = pd.DataFrame(
            {
                "instrument": ["000001.SZ", "000001.SZ"],
                "datetime": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
                "$open": [1.0, float("nan")],
                "$high": [1.0, float("nan")],
                "$low": [1.0, float("nan")],
                "$close": [1.0, float("nan")],
                "$volume": [1.0, float("nan")],
                "$amount": [1.0, float("nan")],
            }
        )
        audit = build_candidate_gap_audit(
            calendar_dates=calendar_dates,
            instrument_df=instrument_df,
            raw_frames=raw_frames,
            qlib_frame=qlib_frame,
            validation_summary={"core_market_field_coverage_min": 0.5},
            candidate_qlib_path="candidate",
            start_date="2025-01-02",
            end_date="2025-01-03",
        )
        summary = audit["summary"]
        recommendation = audit["recommendation"]
        self.assertAlmostEqual(summary["eligible_core_market_field_coverage"], 0.5)
        self.assertEqual(summary["naive_core_market_field_coverage"], 0.5)
        self.assertEqual(summary["true_missing_cells"], len(CORE_FIELDS))
        self.assertFalse(recommendation["safe_to_switch_candidate"])
        self.assertNotEqual(recommendation["root_cause"], "validator_denominator_too_strict")

    def test_artifact_contract(self):
        calendar_dates = [pd.Timestamp("2025-01-02")]
        instrument_df = pd.DataFrame(
            [{"instrument": "000001.SZ", "start_date": "2025-01-02", "end_date": "2025-01-02"}]
        )
        raw_frames = {
            "000001.SZ": pd.DataFrame(
                {
                    "trade_date": [pd.Timestamp("2025-01-02")],
                    "$open": [1.0],
                    "$high": [1.0],
                    "$low": [1.0],
                    "$close": [1.0],
                    "$volume": [1.0],
                    "$amount": [1.0],
                    "paused": [0],
                }
            )
        }
        qlib_frame = pd.DataFrame(
            {
                "instrument": ["000001.SZ"],
                "datetime": [pd.Timestamp("2025-01-02")],
                "$open": [1.0],
                "$high": [1.0],
                "$low": [1.0],
                "$close": [1.0],
                "$volume": [1.0],
                "$amount": [1.0],
            }
        )
        audit = build_candidate_gap_audit(
            calendar_dates=calendar_dates,
            instrument_df=instrument_df,
            raw_frames=raw_frames,
            qlib_frame=qlib_frame,
            validation_summary={"core_market_field_coverage_min": 1.0},
            candidate_qlib_path="candidate",
            start_date="2025-01-02",
            end_date="2025-01-02",
        )
        sample_rows = [
            {
                "bucket": "A_best",
                "symbol": "000001.SZ",
                "row_count": 1,
                "first_date": "2025-01-02",
                "last_date": "2025-01-02",
                "non_null_ratio": 1.0,
                "has_only_one_row": True,
                "has_2025_history": True,
                "has_2026_04_30": False,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            write_candidate_gap_artifacts(output_dir=out_dir, audit=audit, sample_rows=sample_rows)
            summary = json.loads((out_dir / "coverage_gap_summary.json").read_text(encoding="utf-8"))
            recommendation = json.loads((out_dir / "candidate_validation_recommendation.json").read_text(encoding="utf-8"))
            by_symbol = pd.read_csv(out_dir / "core_field_gap_by_symbol.csv")
            self.assertIn("naive_core_market_field_coverage", summary)
            self.assertIn("eligible_core_market_field_coverage", summary)
            self.assertIn("root_cause", recommendation)
            self.assertIn("safe_to_switch_candidate", recommendation)
            self.assertTrue(set(["symbol", "eligible_coverage", "true_missing_cells"]).issubset(by_symbol.columns))


if __name__ == "__main__":
    unittest.main()
