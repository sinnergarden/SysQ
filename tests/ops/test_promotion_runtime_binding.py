from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from qsys.ops.model_resolver import write_model_pointer
from qsys.ops.promotion_resolver import resolve_shadow_promotion
from qsys.research.candidate import (
    build_candidate_payload,
    promote_candidate_to_shadow,
    write_candidate,
)


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path
    config_path = project / "configs/strategies/alpha_v1.yaml"
    _write_yaml(config_path, {"strategy_id": "alpha_v1", "stage": "candidate"})

    model_dir = project / "experiments/alpha_v1_models/model_v1"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"approved-model")
    model_pointer = write_model_pointer(
        project_root=project,
        strategy_id="alpha_v1",
        mode="shadow",
        model_id="model_v1",
        model_path="experiments/alpha_v1_models/model_v1",
    )
    model_payload = yaml.safe_load(model_pointer.read_text(encoding="utf-8"))

    runtime = {
        "strategy_id": "alpha_v1",
        "strategy_config_path": "configs/strategies/alpha_v1.yaml",
        "strategy_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "model_ref": {
            "mode": "shadow",
            "model_id": "model_v1",
            "model_path": "experiments/alpha_v1_models/model_v1",
            "artifact_hash": model_payload["artifact_hash"],
            "pointer_path": "artifacts/registry/models/alpha_v1/shadow.json",
            "pointer_sha256": hashlib.sha256(model_pointer.read_bytes()).hexdigest(),
        },
    }
    candidate = {
        "candidate_id": "cand_v1",
        "signal_ref": {"signal_id": "sig", "signal_run_id": "sig_run"},
        "strategy": {
            "strategy_id": "alpha_v1",
            "strategy_config_id": "cfg",
            "strategy_template_id": "template",
        },
        "backtest_ref": {"backtest_id": "bt", "strategy_run_id": "strat_run"},
        "runtime_binding": runtime,
    }
    candidate_path = project / "data/research/candidates/cand_v1/candidate.yaml"
    _write_yaml(candidate_path, candidate)

    pointer = {
        "artifact_type": "shadow_promotion_pointer",
        "promotion_target": "shadow",
        "candidate_id": "cand_v1",
        "candidate_path": "data/research/candidates/cand_v1/candidate.yaml",
        "signal_ref": {"signal_id": "sig", "signal_run_id": "sig_run"},
        "strategy_config_id": "cfg",
        "strategy_template_id": "template",
        "backtest_id": "bt",
        "strategy_run_id": "strat_run",
        "promoted_at": "2026-08-12T00:00:00Z",
        "promoted_by": "test",
        "runtime_binding": runtime,
    }
    pointer_path = project / "data/research/promotions/shadow.yaml"
    _write_yaml(pointer_path, pointer)
    return project, pointer_path


def test_resolves_exact_strategy_config_and_model_binding(tmp_path: Path) -> None:
    project, pointer = _project(tmp_path)
    resolved = resolve_shadow_promotion(
        pointer, expected_strategy_id="alpha_v1", project_root=project
    )
    assert resolved["strategy_id"] == "alpha_v1"
    assert resolved["model_id"] == "model_v1"
    assert len(resolved["model_artifact_hash"]) == 64


def test_requested_strategy_must_match_promoted_strategy(tmp_path: Path) -> None:
    project, pointer = _project(tmp_path)
    with pytest.raises(ValueError, match="strategy mismatch"):
        resolve_shadow_promotion(
            pointer, expected_strategy_id="alpha_v2", project_root=project
        )


def test_config_mutation_invalidates_promotion(tmp_path: Path) -> None:
    project, pointer = _project(tmp_path)
    config = project / "configs/strategies/alpha_v1.yaml"
    config.write_text(config.read_text() + "display_name: changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="config hash mismatch"):
        resolve_shadow_promotion(pointer, project_root=project)


def test_model_mutation_invalidates_promotion(tmp_path: Path) -> None:
    project, pointer = _project(tmp_path)
    model = project / "experiments/alpha_v1_models/model_v1/model.bin"
    model.write_bytes(b"mutated-model")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        resolve_shadow_promotion(pointer, project_root=project)


def test_promotion_workflow_materializes_runtime_binding(tmp_path: Path) -> None:
    project = tmp_path
    config = project / "configs/strategies/alpha_v1.yaml"
    _write_yaml(config, {"strategy_id": "alpha_v1", "stage": "candidate"})
    model_dir = project / "experiments/alpha_v1_models/model_v1"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"approved-model")
    write_model_pointer(
        project_root=project,
        strategy_id="alpha_v1",
        mode="shadow",
        model_id="model_v1",
        model_path="experiments/alpha_v1_models/model_v1",
    )
    research_root = project / "data/research"
    candidate = build_candidate_payload(
        candidate_id="cand_v1",
        signal_ref={"signal_id": "sig", "signal_run_id": "sig_run"},
        strategy={
            "strategy_id": "alpha_v1",
            "strategy_config_id": "cfg",
            "strategy_config_path": "configs/strategies/alpha_v1.yaml",
            "strategy_template_id": "template",
        },
        backtest_ref={
            "strategy_run_id": "strat_run", "backtest_id": "bt", "path": "bt/path"
        },
    )
    write_candidate(candidate, research_root=research_root)
    pointer = promote_candidate_to_shadow(
        "cand_v1", research_root=research_root, project_root=project
    )

    assert pointer["runtime_binding"]["strategy_id"] == "alpha_v1"
    resolved = resolve_shadow_promotion(
        research_root / "promotions/shadow.yaml", project_root=project
    )
    assert resolved["model_id"] == "model_v1"
