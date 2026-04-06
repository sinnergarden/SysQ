from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.live.ops_paths import DEFAULT_DAILY_ROOT, DEFAULT_DERIVED_ROOT

_SIGNAL_BASKET_COLUMNS = [
    "signal_date",
    "execution_date",
    "account_name",
    "symbol",
    "score",
    "score_rank",
    "weight",
    "price",
    "price_basis_date",
    "price_basis_field",
    "price_basis_label",
    "model_name",
    "model_path",
    "universe",
    "artifact_source",
]

_ORDER_INTENT_COLUMNS = [
    "signal_date",
    "execution_date",
    "account_name",
    "intent_id",
    "symbol",
    "side",
    "amount",
    "price",
    "est_value",
    "score",
    "score_rank",
    "weight",
    "target_value",
    "current_value",
    "diff_value",
    "execution_bucket",
    "cash_dependency",
    "t1_rule",
    "plan_role",
    "status",
    "note",
    "artifact_source",
]

_RECONCILIATION_SUMMARY_COLUMNS = [
    "signal_date",
    "execution_date",
    "account_name",
    "metric",
    "real",
    "shadow",
    "diff",
    "artifact_source",
]

_POSITION_GAP_COLUMNS = [
    "signal_date",
    "execution_date",
    "account_name",
    "symbol",
    "real_amount",
    "shadow_amount",
    "amount_diff",
    "real_price",
    "shadow_price",
    "real_cost_basis",
    "shadow_cost_basis",
    "cost_basis_diff",
    "real_market_value",
    "shadow_market_value",
    "market_value_diff",
    "artifact_source",
]


@dataclass(frozen=True)
class RollupTableResult:
    table_name: str
    output_path: str
    added_rows: int
    total_rows: int
    source_files: int


@dataclass(frozen=True)
class DailyRollupResult:
    execution_date: str
    derived_root: str
    tables: dict[str, RollupTableResult]


def _artifact_source(path: Path, daily_root: Path) -> str:
    try:
        return str(path.relative_to(daily_root.parent))
    except ValueError:
        return str(path)


def _load_signal_date(day_root: Path, execution_date: str) -> str:
    manifest_dirs = [
        day_root / "pre_open" / "manifests",
        day_root / "post_close" / "manifests",
    ]
    for manifest_dir in manifest_dirs:
        for path in sorted(manifest_dir.glob("daily_ops_manifest_*.json"), reverse=True):
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle) or {}
            signal_date = payload.get("signal_date")
            if signal_date:
                return str(signal_date)
    return execution_date


def _append_dedup_csv(
    *,
    output_path: Path,
    new_rows: pd.DataFrame,
    subset: list[str],
    sort_by: list[str],
) -> RollupTableResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        existing = pd.read_csv(output_path)
    else:
        existing = pd.DataFrame(columns=new_rows.columns)

    before_total = len(existing)
    if existing.empty:
        combined = new_rows.copy()
    elif new_rows.empty:
        combined = existing.copy()
    else:
        combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=subset, keep="last")
    available_sort = [column for column in sort_by if column in combined.columns]
    if available_sort:
        combined = combined.sort_values(available_sort).reset_index(drop=True)
    combined.to_csv(output_path, index=False)
    return RollupTableResult(
        table_name=output_path.stem,
        output_path=str(output_path),
        added_rows=max(len(combined) - before_total, 0),
        total_rows=len(combined),
        source_files=0,
    )


def _collect_signal_baskets(day_root: Path, daily_root: Path, execution_date: str) -> tuple[pd.DataFrame, int]:
    signal_dir = day_root / "pre_open" / "signals"
    rows: list[pd.DataFrame] = []
    sources = 0
    for path in sorted(signal_dir.glob("signal_basket_*.csv")):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        sources += 1
        frame = frame.copy()
        if "signal_date" not in frame.columns:
            frame["signal_date"] = execution_date
        if "execution_date" not in frame.columns:
            frame["execution_date"] = execution_date
        frame["signal_date"] = frame["signal_date"].astype(str)
        frame["execution_date"] = frame["execution_date"].astype(str)
        frame["account_name"] = "shared"
        frame["artifact_source"] = _artifact_source(path, daily_root)
        for column in _SIGNAL_BASKET_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        rows.append(frame[_SIGNAL_BASKET_COLUMNS])
    if not rows:
        return pd.DataFrame(columns=_SIGNAL_BASKET_COLUMNS), sources
    return pd.concat(rows, ignore_index=True), sources


def _collect_order_intents(day_root: Path, daily_root: Path) -> tuple[pd.DataFrame, int]:
    intents_dir = day_root / "pre_open" / "order_intents"
    rows: list[dict[str, Any]] = []
    sources = 0
    for path in sorted(intents_dir.glob("order_intents_*.json")):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
        intents = payload.get("intents") or []
        if not intents:
            continue
        sources += 1
        artifact_source = _artifact_source(path, daily_root)
        for intent in intents:
            rows.append(
                {
                    "signal_date": str(payload.get("signal_date") or intent.get("signal_date") or payload.get("execution_date") or ""),
                    "execution_date": str(payload.get("execution_date") or intent.get("execution_date") or ""),
                    "account_name": str(payload.get("account_name") or intent.get("account_name") or "unknown"),
                    "intent_id": intent.get("intent_id"),
                    "symbol": intent.get("symbol"),
                    "side": intent.get("side"),
                    "amount": intent.get("amount"),
                    "price": intent.get("price"),
                    "est_value": intent.get("est_value"),
                    "score": intent.get("score"),
                    "score_rank": intent.get("score_rank"),
                    "weight": intent.get("weight"),
                    "target_value": intent.get("target_value"),
                    "current_value": intent.get("current_value"),
                    "diff_value": intent.get("diff_value"),
                    "execution_bucket": intent.get("execution_bucket"),
                    "cash_dependency": intent.get("cash_dependency"),
                    "t1_rule": intent.get("t1_rule"),
                    "plan_role": intent.get("plan_role"),
                    "status": intent.get("status"),
                    "note": intent.get("note"),
                    "artifact_source": artifact_source,
                }
            )
    if not rows:
        return pd.DataFrame(columns=_ORDER_INTENT_COLUMNS), sources
    return pd.DataFrame(rows, columns=_ORDER_INTENT_COLUMNS), sources


