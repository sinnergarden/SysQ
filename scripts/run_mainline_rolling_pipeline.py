#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import click

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from scripts.publish_mainline_rolling_ui_reports import main as publish_ui_main
from scripts.run_mainline_rolling_comparison import main as comparison_main
from scripts.run_mainline_rolling_eval import main as rolling_eval_main
from scripts.update_mainline_decision_evidence import main as update_decision_main


@click.command(name="run_mainline_rolling_pipeline")
@click.option("--start", default="2025-01-02")
@click.option("--end", default="2026-03-20")
@click.option("--rolling_dir", default="experiments/mainline_rolling")
@click.option("--reports_dir", default="experiments/reports")
@click.option("--mainline_object", "mainline_objects", multiple=True)
def main(start: str, end: str, rolling_dir: str, reports_dir: str, mainline_objects: tuple[str, ...]) -> None:
    args = [
        "--start", start,
        "--end", end,
        "--output_dir", rolling_dir,
    ]
    for mainline_object in mainline_objects:
        args.extend(["--mainline_object", mainline_object])
    rolling_eval_main.main(args=args, standalone_mode=False)

    cmp_args = ["--rolling_dir", rolling_dir]
    for mainline_object in mainline_objects:
        cmp_args.extend(["--mainline_object", mainline_object])
    comparison_main.main(args=cmp_args, standalone_mode=False)

    update_decision_main.main(
        args=[
            "--comparison_csv", f"{rolling_dir}/comparison_summary.csv",
            "--comparison_source", f"{rolling_dir}/comparison_summary.csv",
        ],
        standalone_mode=False,
    )

    publish_ui_main.main(
        args=["--rolling_dir", rolling_dir, "--reports_dir", reports_dir],
        standalone_mode=False,
    )

    print(f"rolling_pipeline_complete={project_root / rolling_dir}")


if __name__ == "__main__":
    main()
