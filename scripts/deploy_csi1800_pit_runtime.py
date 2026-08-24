#!/usr/bin/env python3
"""Materialize and validate the clean runtime used by the CSI1800 PIT timer.

The command is intentionally fail-closed.  A preflight (the default) only
inspects git, the runtime contract, and the unit files.  ``--apply`` installs
the already-validated unit/timer and enables the timer, but never starts the
data-sync service.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SERVICE_NAME = "qsys-csi1800-pit-daily-sync.service"
TIMER_NAME = "qsys-csi1800-pit-daily-sync.timer"
DEFAULT_PYTHON = Path("/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python")


class DeploymentError(RuntimeError):
    """A deployment precondition failed; no systemd mutation was attempted."""


@dataclass(frozen=True)
class DeploymentConfig:
    repo: Path
    revision: str
    runtime: Path
    python: Path
    settings_file: Path
    data_root: Path
    service_file: Path
    timer_file: Path
    systemd_user_dir: Path


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise DeploymentError(f"git {' '.join(args)} failed: {detail}")
    return result


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise DeploymentError(f"source repository does not exist: {repo}")
    _run_git(repo, "rev-parse", "--show-toplevel")


def _resolve_revision(cfg: DeploymentConfig) -> str:
    result = _run_git(cfg.repo, "rev-parse", "--verify", f"{cfg.revision}^{{commit}}")
    resolved = result.stdout.strip()
    if not resolved:
        raise DeploymentError(f"revision did not resolve to a commit: {cfg.revision}")
    return resolved


def _registered_worktree(repo: Path, runtime: Path) -> bool:
    result = _run_git(repo, "worktree", "list", "--porcelain")
    expected = _canonical(runtime)
    for line in result.stdout.splitlines():
        if line.startswith("worktree ") and _canonical(Path(line[9:])) == expected:
            return True
    return False


def _assert_clean_detached(runtime: Path) -> None:
    status = _run_git(
        runtime,
        "status",
        "--porcelain",
        "--untracked-files=all",
    ).stdout.strip()
    if status:
        raise DeploymentError(
            f"runtime is not clean; refusing to overwrite it: {runtime}\n{status}"
        )
    symbolic = _run_git(runtime, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if symbolic.returncode == 0:
        raise DeploymentError(f"runtime must be detached, found branch {symbolic.stdout.strip()}")


def materialize_runtime(cfg: DeploymentConfig) -> str:
    """Create/update a registered detached worktree without destructive git commands."""

    _validate_repo(cfg.repo)
    revision = _resolve_revision(cfg)
    runtime = _canonical(cfg.runtime)
    repo = _canonical(cfg.repo)
    if runtime == repo or repo in runtime.parents:
        raise DeploymentError(f"runtime must be outside the source repository: {runtime}")
    if cfg.runtime.is_symlink():
        raise DeploymentError(f"runtime symlinks are not allowed: {cfg.runtime}")

    if not runtime.exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        _run_git(cfg.repo, "worktree", "add", "--detach", str(runtime), revision)
    else:
        if not runtime.is_dir():
            raise DeploymentError(f"runtime path is not a directory: {runtime}")
        if not _registered_worktree(cfg.repo, runtime):
            raise DeploymentError(
                f"existing runtime is not a registered worktree; refusing to touch it: {runtime}"
            )
        _assert_clean_detached(runtime)
        current = _run_git(runtime, "rev-parse", "HEAD").stdout.strip()
        if current != revision:
            # A clean detached worktree can be moved with checkout.  We do not
            # use reset --hard, remove files, or overwrite a dirty checkout.
            _run_git(runtime, "checkout", "--detach", revision)

    if not _registered_worktree(cfg.repo, runtime):
        raise DeploymentError(f"runtime was not registered after materialization: {runtime}")
    _assert_clean_detached(runtime)
    actual = _run_git(runtime, "rev-parse", "HEAD").stdout.strip()
    if actual != revision:
        raise DeploymentError(f"runtime revision mismatch: expected {revision}, got {actual}")
    return revision


def _service_command(service_text: str) -> str:
    lines = service_text.splitlines()
    command: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ExecStart="):
            collecting = True
            command.append(stripped[len("ExecStart=") :].rstrip("\\").strip())
            continue
        if collecting:
            command.append(stripped.rstrip("\\").strip())
            if not line.rstrip().endswith("\\"):
                break
    return " ".join(part for part in command if part)


def validate_runtime_contract(cfg: DeploymentConfig) -> None:
    """Validate every path and the PIT-only service command before apply."""

    runtime = _canonical(cfg.runtime)
    if not runtime.is_dir():
        raise DeploymentError(f"runtime directory is missing: {runtime}")
    if not _registered_worktree(cfg.repo, runtime):
        raise DeploymentError(f"runtime is not a registered source worktree: {runtime}")
    _assert_clean_detached(runtime)

    required_files = {
        "runtime entrypoint": runtime / "scripts" / "data_sync.py",
        "fixed Python interpreter": cfg.python,
        "QSYS_SETTINGS_FILE": cfg.settings_file,
        "systemd service unit": cfg.service_file,
        "systemd timer unit": cfg.timer_file,
    }
    for label, path in required_files.items():
        if not path.is_file():
            raise DeploymentError(f"{label} is missing: {path}")
    if not os.access(cfg.python, os.X_OK):
        raise DeploymentError(f"fixed Python interpreter is not executable: {cfg.python}")
    if not cfg.data_root.is_dir():
        raise DeploymentError(f"QSYS_DATA_ROOT is missing: {cfg.data_root}")

    service_text = cfg.service_file.read_text(encoding="utf-8")
    expected = {
        f"WorkingDirectory={runtime}",
        f"Environment=PYTHONPATH={runtime}",
        f"Environment=QSYS_SETTINGS_FILE={cfg.settings_file}",
        f"Environment=QSYS_DATA_ROOT={cfg.data_root}",
    }
    lines = {line.strip() for line in service_text.splitlines()}
    missing = sorted(expected - lines)
    if missing:
        raise DeploymentError("service path contract is missing: " + ", ".join(missing))
    command = _service_command(service_text)
    if "--universe csi1800" not in command or "--apply" not in command:
        raise DeploymentError("service must run data_sync.py --universe csi1800 --apply")
    if not command.startswith(f"{cfg.python} "):
        raise DeploymentError("service does not use the validated fixed Python interpreter")
    forbidden = ("--universe csi800", "run_daily.py", "financial_rc", " infer")
    if any(token in service_text or token in command for token in forbidden):
        raise DeploymentError("service is not PIT-only data sync; inference/CSI800 command detected")
    required_prechecks = {
        f"ExecStartPre=/usr/bin/test -d {runtime}",
        f"ExecStartPre=/usr/bin/test -f {runtime}/scripts/data_sync.py",
        f"ExecStartPre=/usr/bin/test -x {cfg.python}",
        f"ExecStartPre=/usr/bin/test -f {cfg.settings_file}",
        f"ExecStartPre=/usr/bin/test -d {cfg.data_root}",
    }
    missing_prechecks = sorted(required_prechecks - lines)
    if missing_prechecks:
        raise DeploymentError("service path ExecStartPre checks are missing: " + ", ".join(missing_prechecks))
    if f"{runtime}/scripts/data_sync.py" not in command:
        raise DeploymentError("service entrypoint is not the validated runtime data_sync.py")

    timer_text = cfg.timer_file.read_text(encoding="utf-8")
    if f"Unit={SERVICE_NAME}" not in timer_text:
        raise DeploymentError("timer is not bound to the CSI1800 PIT service")


def apply_systemd(cfg: DeploymentConfig) -> None:
    """Install validated units and enable the timer, without starting sync."""

    validate_runtime_contract(cfg)
    target = _canonical(cfg.systemd_user_dir)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg.service_file, target / SERVICE_NAME)
    shutil.copy2(cfg.timer_file, target / TIMER_NAME)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", TIMER_NAME], check=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    script_repo = Path(__file__).resolve().parents[1]
    parser.add_argument("--repo", type=Path, default=script_repo)
    parser.add_argument("--revision", required=True, help="explicit commit/tag resolving to a commit")
    parser.add_argument("--runtime", type=Path, default=script_repo.parent / f"{script_repo.name}-runtime")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--settings-file", type=Path, default=script_repo / "config/settings.yaml")
    parser.add_argument("--data-root", type=Path, default=script_repo / "data")
    parser.add_argument("--service-file", type=Path, default=script_repo / "deploy/systemd" / SERVICE_NAME)
    parser.add_argument("--timer-file", type=Path, default=script_repo / "deploy/systemd" / TIMER_NAME)
    parser.add_argument("--systemd-user-dir", type=Path, default=Path("~/.config/systemd/user"))
    parser.add_argument("--apply", action="store_true", help="install units and enable timer after preflight")
    parser.add_argument("--dry-run", action="store_true", help="explicit preflight-only mode (the default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive")
    repo = _canonical(args.repo)
    # Keep the operator-supplied runtime path unresolved until
    # ``materialize_runtime`` has had a chance to reject a symlink.  Resolving
    # it here would erase the very condition the fail-closed check protects.
    runtime = args.runtime.expanduser().absolute()
    cfg = DeploymentConfig(
        repo=repo,
        revision=args.revision,
        runtime=runtime,
        # Preserve the operator-facing interpreter path.  Conda/mamba expose
        # ``bin/python`` as a stable symlink and the unit intentionally binds
        # that exact path; resolving it to ``python3.x`` would make two
        # equivalent paths fail the textual deployment contract.
        python=args.python.expanduser().absolute(),
        settings_file=_canonical(args.settings_file),
        data_root=_canonical(args.data_root),
        service_file=_canonical(args.service_file),
        timer_file=_canonical(args.timer_file),
        systemd_user_dir=_canonical(args.systemd_user_dir),
    )
    try:
        resolved = materialize_runtime(cfg)
        validate_runtime_contract(cfg)
        if args.apply:
            apply_systemd(cfg)
            print(f"applied {SERVICE_NAME} and enabled {TIMER_NAME} for {resolved}")
        else:
            print(f"preflight passed for {SERVICE_NAME} at {resolved}; no systemd changes made")
        return 0
    except DeploymentError as exc:
        print(f"deployment preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
