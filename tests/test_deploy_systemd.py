"""Static contracts for the CSI1800 PIT systemd deployment units.

These tests intentionally do not call systemctl. They protect the deployment
boundary from silently regressing to the historical CSI800 + inference unit or
to a mutable development checkout.
"""

from pathlib import Path


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
