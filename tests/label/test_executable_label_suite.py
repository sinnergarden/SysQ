from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.label.compute import iter_executable_forward_returns
from qsys.label.store import LabelStore


def _market_panel() -> pd.DataFrame:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    rows = []
    for instrument in ("A.SZ", "B.SZ", "D.SZ"):
        for offset, date in enumerate(dates):
            rows.append(
                {
                    "instrument": instrument,
                    "datetime": pd.Timestamp(date),
                    "$open": 10.0 + offset,
                    "$close": 10.5 + offset,
                    "$factor": 1.0,
                    "$paused": 0.0,
                    "$high_limit": 20.0,
                    "$low_limit": 5.0,
                }
            )
    # C has no row on the globally resolved T+2 target session.
    for offset, date in enumerate(("2024-01-02", "2024-01-03", "2024-01-05")):
        rows.append(
            {
                "instrument": "C.SZ",
                "datetime": pd.Timestamp(date),
                "$open": 30.0 + offset,
                "$close": 30.5 + offset,
                "$factor": 1.0,
                "$paused": 0.0,
                "$high_limit": 40.0,
                "$low_limit": 20.0,
            }
        )
    panel = pd.DataFrame(rows)
    panel.loc[
        (panel["instrument"] == "A.SZ")
        & (panel["datetime"] == pd.Timestamp("2024-01-04")),
        ["$factor", "$paused"],
    ] = [2.0, 1.0]
    panel.loc[
        (panel["instrument"] == "B.SZ")
        & (panel["datetime"] == pd.Timestamp("2024-01-02")),
        "$high_limit",
    ] = 10.0
    return panel.set_index(["instrument", "datetime"]).sort_index()


def test_executable_labels_use_global_sessions_and_entry_only_filtering(
    monkeypatch,
) -> None:
    import qsys.data.adapter as adapter_module
    import qsys.data.calendar as calendar_module
    import qsys.label.compute as compute_module

    panel = _market_panel()

    class FakeAdapter:
        def init_qlib(self) -> None:
            return None

        def get_features(self, instruments, fields, start_time, end_time):
            assert set(fields) == {
                "$open", "$close", "$factor", "$paused",
                "$high_limit", "$low_limit",
            }
            return panel.copy()

    spans = pd.DataFrame(
        {
            "instrument": ["A.SZ", "B.SZ", "C.SZ"],
            "effective_from": ["2024-01-01"] * 3,
            "effective_to": ["2024-12-31"] * 3,
        }
    )
    monkeypatch.setattr(adapter_module, "QlibAdapter", FakeAdapter)
    monkeypatch.setattr(
        calendar_module,
        "get_trading_calendar",
        lambda start, end: [
            "2023-12-29", "2024-01-02", "2024-01-03",
            "2024-01-04", "2024-01-05",
        ],
    )
    monkeypatch.setattr(
        compute_module,
        "_resolve_pit_artifact",
        lambda artifact: (["A.SZ", "B.SZ", "C.SZ"], spans.copy()),
    )

    outputs = dict(
        (label_id, frame)
        for label_id, frame, _ in iter_executable_forward_returns(
            universe="csi1800_pit_union",
            horizons=[2],
            start="2024-01-02",
            end="2024-01-05",
            pit_universe_artifact="fixture",
            label_templates={
                "open_to_open": "open_{horizon}",
                "close_to_close": "close_{horizon}",
            },
        )
    )

    primary = outputs["open_2"]
    a = primary[
        (primary["instrument"] == "A.SZ")
        & (primary["trade_date"] == "2024-01-02")
    ].iloc[0]
    assert a["signal_data_cutoff"] == "2023-12-29"
    assert a["return_end_date"] == "2024-01-04"
    assert a["return_start_price"] == 10.0
    assert a["return_end_price"] == 24.0
    assert np.isclose(a["label_value"], 1.4)
    assert a["exit_execution_status"] == "target_suspended"
    assert bool(a["is_valid"])

    b = primary[
        (primary["instrument"] == "B.SZ")
        & (primary["trade_date"] == "2024-01-02")
    ].iloc[0]
    assert not bool(b["entry_eligible"])
    assert b["invalid_reason"] == "entry_limit_up"
    assert not bool(b["is_valid"])

    c = primary[
        (primary["instrument"] == "C.SZ")
        & (primary["trade_date"] == "2024-01-02")
    ].iloc[0]
    assert c["return_end_date"] == "2024-01-04"
    assert bool(c["is_mature"])
    assert np.isnan(c["label_value"])
    assert c["label_missing_reason"] == "target_price_unobserved"
    assert c["exit_execution_status"] == "target_open_unobserved"
    assert not (primary["instrument"] == "D.SZ").any()

    secondary = outputs["close_2"]
    a_close = secondary[
        (secondary["instrument"] == "A.SZ")
        & (secondary["trade_date"] == "2024-01-02")
    ].iloc[0]
    assert np.isclose(a_close["label_value"], 25.0 / 10.5 - 1.0)


