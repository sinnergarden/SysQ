"""Tests for scripts/check_framework_stability.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from qsys.strategy.validators import validate_strategy_spec


# ── Import module under test ─────────────────────────────────────────────────


import importlib.util

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_framework_stability.py"

spec = importlib.util.spec_from_file_location(
    "check_framework_stability", str(SCRIPT_PATH),
)
mod = importlib.util.module_from_spec(spec)
sys.modules["check_framework_stability"] = mod
spec.loader.exec_module(mod)

CheckResult = mod.CheckResult
run_checks = mod.run_checks
print_summary = mod.print_summary
build_report = mod.build_report
write_report = mod.write_report


# ── CheckResult ──────────────────────────────────────────────────────────────


class TestCheckResult:
    def test_default_state(self):
        r = CheckResult("test_check")
        assert r.name == "test_check"
        assert r.status == "pending"
        assert r.required is True
        assert r.error is None

    def test_optional_check(self):
        r = CheckResult("optional_check", required=False)
        assert r.required is False

    def test_to_dict(self):
        r = CheckResult("test", required=True)
        r.status = "pass"
        r.duration_sec = 1.5
        d = r.to_dict()
        assert d["name"] == "test"
        assert d["status"] == "pass"
        assert d["duration_sec"] == 1.5


# ── run_checks ───────────────────────────────────────────────────────────────


class TestRunChecks:
    def test_quick_mode_does_not_include_dr_bt_or_batch_dry_run(self):
        """Quick mode should run only lightweight tests."""
        checks = run_checks(quick=True)
        names = {c.name for c in checks}
        assert "calendar_tests" in names
        assert "strategy_calendar_contract" in names
        assert "strategy_spec_tests" in names
        assert "strategy_spec_validators" in names
        assert "batch_runner_tests" in names
        assert "batch_dry_run" not in names
        assert "dr_bt_equivalence" not in names

    def test_skip_dr_bt_excludes_dr_bt(self):
        """--skip-dr-bt removes the DR=BT check."""
        checks = run_checks(skip_dr_bt=True)
        assert all(c.name != "dr_bt_equivalence" for c in checks)

    def test_skip_batch_dry_run_excludes_batch_dry_run(self):
        """--skip-batch-dry-run removes the batch dry-run check."""
        checks = run_checks(skip_batch_dry_run=True)
        assert all(c.name != "batch_dry_run" for c in checks)


# ── build_report ─────────────────────────────────────────────────────────────


class TestBuildReport:
    def test_report_has_expected_fields(self, monkeypatch):
        r1 = CheckResult("passing_check", required=True)
        r1.status = "pass"
        r1.duration_sec = 1.0

        from datetime import datetime

        started = datetime(2026, 5, 22, 8, 0, 0)
        report = build_report([r1], started)
        assert "status" in report
        assert "started_at" in report
        assert "finished_at" in report
        assert "duration_sec" in report
        assert "checks" in report
        assert len(report["checks"]) == 1

    def test_required_failure_means_overall_fail(self, monkeypatch):
        r1 = CheckResult("failing_check", required=True)
        r1.status = "fail"
        r1.error = "something broke"

        from datetime import datetime

        report = build_report([r1], datetime(2026, 5, 22, 8, 0, 0))
        assert report["status"] == "fail"

    def test_optional_failure_is_partial(self, monkeypatch):
        r1 = CheckResult("optional_check", required=False)
        r1.status = "fail"
        r1.error = "optional failure"

        from datetime import datetime

        report = build_report([r1], datetime(2026, 5, 22, 8, 0, 0))
        assert report["status"] == "partial"

    def test_all_pass(self, monkeypatch):
        from datetime import datetime

        checks = []
        for name in ["c1", "c2"]:
            r = CheckResult(name, required=True)
            r.status = "pass"
            checks.append(r)

        report = build_report(checks, datetime(2026, 5, 22, 8, 0, 0))
        assert report["status"] == "pass"


# ── write_report ─────────────────────────────────────────────────────────────


class TestWriteReport:
    def test_writes_json(self, tmp_path: Path):
        report = {
            "status": "pass",
            "started_at": "2026-05-22T08:00:00",
            "finished_at": "2026-05-22T08:01:00",
            "duration_sec": 60.0,
            "checks": [],
        }
        path = write_report(report, str(tmp_path))
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["status"] == "pass"
        assert loaded["checks"] == []

    def test_default_output_dir(self, monkeypatch):
        report = {"status": "pass", "started_at": "", "finished_at": "",
                  "duration_sec": 0, "checks": []}
        path = write_report(report, None)
        assert path.parent.name == "qsys_framework_stability"
        assert path.name == "framework_stability_check.json"
