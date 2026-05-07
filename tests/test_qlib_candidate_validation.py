import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.qlib_candidate import apply_candidate_switch, plan_candidate_switch, validate_candidate


FAKE_GAP_AUDIT = {
    "coverage_gap_summary": {
        "naive_core_market_field_coverage": 0.955068882962862,
        "eligible_core_market_field_coverage": 1.0,
        "eligible_non_null_cells": 6725958,
        "eligible_cells": 6725958,
        "true_missing_cells": 0,
        "excluded_pre_listing_cells": 0,
        "excluded_not_active_cells": 0,
        "excluded_suspended_cells": 316422,
        "excluded_static_universe_cells": 0,
    }
}


class _FakeCandidateAdapter:
    def __init__(self, *args, qlib_dir=None, **kwargs):
        self.qlib_dir = Path(qlib_dir)

    def init_qlib(self):
        return None

    def get_features(self, symbols, fields, start_time=None, end_time=None):
        index = pd.MultiIndex.from_tuples(
            [
                ("000001.SZ", pd.Timestamp("2026-04-30")),
                ("000002.SZ", pd.Timestamp("2026-04-28")),
                ("000002.SZ", pd.Timestamp("2026-04-29")),
                ("000002.SZ", pd.Timestamp("2026-04-30")),
                ("000003.SZ", pd.Timestamp("2026-04-28")),
                ("000003.SZ", pd.Timestamp("2026-04-29")),
                ("000003.SZ", pd.Timestamp("2026-04-30")),
            ],
            names=["instrument", "datetime"],
        )
        return pd.DataFrame(
            {
                "$open": [1, 1, 1, 1, 1, 1, 1],
                "$high": [1, 1, 1, 1, 1, 1, 1],
                "$low": [1, 1, 1, 1, 1, 1, 1],
                "$close": [1, 1, 1, 1, 1, 1, 1],
                "$volume": [1, 1, 1, 1, 1, 1, 1],
                "$amount": [1, 1, 1, 1, 1, 1, 1],
            },
            index=index,
        )


class TestQlibCandidateValidation(unittest.TestCase):
    def test_validation_detects_only_one_row_duplicate_and_future_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            candidate_dir = base_dir / "data" / "qlib_bin_candidate"
            (candidate_dir / "_build_meta").mkdir(parents=True, exist_ok=True)
            (candidate_dir / "calendars").mkdir(parents=True, exist_ok=True)
            (candidate_dir / "calendars" / "day.txt").write_text("2026-04-28\n2026-04-29\n2026-04-30\n", encoding="utf-8")
            pd.DataFrame(
                [
                    {"symbol": "000001.SZ", "source_row_count": 1, "source_unique_date_count": 1, "source_duplicate_date_count": 0, "source_future_date_count": 0},
                    {"symbol": "000002.SZ", "source_row_count": 3, "source_unique_date_count": 2, "source_duplicate_date_count": 1, "source_future_date_count": 0},
                    {"symbol": "000003.SZ", "source_row_count": 3, "source_unique_date_count": 3, "source_duplicate_date_count": 0, "source_future_date_count": 1},
                ]
            ).to_csv(candidate_dir / "_build_meta" / "source_symbol_coverage.csv", index=False)

            with patch("qsys.ops.qlib_candidate.QlibAdapter", side_effect=lambda *args, qlib_dir=None, **kwargs: _FakeCandidateAdapter(qlib_dir=qlib_dir)), patch(
                "qsys.ops.qlib_candidate.read_calendar_summary",
                return_value={"calendar_first_date": "2026-04-28", "calendar_last_date": "2026-04-30", "calendar_count": 3},
            ), patch(
                "qsys.ops.qlib_candidate.summarize_universe_registry"
            ) as mock_registry, patch(
                "qsys.ops.qlib_candidate.build_instrument_coverage_rows",
                return_value=[
                    {"instrument": "000001.SZ"},
                    {"instrument": "000002.SZ"},
                    {"instrument": "000003.SZ"},
                ],
            ), patch(
                "qsys.ops.qlib_candidate.run_candidate_gap_audit",
                return_value=FAKE_GAP_AUDIT,
            ):
                mock_registry.return_value.to_dict.return_value = {
                    "instrument_total": 800,
                    "active_on_trade_date": 800,
                    "stale_end_date_count": 0,
                }
                summary = validate_candidate(
                    base_dir=base_dir,
                    candidate_qlib_dir=candidate_dir,
                    output_dir=base_dir / "out",
                )

            self.assertEqual(summary["go_no_go"], "No-Go")
            self.assertAlmostEqual(summary["naive_core_market_field_coverage"], 0.955068882962862)
            self.assertAlmostEqual(summary["eligible_core_market_field_coverage"], 1.0)
            self.assertEqual(summary["excluded_suspended_or_no_trade_cells"], 316422)
            self.assertEqual(summary["true_missing_cells"], 0)
            self.assertEqual(summary["coverage_denominator_mode"], "eligible_trading_cells")
            self.assertEqual(summary["coverage_gate_status"], "pass")
            self.assertIn("eligible_core_market_field_coverage_ge_0_98", summary["hard_gate"])
            self.assertNotIn("core_market_field_coverage_ge_0_98", summary["hard_gate"])
            failed = (base_dir / "out" / "candidate_failed_symbols.csv").read_text(encoding="utf-8")
            self.assertIn("000001.SZ", failed)
            self.assertIn("000002.SZ", failed)
            self.assertIn("000003.SZ", failed)

    def test_switch_plan_does_not_execute_and_failed_validation_blocks_switch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            candidate_dir = base_dir / "data" / "qlib_bin_candidate"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            validation_path = base_dir / "out" / "candidate_validation_summary.json"
            validation_path.parent.mkdir(parents=True, exist_ok=True)
            validation_path.write_text(json.dumps({"go_no_go": "No-Go"}) + "\n", encoding="utf-8")

            plan = plan_candidate_switch(
                base_dir=base_dir,
                candidate_qlib_dir=candidate_dir,
                validation_summary_path=validation_path,
                output_dir=base_dir / "out",
            )
            result = apply_candidate_switch(
                base_dir=base_dir,
                candidate_qlib_dir=candidate_dir,
                validation_summary_path=validation_path,
                output_dir=base_dir / "out",
            )

            self.assertFalse(plan["apply_executed"])
            self.assertEqual(plan["candidate_validation_status"], "No-Go")
            self.assertTrue(plan["backup_required"])
            self.assertTrue(plan["post_switch_audit_required"])
            self.assertTrue(plan["post_switch_daily_smoke_required"])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "candidate_validation_failed")


if __name__ == "__main__":
    unittest.main()
