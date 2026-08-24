"""Contract tests for the bounded rolling-research supervisor."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.research.run_checkpointed_supervisor import (
    ChildResult,
    SupervisorError,
    run_supervisor,
)


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "research.json"
    path.write_text(json.dumps({
        "experiment_id": "exp_supervisor",
        "calendar": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "train_window_days": 20,
            "step_days": 20,
        },
        "labels": [{"label_id": "label_1"}],
        "signal_transforms": [{"transform_id": "raw", "type": "raw"}],
        "generators": [{
            "generator_id": "fixture",
            "type": "fixture",
            "params": {"n_instruments": 2},
        }],
        "window_checkpoints": True,
        "source_manifest_hash": "source-v1",
    }), encoding="utf-8")
    return path


class FakeChildren:
    def __init__(self, results: list[ChildResult]) -> None:
        self.results = list(results)
        self.commands: list[list[str]] = []

    def __call__(self, command, cwd, on_started):
        self.commands.append(command)
        on_started(999_000 + len(self.commands))
        return self.results.pop(0)


def _validator(config, *, project_root):
    return {"validated": True, "experiment_id": config.experiment_id}


def _progress(done: int, total: int) -> ChildResult:
    return ChildResult(
        75,
        "noise\n" + json.dumps({
            "status": "checkpoint_batch_complete",
            "completed_windows": done,
            "total_windows": total,
        }),
    )


def _all_progress(total: int, *, start: int = 1) -> list[ChildResult]:
    return [_progress(done, total) for done in range(start, total)]


def test_checkpoint_exit_75_progresses_then_terminal_exit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    # The fixture calendar creates 13 windows.
    children = FakeChildren([
        *_all_progress(13),
        ChildResult(0, "completed\n"),
    ])
    state = run_supervisor(
        config_path=config,
        checkpoint_batch_size=1,
        run_state_path=state_path,
        child_runner=children,
        terminal_validator=_validator,
        revision="rev-1",
    )
    assert state["status"] == "complete"
    assert state["completed_windows"] == state["total_windows"]
    assert len(children.commands) == 13
    assert children.commands[0][0] == sys.executable
    assert "scripts/run_research.py" in children.commands[0][1]
    assert children.commands[0][-2:] == ["--checkpoint-batch-size", "1"]
    assert json.loads(state_path.read_text())["stage"] == "complete"


def test_exit_75_without_monotonic_progress_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([_progress(0, 13)])
    with pytest.raises(SupervisorError, match="invalid checkpoint progress"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=children,
            terminal_validator=_validator,
            revision="rev-1",
        )
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["completed_windows"] == 0
    assert state["last_exit_code"] == 75


def test_exit_75_without_protocol_payload_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([ChildResult(75, "not-json")])
    with pytest.raises(SupervisorError, match="without checkpoint_batch_complete"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=children,
            terminal_validator=_validator,
            revision="rev-1",
        )
    assert json.loads(state_path.read_text())["status"] == "failed"


def test_exit_zero_requires_terminal_artifacts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([*_all_progress(13), ChildResult(0, "done")])

    def reject(config, *, project_root):
        raise ValueError("missing terminal manifest")

    with pytest.raises(SupervisorError, match="terminal artifact validation failed"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=children,
            terminal_validator=reject,
            revision="rev-1",
        )
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["completed_windows"] == 12


def test_identity_conflict_rejects_without_launching_child(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "schema_version": "checkpoint_supervisor_v1",
        "run_identity": "different",
        "config_sha256": "different",
        "revision": "rev-old",
        "experiment_id": "exp_supervisor",
        "total_windows": 13,
        "status": "running",
        "pid": None,
    }), encoding="utf-8")
    children = FakeChildren([])
    with pytest.raises(SupervisorError, match="run identity conflict"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=children,
            terminal_validator=_validator,
            revision="rev-1",
        )
    assert children.commands == []


def test_existing_complete_state_is_reused_after_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([ChildResult(99, "must not run")])
    first = run_supervisor(
        config_path=config,
        checkpoint_batch_size=1,
        run_state_path=state_path,
        child_runner=FakeChildren([ChildResult(0, "done")]),
        terminal_validator=_validator,
        revision="rev-1",
    )
    second = run_supervisor(
        config_path=config,
        checkpoint_batch_size=1,
        run_state_path=state_path,
        child_runner=children,
        terminal_validator=_validator,
        revision="rev-1",
    )
    assert second["status"] == "complete"
    assert second["run_identity"] == first["run_identity"]
    assert children.commands == []


def test_child_failure_preserves_completed_checkpoint_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([
        _progress(1, 13),
        ChildResult(2, "child error"),
    ])
    with pytest.raises(SupervisorError, match="child failed with exit code 2"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=children,
            terminal_validator=_validator,
            revision="rev-1",
        )
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["completed_windows"] == 1
    assert state["last_exit_code"] == 2


def test_max_restarts_stops_without_deleting_checkpoints(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    children = FakeChildren([_progress(1, 13), _progress(2, 13)])
    with pytest.raises(SupervisorError, match="max_restarts exceeded"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            max_restarts=1,
            child_runner=children,
            terminal_validator=_validator,
            revision="rev-1",
        )
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["completed_windows"] == 2
    assert len(children.commands) == 2


def test_live_child_state_blocks_duplicate_supervisor(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    state_path = tmp_path / "state.json"
    # Build a valid identity by starting a run whose child never gets called;
    # then mark that state as live.
    run_supervisor(
        config_path=config,
        checkpoint_batch_size=1,
        run_state_path=state_path,
        child_runner=FakeChildren([ChildResult(0, "done")]),
        terminal_validator=_validator,
        revision="rev-1",
    )
    state = json.loads(state_path.read_text())
    state["status"] = "running"
    state["pid"] = 12345
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.research.run_checkpointed_supervisor._pid_is_live",
        lambda pid: pid == 12345,
    )
    with pytest.raises(SupervisorError, match="still live"):
        run_supervisor(
            config_path=config,
            checkpoint_batch_size=1,
            run_state_path=state_path,
            child_runner=FakeChildren([]),
            terminal_validator=_validator,
            revision="rev-1",
        )


def test_direct_entrypoint_resolves_repo_package_from_arbitrary_cwd(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "research"
        / "run_checkpointed_supervisor.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            str(config),
            "--run-state",
            str(tmp_path / "state.json"),
            "--max-restarts",
            "0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "max_restarts exceeded" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
