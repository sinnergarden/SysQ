from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


EXPOSURE_COLUMNS = ["size", "industry", "beta"]


def compute_portfolio_exposure_diagnostics(
    selection_panel: pd.DataFrame,
    exposure_panel: pd.DataFrame | None = None,
    *,
    top_k: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    selection = _normalize_selection_panel(selection_panel)
    exposures = _normalize_exposure_panel(exposure_panel)

    if selection.empty:
        return {
            "status": "not_available",
            "reason": "selection_panel_empty",
            "top_k": int(top_k),
            "metrics": {},
            "availability": _availability_payload(exposures),
        }, pd.DataFrame(columns=["date", "metric", "value"])

    merged = selection.merge(exposures, on=["date", "instrument"], how="left") if not exposures.empty else selection.copy()
    rows: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    holding_count = merged.groupby("date")["instrument"].nunique().rename("holding_count")
    rows.extend(_series_rows(holding_count, "holding_count"))
    metrics["holding_count"] = _series_summary(holding_count)

    if "selected_rank" in merged.columns:
        max_rank = merged.groupby("date")["selected_rank"].max().rename("max_selected_rank")
        rows.extend(_series_rows(max_rank, "max_selected_rank"))
        metrics["max_selected_rank"] = _series_summary(max_rank)

    weight_top1 = merged.groupby("date")["target_weight"].max().rename("top1_weight")
    rows.extend(_series_rows(weight_top1, "top1_weight"))
    metrics["top1_weight"] = _series_summary(weight_top1)

    topk_concentration = merged.groupby("date")["target_weight"].apply(lambda s: float(np.square(s.astype(float)).sum())).rename("topk_weight_hhi")
    rows.extend(_series_rows(topk_concentration, "topk_weight_hhi"))
    metrics["topk_weight_hhi"] = _series_summary(topk_concentration)

    if "size" in merged.columns and merged["size"].notna().any():
        weighted_size = merged.groupby("date").apply(lambda df: _weighted_average(df, "size")).rename("size_weighted_mean")
        universe_size = exposures.groupby("date")["size"].mean().rename("size_universe_mean") if not exposures.empty else pd.Series(dtype=float)
        size_tilt = (weighted_size - universe_size.reindex(weighted_size.index)).rename("size_tilt_vs_universe")
        size_qcut = _bucket_distribution(merged, value_col="size", bucket_prefix="size_bucket")
        rows.extend(_series_rows(weighted_size, "size_weighted_mean"))
        rows.extend(_series_rows(universe_size, "size_universe_mean"))
        rows.extend(_series_rows(size_tilt, "size_tilt_vs_universe"))
        rows.extend(size_qcut)
        metrics["size_weighted_mean"] = _series_summary(weighted_size)
        metrics["size_universe_mean"] = _series_summary(universe_size)
        metrics["size_tilt_vs_universe"] = _series_summary(size_tilt)
    else:
        metrics["size_tilt_vs_universe"] = {"status": "missing_input", "required": "size"}

    if "industry" in merged.columns and merged["industry"].notna().any():
        industry_rows, industry_metrics = _industry_metrics(merged, exposures)
        rows.extend(industry_rows)
        metrics.update(industry_metrics)
    else:
        metrics["industry_weight_hhi"] = {"status": "missing_input", "required": "industry"}
        metrics["industry_drift_l1"] = {"status": "missing_input", "required": "industry"}

    beta_metrics = {"status": "missing_input", "required": "beta"}
    if "beta" in merged.columns and merged["beta"].notna().any():
        weighted_beta = merged.groupby("date").apply(lambda df: _weighted_average(df, "beta")).rename("beta_weighted_mean")
        rows.extend(_series_rows(weighted_beta, "beta_weighted_mean"))
        beta_metrics = _series_summary(weighted_beta)
    metrics["beta_weighted_mean"] = beta_metrics

    artifacts = pd.DataFrame(rows)
    if not artifacts.empty:
        artifacts = artifacts.sort_values(["date", "metric"]).reset_index(drop=True)

    stable_summary = summarize_exposure_timeseries(artifacts)
    summary = {
        "status": "available",
        "top_k": int(top_k),
        "availability": _availability_payload(merged),
        "metrics": metrics,
        "stable_summary": stable_summary,
    }
    return summary, artifacts


def summarize_exposure_timeseries(artifacts: pd.DataFrame) -> dict[str, Any]:
    if artifacts is None or artifacts.empty:
        return {
            "status": "not_available",
            "size_tilt_vs_universe_mean": "missing_input",
            "size_tilt_vs_universe_abs_mean": "missing_input",
            "industry_drift_l1_mean": "missing_input",
            "industry_weight_hhi_mean": "missing_input",
            "beta_weighted_mean_mean": "missing_input",
            "top1_weight_mean": "missing_input",
            "topk_weight_hhi_mean": "missing_input",
            "avg_holding_count": "missing_input",
        }

    def metric_mean(metric: str, *, abs_value: bool = False) -> float | str:
        subset = artifacts.loc[artifacts["metric"] == metric, "value"]
        subset = pd.to_numeric(subset, errors="coerce").dropna()
        if subset.empty:
            return "missing_input"
        if abs_value:
            subset = subset.abs()
        return round(float(subset.mean()), 8)

    return {
        "status": "available",
        "size_tilt_vs_universe_mean": metric_mean("size_tilt_vs_universe"),
        "size_tilt_vs_universe_abs_mean": metric_mean("size_tilt_vs_universe", abs_value=True),
        "industry_drift_l1_mean": metric_mean("industry_drift_l1"),
        "industry_weight_hhi_mean": metric_mean("industry_weight_hhi"),
        "beta_weighted_mean_mean": metric_mean("beta_weighted_mean"),
        "top1_weight_mean": metric_mean("top1_weight"),
        "topk_weight_hhi_mean": metric_mean("topk_weight_hhi"),
        "avg_holding_count": metric_mean("holding_count"),
    }



def _normalize_selection_panel(selection_panel: pd.DataFrame) -> pd.DataFrame:
    if selection_panel is None or selection_panel.empty:
        return pd.DataFrame(columns=["date", "instrument", "target_weight", "signal_value", "selected_rank"])
    panel = selection_panel.copy()
    rename_map = {"symbol": "instrument", "score": "signal_value", "weight": "target_weight", "rank": "selected_rank"}
    panel = panel.rename(columns={k: v for k, v in rename_map.items() if k in panel.columns and v not in panel.columns})
    for col in ["date", "instrument", "target_weight"]:
        if col not in panel.columns:
            raise ValueError(f"selection_panel missing required column: {col}")
    if "signal_value" not in panel.columns:
        panel["signal_value"] = np.nan
    if "selected_rank" not in panel.columns:
        panel["selected_rank"] = panel.groupby("date")["target_weight"].rank(method="first", ascending=False)
    panel["date"] = pd.to_datetime(panel["date"]).dt.strftime("%Y-%m-%d")
    panel["instrument"] = panel["instrument"].astype(str)
    panel["target_weight"] = pd.to_numeric(panel["target_weight"], errors="coerce")
    panel["signal_value"] = pd.to_numeric(panel["signal_value"], errors="coerce")
    panel["selected_rank"] = pd.to_numeric(panel["selected_rank"], errors="coerce")
    return panel.dropna(subset=["date", "instrument", "target_weight"]).reset_index(drop=True)



def _normalize_exposure_panel(exposure_panel: pd.DataFrame | None) -> pd.DataFrame:
    if exposure_panel is None or exposure_panel.empty:
        return pd.DataFrame(columns=["date", "instrument", *EXPOSURE_COLUMNS])
    panel = exposure_panel.copy()
    rename_map = {"symbol": "instrument"}
    panel = panel.rename(columns={k: v for k, v in rename_map.items() if k in panel.columns and v not in panel.columns})
    for col in ["date", "instrument"]:
        if col not in panel.columns:
            raise ValueError(f"exposure_panel missing required column: {col}")
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    panel["instrument"] = panel["instrument"].astype(str)
    if "size" in panel.columns:
        panel["size"] = pd.to_numeric(panel["size"], errors="coerce")
    if "industry" in panel.columns:
        panel["industry"] = panel["industry"].astype(str).replace({"nan": np.nan, "": np.nan})
    if "beta" in panel.columns:
        panel["beta"] = pd.to_numeric(panel["beta"], errors="coerce")
    keep_cols = [c for c in ["date", "instrument", *EXPOSURE_COLUMNS] if c in panel.columns]
    return panel[keep_cols].dropna(subset=["date", "instrument"]).drop_duplicates(["date", "instrument"])



def _series_rows(series: pd.Series, metric: str) -> list[dict[str, Any]]:
    clean = series.dropna()
    return [{"date": idx, "metric": metric, "value": float(val)} for idx, val in clean.items()]



def _series_summary(series: pd.Series) -> dict[str, Any]:
    clean = series.dropna().astype(float)
    if clean.empty:
        return {"status": "not_available"}
    return {
        "status": "available",
        "mean": round(float(clean.mean()), 8),
        "min": round(float(clean.min()), 8),
        "max": round(float(clean.max()), 8),
        "observations": int(clean.shape[0]),
    }



def _weighted_average(frame: pd.DataFrame, value_col: str) -> float:
    valid = frame[[value_col, "target_weight"]].dropna()
    if valid.empty:
        return np.nan
    weight_sum = float(valid["target_weight"].sum())
    if weight_sum <= 0:
        return np.nan
    return float((valid[value_col] * valid["target_weight"]).sum() / weight_sum)



def _bucket_distribution(frame: pd.DataFrame, *, value_col: str, bucket_prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date, group in frame.groupby("date"):
        valid = group[[value_col, "target_weight"]].dropna()
        if valid.empty or valid[value_col].nunique() < 2:
            continue
        bucket_count = min(5, valid[value_col].nunique())
        try:
            buckets = pd.qcut(valid[value_col], q=bucket_count, labels=False, duplicates="drop")
        except ValueError:
            continue
        for bucket, weight in valid.groupby(buckets)["target_weight"].sum().items():
            rows.append({"date": date, "metric": f"{bucket_prefix}_{int(bucket) + 1}_weight", "value": float(weight)})
    return rows



def _industry_metrics(selection: pd.DataFrame, exposures: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    portfolio_industry = selection.groupby(["date", "industry"]) ["target_weight"].sum().rename("portfolio_weight").reset_index()
    industry_hhi = portfolio_industry.groupby("date")["portfolio_weight"].apply(lambda s: float(np.square(s.astype(float)).sum())).rename("industry_weight_hhi")
    rows.extend(_series_rows(industry_hhi, "industry_weight_hhi"))
    metrics["industry_weight_hhi"] = _series_summary(industry_hhi)

    if exposures.empty or "industry" not in exposures.columns or exposures["industry"].notna().sum() == 0:
        metrics["industry_drift_l1"] = {"status": "missing_universe_baseline", "required": "industry"}
        return rows, metrics

    universe_industry = exposures.groupby(["date", "industry"]).size().rename("universe_count").reset_index()
    universe_total = universe_industry.groupby("date")["universe_count"].transform("sum").replace(0, np.nan)
    universe_industry["universe_weight"] = universe_industry["universe_count"] / universe_total
    merged = portfolio_industry.merge(universe_industry[["date", "industry", "universe_weight"]], on=["date", "industry"], how="outer").fillna(0.0)
    drift = merged.groupby("date").apply(lambda df: float(np.abs(df["portfolio_weight"] - df["universe_weight"]).sum() / 2.0)).rename("industry_drift_l1")
    rows.extend(_series_rows(drift, "industry_drift_l1"))
    metrics["industry_drift_l1"] = _series_summary(drift)
    return rows, metrics



def _availability_payload(frame: pd.DataFrame) -> dict[str, Any]:
    payload = {"selection_rows": int(frame.shape[0]) if frame is not None else 0}
    for column in ["size", "industry", "beta"]:
        payload[column] = bool(frame is not None and column in frame.columns and frame[column].notna().any())
    return payload
