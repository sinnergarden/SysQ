"""
ADR-007 Artifact Adapters.

Read existing artifact outputs and construct ADR-007 compliant dataclass instances.
Missing fields use explicit "not_available" / "not_applicable" / None values.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.artifacts.contracts import (
    SignalArtifact,
    OrderIntentArtifact,
    ExecutionArtifact,
    PortfolioSnapshot,
    RunManifest,
    NOT_AVAILABLE,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return NOT_AVAILABLE


def _read_json(path: str | Path) -> dict | None:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _now() -> str:
    return datetime.now().isoformat()


def _cs_rank(scores: pd.Series) -> pd.Series:
    """Cross-sectional rank (1-based, highest score = rank 1)."""
    return scores.rank(ascending=False, method="min").astype(int)


# ── SignalArtifact adapter ─────────────────────────────────────────────

def adapt_predictions(predictions_csv: str | Path,
                      strategy_id: str = NOT_AVAILABLE,
                      plan_meta_path: str | Path | None = None,
                      created_at: str | None = None) -> list[SignalArtifact]:
    """Read predictions CSV and produce SignalArtifact instances.

    Maps existing columns:
        trade_date, instrument, score → ADR-7 fields
    Missing fields (candidate_id, model_version, etc.) → "not_available"
    """
    pred_path = Path(predictions_csv)
    if not pred_path.exists():
        return []

    df = pd.read_csv(pred_path)
    if df.empty:
        return []

    ts = created_at or _now()

    # Cross-sectional rank from score
    if "score" in df.columns:
        ranks = _cs_rank(df["score"])
    else:
        ranks = pd.Series([1] * len(df))

    artifacts: list[SignalArtifact] = []
    for _, row in df.iterrows():
        score = float(row.get("score", 0.0))
        art = SignalArtifact(
            trade_date=str(row.get("trade_date", NOT_AVAILABLE)),
            strategy_id=strategy_id,
            instrument=str(row.get("instrument", NOT_AVAILABLE)),
            score=score if np.isfinite(score) else 0.0,
            rank=int(ranks.loc[row.name]),
            raw_prediction=score if np.isfinite(score) else None,
            normalized_score=score if np.isfinite(score) else None,
            created_at=ts,
        )
        artifacts.append(art)

    return artifacts


# ── OrderIntentArtifact adapter ────────────────────────────────────────

def adapt_order_intents(order_intents_csv: str | Path,
                        strategy_id: str = NOT_AVAILABLE,
                        account_id: str = NOT_AVAILABLE,
                        created_at: str | None = None) -> list[OrderIntentArtifact]:
    """Read order_intents CSV and produce OrderIntentArtifact instances."""
    oi_path = Path(order_intents_csv)
    if not oi_path.exists():
        return []

    df = pd.read_csv(oi_path)
    if df.empty:
        return []

    ts = created_at or _now()

    artifacts: list[OrderIntentArtifact] = []
    for _, row in df.iterrows():
        side = str(row.get("side", "")).upper()
        target_weight = float(row.get("target_weight", 0.0))
        current_weight = float(row.get("current_weight", 0.0))
        requested_qty = row.get("requested_qty", 0)

        # delta_quantity: positive for buy, negative for sell
        if side == "BUY":
            delta_qty = int(requested_qty) if pd.notna(requested_qty) else None
        elif side == "SELL":
            delta_qty = -int(requested_qty) if pd.notna(requested_qty) else None
        else:
            delta_qty = None

        art = OrderIntentArtifact(
            trade_date=str(row.get("trade_date", NOT_AVAILABLE)),
            strategy_id=strategy_id,
            account_id=account_id,
            instrument=str(row.get("instrument", NOT_AVAILABLE)),
            side=side,
            target_weight=target_weight,
            current_weight=current_weight,
            delta_quantity=delta_qty,
            reason=str(row.get("reason", NOT_AVAILABLE)),
            created_at=ts,
        )
        artifacts.append(art)

    return artifacts


# ── ExecutionArtifact adapter ──────────────────────────────────────────

def adapt_executions(ledger_rows_csv: str | Path,
                     strategy_id: str = NOT_AVAILABLE,
                     account_id: str = NOT_AVAILABLE,
                     created_at: str | None = None) -> list[ExecutionArtifact]:
    """Read ledger_rows CSV and produce ExecutionArtifact instances."""
    lr_path = Path(ledger_rows_csv)
    if not lr_path.exists():
        return []

    df = pd.read_csv(lr_path)
    if df.empty:
        return []

    ts = created_at or _now()

    artifacts: list[ExecutionArtifact] = []
    for _, row in df.iterrows():
        art = ExecutionArtifact(
            trade_date=str(row.get("trade_date", NOT_AVAILABLE)),
            run_id=str(row.get("run_id", NOT_AVAILABLE)),
            strategy_id=strategy_id,
            account_id=account_id,
            instrument=str(row.get("instrument", NOT_AVAILABLE)),
            side=str(row.get("side", "")).upper(),
            quantity=int(row.get("quantity", 0)),
            price=float(row.get("price", 0.0)),
            commission=float(row.get("fee", 0.0)),
            status=str(row.get("status", "filled")),
            reason=str(row.get("reason", NOT_AVAILABLE)),
            created_at=ts,
        )
        artifacts.append(art)

    return artifacts


# ── PortfolioSnapshot adapter ──────────────────────────────────────────

def adapt_portfolio_snapshot(mtm_snapshot_json: str | Path,
                             trade_date: str = NOT_AVAILABLE,
                             account_id: str = NOT_AVAILABLE,
                             strategy_id: str = NOT_AVAILABLE,
                             turnover: float = 0.0,
                             created_at: str | None = None) -> PortfolioSnapshot | None:
    """Read MTM snapshot JSON and produce a PortfolioSnapshot instance."""
    data = _read_json(mtm_snapshot_json)
    if not data:
        return None

    ts = created_at or _now()
    cash = float(data.get("cash", 0.0))
    market_value = float(data.get("market_value", 0.0))
    total_asset = float(data.get("total_value", 0.0))
    daily_pnl = data.get("daily_pnl")
    position_count = len(data.get("details", []))
    cumulative_pnl = data.get("cumulative_pnl")
    cumulative_pnl_pct = data.get("cumulative_pnl_pct")
    initial_capital = data.get("initial_capital")

    return PortfolioSnapshot(
        trade_date=trade_date,
        account_id=account_id,
        strategy_id=strategy_id,
        cash=cash,
        market_value=market_value,
        total_asset=total_asset,
        daily_pnl=float(daily_pnl) if daily_pnl is not None else None,
        position_count=position_count,
        turnover=turnover,
        cumulative_pnl=float(cumulative_pnl) if cumulative_pnl is not None else None,
        cumulative_pnl_pct=float(cumulative_pnl_pct) if cumulative_pnl_pct is not None else None,
        initial_capital=float(initial_capital) if initial_capital is not None else None,
        created_at=ts,
    )


# ── RunManifest adapter ────────────────────────────────────────────────

def build_run_manifest(
    run_id: str,
    trade_date: str,
    stage: str,
    strategy_id: str,
    account_id: str,
    status: str = "completed",
    input_artifacts: list[dict[str, str]] | None = None,
    output_artifacts: list[dict[str, str]] | None = None,
    error: str | None = None,
    notes: str | None = None,
    config_hash: str | None = None,
    model_version: str | None = None,
    signal_version: str | None = None,
    data_version: str | None = None,
) -> RunManifest:
    """Construct a RunManifest from run metadata."""
    return RunManifest(
        run_id=run_id,
        trade_date=trade_date,
        stage=stage,
        strategy_id=strategy_id,
        account_id=account_id,
        status=status,
        git_commit=_git_commit(),
        config_hash=config_hash or NOT_AVAILABLE,
        data_version=data_version or NOT_AVAILABLE,
        model_version=model_version or NOT_AVAILABLE,
        signal_version=signal_version or NOT_AVAILABLE,
        input_artifacts=input_artifacts or [],
        output_artifacts=output_artifacts or [],
        error=error,
        notes=notes,
        created_at=_now(),
        updated_at=_now(),
    )


# ── Bulk: read plan_meta.json helpers ──────────────────────────────────

def read_plan_meta(plan_meta_json: str | Path) -> dict[str, Any]:
    """Read plan_meta.json and return a dict of known fields."""
    data = _read_json(plan_meta_json) or {}
    return {
        "strategy_id": data.get("strategy_id", NOT_AVAILABLE),
        "strategy_version": data.get("strategy_version", NOT_AVAILABLE),
        "top_n": data.get("top_n"),
        "buffer_buy": data.get("buffer_buy"),
        "buffer_hold": data.get("buffer_hold"),
        "single_stock_cap": data.get("single_stock_cap"),
        "total_value_before": data.get("total_value_before"),
    }


def read_execution_summary(execution_summary_json: str | Path) -> dict[str, Any]:
    """Read execution_summary.json and return a dict of known fields."""
    data = _read_json(execution_summary_json) or {}
    return {
        "run_id": data.get("run_id"),
        "strategy_id": data.get("strategy_id"),
        "trade_date": data.get("trade_date"),
        "cash_after": data.get("cash_after"),
        "market_value_after": data.get("market_value_after"),
        "total_value_after": data.get("total_value_after"),
        "turnover": data.get("turnover"),
        "status": data.get("status"),
        "order_count": data.get("order_count"),
        "buy_count": data.get("buy_count"),
        "sell_count": data.get("sell_count"),
        "filled_count": data.get("filled_count"),
    }