def _write_pit_artifact(directory: Path) -> tuple[Path, str]:
    directory.mkdir(parents=True)
    membership = pd.DataFrame(
        {
            "index_code": ["000906.SH"],
            "instrument": ["A.SZ"],
            "effective_from": ["20240101"],
            "effective_to": ["20241231"],
            "source": ["fixture"],
            "source_date": ["2024-01-01"],
            "source_version": ["v1"],
        }
    )
    membership_path = directory / "membership.parquet"
    membership.to_parquet(membership_path, index=False)
    membership_sha256 = hashlib.sha256(membership_path.read_bytes()).hexdigest()
    return membership_path, membership_sha256


def test_suite_manifest_binds_every_output(tmp_path: Path, monkeypatch) -> None:
    import qsys.data.adapter as adapter_module
    import qsys.label.compute as compute_module
    import qsys.research.pit_universe as pit_module

    registry_dir = tmp_path / "qlib" / "instruments"
    registry_dir.mkdir(parents=True)
    registry_path = registry_dir / "test_union.txt"
    registry_path.write_text("A.SZ\t2024-01-01\t2024-12-31\n")
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    pit_dir = tmp_path / "pit"
    _, membership_sha256 = _write_pit_artifact(pit_dir)
    (pit_dir / "manifest.json").write_text(
        json.dumps(
            {
                "universe_id": "test_pit_v1",
                "membership_sha256": membership_sha256,
                "raw_source_hash": "a" * 64,
                "registry_sha256": registry_sha256,
                "source": "fixture",
                "source_date": "2024-01-01",
                "n_snapshots": 1,
                "snapshot_date_range": ["20240101", "20240101"],
                "n_unique_instruments": 1,
                "n_membership_spans": 1,
                "description": "fixture",
            }
        )
    )
    source_path = tmp_path / "receipt.json"
    source_path.write_text('{"trusted": true}')
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    class FakeAdapter:
        qlib_dir = tmp_path / "qlib"

    monkeypatch.setattr(adapter_module, "QlibAdapter", FakeAdapter)
    monkeypatch.setattr(
        pit_module,
        "_git_provenance",
        lambda root: {
            "git_commit_full": "b" * 40,
            "git_commit_short": "b" * 7,
            "git_worktree_dirty": False,
            "git_scoped_dirty": False,
            "git_scoped_paths": [],
        },
    )
    def fake_iter(**kwargs):
        for return_type, label_id in (
            ("open_to_open", "open_5"),
            ("close_to_close", "close_5"),
        ):
            frame = pd.DataFrame(
                {
                    "trade_date": ["2024-01-02"],
                    "instrument": ["A.SZ"],
                    "label_id": [label_id],
                    "horizon": [5],
                    "label_value": [0.1],
                    "entry_eligible": [True],
                    "is_mature": [True],
                }
            )
            yield label_id, frame, {
                "horizon": 5,
                "return_type": return_type,
                "future_exit_status_used_for_filter": False,
            }

    monkeypatch.setattr(
        compute_module, "iter_executable_forward_returns", fake_iter
    )
    config = {
        "label_suite": {
            "suite_id": "suite_v1",
            "horizons": [5],
            "primary_label_template": "open_{horizon}",
            "secondary_label_template": "close_{horizon}",
        },
        "universe": "test_union",
        "pit_universe_artifact": str(pit_dir),
        "date_range": {
            "start_date": "2024-01-02",
            "data_cutoff": "2024-01-10",
        },
        "source_artifacts": {
            "receipt": {"path": str(source_path), "sha256": source_sha256}
        },
    }
    store = LabelStore(tmp_path / "research")
    outputs = store.compute_and_save_suite_from_config(config)
    assert set(outputs) == {"open_5", "close_5"}

    suite_path = store.paths.label_suite_manifest("suite_v1")
    suite = json.loads(suite_path.read_text())
    assert suite["output_count"] == 2
    assert suite["label_ids"] == ["close_5", "open_5"]
    for record in suite["outputs"]:
        data_path = store.paths.root / record["data_path"]
        manifest_path = store.paths.root / record["manifest_path"]
        assert hashlib.sha256(data_path.read_bytes()).hexdigest() == record[
            "labels_sha256"
        ]
        assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == record[
            "manifest_sha256"
        ]
        manifest = json.loads(manifest_path.read_text())
        assert manifest["label_suite_identity_sha256"] == suite[
            "label_suite_identity_sha256"
        ]


def test_canonical_cli_routes_suite_to_requested_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import scripts.research.compute_labels as cli

    config_path = tmp_path / "suite.yaml"
    config_path.write_text(
        "label_suite:\n  suite_id: fixture_suite\n",
        encoding="utf-8",
    )
    observed = {}

    class FakePaths:
        def label_suite_manifest(self, suite_id: str) -> Path:
            return tmp_path / suite_id / "manifest.json"

    class FakeStore:
        def __init__(self, root: str) -> None:
            observed["root"] = root
            self.paths = FakePaths()

        def compute_and_save_suite_from_config(self, config, overwrite=False):
            observed["config"] = config
            observed["overwrite"] = overwrite
            return {"label_a": tmp_path / "label_a.parquet"}

    monkeypatch.setattr(cli, "LabelStore", FakeStore)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compute_labels.py",
            "--config", str(config_path),
            "--research-root", str(tmp_path / "research"),
        ],
    )
    cli.main()
    assert observed["root"] == str(tmp_path / "research")
    assert "Done: fixture_suite (1 labels)" in capsys.readouterr().out
