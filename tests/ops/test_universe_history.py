from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.ops.universe_history import (
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
    assert adapter.calls == [(["LATE.SZ"], [])]
