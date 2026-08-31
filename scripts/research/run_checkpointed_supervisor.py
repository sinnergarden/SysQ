#!/usr/bin/env python3
"""Bounded-address-space supervisor for checkpointed rolling signal research.

The canonical research entrypoint remains :mod:`scripts.run_research`.  This
module only supervises that entrypoint: each bounded child process exits after
committing a small number of window checkpoints, allowing the operating system
to reclaim the child's memory before the next batch starts.

It is deliberately fail-closed.  Exit 75 is accepted only when the child
prints the checkpoint protocol payload and advances the validated progress
counter.  Exit 0 is accepted only after the complete experiment, signal, and
window-checkpoint artifacts have been independently validated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


# This entrypoint lives one directory below the other scripts.  Make direct
# execution from an arbitrary working directory resolve the in-repo package in
# the same way as ``scripts/run_research.py``.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SCHEMA_VERSION = "checkpoint_supervisor_v2"
CHECKPOINT_EXIT = 75


class SupervisorError(RuntimeError):
    """A fail-closed supervisor protocol or artifact error."""


class _SupervisorFileLock:
    """Process-lifetime lock for one supervisor state path.

    ``flock`` is the synchronization primitive.  The metadata is diagnostic
    only and is never consulted to decide ownership, so a PID from another
    namespace cannot make a second supervisor appear safe to start.
    """

    def __init__(self, path: Path, *, initial_metadata: dict[str, Any]) -> None:
        self.path = path
        self.initial_metadata = initial_metadata
        self._handle = None

    def __enter__(self) -> "_SupervisorFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("a+", encoding="utf-8")
        except OSError as exc:
            raise SupervisorError(
                f"cannot open supervisor lock file: {self.path}"
            ) from exc
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise SupervisorError(
                    f"supervisor lock is already held: {self.path}"
                ) from exc
            raise SupervisorError(
                f"cannot acquire supervisor lock: {self.path}"
            ) from exc
        self._handle = handle
        try:
            self.write_metadata(self.initial_metadata)
        except BaseException:
            self.release()
            raise
        return self

    def write_metadata(self, metadata: dict[str, Any]) -> None:
        """Write audit metadata after the kernel lock has been acquired."""
        if self._handle is None:
            raise SupervisorError("supervisor lock is not held")
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(
            json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        )
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


@dataclass(frozen=True)
class ChildResult:
    returncode: int
    stdout: str
    pid: int | None = None


ChildRunner = Callable[
    [list[str], Path, Callable[[int], None]], ChildResult
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _git_revision(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision or "unknown"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"invalid run state JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise SupervisorError(f"run state must be a JSON object: {path}")
    return payload


def _pid_is_live(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _checkpoint_payload(stdout: str) -> dict[str, Any] | None:
    """Find the protocol JSON object in potentially noisy child output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("status") == "checkpoint_batch_complete":
            return payload
    return None


def _default_child_runner(
    command: list[str],
    cwd: Path,
    on_started: Callable[[int], None],
) -> ChildResult:
    """Run one child synchronously; no detached/background process is used."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    on_started(process.pid)
    stdout, _ = process.communicate()
    return ChildResult(process.returncode, stdout or "", process.pid)


def _build_windows(config: Any) -> list[Any]:
    from qsys.research.rolling_window import build_rolling_windows

    lag = max(
        (label.get("label_maturity_lag_trading_days") or 0)
        for label in config.labels
    ) if config.labels else 0
    return build_rolling_windows(
        config.calendar.get("start_date", ""),
        config.calendar.get("end_date", ""),
        train_window_days=config.calendar.get("train_window_days", 252),
        step_days=config.calendar.get("step_days", 5),
        label_maturity_lag_trading_days=lag,
    )


def _read_row_count(data_path: Path) -> int:
    if data_path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            return int(pq.ParquetFile(data_path).metadata.num_rows)
        except ImportError:
            import pandas as pd
            return int(len(pd.read_parquet(data_path, columns=[])))
    with data_path.open("rb") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def _research_runtime_identity(
    config: Any,
    *,
    project_root: Path,
    effective_generators: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind supervisor state to the exact checkpoint-producing runtime."""
    from qsys.research.matrix_job import _create_generator_from_config
    from qsys.research.signal_pipeline import SignalResearchPipeline

    pipeline = SignalResearchPipeline(project_root / "data" / "research")
    checkpoint_identities: dict[str, str] = {}
    for generator_config in effective_generators:
        generator_id = str(generator_config["generator_id"])
        generator = _create_generator_from_config(
            generator_config,
            feature_list_id=config.feature_list_id,
            use_feature_cache=config.use_feature_cache,
            write_through=config.write_through,
            feature_cache_root=config.feature_cache_root,
            source_manifest_hash=config.source_manifest_hash,
        )
        checkpoint_identities[generator_id] = _canonical_sha256(
            pipeline._window_checkpoint_base_identity(
                config, generator_config, generator
            )
        )
    return {
        "supervisor_code_sha256": _sha256_file(Path(__file__).resolve()),
        "canonical_child_code_sha256": _sha256_file(
            project_root / "scripts" / "run_research.py"
        ),
        "checkpoint_base_identity_sha256_by_generator": checkpoint_identities,
    }


