from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import data_sync
import scripts.ops.sync_csi800_daily as daily_sync
from scripts.ops.sync_csi800_daily import (
    _abort_if_stage_failed,
    _canonical_symbols_with_data_on_date,
    _repair_same_date_qlib_gap,
    _write_audit,
)


def test_data_sync_routes_csi1800_to_canonical_sync_entrypoint():
    with patch.object(
        sys,
        "argv",
        ["data_sync.py", "--universe", "csi1800", "--target-date", "2026-08-21"],
    ), patch("scripts.data_sync.subprocess.run") as run:
        data_sync.main()

    command = run.call_args.args[0]
    assert command[1].endswith("scripts/ops/sync_csi800_daily.py")
    assert command[2:] == [
        "--universe", "csi1800", "--target-date", "2026-08-21"
    ]
    assert run.call_args.kwargs["check"] is True


def test_shareholder_history_start_uses_target_and_positive_lookback():
    assert data_sync._shareholder_required_history_start_date(
        "2026-08-21", 1461
    ) == "2022-08-21"
    assert data_sync._shareholder_required_history_start_date(
        "20260821", 1
    ) == "2026-08-20"
    with pytest.raises(ValueError, match="positive"):
        data_sync._shareholder_required_history_start_date("2026-08-21", 0)


def test_csi1800_audit_uses_distinct_target_date_path(tmp_path: Path):
    path = _write_audit(
        tmp_path,
        {"universe": "csi1800", "target_date": "20260821"},
    )

    assert path == tmp_path / "sync_csi1800_20260821.json"


def test_sync_target_rejects_fallback_date():
    with pytest.raises(RuntimeError, match="exact synced csi1800"):
        data_sync._require_exact_sync_target(
            {
                "status": "fallback_to_latest_available",
                "resolved_trade_date": "2026-08-20",
            },
            requested_target="2026-08-21",
            universe="csi1800",
        )


def test_repair_audits_follow_external_data_root(tmp_path: Path):
    runtime = tmp_path / "runtime"
    data_root = tmp_path / "production" / "data"

    result = data_sync._data_sync_run_root(data_root, "20260823_210000")

    assert result == data_root / "audit" / "data_sync" / "20260823_210000"
    assert runtime not in result.parents


def test_failed_raw_stage_is_audited_and_blocks(tmp_path: Path):
    report = {"universe": "csi1800", "target_date": "20260821"}
    with pytest.raises(RuntimeError, match="raw_fetch failed"):
        _abort_if_stage_failed(
            report,
            stage="raw_fetch",
            summary={"status": "failed", "error": "source timeout"},
            do_apply=True,
            audit_dir=tmp_path,
        )

    assert report["overall_status"] == "failed"
    assert report["failure_stage"] == "raw_fetch"
    assert (tmp_path / "sync_csi1800_20260821.json").is_file()