def _collect_reconciliation_summary(day_root: Path, daily_root: Path, signal_date: str) -> tuple[pd.DataFrame, int]:
    reconciliation_dir = day_root / "post_close" / "reconciliation"
    rows: list[pd.DataFrame] = []
    sources = 0
    execution_date = day_root.name
    for path in sorted(reconciliation_dir.glob("reconcile_summary_*.csv")):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        sources += 1
        frame = frame.copy()
        frame["signal_date"] = signal_date
        frame["execution_date"] = execution_date
        frame["account_name"] = "real_vs_shadow"
        frame["artifact_source"] = _artifact_source(path, daily_root)
        for column in _RECONCILIATION_SUMMARY_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        rows.append(frame[_RECONCILIATION_SUMMARY_COLUMNS])
    if not rows:
        return pd.DataFrame(columns=_RECONCILIATION_SUMMARY_COLUMNS), sources
    return pd.concat(rows, ignore_index=True), sources


def _collect_position_gaps(day_root: Path, daily_root: Path, signal_date: str) -> tuple[pd.DataFrame, int]:
    reconciliation_dir = day_root / "post_close" / "reconciliation"
    rows: list[pd.DataFrame] = []
    sources = 0
    execution_date = day_root.name
    for path in sorted(reconciliation_dir.glob("reconcile_positions_*.csv")):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        frame = frame.copy()
        if "amount_diff" in frame.columns:
            frame = frame[frame["amount_diff"].fillna(0) != 0]
        if frame.empty:
            continue
        sources += 1
        frame["signal_date"] = signal_date
        frame["execution_date"] = execution_date
        frame["account_name"] = "real_vs_shadow"
        frame["artifact_source"] = _artifact_source(path, daily_root)
        for column in _POSITION_GAP_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        rows.append(frame[_POSITION_GAP_COLUMNS])
    if not rows:
        return pd.DataFrame(columns=_POSITION_GAP_COLUMNS), sources
    return pd.concat(rows, ignore_index=True), sources


def rollup_daily_artifacts(
    *,
    execution_date: str,
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
    derived_root: str | Path = DEFAULT_DERIVED_ROOT,
) -> DailyRollupResult:
    daily_root = Path(daily_root)
    derived_root = Path(derived_root)
    day_root = daily_root / execution_date
    if not day_root.exists():
        raise FileNotFoundError(f"Missing daily evidence package: {day_root}")

    signal_date = _load_signal_date(day_root, execution_date)
    tables: dict[str, RollupTableResult] = {}

    signal_rows, signal_sources = _collect_signal_baskets(day_root, daily_root, execution_date)
    signal_result = _append_dedup_csv(
        output_path=derived_root / "signal_baskets.csv",
        new_rows=signal_rows,
        subset=["execution_date", "signal_date", "account_name", "symbol"],
        sort_by=["execution_date", "account_name", "score_rank", "symbol"],
    )
    tables["signal_baskets"] = RollupTableResult(
        table_name=signal_result.table_name,
        output_path=signal_result.output_path,
        added_rows=signal_result.added_rows,
        total_rows=signal_result.total_rows,
        source_files=signal_sources,
    )

    intent_rows, intent_sources = _collect_order_intents(day_root, daily_root)
    intent_result = _append_dedup_csv(
        output_path=derived_root / "order_intents.csv",
        new_rows=intent_rows,
        subset=["execution_date", "account_name", "intent_id"],
        sort_by=["execution_date", "account_name", "side", "symbol"],
    )
    tables["order_intents"] = RollupTableResult(
        table_name=intent_result.table_name,
        output_path=intent_result.output_path,
        added_rows=intent_result.added_rows,
        total_rows=intent_result.total_rows,
        source_files=intent_sources,
    )

    reconciliation_rows, reconciliation_sources = _collect_reconciliation_summary(day_root, daily_root, signal_date)
    reconciliation_result = _append_dedup_csv(
        output_path=derived_root / "reconciliation_summary.csv",
        new_rows=reconciliation_rows,
        subset=["execution_date", "account_name", "metric"],
        sort_by=["execution_date", "account_name", "metric"],
    )
    tables["reconciliation_summary"] = RollupTableResult(
        table_name=reconciliation_result.table_name,
        output_path=reconciliation_result.output_path,
        added_rows=reconciliation_result.added_rows,
        total_rows=reconciliation_result.total_rows,
        source_files=reconciliation_sources,
    )

    position_rows, position_sources = _collect_position_gaps(day_root, daily_root, signal_date)
    position_result = _append_dedup_csv(
        output_path=derived_root / "position_gaps.csv",
        new_rows=position_rows,
        subset=["execution_date", "account_name", "symbol"],
        sort_by=["execution_date", "account_name", "symbol"],
    )
    tables["position_gaps"] = RollupTableResult(
        table_name=position_result.table_name,
        output_path=position_result.output_path,
        added_rows=position_result.added_rows,
        total_rows=position_result.total_rows,
        source_files=position_sources,
    )

    return DailyRollupResult(
        execution_date=execution_date,
        derived_root=str(derived_root),
        tables=tables,
    )
