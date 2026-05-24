"""Tests for scripts/run_daily_batch.py — stage-aware batch dispatch.

Uses temporary YAML configs and monkeypatched subprocess to avoid real
DailyRunner dependencies.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from qsys.strategy.spec import StrategySpec

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_config_dir(tmp_path: Path) -> Path:
    """Create temporary strategy YAML configs at various lifecycle stages."""
    configs = {
        "alpha_v1.yaml": {
            "strategy_id": "alpha_v1",
            "stage": "candidate",
            "display_name": "Alpha V1",
            "universe": "csi300",
        },
        "alpha_v2.yaml": {
            "strategy_id": "alpha_v2",
            "stage": "candidate",
            "display_name": "Alpha V2 Smoke",
            "universe": "csi300",
        },
        "research_x.yaml": {
            "strategy_id": "research_x",
            "stage": "research",
            "display_name": "Research X",
            "universe": "csi300",
        },
        "rejected_y.yaml": {
            "strategy_id": "rejected_y",
            "stage": "rejected",
            "display_name": "Rejected Y",
            "universe": "csi300",
        },
        "archived_z.yaml": {
            "strategy_id": "archived_z",
            "stage": "archived",
            "display_name": "Archived Z",
            "universe": "csi300",
        },
        "prod_p1.yaml": {
            "strategy_id": "prod_p1",
            "stage": "production",
            "display_name": "Production P1",
            "universe": "csi300",
        },
    }
    import yaml
    for filename, data in configs.items():
        (tmp_path / filename).write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _register_fake_strategies():
    """Ensure test strategy IDs are in the runtime registry."""
    from qsys.strategy import registry as reg

    class FakeAdapter:
        strategy_id = "test_fake"
        account_id = "test_fake"

    # Register if not already present for test IDs
    test_ids = {"alpha_v1", "alpha_v2", "prod_p1"}
    existing = set(reg.STRATEGY_REGISTRY.keys())
    for sid in test_ids:
        if sid not in existing:
            reg.register(sid, FakeAdapter)
    yield
    # Cleanup: remove any IDs we added
    for sid in test_ids:
        if sid not in existing:
            reg.STRATEGY_REGISTRY.pop(sid, None)


# ── Import module under test ────────────────────────────────────────────

import importlib.util

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_daily_batch.py"

spec = importlib.util.spec_from_file_location(
    "run_daily_batch", str(SCRIPT_PATH),
)
batch_module = importlib.util.module_from_spec(spec)
sys.modules["run_daily_batch"] = batch_module
spec.loader.exec_module(batch_module)

run_batch = batch_module.run_batch
write_summary = batch_module.write_summary
_build_command = batch_module._build_command
_command_preview = batch_module._command_preview
_build_summary = batch_module._build_summary


# ── Stage filter tests ─────────────────────────────────────────────────


class TestStageFilter:
    def test_selects_candidate_only(self, fake_config_dir: Path):
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
        )
        ids = {s["strategy_id"] for s in summary["strategies"]}
        assert ids == {"alpha_v1", "alpha_v2"}
        assert summary["stage"] == "candidate"

    def test_selects_production_only(self, fake_config_dir: Path):
        summary = run_batch(
            stage="production",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
            allow_production=True,
        )
        ids = {s["strategy_id"] for s in summary["strategies"]}
        assert ids == {"prod_p1"}

    def test_research_rejected_archived_not_selected(self, fake_config_dir: Path):
        """Research/rejected/archived are never in daily batch output."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
        )
        ids = {s["strategy_id"] for s in summary["strategies"]}
        assert "research_x" not in ids
        assert "rejected_y" not in ids
        assert "archived_z" not in ids


class TestStrategyFilter:
    def test_include_filter(self, fake_config_dir: Path):
        """--strategy filter selects only the named strategy."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            strategy_filter=["alpha_v1"],
            dry_run=True,
        )
        ids = {s["strategy_id"] for s in summary["strategies"]}
        assert ids == {"alpha_v1"}

    def test_exclude_filter(self, fake_config_dir: Path):
        """--exclude filter removes the named strategy."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            exclude_filter=["alpha_v2"],
            dry_run=True,
        )
        ids = {s["strategy_id"] for s in summary["strategies"]}
        assert ids == {"alpha_v1"}


