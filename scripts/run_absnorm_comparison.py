from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.feature.library import FeatureLibrary

DEFAULT_VARIANTS = [
    ("extended", "qlib_lgbm_extended", "extended"),
    ("extended_absnorm", "qlib_lgbm_extended_absnorm", "extended_absnorm"),
    ("phase123", "qlib_lgbm_phase123", "phase123"),
    ("phase123_absnorm", "qlib_lgbm_phase123_absnorm", "phase123_absnorm"),
]


@click.command()
@click.option("--start", default="2026-02-02", help="Backtest start date")
@click.option("--end", default="2026-03-20", help="Backtest end date")
@click.option("--universe", default="csi300", help="Backtest universe")
@click.option("--top_k", default=5, type=int, help="Top-K holdings")
@click.option("--output_dir", default="experiments/absnorm_compare", help="Output directory")
def main(start, end, universe, top_k, output_dir):
    out_dir = project_root / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = FeatureLibrary.get_absolute_value_audit()
    (out_dir / "feature_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for variant, model_name, feature_set in DEFAULT_VARIANTS:
        model_path = project_root / "data" / "models" / model_name
        if not model_path.exists():
            rows.append(
                {
                    "variant": variant,
                    "feature_set": feature_set,
                    "model_path": str(model_path),
                    "status": "missing_model",
                }
            )
            continue

        cmd = [
            sys.executable,
            str(project_root / "scripts" / "run_backtest.py"),
            "--model_path",
            str(model_path),
            "--feature_set",
            feature_set,
            "--universe",
            universe,
            "--start",
            start,
            "--end",
            end,
            "--top_k",
            str(top_k),
        ]
        proc = subprocess.run(cmd, cwd=project_root, text=True, capture_output=True)
        rows.append(
            {
                "variant": variant,
                "feature_set": feature_set,
                "model_path": str(model_path),
                "status": "ok" if proc.returncode == 0 else "failed",
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-1000:],
                "stderr_tail": (proc.stderr or "")[-1000:],
            }
        )

    summary = pd.DataFrame(rows)
    summary_path = out_dir / "comparison_summary.csv"
    summary.to_csv(summary_path, index=False)

    md_lines = [
        "# Absolute-value normalization comparison",
        "",
        f"- start: {start}",
        f"- end: {end}",
        f"- universe: {universe}",
        f"- top_k: {top_k}",
        f"- feature_audit: {out_dir / 'feature_audit.json'}",
        f"- summary_csv: {summary_path}",
        "",
        "## Results",
        "",
        summary.to_markdown(index=False),
    ]
    report_path = out_dir / "comparison_summary.md"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"audit={out_dir / 'feature_audit.json'}")


if __name__ == "__main__":
    main()
