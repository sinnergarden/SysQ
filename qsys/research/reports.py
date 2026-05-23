"""Research report dataclasses — evaluation, backtest, and promotion records.

All reports use plain Python dataclasses and JSON serialisation.
No database, no pydantic, no MLflow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ── EvaluationReport ───────────────────────────────────────────────────────────


@dataclass
class EvaluationReport:
    """Outcome of a single research evaluation run."""

    strategy_id: str
    stage: str
    evaluation_id: str
    start_date: str
    end_date: str
    universe: str
    feature_set: str
    label: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    signal: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    turnover: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    status: str = "success"
    notes: str | None = None


# ── BacktestResult ─────────────────────────────────────────────────────────────


@dataclass
class BacktestResult:
    """Outcome of a single backtest run."""

    strategy_id: str
    backtest_id: str
    start_date: str
    end_date: str
    mode: str
    rebalance_freq: str
    initial_capital: float = 1_000_000.0
    final_value: float | None = None
    total_return: float | None = None
    annualized_return: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None
    turnover: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    status: str = "success"
    notes: str | None = None


# ── PromotionRecord ────────────────────────────────────────────────────────────


@dataclass
class PromotionRecord:
    """Record of a lifecycle stage promotion."""

    strategy_id: str
    from_stage: str
    to_stage: str
    reason: str
    approved_by: str | None = None
    created_at: str = ""
    evidence: dict[str, str] = field(default_factory=dict)


# ── Serialisation ──────────────────────────────────────────────────────────────


def write_report(report: EvaluationReport | BacktestResult, path: str | Path) -> None:
    """Write a single report to *path* as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_report(path: str | Path) -> dict[str, Any]:
    """Read a report JSON file and return a dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def append_promotion_record(record: PromotionRecord, path: str | Path) -> None:
    """Append a promotion record as a JSON line to *path* (JSONL format)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
