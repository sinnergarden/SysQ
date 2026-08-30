"""Time-split, horizon-aware Stage-A feature evidence.

The evaluator deliberately stops before model or portfolio research.  A feature
direction is learned only on the discovery split, locked, and then reused on the
confirmation split.  The configured holdout is recorded but never consumed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.utils.logger import log
from qsys.research.evaluation import (
    compute_daily_ic,
    compute_daily_rank_ic,
    compute_group_returns,
    compute_ic_decay,
    compute_neutralized_rank_ic,
    compute_overlap_robustness,
    compute_rank_stability,
    compute_regime_ic,
    summarize_group_returns,
)


def _finite(value: Any) -> float | None:
    if value is None or not pd.notna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return _finite(clean.mean()) if not clean.empty else None


def _ir(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    std = float(clean.std(ddof=1))
    return _finite(clean.mean() / std) if std > 1e-12 else None


def _robustness_columns(values: pd.Series, horizon: int) -> dict[str, Any]:
    evidence = compute_overlap_robustness(values, horizon=horizon)
    bootstrap = evidence["block_bootstrap"]
    hac = evidence["newey_west"]
    offsets = evidence["non_overlapping_offsets"]
    usable = [float(row["mean"]) for row in offsets if row.get("mean") is not None]
    return {
        "mean": _finite(bootstrap.get("mean")),
        "bootstrap_ci_low": _finite(bootstrap.get("ci95", [None, None])[0]),
        "bootstrap_ci_high": _finite(bootstrap.get("ci95", [None, None])[1]),
        "hac_t": _finite(hac.get("t_stat")),
        "nonoverlap_positive_ratio": (
            _finite(np.mean(np.asarray(usable) > 0)) if usable else None
        ),
        "nonoverlap_offset_count": len(usable),
    }


def _evidence_pass(
    robust: dict[str, Any],
    *,
    minimum_hac_t: float,
    minimum_nonoverlap_ratio: float,
) -> bool:
    return bool(
        robust.get("bootstrap_ci_low") is not None
        and robust["bootstrap_ci_low"] > 0
        and robust.get("hac_t") is not None
        and robust["hac_t"] >= minimum_hac_t
        and robust.get("nonoverlap_positive_ratio") is not None
        and robust["nonoverlap_positive_ratio"] >= minimum_nonoverlap_ratio
    )


def _compute_topk_exact_random(
    joined: pd.DataFrame,
    *,
    top_ks: tuple[int, ...],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Top-K evidence with exact random-without-replacement moments."""
    rows: list[dict[str, Any]] = []
    for trade_date, day in joined.groupby("trade_date", sort=True):
        day = day.dropna(subset=["score", "label_value"])
        if day.empty:
            continue
        prediction = day.sort_values(
            ["score", "instrument"], ascending=[False, True], kind="mergesort"
        )
        actual = day.sort_values(
            ["label_value", "instrument"], ascending=[False, True], kind="mergesort"
        )
        population = pd.to_numeric(day["label_value"], errors="coerce")
        population_mean = float(population.mean())
        population_variance = float(population.var(ddof=1)) if len(day) > 1 else 0.0
        for top_k in top_ks:
            if top_k <= 0 or len(day) < top_k:
                continue
            predicted = prediction.head(top_k)
            predicted_mean = float(predicted["label_value"].mean())
            random_std = math.sqrt(
                max(1.0 - top_k / len(day), 0.0)
                * population_variance
                / top_k
            )
            rows.append({
                "trade_date": trade_date,
                "top_k": top_k,
                "n_eligible": int(len(day)),
                "hit_recall_at_k": len(
                    set(predicted["instrument"]) & set(actual.head(top_k)["instrument"])
                ) / top_k,
                "predicted_topk_return": predicted_mean,
                "universe_return": population_mean,
                "excess_vs_universe": predicted_mean - population_mean,
                "random_topk_mean": population_mean,
                "random_topk_std": random_std,
                "excess_vs_random": predicted_mean - population_mean,
            })
    frame = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "contract": "eligible_executable_label_topk_exact_random_moments_v1",
        "top_ks": list(top_ks),
        "by_k": {},
        "yearly": {},
    }
    if frame.empty:
        return frame, summary
    frame["year"] = pd.to_datetime(frame["trade_date"]).dt.year
    metrics = (
        "hit_recall_at_k", "predicted_topk_return", "excess_vs_universe",
        "excess_vs_random",
    )
    for top_k, group in frame.groupby("top_k"):
        summary["by_k"][str(int(top_k))] = {
            "n_dates": int(len(group)),
            **{metric: _mean(group[metric]) for metric in metrics},
        }
        summary["yearly"][str(int(top_k))] = {
            str(int(year)): {metric: _mean(values[metric]) for metric in metrics}
            for year, values in group.groupby("year")
        }
    return frame.drop(columns="year"), summary


