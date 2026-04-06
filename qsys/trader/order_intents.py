from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.live.ops_paths import ensure_stage_subdir


def build_order_intents(
    plan_df: pd.DataFrame | None,
    *,
    signal_date: str,
    execution_date: str,
    account_name: str,
    model_info: dict[str, Any] | None = None,
    assumptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = (plan_df.copy() if plan_df is not None else pd.DataFrame()).fillna(pd.NA)
    intents: list[dict[str, Any]] = []

    if not normalized.empty:
        for _, row in normalized.iterrows():
            amount = int(row.get("amount") or 0)
            if amount <= 0:
                continue

            side = str(row.get("side") or "review").lower()
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue

            intent = {
                "intent_id": f"{execution_date}:{account_name}:{side}:{symbol}",
                "account_name": account_name,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": float(row.get("price") or 0.0),
                "est_value": float(row.get("est_value") or 0.0),
                "score": None if pd.isna(row.get("score")) else float(row.get("score")),
                "score_rank": None if pd.isna(row.get("score_rank")) else int(row.get("score_rank")),
                "weight": float(row.get("weight") or 0.0),
                "target_value": float(row.get("target_value") or 0.0),
                "current_value": float(row.get("current_value") or 0.0),
                "diff_value": float(row.get("diff_value") or 0.0),
                "execution_bucket": str(row.get("execution_bucket") or "review"),
                "cash_dependency": str(row.get("cash_dependency") or "review"),
                "t1_rule": str(row.get("t1_rule") or "review"),
                "plan_role": str(row.get("plan_role") or "target_portfolio_delta"),
                "price_basis": {
                    "date": str(row.get("price_basis_date") or signal_date),
                    "field": str(row.get("price_basis_field") or "close"),
                    "label": str(row.get("price_basis_label") or f"close@{signal_date}"),
                },
                "status": str(row.get("status") or "planned"),
                "note": str(row.get("note") or ""),
            }
            intents.append(intent)

    return {
        "artifact_type": "order_intents",
        "signal_date": signal_date,
        "execution_date": execution_date,
        "account_name": account_name,
        "model_info": dict(model_info or {}),
        "assumptions": dict(assumptions or {}),
        "intent_count": len(intents),
        "intents": intents,
    }


def save_order_intents(
    payload: dict[str, Any],
    *,
    output_dir: str | Path,
    execution_date: str,
    account_name: str,
) -> str:
    output_dir = ensure_stage_subdir(output_dir, "order_intents")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"order_intents_{execution_date}_{account_name}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return str(path)
