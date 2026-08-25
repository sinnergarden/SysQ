from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.data.source_audit import SourceAuditStore
from qsys.ops.universe_history import (
    UniverseHistoryCatchupError,
    inspect_universe_history,
    repair_qlib_instrument_history_spans,
    run_universe_history_catchup,
)


def _write(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": dates}).to_feather(path)


def _write_registry(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join("\t".join(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_detects_members_whose_history_starts_at_index_inclusion(tmp_path: Path) -> None:
    canonical = tmp_path / "data" / "canonical" / "daily"
    _write(
        canonical / "FULL.SZ.feather",
        ["20220801", "20260807"],
    )
    _write(
        canonical / "TRUNC.SZ.feather",
        ["20260605", "20260807"],
    )
    _write_registry(
        tmp_path / "data" / "qlib_bin" / "instruments" / "all.txt",
        [
            ("FULL.SZ", "2022-08-01", "2026-08-07"),
            ("TRUNC.SZ", "2026-06-05", "2026-08-07"),
        ],
    )
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["FULL.SZ", "TRUNC.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )
    assert result["status"] == "fail"
    assert result["deficient_symbols"] == ["TRUNC.SZ"]


def test_missing_canonical_member_is_deficient(tmp_path: Path) -> None:
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["MISSING.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )
    assert result["deficient_count"] == 1


def test_old_first_row_cannot_hide_large_calendar_gap(tmp_path: Path) -> None:
    calendar = tmp_path / "data" / "qlib_bin" / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True)
    sessions = pd.date_range("2026-01-01", "2026-01-30", freq="B")
    calendar.write_text(
        "\n".join(value.strftime("%Y-%m-%d") for value in sessions) + "\n",
        encoding="utf-8",
    )
    _write(
        tmp_path / "data" / "canonical" / "daily" / "GAPPED.SZ.feather",
        [sessions[0].strftime("%Y%m%d"), sessions[-1].strftime("%Y%m%d")],
    )
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["GAPPED.SZ"],
        as_of_date="2026-01-30",
        lookback_calendar_days=29,
    )
    assert result["deficient_symbols"] == ["GAPPED.SZ"]
    assert result["details"][0]["session_coverage"] < 0.95


def test_detects_and_repairs_qlib_registry_that_hides_canonical_history(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "data" / "canonical" / "daily"
    _write(canonical / "LATE.SZ.feather", ["20220810", "20260807"])
    registry_dir = tmp_path / "data" / "qlib_bin" / "instruments"
    for name in ("all", "csi800"):
        _write_registry(
            registry_dir / f"{name}.txt",
            [("LATE.SZ", "2026-06-05", "2026-08-07")],
        )

    before = inspect_universe_history(
        project_root=tmp_path,
        symbols=["LATE.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )
    assert before["canonical_deficient_symbols"] == []
    assert before["qlib_registry_deficient_symbols"] == ["LATE.SZ"]

    repair = repair_qlib_instrument_history_spans(
        project_root=tmp_path,
        symbols=["LATE.SZ"],
    )

    assert repair["changed_rows"] == {"all": 1, "csi800": 1}
    assert inspect_universe_history(
        project_root=tmp_path,
        symbols=["LATE.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )["status"] == "pass"
    assert (registry_dir / "all.txt").read_text(encoding="utf-8") == (
        "LATE.SZ\t2022-08-10\t2026-08-07\n"
    )


def test_registry_only_catchup_does_not_call_remote_collector(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "canonical" / "daily" / "LATE.SZ.feather",
        ["20220810", "20260807"],
    )
    _write_registry(
        tmp_path / "data" / "qlib_bin" / "instruments" / "all.txt",
        [("LATE.SZ", "2026-06-05", "2026-08-07")],
    )

    class NoRemoteCollector:
        def update_universe_history(self, **_kwargs: object) -> None:
            raise AssertionError("canonical-complete repair must not fetch remote data")

    class RecordingAdapter:
        calls: list[tuple[list[str], list[str] | None]] = []

        def convert_fix_symbols(
            self,
            symbols: list[str],
            *,
            refresh_universes: list[str] | None = None,
        ) -> dict[str, object]:
            self.calls.append((symbols, refresh_universes))
            return {"status": "success"}

    adapter = RecordingAdapter()
    result = run_universe_history_catchup(
        project_root=tmp_path,
        symbols=["LATE.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
        output_dir=tmp_path / "run",
        apply=True,
        collector=NoRemoteCollector(),
        adapter=adapter,
    )

    assert result["status"] == "success"
    assert result["before"]["canonical_deficient_symbols"] == []
    assert result["canonical_mutated_symbols"] == []
    assert result["canonical_mutation_range"] is None
    assert adapter.calls == [(["LATE.SZ"], [])]


class _NoopAdapter:
    def convert_fix_symbols(
        self,
        symbols: list[str],
        *,
        refresh_universes: list[str] | None = None,
    ) -> dict[str, object]:
        return {"status": "success", "symbols": symbols}


def _seed_nonempty_registry(tmp_path: Path) -> None:
    _write_registry(
        tmp_path / "data" / "qlib_bin" / "instruments" / "all.txt",
        [("OTHER.SZ", "2022-08-07", "2026-08-07")],
    )


def test_canonical_catchup_returns_conservative_mutation_scope(tmp_path: Path) -> None:
    _seed_nonempty_registry(tmp_path)

    class WritingCollector:
        def update_universe_history(self, **_kwargs: object) -> None:
            _write(
                tmp_path / "data" / "canonical" / "daily" / "MISS.SZ.feather",
                ["20220807", "20260807"],
            )

    result = run_universe_history_catchup(
        project_root=tmp_path,
        symbols=["MISS.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
        output_dir=tmp_path / "run",
        apply=True,
        collector=WritingCollector(),
        adapter=_NoopAdapter(),
    )

    assert result["status"] == "success"
    assert result["canonical_mutated_symbols"] == ["MISS.SZ"]
    assert result["canonical_mutation_range"] == {
        "range_start": "2022-08-07",
        "range_end": "2026-08-07",
    }
    assert result["canonical_mutation_scope_semantics"] == (
        "conservative_planned_scope_after_write_started"
    )


def test_canonical_after_check_failure_keeps_mutation_scope(tmp_path: Path) -> None:
    _seed_nonempty_registry(tmp_path)

    class NonWritingCollector:
        def update_universe_history(self, **_kwargs: object) -> None:
            return None

    result = run_universe_history_catchup(
        project_root=tmp_path,
        symbols=["MISS.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
        output_dir=tmp_path / "run",
        apply=True,
        collector=NonWritingCollector(),
        adapter=_NoopAdapter(),
    )

    assert result["status"] == "failed"
    assert result["canonical_mutated_symbols"] == ["MISS.SZ"]
    persisted = (tmp_path / "run" / "universe_history_catchup.json").read_text()
    assert "conservative_planned_scope_after_write_started" in persisted


def test_collector_exception_carries_and_persists_mutation_scope(tmp_path: Path) -> None:
    _seed_nonempty_registry(tmp_path)

    class PartiallyWritingCollector:
        def update_universe_history(self, **_kwargs: object) -> None:
            _write(
                tmp_path / "data" / "canonical" / "daily" / "MISS.SZ.feather",
                ["20260807"],
            )
            raise RuntimeError("collector stopped after write")

    with pytest.raises(UniverseHistoryCatchupError) as caught:
        run_universe_history_catchup(
            project_root=tmp_path,
            symbols=["MISS.SZ"],
            as_of_date="2026-08-07",
            lookback_calendar_days=1461,
            output_dir=tmp_path / "run",
            apply=True,
            collector=PartiallyWritingCollector(),
            adapter=_NoopAdapter(),
        )

    result = caught.value.result
    assert result["status"] == "failed"
    assert result["canonical_mutated_symbols"] == ["MISS.SZ"]
    persisted = json.loads(
        (tmp_path / "run" / "universe_history_catchup.json").read_text()
    )
    assert persisted["canonical_mutated_symbols"] == ["MISS.SZ"]

    from scripts import data_sync

    audit = SourceAuditStore(tmp_path / "data" / "audit" / "audit.db")
    run_id = "collector-partial-write"
    receipt_root = tmp_path / "data" / "audit" / "source_runs"
    audit.append_event(run_id, "run_started", {"entrypoint": "scripts/data_sync.py"})
    watermark_before = audit.watermark_snapshot_bytes()
    with pytest.raises(RuntimeError, match="mutated untrusted core scope"):
        data_sync._validate_universe_history_result(
            audit_store=audit,
            run_id=run_id,
            universe="csi1800",
            result=result,
        )
    audit.record_crash_receipt(
        run_id=run_id,
        receipt_root=receipt_root,
        entrypoint="scripts/data_sync.py",
        error="collector partial write",
    )
    events = audit.run_evidence_summary(run_id)["events"]
    assert any(event["event_type"] == "untrusted_outer_repair_scope" for event in events)
    receipt = json.loads((receipt_root / run_id / "receipt.json").read_text())
    assert receipt["trust_state"] == "untrusted"
    assert audit.watermark_snapshot_bytes() == watermark_before


def test_explicit_data_root_ignores_clean_runtime_data(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    data_root = tmp_path / "production" / "data"
    _write(
        data_root / "canonical" / "daily" / "BOUND.SZ.feather",
        ["20220810", "20260807"],
    )
    _write_registry(
        data_root / "qlib_bin" / "instruments" / "all.txt",
        [("BOUND.SZ", "2022-08-10", "2026-08-07")],
    )
    _write(
        runtime_root / "data" / "canonical" / "daily" / "BOUND.SZ.feather",
        ["20260807"],
    )

    result = inspect_universe_history(
        data_root=data_root,
        symbols=["BOUND.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )

    assert result["status"] == "pass"
    assert result["details"][0]["first_date"] == "2022-08-10"
