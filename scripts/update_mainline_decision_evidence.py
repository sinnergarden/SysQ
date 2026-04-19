#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import click
import pandas as pd
import yaml

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.research import MAINLINE_OBJECTS
from qsys.research.rolling import decision_evidence_payload


DEFAULT_DECISION_FILES = {
    "feature_173": "research/decisions/feature_173_candidate.yaml",
    "feature_254": "research/decisions/feature_254_research_only.yaml",
    "feature_254_absnorm": "research/decisions/feature_254_absnorm_research_only.yaml",
}


@click.command(name="update_mainline_decision_evidence")
@click.option("--comparison_csv", default="experiments/mainline_rolling/comparison_summary.csv", help="Rolling comparison summary csv")
@click.option("--comparison_source", default="experiments/mainline_rolling/comparison_summary.csv", help="Stored comparison source reference")
def main(comparison_csv: str, comparison_source: str) -> None:
    comparison_path = (project_root / comparison_csv).resolve()
    if not comparison_path.exists():
        raise FileNotFoundError(f"Comparison summary not found: {comparison_path}")

    frame = pd.read_csv(comparison_path)
    if frame.empty:
        raise ValueError(f"Comparison summary is empty: {comparison_path}")

    updated_files: list[str] = []
    by_name = {str(row["mainline_object_name"]): row for _, row in frame.iterrows()}
    for mainline_object_name, spec in MAINLINE_OBJECTS.items():
        row = by_name.get(mainline_object_name)
        if row is None:
            raise ValueError(f"Missing comparison row for {mainline_object_name}")
        decision_path = (project_root / DEFAULT_DECISION_FILES[mainline_object_name]).resolve()
        payload = yaml.safe_load(decision_path.read_text(encoding="utf-8")) or {}
        payload["evidence"] = decision_evidence_payload(
            row.to_dict(),
            comparison_source=comparison_source,
        )
        payload["evidence"]["lineage"].setdefault("bundle_id", spec.bundle_id)
        payload["evidence"]["lineage"].setdefault("legacy_feature_set_alias", spec.legacy_feature_set_alias)
        decision_path.write_text(yaml.safe_dump(payload, allow_unicode=False, sort_keys=False), encoding="utf-8")
        updated_files.append(str(decision_path))

    for path in updated_files:
        print(f"updated_decision={path}")


if __name__ == "__main__":
    main()
