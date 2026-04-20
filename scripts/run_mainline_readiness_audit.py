#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import click

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.research.readiness import (
    build_feature_coverage,
    build_missingness_summary,
    build_readiness_summary,
    fetch_mainline_feature_frame,
    resolve_mainline_specs,
    write_json,
)


@click.command(name="run_mainline_readiness_audit")
@click.option("--start", default="2025-01-02")
@click.option("--end", default="2026-03-20")
@click.option("--universe", default="csi300")
@click.option("--output_dir", default="experiments/mainline_readiness")
@click.option("--mainline_object", "mainline_objects", multiple=True)
def main(start: str, end: str, universe: str, output_dir: str, mainline_objects: tuple[str, ...]) -> None:
    out_root = (project_root / output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    coverage_frames = {}
    for spec in resolve_mainline_specs(mainline_objects or None):
        frame = fetch_mainline_feature_frame(spec=spec, start=start, end=end, universe=universe)
        coverage = build_feature_coverage(spec=spec, frame=frame)
        coverage_frames[spec.mainline_object_name] = coverage

    base_coverage = coverage_frames.get("feature_254")
    for spec in resolve_mainline_specs(mainline_objects or None):
        coverage = coverage_frames[spec.mainline_object_name]
        object_dir = out_root / spec.mainline_object_name
        object_dir.mkdir(parents=True, exist_ok=True)

        coverage.to_csv(object_dir / "feature_coverage_by_field.csv", index=False)
        coverage[coverage["constant_ratio"] >= 0.95].to_csv(object_dir / "feature_dead_or_constant.csv", index=False)

        missingness = build_missingness_summary(coverage)
        write_json(object_dir / "feature_missingness_summary.json", missingness)

        comparison_base = base_coverage if spec.mainline_object_name == "feature_254_absnorm" else None
        summary = build_readiness_summary(spec=spec, coverage=coverage, comparison_base=comparison_base)
        write_json(object_dir / "feature_readiness_summary.json", summary)
        print(f"readiness_summary={object_dir / 'feature_readiness_summary.json'}")


if __name__ == "__main__":
    main()
