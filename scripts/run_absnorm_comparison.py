from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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


@click.command(name="compare_absnorm_variants")
@click.option("--start", default="2026-02-02", help="Backtest start date")
@click.option("--end", default="2026-03-20", help="Backtest end date")
@click.option("--universe", default="csi300", help="Backtest universe")
@click.option("--top_k", default=5, type=int, help="Top-K holdings")
@click.option("--output_dir", default="experiments/absnorm_compare", help="Output directory")
def main(start: str, end: str, universe: str, top_k: int, output_dir: str):
    out_dir = project_root / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = FeatureLibrary.get_absolute_value_audit()
    (out_dir / "feature_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for variant, model_name, feature_set in DEFAULT_VARIANTS:
        model_path = project_root / "data" / "models" / model_name
        variant_dir = out_dir / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "variant": variant,
            "feature_set": feature_set,
            "model_path": str(model_path),
        }
        if not model_path.exists():
            row.update(_missing_row("missing_model"))
            rows.append(row)
            continue

        cmd = [
            sys.executable,
            str(project_root / "scripts" / "run_backtest.py"),
            "--model_path", str(model_path),
            "--feature_set", feature_set,
            "--universe", universe,
            "--start", start,
            "--end", end,
            "--top_k", str(top_k),
        ]
        proc = subprocess.run(cmd, cwd=project_root, text=True, capture_output=True)
        row["status"] = "ok" if proc.returncode == 0 else "failed"
        row["returncode"] = proc.returncode
        row["stdout_tail"] = (proc.stdout or "")[-1200:]
        row["stderr_tail"] = (proc.stderr or "")[-1200:]
        if proc.returncode != 0:
            row.update(_missing_row("backtest_failed"))
            rows.append(row)
            continue

        row.update(_collect_artifact_metrics(project_root / "experiments"))
        rows.append(row)
        (variant_dir / "result_snapshot.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = pd.DataFrame(rows)
    summary = _ordered_summary(summary)
    summary_path = out_dir / "comparison_summary.csv"
    summary.to_csv(summary_path, index=False)

    report_path = out_dir / "comparison_summary.md"
    report_path.write_text(_build_markdown(summary, start=start, end=end, universe=universe, top_k=top_k, audit_path=out_dir / "feature_audit.json"), encoding="utf-8")

    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"audit={out_dir / 'feature_audit.json'}")


def _collect_artifact_metrics(experiment_dir: Path) -> dict[str, Any]:
    metrics = _read_json(experiment_dir / "metrics.json")
    signal_metrics = _read_json(experiment_dir / "signal_metrics.json")
    execution_summary = _read_json(experiment_dir / "execution_summary.json")
    exposure_summary = _read_json(experiment_dir / "exposure_summary.json")
    group_returns = _read_csv(experiment_dir / "group_returns.csv")

    stable = exposure_summary.get("stable_summary") if isinstance(exposure_summary, dict) else {}
    if not isinstance(stable, dict):
        stable = {}

    return {
        "total_return": _to_float(metrics.get("total_return")),
        "sharpe": _to_float(metrics.get("sharpe")),
        "max_drawdown": _to_float(metrics.get("max_drawdown")),
        "turnover": _to_float(execution_summary.get("avg_turnover_ratio")),
        "IC": _to_float(signal_metrics.get("IC")),
        "RankIC": _to_float(signal_metrics.get("RankIC")),
        "long_short_spread": _to_float(signal_metrics.get("long_short_spread")),
        "group_monotonicity_proxy": _group_monotonicity_proxy(group_returns),
        "empty_portfolio_ratio": _to_float(execution_summary.get("empty_portfolio_ratio")),
        "avg_holding_count": _to_float(execution_summary.get("avg_holding_count")),
        "size_tilt_vs_universe_mean": _stable_or_missing(stable, "size_tilt_vs_universe_mean"),
        "industry_drift_l1_mean": _stable_or_missing(stable, "industry_drift_l1_mean"),
        "top1_weight_mean": _stable_or_missing(stable, "top1_weight_mean"),
        "topk_weight_hhi_mean": _stable_or_missing(stable, "topk_weight_hhi_mean"),
    }