def test_failed_qlib_stage_blocks_even_without_apply(tmp_path: Path):
    with pytest.raises(RuntimeError, match="qlib_convert failed"):
        _abort_if_stage_failed(
            {"universe": "csi1800", "target_date": "20260821"},
            stage="qlib_convert",
            summary={"status": "failed", "error": "dump failed"},
            do_apply=False,
            audit_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_failed_registry_refresh_is_audited_and_blocks(tmp_path: Path):
    report = {"universe": "csi1800", "target_date": "20260821"}
    with pytest.raises(RuntimeError, match="refresh_instruments failed"):
        _abort_if_stage_failed(
            report,
            stage="refresh_instruments",
            summary={"status": "failed", "error": "registry write failed"},
            do_apply=True,
            audit_dir=tmp_path,
        )

    audit = tmp_path / "sync_csi1800_20260821.json"
    assert audit.is_file()
    assert report["failure_stage"] == "refresh_instruments"


def test_same_date_canonical_gap_is_repaired_and_verified():
    target = "20260821"

    class Store:
        def __init__(self):
            self.frames = {
                "A.SZ": {"trade_date": [target], "close": [10.0]},
                "B.SZ": {"trade_date": [target], "close": [20.0]},
            }

        def load_daily(self, symbol):
            import pandas as pd

            return pd.DataFrame(self.frames.get(symbol, {}))

    class Adapter:
        def __init__(self):
            import pandas as pd

            self.frames = [
                pd.DataFrame(
                    {"$close": [20.0]},
                    index=pd.MultiIndex.from_tuples(
                        [("2026-08-21", "B.SZ")], names=["datetime", "instrument"]
                    ),
                ),
                pd.DataFrame(
                    {"$close": [10.0, 20.0]},
                    index=pd.MultiIndex.from_tuples(
                        [
                            ("2026-08-21", "A.SZ"),
                            ("2026-08-21", "B.SZ"),
                        ],
                        names=["datetime", "instrument"],
                    ),
                ),
            ]
            self.repaired = []
            self.feature_calls = []

        def get_features(self, *args, **kwargs):
            self.feature_calls.append((args, kwargs))
            return self.frames.pop(0)

        def convert_fix_symbols(self, symbols, **kwargs):
            self.repaired.append((symbols, kwargs))
            return {"status": "success"}

    adapter = Adapter()
    summary = _repair_same_date_qlib_gap(
        adapter,
        Store(),
        ["A.SZ", "B.SZ"],
        universe="csi800",
        target_dt=target,
        apply=True,
    )

    assert adapter.feature_calls[0][0][0] == ["A.SZ", "B.SZ"]
    assert adapter.feature_calls[1][0][0] == ["A.SZ", "B.SZ"]
    assert adapter.repaired == [(["A.SZ"], {"refresh_universes": []})]
    assert summary["missing_symbols"] == ["A.SZ"]
    assert summary["residual_symbols"] == []
    assert summary["verified_no_gap"] is True
    assert summary["status"] == "success"


def test_same_date_gap_fails_closed_when_repair_leaves_residual():
    import pandas as pd

    class Store:
        def load_daily(self, symbol):
            return pd.DataFrame({"trade_date": ["20260821"], "close": [1.0]})

    frame = pd.DataFrame(
        {"$close": [1.0]},
        index=pd.MultiIndex.from_tuples(
            [("2026-08-21", "OTHER.SZ")], names=["datetime", "instrument"]
        ),
    )

    class Adapter:
        def __init__(self):
            self.feature_calls = []

        def get_features(self, *_args, **_kwargs):
            self.feature_calls.append((_args, _kwargs))
            return frame

        def convert_fix_symbols(self, symbols, **kwargs):
            assert symbols == ["A.SZ"]
            assert kwargs == {"refresh_universes": []}
            return {"status": "success"}

    adapter = Adapter()
    summary = _repair_same_date_qlib_gap(
        adapter,
        Store(),
        ["A.SZ"],
        universe="csi1800",
        target_dt="20260821",
        apply=True,
    )

    assert summary["status"] == "failed"
    assert summary["verified_no_gap"] is False
    assert summary["residual_symbols"] == ["A.SZ"]


def test_paused_or_suspended_canonical_rows_do_not_trigger_repair():
    import pandas as pd

    target = "20260821"

    class Store:
        def __init__(self):
            self.frames = {
                "PAUSED_EMPTY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "open": [float("nan")],
                        "high": [float("nan")],
                        "low": [float("nan")],
                        "close": [float("nan")],
                        "vol": [0.0],
                        "amount": [0.0],
                        "paused": [1],
                    }
                ),
                "PAUSED_CARRY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "open": [10.0],
                        "high": [10.0],
                        "low": [10.0],
                        "close": [10.0],
                        "vol": [0.0],
                        "amount": [0.0],
                        "paused": [1],
                    }
                ),
                "SUSPENDED_CARRY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "close": [10.0],
                        "is_suspended": ["true"],
                    }
                ),
                "NO_CLOSE.SZ": pd.DataFrame({"trade_date": [target]}),
            }

        def load_daily(self, symbol):
            return self.frames[symbol]

    store = Store()
    symbols = list(store.frames)
    assert _canonical_symbols_with_data_on_date(store, symbols, target) == set()

    class Adapter:
        def get_features(self, requested_symbols, *_args, **_kwargs):
            assert requested_symbols == sorted(symbols)
            return pd.DataFrame({"$close": pd.Series(dtype=float)})

        def convert_fix_symbols(self, *_args, **_kwargs):
            raise AssertionError("suspended rows must not trigger same-date repair")

    summary = _repair_same_date_qlib_gap(
        Adapter(),
        store,
        symbols,
        universe="csi1800",
        target_dt=target,
        apply=True,
    )

    assert summary["status"] == "success"
    assert summary["canonical_symbols_with_data_count"] == 0
    assert summary["canonical_exclusion_count"] == len(symbols)
    assert {item["ts_code"] for item in summary["canonical_exclusions"]} == set(symbols)
    assert summary["missing_symbols"] == []
    assert summary["residual_symbols"] == []
    assert summary["verified_no_gap"] is True