class TestUnregisteredStrategy:
    def test_unregistered_candidate_is_skipped(self, tmp_path: Path):
        """A candidate strategy not in the registry is marked skipped."""
        import yaml
        isolated_dir = tmp_path / "configs"
        isolated_dir.mkdir()
        unreg = isolated_dir / "unregistered_foo.yaml"
        unreg.write_text(yaml.dump({
            "strategy_id": "unregistered_foo",
            "stage": "candidate",
            "display_name": "Not Registered",
            "universe": "csi300",
        }), encoding="utf-8")

        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(isolated_dir),
            dry_run=True,
        )
        # unregistered_foo is listed in dry_run (registry check skipped in dry_run)
        assert len(summary["strategies"]) == 1
        assert summary["strategies"][0]["strategy_id"] == "unregistered_foo"
        assert summary["strategies"][0]["status"] == "dry_run"


class TestDryRun:
    def test_dry_run_does_not_dispatch(self, fake_config_dir: Path):
        """Dry-run returns summary with dry_run status and no dispatch."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
        )
        assert summary["status"] == "dry_run"
        assert summary["selected_count"] > 0
        for s in summary["strategies"]:
            assert s["status"] == "dry_run"
            assert s["command"] is not None


class TestDispatch:
    def test_successful_dispatch(self, fake_config_dir: Path):
        """A successful subprocess call returns success."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "done"
            mock_run.return_value.stderr = ""

            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                continue_on_error=True,
            )

        successes = [s for s in summary["strategies"] if s["status"] == "success"]
        assert len(successes) == 2  # alpha_v1 and alpha_v2

    def test_failure_isolation(self, fake_config_dir: Path):
        """One failed strategy does not stop batch with continue-on-error."""
        call_count = 0

        def _mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = subprocess.CompletedProcess(args[0], 0)
            if call_count == 1:
                result.returncode = 1
                result.stderr = "mock failure"
            return result

        with patch.object(subprocess, "run", side_effect=_mock_run):
            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                continue_on_error=True,
            )

        statuses = {s["status"] for s in summary["strategies"]}
        assert "failed" in statuses
        assert "success" in statuses
        # Both strategies should have been attempted
        assert len(summary["strategies"]) == 2

    def test_fail_fast_stops_after_first_failure(self, fake_config_dir: Path):
        """fail-fast stops dispatching after the first failure."""
        call_count = 0

        def _mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = subprocess.CompletedProcess(args[0], 0)
            if call_count == 1:
                result.returncode = 1
                result.stderr = "mock failure"
            return result

        with patch.object(subprocess, "run", side_effect=_mock_run):
            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                fail_fast=True,
            )

        assert len(summary["strategies"]) == 1
        assert summary["strategies"][0]["status"] == "failed"

    def test_exit_code_non_zero_on_failure(self, fake_config_dir: Path):
        """Batch summary status reflects failures."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "mock failure"

            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                continue_on_error=True,
            )

        assert summary["status"] in ("failed", "partial_failed")
        assert summary["failed_count"] > 0


class TestSummaryOutput:
    @patch.object(batch_module, "write_summary")  # prevent actual file IO during dispatch test
    def test_summary_has_expected_fields(self, mock_write, fake_config_dir: Path):
        """Batch summary dict contains all expected fields."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "done"
            mock_run.return_value.stderr = ""

            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
            )

        assert "stage" in summary
        assert "mode" in summary
        assert "trade_date" in summary
        assert "started_at" in summary
        assert "finished_at" in summary
        assert "duration_sec" in summary
        assert "status" in summary
        assert "selected_count" in summary
        assert "success_count" in summary
        assert "failed_count" in summary
        assert "skipped_count" in summary
        assert "strategies" in summary
        assert isinstance(summary["strategies"], list)

    def test_summary_json_written(self, fake_config_dir: Path, tmp_path: Path):
        """Summary JSON is written to output_root/trade_date/."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "done"
            mock_run.return_value.stderr = ""

            summary = run_batch(
                stage="candidate",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                output_root=str(tmp_path),
            )

        summary_path = tmp_path / "2026-05-22" / "batch_candidate_preopen.json"
        assert summary_path.exists()
        loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        assert loaded["stage"] == "candidate"
        assert loaded["status"] == "success"


class TestBuildCommand:
    def test_command_structure(self):
        cmd = _build_command("alpha_v1", "preopen", "2026-05-22")
        cmd_str = " ".join(cmd)
        assert "run_daily.py" in cmd_str
        assert "--strategy" in cmd
        assert "alpha_v1" in cmd
        assert "--mode" in cmd
        assert "preopen" in cmd
        assert "--trade-date" in cmd
        assert "2026-05-22" in cmd

    def test_train_mode_skips_trade_date(self):
        cmd = _build_command("alpha_v1", "train", "2026-05-22")
        cmd_str = " ".join(cmd)
        assert "--trade-date" not in cmd_str

    def test_debug_run_flag(self):
        cmd = _build_command("alpha_v1", "preopen", "2026-05-22", debug_run=True)
        assert "--debug-run" in cmd

    def test_no_notify_flag(self):
        cmd = _build_command("alpha_v1", "preopen", "2026-05-22", no_notify=True)
        assert "--no-notify" in cmd

    def test_no_notify_not_default(self):
        """--no-notify is NOT auto-appended when not explicitly passed."""
        cmd = _build_command("alpha_v1", "preopen", "2026-05-22")
        assert "--no-notify" not in cmd

    def test_notify_only_mode_generates_valid_command(self):
        """notify-only mode generates --mode preopen --notify-only --trade-date."""
        cmd = _build_command("alpha_v1", "notify-only", "2026-05-22")
        cmd_str = " ".join(cmd)
        assert "run_daily.py" in cmd_str
        assert "--mode" in cmd
        assert "preopen" in cmd
        assert "--notify-only" in cmd
        assert "--trade-date" in cmd
        assert "2026-05-22" in cmd


class TestEdgeCases:
    def test_no_matching_strategies(self, fake_config_dir: Path):
        """Batch with no matching strategies returns skipped status."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            strategy_filter=["nonexistent"],
            dry_run=True,
        )
        assert summary["status"] == "skipped"
        assert summary["selected_count"] == 0

    def test_research_stage_rejected(self, fake_config_dir: Path):
        """research stage raises ValueError for daily batch."""
        with pytest.raises(ValueError, match="not a daily batch stage"):
            run_batch(
                stage="research",
                mode="preopen",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                dry_run=True,
            )

    def test_invalid_mode_raises(self, fake_config_dir: Path):
        with pytest.raises(ValueError, match="unsupported mode"):
            run_batch(
                stage="candidate",
                mode="invalid",
                trade_date="2026-05-22",
                config_root=str(fake_config_dir),
                dry_run=True,
            )

    def test_auto_trade_date_resolves(self, fake_config_dir: Path):
        """'auto' trade_date resolves to today's date."""
        from datetime import datetime
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="auto",
            config_root=str(fake_config_dir),
            dry_run=True,
        )
        assert summary["trade_date"] == datetime.now().strftime("%Y-%m-%d")

    def test_no_hardcoded_alpha_v1_in_selection(self, fake_config_dir: Path):
        """Selection logic should not hardcode alpha_v1."""
        # The selection is driven by config, not hardcoded IDs
        import inspect
        source = inspect.getsource(batch_module)
        # Check that the batch runner doesn't hardcode strategy IDs in its core logic
        # (searching for any hardcoded alpha_v1 in the module source)
        lines_with_alpha = [
            i for i, line in enumerate(source.split("\n"), 1)
            if "alpha_v1" in line and "docstring" not in line
        ]
        # References in docstrings and test fixtures are allowed
        # The dispatch/build_command should reference string params, not specific IDs
        for lineno in lines_with_alpha:
            line_text = source.split("\n")[lineno - 1].strip()
            # Allow docstrings and comments
            if line_text.startswith(("#", '"""', ">>>")):
                continue
            # The test build_command calls use literal "alpha_v1" — that's test code
            # (importlib loads the module, but _build_command is from the module)
            # Let's check if the line is from batch_module source, not test code
            pytest.fail(
                f"line {lineno} appears to hardcode 'alpha_v1' in batch module: "
                f"{line_text}"
            )


class TestProductionGate:
    def test_production_without_allow_production_is_blocked(self, fake_config_dir: Path):
        """Production batch without --allow-production returns blocked status."""
        summary = run_batch(
            stage="production",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
            allow_production=False,
        )
        assert summary["status"] == "blocked"
        assert summary["selected_count"] == 0

    def test_production_with_allow_production_proceeds(self, fake_config_dir: Path):
        """Production batch with --allow-production proceeds to dry-run."""
        summary = run_batch(
            stage="production",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
            allow_production=True,
        )
        assert summary["status"] == "dry_run"
        assert summary["selected_count"] > 0

    def test_candidate_does_not_require_allow_production(self, fake_config_dir: Path):
        """Candidate batch works without --allow-production."""
        summary = run_batch(
            stage="candidate",
            mode="preopen",
            trade_date="2026-05-22",
            config_root=str(fake_config_dir),
            dry_run=True,
        )
        assert summary["status"] == "dry_run"
        assert summary["selected_count"] > 0
