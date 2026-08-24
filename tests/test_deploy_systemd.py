"""Contracts for the CSI1800 PIT systemd deployment units and installer.

These tests intentionally do not call systemctl. They protect the deployment
boundary from silently regressing to the historical CSI800 + inference unit or
to a mutable development checkout.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.deploy_csi1800_pit_runtime import (
    DeploymentConfig,
    DeploymentError,
    SERVICE_NAME,
    TIMER_NAME,
    apply_systemd,
    main as deploy_main,
    materialize_runtime,
    validate_runtime_contract,
)


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD / "qsys-csi1800-pit-daily-sync.service"
TIMER = SYSTEMD / "qsys-csi1800-pit-daily-sync.timer"


def _unit_sections(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    section = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections[section] = {}
            continue
        if section is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        sections[section].setdefault(key, []).append(value)
    return sections


def test_csi1800_pit_units_exist_and_are_paired():
    assert SERVICE.is_file()
    assert TIMER.is_file()

    timer = _unit_sections(TIMER)
    assert timer["Timer"]["Unit"] == ["qsys-csi1800-pit-daily-sync.service"]


def test_csi1800_service_is_pit_only_and_runtime_is_clean():
    text = SERVICE.read_text(encoding="utf-8")
    service = _unit_sections(SERVICE)["Service"]

    runtime = "/home/liuming/.openclaw/workspace/SysQ-runtime"
    assert service["WorkingDirectory"] == [runtime]
    assert service["Environment"] == [
        f"PYTHONPATH={runtime}",
        "PYTHONUNBUFFERED=1",
        "QSYS_SETTINGS_FILE=/home/liuming/.openclaw/workspace/SysQ/config/settings.yaml",
        "QSYS_DATA_ROOT=/home/liuming/.openclaw/workspace/SysQ/data",
        "HTTP_PROXY=http://172.31.144.1:12334",
        "HTTPS_PROXY=http://172.31.144.1:12334",
    ]
    assert service["ExecStart"] == [
        "/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python \\",
    ]
    assert text.count("ExecStart=") == 1
    # Continuation lines are checked from the raw unit text because the small
    # parser above intentionally models only complete key/value lines.
    assert (
        "/home/liuming/.openclaw/workspace/SysQ-runtime/scripts/data_sync.py \\\n"
        "    --universe csi1800 --apply"
    ) in text
    assert "--universe csi800" not in text
    assert "run_daily.py" not in text
    assert "financial_rc" not in text
    assert "WorkingDirectory=/home/liuming/.openclaw/workspace/SysQ\n" not in text
    assert "/home/liuming/.openclaw/workspace/SysQ/scripts/" not in text
    assert "ExecStartPre=/usr/bin/test -d /home/liuming/.openclaw/workspace/SysQ-runtime" in text
    assert "ExecStartPre=/usr/bin/test -f /home/liuming/.openclaw/workspace/SysQ-runtime/scripts/data_sync.py" in text


def test_csi1800_timer_is_weekday_persistent_and_not_csi800():
    text = TIMER.read_text(encoding="utf-8")
    timer = _unit_sections(TIMER)["Timer"]
    assert timer["OnCalendar"] == ["Mon..Fri 19:00:00"]
    assert timer["Persistent"] == ["true"]
    assert "csi800" not in text.lower()


def test_csi800_unit_remains_available_for_explicit_rollback():
    assert (SYSTEMD / "qsys-csi800-daily-sync.service").is_file()
    assert (SYSTEMD / "qsys-csi800-daily-sync.timer").is_file()


def test_cutover_docs_quiesce_running_services_before_switching():
    docs = (SYSTEMD / "README.md").read_text(encoding="utf-8")
    assert "stop qsys-csi800-daily-sync.service" in docs
    assert "ActiveState=inactive" in docs
    assert "stop qsys-csi1800-pit-daily-sync.service" in docs
    assert docs.index("stop qsys-csi800-daily-sync.service") < docs.index(
        "enable --now qsys-csi1800-pit-daily-sync.timer"
    )
    assert docs.index("stop qsys-csi1800-pit-daily-sync.service") < docs.index(
        "enable --now qsys-csi800-daily-sync.timer"
    )
    assert docs.count("grep -qx 'ActiveState=inactive'") == 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _fixture_config(tmp_path: Path) -> DeploymentConfig:
    repo = tmp_path / "source"
    (repo / "scripts").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "data").mkdir()
    (repo / "scripts/data_sync.py").write_text("# canonical entrypoint\n", encoding="utf-8")
    (repo / "config/settings.yaml").write_text("settings: test\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Qsys test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    runtime = tmp_path / "runtime"
    service = tmp_path / "service"
    timer = tmp_path / "timer"
    service.write_text(
        "\n".join(
            [
                "[Service]",
                f"WorkingDirectory={runtime}",
                f"Environment=PYTHONPATH={runtime}",
                f"Environment=QSYS_SETTINGS_FILE={repo / 'config/settings.yaml'}",
                f"Environment=QSYS_DATA_ROOT={repo / 'data'}",
                f"ExecStartPre=/usr/bin/test -d {runtime}",
                f"ExecStartPre=/usr/bin/test -f {runtime}/scripts/data_sync.py",
                f"ExecStartPre=/usr/bin/test -x {sys.executable}",
                f"ExecStartPre=/usr/bin/test -f {repo / 'config/settings.yaml'}",
                f"ExecStartPre=/usr/bin/test -d {repo / 'data'}",
                f"ExecStart={sys.executable} \\",
                f"    {runtime}/scripts/data_sync.py \\",
                "    --universe csi1800 --apply",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    timer.write_text(f"[Timer]\nUnit={SERVICE_NAME}\n", encoding="utf-8")
    return DeploymentConfig(
        repo=repo,
        revision="HEAD",
        runtime=runtime,
        python=Path(sys.executable),
        settings_file=repo / "config/settings.yaml",
        data_root=repo / "data",
        service_file=service,
        timer_file=timer,
        systemd_user_dir=tmp_path / "systemd",
    )


def test_missing_runtime_is_rejected_by_runtime_preflight(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    with pytest.raises(DeploymentError, match="runtime directory is missing"):
        validate_runtime_contract(cfg)


def test_non_worktree_runtime_is_rejected(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    cfg.runtime.mkdir()
    (cfg.runtime / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(DeploymentError, match="not a registered worktree"):
        materialize_runtime(cfg)
    assert (cfg.runtime / "sentinel").read_text(encoding="utf-8") == "keep"


def test_dirty_registered_runtime_is_rejected_without_overwrite(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    materialize_runtime(cfg)
    tracked = cfg.runtime / "scripts/data_sync.py"
    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DeploymentError, match="not clean"):
        materialize_runtime(cfg)
    assert tracked.read_text(encoding="utf-8") == "dirty\n"


def test_ignored_runtime_files_do_not_block_safe_revision_update(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    (cfg.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(cfg.repo, "add", ".gitignore")
    _git(cfg.repo, "commit", "-qm", "ignore runtime bytecode")
    materialize_runtime(cfg)
    cache = cfg.runtime / "scripts/__pycache__"
    cache.mkdir()
    (cache / "data_sync.cpython-312.pyc").write_bytes(b"ignored")

    materialize_runtime(cfg)
    validate_runtime_contract(cfg)


def test_runtime_symlink_is_rejected(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    actual = tmp_path / "actual-runtime"
    actual.mkdir()
    cfg.runtime.symlink_to(actual, target_is_directory=True)

    with pytest.raises(DeploymentError, match="symlinks are not allowed"):
        materialize_runtime(cfg)


def test_valid_runtime_materializes_and_passes_contract(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    revision = materialize_runtime(cfg)
    assert len(revision) == 40
    validate_runtime_contract(cfg)


def test_cli_preserves_stable_python_symlink_used_by_unit(tmp_path: Path):
    cfg = _fixture_config(tmp_path)
    stable_python = tmp_path / "python"
    stable_python.symlink_to(Path(sys.executable))
    cfg.service_file.write_text(
        cfg.service_file.read_text(encoding="utf-8").replace(
            str(sys.executable), str(stable_python)
        ),
        encoding="utf-8",
    )

    assert deploy_main([
        "--repo", str(cfg.repo),
        "--revision", "HEAD",
        "--runtime", str(cfg.runtime),
        "--python", str(stable_python),
        "--settings-file", str(cfg.settings_file),
        "--data-root", str(cfg.data_root),
        "--service-file", str(cfg.service_file),
        "--timer-file", str(cfg.timer_file),
        "--systemd-user-dir", str(cfg.systemd_user_dir),
        "--dry-run",
    ]) == 0


def test_apply_enables_timer_but_never_starts_service(tmp_path: Path, monkeypatch):
    cfg = _fixture_config(tmp_path)
    materialize_runtime(cfg)
    validate_runtime_contract(cfg)
    calls: list[list[str]] = []

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "git":
            return real_run(command, **kwargs)
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.deploy_csi1800_pit_runtime.subprocess.run", fake_run)
    apply_systemd(cfg)
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", TIMER_NAME],
    ]
    assert (cfg.systemd_user_dir / SERVICE_NAME).is_file()
    assert (cfg.systemd_user_dir / TIMER_NAME).is_file()


def test_docs_materialize_and_validate_before_enable():
    docs = (SYSTEMD / "README.md").read_text(encoding="utf-8")
    materialize = docs.index("deploy_csi1800_pit_runtime.py")
    apply = docs.index("--apply", materialize)
    enable = docs.index("enable --now qsys-csi1800-pit-daily-sync.timer")
    assert materialize < apply < enable