def test_canonical_exclusions_are_audited_with_reasons():
    import pandas as pd

    target = "20260821"

    class Store:
        frames = {
            "PAUSED.SZ": pd.DataFrame(
                {"trade_date": [target], "close": [10.0], "paused": [1]}
            ),
            "SUSPENDED.SZ": pd.DataFrame(
                {"trade_date": [target], "close": [10.0], "is_suspended": ["true"]}
            ),
            "NO_ROW.SZ": pd.DataFrame({"trade_date": ["20260820"], "close": [10.0]}),
            "NO_CLOSE.SZ": pd.DataFrame({"trade_date": [target]}),
            "NULL_CLOSE.SZ": pd.DataFrame({"trade_date": [target], "close": [None]}),
        }

        def load_daily(self, symbol):
            return self.frames[symbol]

    available, exclusions = daily_sync._canonical_symbol_availability_on_date(
        Store(), list(Store.frames), target
    )
    assert available == set()
    assert exclusions == [
        {"ts_code": "NO_CLOSE.SZ", "reasons": ["missing_close_column"]},
        {"ts_code": "NO_ROW.SZ", "reasons": ["missing_target_row"]},
        {"ts_code": "NULL_CLOSE.SZ", "reasons": ["null_close"]},
        {"ts_code": "PAUSED.SZ", "reasons": ["paused"]},
        {"ts_code": "SUSPENDED.SZ", "reasons": ["is_suspended"]},
    ]


def test_main_ignores_stale_qlib_watermark_unless_repair_is_explicit(tmp_path):
    target = "20260821"
    collectors = []
    adapters = []

    class Collector:
        def __init__(self):
            self.daily_calls = []
            self.history_calls = []

        def update_daily(self, *args, **kwargs):
            self.daily_calls.append((args, kwargs))

        def update_universe_history(self, **kwargs):
            self.history_calls.append(kwargs)

    class Adapter:
        def __init__(self):
            self.qlib_dir = tmp_path / "qlib"
            self.incremental_calls = []
            self.fix_calls = []

        def init_qlib(self):
            pass

        def convert_incremental(self, since):
            self.incremental_calls.append(since)

        def convert_fix(self, since):
            self.fix_calls.append(since)

        def _refresh_universe_instruments(self, **_kwargs):
            pass

    class Store:
        pass

    def collector_factory():
        value = Collector()
        collectors.append(value)
        return value

    def adapter_factory():
        value = Adapter()
        adapters.append(value)
        return value

    snapshot = SimpleNamespace(
        instruments=["000001.SZ", "000002.SZ"],
        to_dict=lambda: {"snapshot_semantics": "pit"},
    )
    health = SimpleNamespace(ok=True, blocking_issues=[], warnings=[])
    reports = []
    common = {
        "QlibAdapter": adapter_factory,
        "TushareCollector": collector_factory,
        "StockDataStore": Store,
        "_resolve_target_date": lambda _value: target,
        "_check_stock_data_status": lambda *_args: {
            "have": [], "missing": ["000001.SZ", "000002.SZ"],
            "total": 2, "already_up_to_date": 0, "need_fetch": 2,
        },
        "_update_index_daily": lambda *_args: {},
        "_repair_same_date_qlib_gap": lambda *_args, **_kwargs: {
            "status": "success", "verified_no_gap": True,
            "canonical_exclusions": [], "canonical_exclusion_count": 0,
        },
        "_readiness_check": lambda *_args, **_kwargs: health,
        "_write_audit": lambda _audit_dir, report: reports.append(report),
        "_notify_telegram": lambda *_args, **_kwargs: None,
    }
    pit_path = "qsys.ops.pit_universe_snapshot.resolve_csi1800_pit_snapshot"
    registry_path = "qsys.ops.pit_universe_snapshot.write_current_qlib_registry"
    with patch.multiple(daily_sync, **common), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch(pit_path, return_value=snapshot), patch(registry_path, return_value={"status": "success"}):
        with patch.object(sys, "argv", ["sync", "--apply", "--universe", "csi1800", "--target-date", target]):
            daily_sync.main()
        with patch.object(
            sys,
            "argv",
            ["sync", "--apply", "--universe", "csi1800", "--target-date", target,
             "--repair-start-date", "20260820"],
        ):
            daily_sync.main()

    assert collectors[0].daily_calls
    assert collectors[0].daily_calls[0][0] == (target,)
    assert collectors[0].history_calls == []
    assert adapters[0].incremental_calls == ["2026-08-21"]
    assert reports[0]["sync_window"] == {
        "mode": "daily_single_day", "start_date": target, "target_date": target
    }
    assert collectors[1].daily_calls == []
    assert collectors[1].history_calls[0]["start_date"] == "20260820"
    assert adapters[1].incremental_calls == ["2026-08-20"]
    assert reports[1]["sync_window"] == {
        "mode": "explicit_historical_repair",
        "start_date": "20260820",
        "target_date": target,
    }
