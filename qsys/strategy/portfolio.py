from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .base import IStrategy

PORTFOLIO_STAGE = "portfolio"
EPSILON = 1e-9


@dataclass(frozen=True)
class PortfolioIntentResult:
    target_weights: pd.DataFrame
    reason_codes: list[dict[str, Any]]


class PortfolioOptimizer(IStrategy):
    def generate_orders(self, signals, current_portfolio):
        raise NotImplementedError("Use build_portfolio_intent for explicit portfolio intent generation")


def build_portfolio_intent(
    raw_scores: pd.DataFrame,
    broker_snapshot: Mapping[str, Any] | None = None,
    risk_rules: Mapping[str, Any] | None = None,
) -> PortfolioIntentResult:
    scores = _normalize_scores(raw_scores)
    rules = _normalize_risk_rules(risk_rules)
    current_qty = _extract_current_positions(broker_snapshot)
    reason_codes: list[dict[str, Any]] = []

    if scores.empty:
        target_weights = pd.DataFrame(columns=["ts_code", "target_weight", "score", "selection_rank", "current_qty"])
        return PortfolioIntentResult(target_weights=target_weights, reason_codes=reason_codes)

    blacklisted = set(rules["blacklist"])
    if blacklisted:
        rejected_blacklist = scores[scores["ts_code"].isin(blacklisted)]
        reason_codes.extend(
            _build_reason(
                row.ts_code,
                action="rejected",
                reason="rejected_blacklist",
                score=row.score,
            )
            for row in rejected_blacklist.itertuples(index=False)
        )
        scores = scores[~scores["ts_code"].isin(blacklisted)]

    max_positions = rules["max_positions"]
    selected = scores.copy()
    if max_positions is not None and max_positions >= 0:
        kept = selected.head(max_positions)
        dropped = selected.iloc[max_positions:]
        if not dropped.empty:
            reason_codes.extend(
                _build_reason(
                    row.ts_code,
                    action="rejected",
                    reason="rejected_max_positions",
                    score=row.score,
                )
                for row in dropped.itertuples(index=False)
            )
        selected = kept

    if selected.empty:
        target_weights = pd.DataFrame(columns=["ts_code", "target_weight", "score", "selection_rank", "current_qty"])
        return PortfolioIntentResult(target_weights=target_weights, reason_codes=reason_codes)

    selected = selected.reset_index(drop=True)
    weights = _build_base_weights(selected["score"])
    industry_cap = rules["max_industry_weight"]
    if industry_cap is not None and industry_cap > 0:
        if "industry" in selected.columns and selected["industry"].notna().any():
            weights, trimmed_codes = _apply_industry_cap(selected, weights, industry_cap)
            reason_codes.extend(
                _build_reason(
                    ts_code,
                    action="trimmed",
                    reason="trimmed_industry_weight_cap",
                    score=float(selected.loc[selected["ts_code"] == ts_code, "score"].iloc[0]),
                )
                for ts_code in sorted(trimmed_codes)
            )
        else:
            reason_codes.extend(
                _build_reason(
                    row.ts_code,
                    action="selected",
                    reason="selected_industry_limit_skipped_missing_input",
                    score=row.score,
                )
                for row in selected.itertuples(index=False)
            )

    target_weights = selected.copy()
    target_weights["target_weight"] = weights.round(8)
    target_weights["selection_rank"] = range(1, len(target_weights) + 1)
    target_weights["current_qty"] = target_weights["ts_code"].map(current_qty).fillna(0).astype(int)
    keep_columns = ["ts_code", "target_weight", "score", "selection_rank", "current_qty"]
    if "industry" in target_weights.columns:
        keep_columns.append("industry")
    target_weights = target_weights[keep_columns].sort_values(
        ["target_weight", "score", "ts_code"], ascending=[False, False, True]
    ).reset_index(drop=True)

    reason_codes.extend(
        _build_reason(
            row.ts_code,
            action="selected",
            reason="selected_score_rank",
            score=row.score,
        )
        for row in selected.itertuples(index=False)
    )
    return PortfolioIntentResult(target_weights=target_weights, reason_codes=reason_codes)