@dataclass(frozen=True)
class Split:
    name: str
    start: str
    end: str


class StageAEvaluator:
    """Evaluate raw features without leaking confirmation or holdout data."""

    def __init__(
        self,
        *,
        feature_frame: pd.DataFrame,
        features: list[str],
        label_data: dict[str, pd.DataFrame],
        label_configs: list[dict[str, Any]],
        config: dict[str, Any],
        output_dir: Path,
    ) -> None:
        self.frame = feature_frame
        self.features = features
        self.labels = label_data
        self.label_configs = label_configs
        self.cfg = config
        self.output_dir = output_dir
        self.criteria = config.get("criteria", {})
        self.min_count = int(config.get("min_count", 30))
        self.n_groups = int(config.get("quantile_groups", 10))
        self.top_ks = tuple(int(value) for value in config.get("top_ks", [5, 20, 50]))
        self.random_reps = int(config.get("random_reps", 50))
        self.feature_families = self._feature_family_map()

    def _feature_family_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for family, values in (self.cfg.get("feature_families") or {}).items():
            for feature in values:
                if feature in result:
                    raise ValueError(f"Stage-A feature appears in multiple families: {feature}")
                result[str(feature)] = str(family)
        missing = sorted(set(self.features) - set(result))
        extra = sorted(set(result) - set(self.features))
        if missing or extra:
            raise ValueError(
                f"Stage-A family mapping mismatch; missing={missing}, extra={extra}"
            )
        return result

    def _split(self, name: str) -> Split:
        value = (self.cfg.get("splits") or {}).get(name) or {}
        start, end = str(value.get("start", "")), str(value.get("end", ""))
        if not start or not end or start > end:
            raise ValueError(f"invalid Stage-A {name} split")
        return Split(name, start, end)

    def _label_contracts(self) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        configs = [dict(value) for value in self.label_configs]
        primary = [value for value in configs if value.get("role") == "primary"]
        secondary = [value for value in configs if value.get("role") == "secondary"]
        if len(primary) != 1 or not secondary:
            raise ValueError("Stage-A requires exactly one primary and at least one secondary label")
        horizons: set[int] = set()
        for item in configs:
            label_id = str(item["label_id"])
            frame = self.labels.get(label_id)
            if frame is None or frame.empty or "horizon" not in frame.columns:
                raise ValueError(f"Stage-A label lacks materialized horizon: {label_id}")
            values = pd.to_numeric(frame["horizon"], errors="coerce").dropna().unique()
            if len(values) != 1 or int(values[0]) <= 0:
                raise ValueError(f"Stage-A label has invalid horizon: {label_id}")
            horizons.add(int(values[0]))
        if len(horizons) != 1:
            raise ValueError("one Stage-A experiment may evaluate only one horizon")
        return primary[0], secondary, horizons.pop()

    def _joined(
        self,
        feature: str,
        label_id: str,
        split: Split,
        direction: int,
    ) -> pd.DataFrame:
        support = [
            column for column in ("industry", "$industry", "circ_mv", "$circ_mv")
            if column in self.frame.columns
        ]
        left = self.frame.loc[
            self.frame["trade_date"].between(split.start, split.end, inclusive="both"),
            ["trade_date", "instrument", feature, *support],
        ].copy()
        label = self.labels[label_id]
        label = label.loc[
            label["trade_date"].astype(str).str[:10].between(
                split.start, split.end, inclusive="both"
            ),
            ["trade_date", "instrument", "label_value"],
        ].copy()
        joined = left.merge(label, on=["trade_date", "instrument"], how="inner")
        joined = joined.dropna(subset=[feature, "label_value"])
        joined["score"] = pd.to_numeric(joined[feature], errors="coerce") * direction
        joined = joined.dropna(subset=["score"])
        rename = {}
        if "$industry" in joined.columns and "industry" not in joined.columns:
            rename["$industry"] = "industry"
        if "$circ_mv" in joined.columns and "circ_mv" not in joined.columns:
            rename["$circ_mv"] = "circ_mv"
        return joined.rename(columns=rename)

    def _discovery_direction(
        self, feature: str, label_id: str, split: Split
    ) -> int:
        joined = self._joined(feature, label_id, split, 1)
        rank_ic = compute_daily_rank_ic(joined, "score", self.min_count)
        mean = _mean(rank_ic.get("rank_ic", pd.Series(dtype=float)))
        return -1 if mean is not None and mean < 0 else 1

    def _evaluate_pair(
        self,
        *,
        feature: str,
        label: dict[str, Any],
        split: Split,
        direction: int,
        horizon: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        label_id = str(label["label_id"])
        joined = self._joined(feature, label_id, split, direction)
        if joined.empty:
            raise ValueError(f"Stage-A pair has no observations: {feature} / {label_id} / {split.name}")
        diagnostic_only = str(label.get("role", "")) == "secondary"
        ic = compute_daily_ic(joined, "score", self.min_count)
        rank_ic = compute_daily_rank_ic(joined, "score", self.min_count)
        if diagnostic_only:
            quantile_daily = pd.DataFrame(columns=["top_minus_bottom"])
            quantile_summary: dict[str, Any] = {}
            topk_daily = pd.DataFrame(columns=["top_k", "excess_vs_universe"])
            topk_summary: dict[str, Any] = {"by_k": {}}
            stability_summary: dict[str, Any] = {"by_k": {}}
            neutral_summary: dict[str, Any] = {"methods": {}}
            regime = pd.DataFrame()
        else:
            group_returns = compute_group_returns(joined, "score", self.n_groups)
            quantile_daily, quantile_summary = summarize_group_returns(
                group_returns, self.n_groups
            )
            topk_daily, topk_summary = _compute_topk_exact_random(
                joined, top_ks=self.top_ks
            )
            _, stability_summary = compute_rank_stability(
                joined, score_column="score", top_ks=self.top_ks
            )
            _, neutral_summary = compute_neutralized_rank_ic(
                joined, score_column="score", min_count=self.min_count
            )
            regime = compute_regime_ic(
                rank_ic.rename(columns={"rank_ic": "ic"})[["date", "ic", "n"]]
            )

        rank_robust = _robustness_columns(rank_ic["rank_ic"], horizon)
        quantile_robust = _robustness_columns(
            quantile_daily.get("top_minus_bottom", pd.Series(dtype=float)), horizon
        )
        top20 = topk_daily[topk_daily.get("top_k", pd.Series(dtype=int)).eq(20)]
        topk_robust = _robustness_columns(
            top20.get("excess_vs_universe", pd.Series(dtype=float)), horizon
        )

        min_hac = float(self.criteria.get("minimum_hac_t", 1.96))
        min_offset = float(self.criteria.get("minimum_nonoverlap_ratio", 0.60))
        ic_pass = _evidence_pass(
            rank_robust, minimum_hac_t=min_hac,
            minimum_nonoverlap_ratio=min_offset,
        )
        quantile_pass = not diagnostic_only and _evidence_pass(
            quantile_robust, minimum_hac_t=min_hac,
            minimum_nonoverlap_ratio=min_offset,
        ) and bool((quantile_summary.get("monotonicity_mean") or 0) >= 0.5)
        topk_pass = not diagnostic_only and _evidence_pass(
            topk_robust, minimum_hac_t=min_hac,
            minimum_nonoverlap_ratio=min_offset,
        )

        rank_ic = rank_ic.copy()
        rank_ic["year"] = pd.to_datetime(rank_ic["date"]).dt.year
        yearly_rows = []
        for year, values in rank_ic.groupby("year"):
            yearly_rows.append({
                "phase": split.name,
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "label_id": label_id,
                "label_role": str(label.get("role", "")),
                "year": int(year),
                "rank_ic_mean": _mean(values["rank_ic"]),
                "rank_icir": _ir(values["rank_ic"]),
                "n_days": int(values["rank_ic"].notna().sum()),
            })
        valid_years = [row for row in yearly_rows if row["rank_ic_mean"] is not None]
        same_direction_ratio = (
            float(np.mean([row["rank_ic_mean"] > 0 for row in valid_years]))
            if valid_years else 0.0
        )
        minimum_years = int(
            self.criteria.get(
                "minimum_discovery_years" if split.name == "discovery"
                else "minimum_confirmation_years",
                3 if split.name == "discovery" else 2,
            )
        )
        yearly_pass = bool(
            len(valid_years) >= minimum_years
            and same_direction_ratio >= float(
                self.criteria.get("minimum_year_direction_ratio", 0.75)
            )
        )
        evidence_count = int(ic_pass) + int(quantile_pass) + int(topk_pass)
        phase_pass = bool(
            evidence_count >= int(self.criteria.get("minimum_evidence_classes", 2))
            and yearly_pass
        )

        row = {
            "phase": split.name,
            "feature": feature,
            "feature_family": self.feature_families[feature],
            "label_id": label_id,
            "label_role": str(label.get("role", "")),
            "horizon_sessions": horizon,
            "locked_direction": direction,
            "n_obs": int(len(joined)),
            "n_days": int(rank_ic["rank_ic"].notna().sum()),
            "ic_mean": _mean(ic["ic"]),
            "icir": _ir(ic["ic"]),
            "rank_ic_mean": _mean(rank_ic["rank_ic"]),
            "rank_icir": _ir(rank_ic["rank_ic"]),
            "rank_ic_bootstrap_ci_low": rank_robust["bootstrap_ci_low"],
            "rank_ic_bootstrap_ci_high": rank_robust["bootstrap_ci_high"],
            "rank_ic_hac_t": rank_robust["hac_t"],
            "rank_ic_nonoverlap_positive_ratio": rank_robust["nonoverlap_positive_ratio"],
            "quantile_top_minus_bottom_mean": quantile_robust["mean"],
            "quantile_bootstrap_ci_low": quantile_robust["bootstrap_ci_low"],
            "quantile_bootstrap_ci_high": quantile_robust["bootstrap_ci_high"],
            "quantile_hac_t": quantile_robust["hac_t"],
            "quantile_nonoverlap_positive_ratio": quantile_robust["nonoverlap_positive_ratio"],
            "quantile_monotonicity_mean": _finite(quantile_summary.get("monotonicity_mean")),
            "top20_excess_vs_universe_mean": topk_robust["mean"],
            "top20_bootstrap_ci_low": topk_robust["bootstrap_ci_low"],
            "top20_bootstrap_ci_high": topk_robust["bootstrap_ci_high"],
            "top20_hac_t": topk_robust["hac_t"],
            "top20_nonoverlap_positive_ratio": topk_robust["nonoverlap_positive_ratio"],
            "ic_evidence": ic_pass,
            "quantile_evidence": quantile_pass,
            "topk_evidence": topk_pass,
            "evidence_class_count": evidence_count,
            "year_count": len(valid_years),
            "same_direction_year_ratio": same_direction_ratio,
            "yearly_direction_pass": yearly_pass,
            "phase_pass": phase_pass,
            "industry_neutral_rank_ic": _finite(
                (neutral_summary.get("methods") or {}).get(
                    "industry_neutral", {}
                ).get("rank_ic_mean")
            ),
            "size_neutral_rank_ic": _finite(
                (neutral_summary.get("methods") or {}).get(
                    "size_neutral", {}
                ).get("rank_ic_mean")
            ),
        }

        topk_rows = []
        for top_k in (() if diagnostic_only else self.top_ks):
            values = topk_daily[topk_daily["top_k"].eq(top_k)]
            robust = _robustness_columns(
                values.get("excess_vs_universe", pd.Series(dtype=float)), horizon
            )
            summary = (topk_summary.get("by_k") or {}).get(str(top_k), {})
            topk_rows.append({
                "phase": split.name,
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "label_id": label_id,
                "label_role": str(label.get("role", "")),
                "top_k": top_k,
                "hit_recall_at_k": _finite(summary.get("hit_recall_at_k")),
                "predicted_topk_return": _finite(summary.get("predicted_topk_return")),
                "excess_vs_universe": _finite(summary.get("excess_vs_universe")),
                "excess_vs_random": _finite(summary.get("excess_vs_random")),
                "bootstrap_ci_low": robust["bootstrap_ci_low"],
                "bootstrap_ci_high": robust["bootstrap_ci_high"],
                "hac_t": robust["hac_t"],
                "nonoverlap_positive_ratio": robust["nonoverlap_positive_ratio"],
            })

        if diagnostic_only:
            decay = pd.DataFrame()
        else:
            signal = joined[["trade_date", "instrument", "score"]]
            label_for_decay = self.labels[label_id].copy()
            label_for_decay["trade_date"] = label_for_decay["trade_date"].astype(str).str[:10]
            decay_lags = tuple(sorted({0, 1, 2, 3, min(5, horizon), horizon}))
            decay = compute_ic_decay(
                signal,
                label_for_decay,
                score_column="score",
                lags=decay_lags,
                min_count=self.min_count,
            )
        decay_rows = [
            {
                "phase": split.name,
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "label_id": label_id,
                "label_role": str(label.get("role", "")),
                **record,
            }
            for record in decay.to_dict("records")
        ]
        regime_rows = [
            {
                "phase": split.name,
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "label_id": label_id,
                "label_role": str(label.get("role", "")),
                **record,
            }
            for record in regime.to_dict("records")
        ]
        stability_rows = []
        for top_k, values in (stability_summary.get("by_k") or {}).items():
            stability_rows.append({
                "phase": split.name,
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "label_id": label_id,
                "label_role": str(label.get("role", "")),
                "top_k": int(top_k),
                **values,
            })
        return row, yearly_rows, topk_rows, decay_rows, regime_rows + stability_rows

    def run(self) -> dict[str, Any]:
        discovery = self._split("discovery")
        confirmation = self._split("confirmation")
        holdout = self._split("holdout")
        if not (discovery.end < confirmation.start <= confirmation.end < holdout.start):
            raise ValueError("Stage-A splits must be disjoint and chronological")
        frame_min = str(self.frame["trade_date"].min())
        frame_max = str(self.frame["trade_date"].max())
        if frame_min > discovery.start or frame_max < confirmation.end:
            raise ValueError("loaded feature frame does not cover discovery and confirmation")
        if frame_max >= holdout.start:
            raise ValueError("Stage-A loaded data overlaps the untouched holdout")

        primary, secondary, horizon = self._label_contracts()
        labels = [primary, *secondary]
        evidence_rows: list[dict[str, Any]] = []
        yearly_rows: list[dict[str, Any]] = []
        topk_rows: list[dict[str, Any]] = []
        decay_rows: list[dict[str, Any]] = []
        mixed_rows: list[dict[str, Any]] = []
        directions: dict[str, int] = {}
        primary_discovery: dict[str, dict[str, Any]] = {}

        def evaluate(feature: str, label: dict[str, Any], split: Split) -> dict[str, Any]:
            result = self._evaluate_pair(
                feature=feature,
                label=label,
                split=split,
                direction=directions[feature],
                horizon=horizon,
            )
            row, yearly, topk, decay, mixed = result
            evidence_rows.append(row)
            yearly_rows.extend(yearly)
            topk_rows.extend(topk)
            decay_rows.extend(decay)
            mixed_rows.extend(mixed)
            return row

        for index, feature in enumerate(self.features, start=1):
            log.info(
                "Stage-A discovery [{}/{}] {}",
                index,
                len(self.features),
                feature,
            )
            directions[feature] = self._discovery_direction(
                feature, str(primary["label_id"]), discovery
            )
            primary_discovery[feature] = evaluate(feature, primary, discovery)
            for label in secondary:
                evaluate(feature, label, discovery)

        candidates = {
            feature for feature, row in primary_discovery.items() if row["phase_pass"]
        }
        confirmation_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for index, feature in enumerate(sorted(candidates), start=1):
            log.info(
                "Stage-A confirmation [{}/{}] {}",
                index,
                len(candidates),
                feature,
            )
            for label in labels:
                row = evaluate(feature, label, confirmation)
                confirmation_rows[(feature, str(label["label_id"]))] = row

        triage_rows = []
        for feature in self.features:
            discovery_row = primary_discovery[feature]
            confirmation_row = confirmation_rows.get(
                (feature, str(primary["label_id"]))
            )
            secondary_confirmation = [
                confirmation_rows.get((feature, str(label["label_id"])))
                for label in secondary
            ]
            secondary_consistent = bool(
                confirmation_row
                and all(
                    row is not None
                    and row.get("rank_ic_mean") is not None
                    and row["rank_ic_mean"] > 0
                    for row in secondary_confirmation
                )
            )
            if feature not in candidates:
                status = "rejected"
            elif confirmation_row and confirmation_row["phase_pass"] and secondary_consistent:
                status = "confirmed"
            elif confirmation_row is not None:
                status = "rejected"
            else:
                status = "candidate"
            triage_rows.append({
                "feature": feature,
                "feature_family": self.feature_families[feature],
                "horizon_sessions": horizon,
                "locked_direction": directions[feature],
                "discovery_evidence_class_count": discovery_row["evidence_class_count"],
                "discovery_yearly_direction_pass": discovery_row["yearly_direction_pass"],
                "discovery_pass": discovery_row["phase_pass"],
                "confirmation_executed": confirmation_row is not None,
                "confirmation_evidence_class_count": (
                    confirmation_row["evidence_class_count"] if confirmation_row else None
                ),
                "confirmation_yearly_direction_pass": (
                    confirmation_row["yearly_direction_pass"] if confirmation_row else None
                ),
                "confirmation_pass": (
                    confirmation_row["phase_pass"] if confirmation_row else False
                ),
                "secondary_direction_consistent": secondary_consistent,
                "research_status": status,
            })

        evidence = pd.DataFrame(evidence_rows).sort_values(
            ["phase", "label_role", "feature"]
        )
        yearly = pd.DataFrame(yearly_rows).sort_values(
            ["phase", "label_role", "feature", "year"]
        )
        topk = pd.DataFrame(topk_rows).sort_values(
            ["phase", "label_role", "feature", "top_k"]
        )
        decay = pd.DataFrame(decay_rows).sort_values(
            ["phase", "label_role", "feature", "lag_sessions"]
        )
        mixed = pd.DataFrame(mixed_rows)
        regime_columns = {"regime", "ic_mean"}
        regime = mixed[
            [column for column in mixed.columns if column in {
                "phase", "feature", "feature_family", "label_id", "label_role",
                "regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio",
            }]
        ].dropna(subset=["regime"]) if regime_columns.issubset(mixed.columns) else pd.DataFrame()
        stability = mixed[
            [column for column in mixed.columns if column in {
                "phase", "feature", "feature_family", "label_id", "label_role",
                "top_k", "n_transitions", "rank_autocorrelation", "topk_jaccard",
                "ranking_turnover",
            }]
        ].dropna(subset=["top_k"]) if "top_k" in mixed.columns else pd.DataFrame()
        triage = pd.DataFrame(triage_rows).sort_values(
            ["research_status", "feature_family", "feature"]
        )

        outputs = {
            "stage_a_evidence.csv": evidence,
            "stage_a_yearly.csv": yearly,
            "stage_a_topk.csv": topk,
            "stage_a_decay.csv": decay,
            "stage_a_regime.csv": regime,
            "stage_a_stability.csv": stability,
            "stage_a_triage.csv": triage,
        }
        for name, frame in outputs.items():
            frame.to_csv(self.output_dir / name, index=False)

        protocol = {
            "schema_version": "stage_a_protocol_v1",
            "methodology_contract": "time_split_locked_direction_feature_evidence_v1",
            "horizon_sessions": horizon,
            "label_roles": {
                "primary": str(primary["label_id"]),
                "secondary": [str(value["label_id"]) for value in secondary],
            },
            "splits": {
                split.name: {"start": split.start, "end": split.end}
                for split in (discovery, confirmation, holdout)
            },
            "holdout_consumed": False,
            "loaded_data_end": frame_max,
            "criteria": self.criteria,
            "feature_trial_count": len(self.features),
            "secondary_discovery_diagnostic_count": len(self.features) * len(secondary),
            "candidate_count": len(candidates),
            "confirmation_trial_count": len(candidates),
            "secondary_confirmation_diagnostic_count": len(candidates) * len(secondary),
            "confirmed_count": int(triage["research_status"].eq("confirmed").sum()),
            "rejected_count": int(triage["research_status"].eq("rejected").sum()),
            "random_signal_contract": "exact_finite_population_moments_v1",
            "top_ks": list(self.top_ks),
            "feature_families": {
                family: sum(value == family for value in self.feature_families.values())
                for family in sorted(set(self.feature_families.values()))
            },
        }
        (self.output_dir / "stage_a_protocol.json").write_text(
            json.dumps(protocol, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return protocol
