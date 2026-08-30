from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from qsys.research.alpha_map import AlphaMap
from qsys.research.alpha_map_validation import validate_alpha_map


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_alpha_map_aggregates_only_validated_inputs(tmp_path: Path) -> None:
    root = tmp_path / "research"
    catalog_dir = root / "feature_catalogs" / "catalog"
    catalog_rows = [
        {
            "feature_name": "f1", "pit_tier": "PIT-A",
            "review_status": "reviewed-static", "review_notes": "ok",
        },
        {
            "feature_name": "blocked", "pit_tier": "PIT-X",
            "review_status": "data-blocked", "review_notes": "unresolved",
        },
    ]
    _json(catalog_dir / "feature_catalog.json", catalog_rows)
    catalog_identity = "a" * 64
    _json(
        catalog_dir / "manifest.json",
        {
            "catalog_id": "catalog",
            "catalog_identity_sha256": catalog_identity,
            "summary": {"unique_feature_count": 2},
            "artifacts": {"feature_catalog.json": _sha(catalog_dir / "feature_catalog.json")},
        },
    )
    _json(
        catalog_dir / "validation.json",
        {"validated": True, "catalog_identity_sha256": catalog_identity},
    )

    label_dir = root / "label_suites" / "labels"
    _json(
        label_dir / "manifest.json",
        {
            "suite_id": "labels",
            "label_suite_identity_sha256": "b" * 64,
        },
    )
    _json(
        label_dir / "validation.json",
        {
            "status": "PASS", "failures": [],
            "input_suite_manifest_sha256": _sha(label_dir / "manifest.json"),
            "label_suite_identity_sha256": "b" * 64,
        },
    )

    diagnostics_config = tmp_path / "diagnostics.yaml"
    diagnostics_config.write_text("diagnostics_id: experiment\n", encoding="utf-8")
    diagnostics_dir = root / "experiments" / "experiment" / "diagnostics"
    protocol = {
        "feature_trial_count": 1, "candidate_count": 1, "confirmed_count": 1,
        "rejected_count": 0, "holdout_consumed": False, "horizon_sessions": 20,
    }
    _json(diagnostics_dir / "stage_a_protocol.json", protocol)
    (diagnostics_dir / "stage_a_triage.csv").write_text(
        "feature,feature_family,discovery_pass,confirmation_pass\n"
        "f1,family,True,True\n",
        encoding="utf-8",
    )
    (diagnostics_dir / "coverage.csv").write_text(
        "feature,coverage,missing_rate,inf_rate,zero_rate\n"
        "f1,0.9,0.1,0.0,0.0\n",
        encoding="utf-8",
    )
    (diagnostics_dir / "coverage_yearly.csv").write_text(
        "year,feature,eligible_count,available_count,coverage\n"
        "2023,f1,10,9,0.9\n",
        encoding="utf-8",
    )
    outputs = {
        name: {"sha256": _sha(diagnostics_dir / name)}
        for name in (
            "stage_a_protocol.json", "stage_a_triage.csv",
            "coverage.csv", "coverage_yearly.csv",
        )
    }
    _json(
        diagnostics_dir / "manifest.json",
        {
            "config_sha256": _sha(diagnostics_config),
            "diagnostics_identity_sha256": "c" * 64,
            "outputs": outputs,
        },
    )
    _json(
        diagnostics_dir.parent / "diagnostics_validation.json",
        {"validated": True, "manifest_sha256": _sha(diagnostics_dir / "manifest.json")},
    )

    config = tmp_path / "alpha_map.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": "alpha_map_request_v1",
                "alpha_map_id": "map",
                "catalog_id": "catalog",
                "label_suite_id": "labels",
                "experiments": [
                    {
                        "experiment_id": "experiment",
                        "diagnostics_config": str(diagnostics_config),
                        "pit_tier": "PIT-A",
                        "track": "core",
                    }
                ],
                "required_ablation_ids": ["core", "core_financial", "core_shareholder", "full"],
                "ablations": [
                    {
                        "ablation_id": name, "components": [name],
                        "experiment_ids": ["experiment"],
                    }
                    for name in ("core", "core_financial", "core_shareholder", "full")
                ],
                "future_directions": [
                    {"rank": 1, "direction": "one"},
                    {"rank": 2, "direction": "two"},
                    {"rank": 3, "direction": "three"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    produced = AlphaMap.from_config(config, root=root).run()
    validated = validate_alpha_map(config, root=root)

    assert produced["alpha_map_identity_sha256"] == validated[
        "alpha_map_identity_sha256"
    ]
    assert validated["validated"] is True
    artifact = json.loads(
        (root / "alpha_maps/map/alpha_map.json").read_text(encoding="utf-8")
    )
    assert artifact["summary"]["confirmed_count"] == 1
    assert artifact["data_blocked_features"][0]["feature"] == "blocked"