def _group_monotonicity_proxy(group_returns: pd.DataFrame) -> float | str:
    if group_returns.empty:
        return "missing_input"
    pivot = group_returns.pivot_table(index="date", columns="group", values="mean_return", aggfunc="mean").sort_index(axis=1)
    if pivot.empty or len(pivot.columns) < 5:
        return "missing_input"
    monotonic = []
    for _, row in pivot.iterrows():
        values = row.dropna().tolist()
        if len(values) < 5:
            continue
        decreasing = all(values[i] >= values[i + 1] for i in range(len(values) - 1))
        monotonic.append(1.0 if decreasing else 0.0)
    if not monotonic:
        return "missing_input"
    return round(float(sum(monotonic) / len(monotonic)), 8)


def _build_markdown(summary: pd.DataFrame, *, start: str, end: str, universe: str, top_k: int, audit_path: Path) -> str:
    lines = [
        "# Absnorm comparison summary",
        "",
        f"- start: {start}",
        f"- end: {end}",
        f"- universe: {universe}",
        f"- top_k: {top_k}",
        f"- feature_audit: {audit_path}",
        "",
    ]
    compact = summary[[
        "variant", "feature_set", "total_return", "sharpe", "max_drawdown", "turnover", "RankIC",
        "long_short_spread", "group_monotonicity_proxy", "empty_portfolio_ratio", "avg_holding_count",
        "size_tilt_vs_universe_mean", "industry_drift_l1_mean", "top1_weight_mean", "topk_weight_hhi_mean", "status",
    ]].copy()
    lines += ["## Comparison table", "", compact.to_markdown(index=False), ""]

    ok = summary[summary["status"] == "ok"].copy()
    if ok.empty:
        lines += ["## Conclusion", "", "- No successful variants. Check stdout_tail/stderr_tail in `comparison_summary.csv`."]
        return "\n".join(lines)

    best_rankic = ok.sort_values(["RankIC", "sharpe", "total_return"], ascending=[False, False, False]).iloc[0]
    best_sharpe = ok.sort_values(["sharpe", "total_return"], ascending=[False, False]).iloc[0]
    lowest_dd = ok.sort_values(["max_drawdown"], ascending=[False]).iloc[0]
    lines += [
        "## Conclusion",
        "",
        f"- Best RankIC variant: `{best_rankic['variant']}`",
        f"- Best risk-adjusted variant: `{best_sharpe['variant']}`",
        f"- Lowest drawdown variant: `{lowest_dd['variant']}`",
        "- Focus judgment on RankIC / group monotonicity / turnover-adjusted return / exposure stability, not single total_return only.",
    ]
    return "\n".join(lines)


def _ordered_summary(summary: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "variant", "feature_set", "status", "returncode", "total_return", "sharpe", "max_drawdown", "turnover",
        "IC", "RankIC", "long_short_spread", "group_monotonicity_proxy", "empty_portfolio_ratio", "avg_holding_count",
        "size_tilt_vs_universe_mean", "industry_drift_l1_mean", "top1_weight_mean", "topk_weight_hhi_mean",
        "model_path", "stdout_tail", "stderr_tail",
    ]
    for column in columns:
        if column not in summary.columns:
            summary[column] = None
    return summary[columns]


def _stable_or_missing(stable: dict[str, Any], key: str) -> float | str:
    value = stable.get(key, "missing_input")
    return _to_float(value) if value != "missing_input" else "missing_input"


def _missing_row(reason: str) -> dict[str, Any]:
    return {
        "status": reason,
        "returncode": None,
        "total_return": "missing_input",
        "sharpe": "missing_input",
        "max_drawdown": "missing_input",
        "turnover": "missing_input",
        "IC": "missing_input",
        "RankIC": "missing_input",
        "long_short_spread": "missing_input",
        "group_monotonicity_proxy": "missing_input",
        "empty_portfolio_ratio": "missing_input",
        "avg_holding_count": "missing_input",
        "size_tilt_vs_universe_mean": "missing_input",
        "industry_drift_l1_mean": "missing_input",
        "top1_weight_mean": "missing_input",
        "topk_weight_hhi_mean": "missing_input",
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _to_float(value: Any) -> float | str:
    if value is None:
        return "missing_input"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "missing_input"
        if text == "missing_input":
            return text
        if text.endswith("%"):
            try:
                return round(float(text[:-1]) / 100.0, 8)
            except ValueError:
                return "missing_input"
        try:
            return round(float(text), 8)
        except ValueError:
            return "missing_input"
    try:
        return round(float(value), 8)
    except (TypeError, ValueError):
        return "missing_input"


if __name__ == "__main__":
    main()
