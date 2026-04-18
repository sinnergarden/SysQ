#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.research.rolling import best_and_worst, build_comparison_summary, comparison_markdown, resolve_mainline_specs


@click.command(name="run_mainline_rolling_comparison")
@click.option("--rolling_dir", default="experiments/mainline_rolling", help="Directory containing per-object rolling outputs")
@click.option("--output_dir", default=None, help="Directory for comparison outputs; defaults to rolling_dir")
@click.option("--mainline_object", "mainline_objects", multiple=True, help="Optional mainline object filter; repeat for multiple values")
def main(rolling_dir: str, output_dir: str | None, mainline_objects: tuple[str, ...]) -> None:
    rolling_root = (project_root / rolling_dir).resolve()
    compare_root = rolling_root if output_dir in (None, "") else (project_root / output_dir).resolve()
    compare_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for spec in resolve_mainline_specs(mainline_objects or None):
        summary_path = rolling_root / spec.mainline_object_name / "rolling_summary.json"
        if not summary_path.exists():
            raise FileNotFoundError(f"Missing rolling summary for {spec.mainline_object_name}: {summary_path}")
        rows.append(json.loads(summary_path.read_text(encoding="utf-8")))

    comparison = build_comparison_summary(pd.DataFrame(rows))
    summary_path = compare_root / "comparison_summary.csv"
    comparison.to_csv(summary_path, index=False)

    outcome = best_and_worst(comparison)
    markdown = comparison_markdown(comparison)
    if outcome["best"] is not None:
        markdown += "\n\n"
        markdown += f"- Best: `{outcome['best']['mainline_object_name']}`\n"
        markdown += f"- Worst: `{outcome['worst']['mainline_object_name']}`\n"
    report_path = compare_root / "comparison_summary.md"
    report_path.write_text(markdown, encoding="utf-8")

    print(f"comparison_summary={summary_path}")
    print(f"comparison_report={report_path}")


if __name__ == "__main__":
    main()
