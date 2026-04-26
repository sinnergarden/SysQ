from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.ops.instrument_coverage import apply_repair_plan, build_repair_plan, read_instrument_file, summarize_universe_registry


class _Adapter:
    def __init__(self, qlib_dir: Path):
        self.qlib_dir = qlib_dir


def test_stale_instrument_repair_plan_marks_repairable() -> None:
    rows = [
        {
            "instrument": "000001.SZ",
            "instrument_file_end_date": "2026-04-03",
            "feature_last_date": "2026-04-17",
            "has_feature_on_last_qlib_date": True,
            "is_active_by_instrument_file": False,
            "coverage_mismatch_reason": "instrument_end_date_stale_but_feature_available",
        }
    ]
    plan = build_repair_plan(universe="csi300", last_qlib_date="2026-04-17", coverage_rows=rows)
    assert plan["stale_but_feature_available_count"] == 1
    assert plan["stale_but_feature_available"] == ["000001.SZ"]


def test_true_feature_missing_is_not_repaired(tmp_path: Path) -> None:
    inst_dir = tmp_path / "instruments"
    inst_dir.mkdir(parents=True)
    (inst_dir / "csi300.txt").write_text("000001.SZ\t2010-01-04\t2026-04-03\n", encoding="utf-8")
    adapter = _Adapter(tmp_path)
    rows = [
        {
            "instrument": "000001.SZ",
            "instrument_file_end_date": "2026-04-03",
            "feature_last_date": "2026-04-03",
            "has_feature_on_last_qlib_date": False,
            "is_active_by_instrument_file": False,
            "coverage_mismatch_reason": "feature_rows_missing",
        }
    ]
    result = apply_repair_plan(adapter, universe="csi300", last_qlib_date="2026-04-17", coverage_rows=rows)
    df = read_instrument_file(inst_dir / "csi300.txt")
    assert result["updated_end_date_count"] == 0
    assert df.iloc[0]["end_date"].strftime("%Y-%m-%d") == "2026-04-03"


def test_apply_repair_updates_end_date(tmp_path: Path) -> None:
    inst_dir = tmp_path / "instruments"
    inst_dir.mkdir(parents=True)
    (inst_dir / "csi300.txt").write_text("000001.SZ\t2010-01-04\t2026-04-03\n", encoding="utf-8")
    adapter = _Adapter(tmp_path)
    rows = [
        {
            "instrument": "000001.SZ",
            "instrument_file_end_date": "2026-04-03",
            "feature_last_date": "2026-04-17",
            "has_feature_on_last_qlib_date": True,
            "is_active_by_instrument_file": False,
            "coverage_mismatch_reason": "instrument_end_date_stale_but_feature_available",
        }
    ]
    result = apply_repair_plan(adapter, universe="csi300", last_qlib_date="2026-04-17", coverage_rows=rows)
    df = pd.read_csv(inst_dir / "csi300.txt", sep="\t", header=None)
    assert result["updated_end_date_count"] == 1
    assert str(df.iloc[0, 2]) == "2026-04-17"


def test_registry_summary_flags_stale_coverage(tmp_path: Path) -> None:
    inst_dir = tmp_path / "instruments"
    inst_dir.mkdir(parents=True)
    (inst_dir / "csi300.txt").write_text(
        "000001.SZ\t2010-01-04\t2026-04-17\n000002.SZ\t2010-01-04\t2026-04-03\n",
        encoding="utf-8",
    )
    adapter = _Adapter(tmp_path)
    summary = summarize_universe_registry(adapter, universe="csi300", trade_date="2026-04-17")
    assert summary.instrument_total == 2
    assert summary.active_on_trade_date == 1
    assert summary.stale_end_date_count == 1
    assert summary.coverage_status == "mismatch"
