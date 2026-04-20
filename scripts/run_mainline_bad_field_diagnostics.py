#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import click

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.research.diagnostics import build_bad_field_diagnostics, load_coverage_csv, write_trimmed_diagnostics


@click.command(name="run_mainline_bad_field_diagnostics")
@click.option("--readiness_dir", default="experiments/mainline_readiness")
@click.option("--output_dir", default="experiments/mainline_diagnostics")
def main(readiness_dir: str, output_dir: str) -> None:
    readiness_root = (project_root / readiness_dir).resolve()
    coverage_254 = load_coverage_csv(readiness_root / "feature_254" / "feature_coverage_by_field.csv")
    coverage_254_absnorm = load_coverage_csv(readiness_root / "feature_254_absnorm" / "feature_coverage_by_field.csv")

    bad_254, bad_absnorm, proposal = build_bad_field_diagnostics(
        coverage_254=coverage_254,
        coverage_254_absnorm=coverage_254_absnorm,
    )
    outputs = write_trimmed_diagnostics(
        output_dir=(project_root / output_dir).resolve(),
        bad_254=bad_254,
        bad_absnorm=bad_absnorm,
        proposal=proposal,
    )
    for key, value in outputs.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