def save_target_weights(target_weights: pd.DataFrame, output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_weights.to_csv(output_path, index=False)
    return str(output_path)


def save_reason_codes(reason_codes: list[dict[str, Any]], output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(reason_codes, handle, indent=2, ensure_ascii=False)
    return str(output_path)


def _normalize_scores(raw_scores: pd.DataFrame) -> pd.DataFrame:
    if raw_scores is None or raw_scores.empty:
        return pd.DataFrame(columns=["ts_code", "score"])

    scores = raw_scores.copy()
    rename_map = {}
    if "ts_code" not in scores.columns and "symbol" in scores.columns:
        rename_map["symbol"] = "ts_code"
    if rename_map:
        scores = scores.rename(columns=rename_map)

    if "ts_code" not in scores.columns or "score" not in scores.columns:
        raise ValueError("raw_scores must contain ts_code/symbol and score")

    industry_column = _find_first_column(scores.columns, ["industry", "industry_name", "sw_industry", "sector"])
    keep_columns = ["ts_code", "score"]
    if industry_column:
        keep_columns.append(industry_column)
    scores = scores[keep_columns].copy()
    if industry_column and industry_column != "industry":
        scores = scores.rename(columns={industry_column: "industry"})

    scores["ts_code"] = scores["ts_code"].astype(str).str.strip()
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    if "industry" in scores.columns:
        scores["industry"] = scores["industry"].fillna("").astype(str).str.strip()

    scores = scores[(scores["ts_code"] != "") & scores["score"].notna()]
    scores = scores.sort_values(["score", "ts_code"], ascending=[False, True])
    scores = scores.drop_duplicates(subset=["ts_code"], keep="first").reset_index(drop=True)
    return scores


def _normalize_risk_rules(risk_rules: Mapping[str, Any] | None) -> dict[str, Any]:
    rules = dict(risk_rules or {})
    blacklist = rules.get("blacklist") or []
    max_positions = rules.get("max_positions")
    if max_positions is not None:
        max_positions = int(max_positions)
    max_industry_weight = rules.get("max_industry_weight")
    if max_industry_weight is not None:
        max_industry_weight = float(max_industry_weight)
    return {
        "blacklist": {str(item).strip() for item in blacklist if str(item).strip()},
        "max_positions": max_positions,
        "max_industry_weight": max_industry_weight,
    }


def _extract_current_positions(broker_snapshot: Mapping[str, Any] | None) -> dict[str, int]:
    if not broker_snapshot:
        return {}

    positions = broker_snapshot.get("positions") if isinstance(broker_snapshot, Mapping) else None
    if not positions:
        return {}

    current_qty: dict[str, int] = {}
    for item in positions:
        ts_code = str(item.get("ts_code") or item.get("symbol") or "").strip()
        if not ts_code:
            continue
        quantity = int(item.get("total_amount") or item.get("quantity") or item.get("amount") or 0)
        current_qty[ts_code] = quantity
    return current_qty


def _build_base_weights(scores: pd.Series) -> pd.Series:
    positive_scores = scores.clip(lower=0)
    if float(positive_scores.sum()) > EPSILON:
        basis = positive_scores.astype(float)
    else:
        basis = pd.Series(range(len(scores), 0, -1), index=scores.index, dtype=float)
    return basis / basis.sum()


def _apply_industry_cap(
    selected: pd.DataFrame,
    weights: pd.Series,
    max_industry_weight: float,
) -> tuple[pd.Series, set[str]]:
    weights = weights.copy().astype(float)
    industries = selected["industry"].where(selected["industry"] != "", selected["ts_code"])
    trimmed_codes: set[str] = set()
    locked_industries: set[str] = set()

    for _ in range(len(selected) + 2):
        industry_weights = weights.groupby(industries).sum()
        overflow = industry_weights[industry_weights > max_industry_weight + EPSILON]
        if overflow.empty:
            break

        capped_industries = set(overflow.index)
        excess_total = 0.0
        for industry_name, current_weight in overflow.items():
            mask = industries == industry_name
            new_weights = weights.loc[mask] * (max_industry_weight / current_weight)
            excess_total += float(weights.loc[mask].sum() - new_weights.sum())
            weights.loc[mask] = new_weights
            trimmed_codes.update(selected.loc[mask, "ts_code"].tolist())

        locked_industries.update(capped_industries)
        recipient_mask = (~industries.isin(locked_industries)) & (weights > 0)
        if excess_total <= EPSILON or not recipient_mask.any():
            break

        recipients = weights.loc[recipient_mask]
        weights.loc[recipient_mask] += recipients / recipients.sum() * excess_total

    weight_sum = float(weights.sum())
    if 0 < weight_sum < 1 and weight_sum > 1 - EPSILON:
        weights = weights / weight_sum
    return weights, trimmed_codes


def _build_reason(ts_code: str, *, action: str, reason: str, score: float | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts_code": ts_code,
        "stage": PORTFOLIO_STAGE,
        "action": action,
        "reason": reason,
    }
    if score is not None:
        payload["score"] = round(float(score), 8)
    return payload


def _find_first_column(columns: pd.Index, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


__all__ = [
    "PortfolioIntentResult",
    "PortfolioOptimizer",
    "build_portfolio_intent",
    "save_reason_codes",
    "save_target_weights",
]