def validate_terminal_artifacts(
    config: Any,
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Validate complete terminal artifacts for one research config.

    This calls the existing checkpoint store validator for every config-derived
    rolling window.  It also checks experiment and signal manifests/data and
    binds each signal manifest to its validated checkpoint-set hash.
    """
    from qsys.research.matrix_job import (
        _create_generator_from_config,
        expand_multi_label_generators,
    )
    from qsys.research.paths import ResearchPaths
    from qsys.research.signal_pipeline import SignalResearchPipeline
    from qsys.research.window_checkpoint import WindowPredictionCheckpointStore

    if not config.window_checkpoints:
        raise SupervisorError("terminal validation requires window_checkpoints=true")
    if not config.generators:
        raise SupervisorError("terminal validation requires matrix generators")

    root = Path(project_root).resolve()
    research_root = root / "data" / "research"
    paths = ResearchPaths(research_root)
    exp_dir = paths.experiment_dir(config.experiment_id)
    exp_manifest_path = exp_dir / "signal_research_manifest.json"
    if not exp_manifest_path.is_file():
        raise SupervisorError(f"missing signal research manifest: {exp_manifest_path}")
    try:
        exp_manifest = _read_json(exp_manifest_path)
    except SupervisorError:
        raise
    if exp_manifest.get("artifact_type") != "signal_research":
        raise SupervisorError("signal research manifest has wrong artifact_type")

    windows = _build_windows(config)
    total = len(windows)
    if exp_manifest.get("window_count") != total:
        raise SupervisorError(
            f"window_count mismatch: manifest={exp_manifest.get('window_count')!r}, "
            f"config={total}"
        )
    windows_csv = exp_dir / "rolling_windows.csv"
    if not windows_csv.is_file():
        raise SupervisorError(f"missing rolling windows artifact: {windows_csv}")
    import pandas as pd
    window_df = pd.read_csv(windows_csv)
    if len(window_df) != total:
        raise SupervisorError(
            f"rolling_windows.csv row count {len(window_df)} != {total}"
        )

    pipeline = SignalResearchPipeline(research_root)
    effective_generators = expand_multi_label_generators(config.generators)
    checkpoint_hashes: dict[str, str] = {}
    checkpoint_diagnostics: dict[str, list[dict[str, Any]]] = {}
    require_checkpoint_diagnostics = bool(
        config.research_protocol.get(
            "require_checkpoint_model_diagnostics", False
        )
    )
    for gen_cfg in effective_generators:
        gen_id = str(gen_cfg["generator_id"])
        generator = _create_generator_from_config(
            gen_cfg,
            feature_list_id=config.feature_list_id,
            use_feature_cache=config.use_feature_cache,
            write_through=config.write_through,
            feature_cache_root=config.feature_cache_root,
            source_manifest_hash=config.source_manifest_hash,
        )
        store = WindowPredictionCheckpointStore(
            paths.window_checkpoint_dir(config.experiment_id, gen_id),
            pipeline._window_checkpoint_base_identity(config, gen_cfg, generator),
        )
        refs = []
        for window in windows:
            try:
                ref = store.validate(window)
            except (OSError, ValueError) as exc:
                raise SupervisorError(
                    f"checkpoint validation failed for {gen_id}/{window.window_id}: {exc}"
                ) from exc
            if ref is None:
                raise SupervisorError(
                    f"missing checkpoint commit marker for {gen_id}/{window.window_id}"
                )
            refs.append(ref)
        diagnostics = [ref.model_diagnostics for ref in refs]
        if require_checkpoint_diagnostics and any(
            item is None for item in diagnostics
        ):
            present = sum(item is not None for item in diagnostics)
            raise SupervisorError(
                "checkpoint model diagnostics incomplete for "
                f"{gen_id}: {present}/{total}"
            )
        checkpoint_diagnostics[gen_id] = [
            item for item in diagnostics if item is not None
        ]
        checkpoint_hashes[gen_id] = store.checkpoint_set_sha256(refs)

    signal_runs = exp_manifest.get("signal_runs")
    combined_runs = exp_manifest.get("combined_signal_runs", [])
    if not isinstance(signal_runs, list) or not signal_runs:
        raise SupervisorError("signal research manifest has no signal_runs")
    if not isinstance(combined_runs, list):
        raise SupervisorError("combined_signal_runs must be a list")
    refs_to_validate = list(signal_runs)
    refs_to_validate.extend(
        {
            "generator_id": ref.get("combine_id"),
            "transform_id": "combined",
            "signal_id": ref.get("signal_id"),
            "signal_run_id": ref.get("signal_run_id"),
        }
        for ref in combined_runs
        if isinstance(ref, dict)
    )
    for ref in refs_to_validate:
        if not isinstance(ref, dict):
            raise SupervisorError("signal_runs contains a non-object reference")
        signal_id = ref.get("signal_id")
        signal_run_id = ref.get("signal_run_id")
        gen_id = ref.get("generator_id")
        if not all(isinstance(value, str) and value for value in (signal_id, signal_run_id, gen_id)):
            raise SupervisorError("signal run reference is missing identity fields")
        is_combined = ref.get("transform_id") == "combined"
        if gen_id not in checkpoint_hashes and not is_combined:
            raise SupervisorError(f"signal run references unknown generator: {gen_id}")
        manifest_path = paths.signal_manifest(signal_id, signal_run_id)
        if not manifest_path.is_file():
            raise SupervisorError(f"missing signal manifest: {manifest_path}")
        signal_manifest = _read_json(manifest_path)
        if signal_manifest.get("artifact_type") != "signal_run":
            raise SupervisorError(f"wrong signal manifest type: {manifest_path}")
        if not is_combined and signal_manifest.get("window_checkpoint_set_sha256") != checkpoint_hashes[gen_id]:
            raise SupervisorError(f"signal checkpoint hash mismatch: {manifest_path}")
        if not is_combined and require_checkpoint_diagnostics:
            model_diagnostics = signal_manifest.get("model_diagnostics")
            if not isinstance(model_diagnostics, dict):
                raise SupervisorError(
                    f"signal model diagnostics missing: {manifest_path}"
                )
            if model_diagnostics.get("source") != "window_checkpoint_manifests":
                raise SupervisorError(
                    f"signal model diagnostics source mismatch: {manifest_path}"
                )
            if model_diagnostics.get("windows") != checkpoint_diagnostics[gen_id]:
                raise SupervisorError(
                    f"signal model diagnostics mismatch: {manifest_path}"
                )
        data_candidates = [
            paths.signal_file(signal_id, signal_run_id, fmt="parquet"),
            paths.signal_file(signal_id, signal_run_id, fmt="csv"),
        ]
        data_path = next((candidate for candidate in data_candidates if candidate.is_file()), None)
        if data_path is None:
            raise SupervisorError(f"missing signal data: {manifest_path.parent}")
        row_count = signal_manifest.get("row_count")
        if not isinstance(row_count, int) or row_count < 0:
            raise SupervisorError(f"invalid signal row_count: {manifest_path}")
        if _read_row_count(data_path) != row_count:
            raise SupervisorError(f"signal row count mismatch: {manifest_path}")
        columns = signal_manifest.get("columns")
        required = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
        if not isinstance(columns, list) or not required.issubset(columns):
            raise SupervisorError(f"signal manifest missing required columns: {manifest_path}")

    return {
        "experiment_id": config.experiment_id,
        "total_windows": total,
        "checkpoint_set_sha256": checkpoint_hashes,
        "signal_run_count": len(refs_to_validate),
        "experiment_manifest": str(exp_manifest_path),
    }


def _new_state(
    *,
    config_path: Path,
    config_sha256: str,
    revision: str,
    run_identity: str,
    experiment_id: str,
    total_windows: int,
    windows_per_generator: int,
    generator_ids: list[str],
    runtime_identity: dict[str, Any],
    checkpoint_batch_size: int,
    log_file: Path,
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "stage": "starting",
        "run_identity": run_identity,
        "config_path": str(config_path),
        "config_sha256": config_sha256,
        "revision": revision,
        "experiment_id": experiment_id,
        "checkpoint_batch_size": checkpoint_batch_size,
        "attempt": 0,
        "completed_windows": 0,
        "total_windows": total_windows,
        "windows_per_generator": windows_per_generator,
        "generator_ids": generator_ids,
        "runtime_identity": runtime_identity,
        "completed_windows_by_generator": {
            generator_id: 0 for generator_id in generator_ids
        },
        "last_exit_code": None,
        "pid": None,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "log_file": str(log_file),
        "error": None,
    }


def _assert_identity(existing: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("schema_version", "run_identity", "config_sha256", "revision", "experiment_id"):
        if existing.get(key) != expected.get(key):
            raise SupervisorError(
                f"run identity conflict for {key}: "
                f"state={existing.get(key)!r}, requested={expected.get(key)!r}"
            )
    if existing.get("total_windows") != expected.get("total_windows"):
        raise SupervisorError("run identity conflict for total_windows")
    for key in ("windows_per_generator", "generator_ids", "runtime_identity"):
        if existing.get(key) != expected.get(key):
            raise SupervisorError(f"run identity conflict for {key}")


def _fail(state: dict[str, Any], state_path: Path, reason: str, *, exit_code: int | None = None) -> None:
    state.update({
        "status": "failed",
        "stage": "failed",
        "error": reason,
        "pid": None,
        "updated_at": _utc_now(),
    })
    if exit_code is not None:
        state["last_exit_code"] = exit_code
    _atomic_write_json(state_path, state)


def _run_supervisor_impl(
    *,
    config_path: str | Path,
    checkpoint_batch_size: int = 1,
    run_state_path: str | Path,
    log_file: str | Path | None = None,
    max_restarts: int | None = None,
    project_root: str | Path | None = None,
    child_runner: ChildRunner | None = None,
    terminal_validator: Callable[..., dict[str, Any]] | None = None,
    revision: str | None = None,
    supervisor_lock: _SupervisorFileLock,
) -> dict[str, Any]:
    """Run/resume a bounded rolling research process to terminal completion."""
    if checkpoint_batch_size <= 0:
        raise SupervisorError("checkpoint_batch_size must be positive")
    if max_restarts is not None and max_restarts < 0:
        raise SupervisorError("max_restarts must be non-negative")

    root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
    config_path = Path(config_path).resolve()
    state_path = Path(run_state_path).resolve()
    log_path = Path(log_file).resolve() if log_file else state_path.with_suffix(".log")
    if not config_path.is_file():
        raise SupervisorError(f"config not found: {config_path}")

    from qsys.research.matrix_job import RollingResearchConfig
    from qsys.research.matrix_job import expand_multi_label_generators
    config = RollingResearchConfig.from_file(config_path)
    windows = _build_windows(config)
    windows_per_generator = len(windows)
    effective_generators = expand_multi_label_generators(config.generators)
    generator_ids = [
        str(item["generator_id"])
        for item in effective_generators
    ]
    if not generator_ids or len(generator_ids) != len(set(generator_ids)):
        raise SupervisorError("generator ids must be present and unique")
    total_windows = windows_per_generator * len(generator_ids)
    runtime_identity = _research_runtime_identity(
        config,
        project_root=root,
        effective_generators=effective_generators,
    )
    config_sha = _sha256_file(config_path)
    revision = revision or _git_revision(root)
    run_identity = _canonical_sha256({
        "schema_version": SCHEMA_VERSION,
        "config_sha256": config_sha,
        "revision": revision,
        "experiment_id": config.experiment_id,
        "total_windows": total_windows,
        "windows_per_generator": windows_per_generator,
        "generator_ids": generator_ids,
        "runtime_identity": runtime_identity,
    })
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "run_identity": run_identity,
        "config_sha256": config_sha,
        "revision": revision,
        "experiment_id": config.experiment_id,
        "total_windows": total_windows,
        "windows_per_generator": windows_per_generator,
        "generator_ids": generator_ids,
        "runtime_identity": runtime_identity,
    }
    supervisor_lock.write_metadata({
        "schema_version": "checkpoint_supervisor_lock_v1",
        "state_path": str(state_path),
        "lock_path": str(supervisor_lock.path),
        "run_identity": run_identity,
        "config_sha256": config_sha,
        "revision": revision,
        "experiment_id": config.experiment_id,
        "acquired_at": supervisor_lock.initial_metadata["acquired_at"],
        "updated_at": _utc_now(),
    })

    if state_path.exists():
        state = _read_json(state_path)
        _assert_identity(state, expected_identity)
        if state.get("status") == "complete":
            validator = terminal_validator or validate_terminal_artifacts
            validator(config, project_root=root)
            return state
        if _pid_is_live(state.get("pid")):
            raise SupervisorError(
                f"an existing supervisor child is still live (pid={state['pid']})"
            )
        if state.get("status") not in {"running", "failed"}:
            raise SupervisorError(f"unsupported previous run state: {state.get('status')!r}")
        if state.get("status") == "failed":
            # Explicit rerun of a failed process is permitted only after the
            # operator invokes the same identity.  Completed checkpoints stay.
            state["status"] = "running"
            state["stage"] = "resuming_after_failure"
            state["error"] = None
    else:
        state = _new_state(
            config_path=config_path,
            config_sha256=config_sha,
            revision=revision,
            run_identity=run_identity,
            experiment_id=config.experiment_id,
            total_windows=total_windows,
            windows_per_generator=windows_per_generator,
            generator_ids=generator_ids,
            runtime_identity=runtime_identity,
            checkpoint_batch_size=checkpoint_batch_size,
            log_file=log_path,
        )
    # Keep the original run's batch sizing and log path stable on resume.
    if state.get("checkpoint_batch_size") != checkpoint_batch_size:
        raise SupervisorError("checkpoint_batch_size differs from existing run state")
    log_path = Path(str(state.get("log_file", log_path))).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(state_path, state)

    child_script = root / "scripts" / "run_research.py"
    if not child_script.is_file():
        _fail(state, state_path, f"canonical child entrypoint missing: {child_script}")
        raise SupervisorError(str(state["error"]))
    runner = child_runner or _default_child_runner
    validator = terminal_validator or validate_terminal_artifacts

    while True:
        attempts = int(state.get("attempt", 0))
        restarts = max(0, attempts - 1)
        if max_restarts is not None and restarts >= max_restarts:
            reason = f"max_restarts exceeded ({max_restarts})"
            _fail(state, state_path, reason)
            raise SupervisorError(reason)
        state.update({"stage": "running_batch", "attempt": attempts + 1,
                      "updated_at": _utc_now(), "pid": None})
        _atomic_write_json(state_path, state)
        command = [
            sys.executable,
            str(child_script),
            "--config", str(config_path),
            "--checkpoint-batch-size", str(checkpoint_batch_size),
        ]

        def on_started(pid: int) -> None:
            state["pid"] = int(pid)
            state["updated_at"] = _utc_now()
            _atomic_write_json(state_path, state)

        try:
            result = runner(command, root, on_started)
        except BaseException as exc:
            reason = f"child launch/collection failed: {type(exc).__name__}: {exc}"
            _fail(state, state_path, reason)
            raise SupervisorError(reason) from exc
        log_path.open("a", encoding="utf-8").write(result.stdout)
        state.update({"pid": None, "last_exit_code": int(result.returncode),
                      "updated_at": _utc_now()})

        if result.returncode == CHECKPOINT_EXIT:
            payload = _checkpoint_payload(result.stdout)
            if payload is None:
                reason = "exit 75 without checkpoint_batch_complete protocol payload"
                _fail(state, state_path, reason, exit_code=result.returncode)
                raise SupervisorError(reason)
            completed = payload.get("completed_windows")
            reported_total = payload.get("total_windows")
            generator_id = payload.get("generator_id")
            if generator_id is None and len(generator_ids) == 1:
                generator_id = generator_ids[0]
            progress_by_generator = dict(
                state.get("completed_windows_by_generator", {})
            )
            previous = int(progress_by_generator.get(str(generator_id), 0))
            if (
                generator_id not in generator_ids
                or not isinstance(completed, int)
                or not isinstance(reported_total, int)
                or reported_total != windows_per_generator
                or completed <= previous
                or completed >= windows_per_generator
            ):
                reason = (
                    "invalid checkpoint progress: "
                    f"generator={generator_id!r}, previous={previous}, "
                    f"completed={completed!r}, reported_total={reported_total!r}, "
                    f"expected_total={windows_per_generator}"
                )
                _fail(state, state_path, reason, exit_code=result.returncode)
                raise SupervisorError(reason)
            generator_index = generator_ids.index(str(generator_id))
            for prior_generator in generator_ids[:generator_index]:
                progress_by_generator[prior_generator] = windows_per_generator
            progress_by_generator[str(generator_id)] = completed
            state.update({"completed_windows": sum(
                              int(value)
                              for value in progress_by_generator.values()
                          ),
                          "completed_windows_by_generator": progress_by_generator,
                          "stage": "checkpoint_batch_complete",
                          "updated_at": _utc_now()})
            _atomic_write_json(state_path, state)
            continue

        if result.returncode != 0:
            reason = f"child failed with exit code {result.returncode}"
            _fail(state, state_path, reason, exit_code=result.returncode)
            raise SupervisorError(reason)

        state.update({"stage": "validating_terminal", "updated_at": _utc_now()})
        _atomic_write_json(state_path, state)
        try:
            validation = validator(config, project_root=root)
        except BaseException as exc:
            reason = f"terminal artifact validation failed: {type(exc).__name__}: {exc}"
            _fail(state, state_path, reason, exit_code=result.returncode)
            raise SupervisorError(reason) from exc
        state.update({
            "status": "complete",
            "stage": "complete",
            "completed_windows": total_windows,
            "completed_windows_by_generator": {
                generator_id: windows_per_generator
                for generator_id in generator_ids
            },
            "terminal_validation": validation,
            "completed_at": _utc_now(),
            "updated_at": _utc_now(),
            "pid": None,
            "error": None,
        })
        _atomic_write_json(state_path, state)
        return state


def run_supervisor(
    *,
    config_path: str | Path,
    checkpoint_batch_size: int = 1,
    run_state_path: str | Path,
    log_file: str | Path | None = None,
    max_restarts: int | None = None,
    project_root: str | Path | None = None,
    child_runner: ChildRunner | None = None,
    terminal_validator: Callable[..., dict[str, Any]] | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Run/resume a bounded rolling research process to terminal completion.

    The lock is acquired before reading or writing the state file and remains
    held through child execution, checkpoint updates, and terminal validation.
    """
    state_path = Path(run_state_path).resolve()
    lock_path = Path(f"{state_path}.lock")
    lock = _SupervisorFileLock(
        lock_path,
        initial_metadata={
            "schema_version": "checkpoint_supervisor_lock_v1",
            "state_path": str(state_path),
            "lock_path": str(lock_path),
            "acquired_at": _utc_now(),
        },
    )
    with lock:
        return _run_supervisor_impl(
            config_path=config_path,
            checkpoint_batch_size=checkpoint_batch_size,
            run_state_path=state_path,
            log_file=log_file,
            max_restarts=max_restarts,
            project_root=project_root,
            child_runner=child_runner,
            terminal_validator=terminal_validator,
            revision=revision,
            supervisor_lock=lock,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint-batch-size", type=int, default=1)
    parser.add_argument("--run-state", required=True)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--max-restarts", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        state = run_supervisor(
            config_path=args.config,
            checkpoint_batch_size=args.checkpoint_batch_size,
            run_state_path=args.run_state,
            log_file=args.log_file,
            max_restarts=args.max_restarts,
        )
    except SupervisorError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": state.get("status"),
        "run_state": args.run_state,
        "completed_windows": state.get("completed_windows"),
        "total_windows": state.get("total_windows"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
