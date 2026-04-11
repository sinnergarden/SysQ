from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import inspect_qlib_data_health
from qsys.data.storage import StockDataStore
from qsys.dataview.research import ResearchDataView
from qsys.feature.library import FeatureLibrary
from qsys.feature.registry import list_feature_groups
from qsys.live.account import RealAccount
from qsys.live.ops_manifest import load_manifest
from qsys.research_ui.schema import (
    BacktestDailyPoint,
    BacktestRunSummary,
    CaseBundle,
    CaseBundleLink,
    DecisionCandidate,
    DecisionOrder,
    DecisionReplay,
    FeatureHealthEntry,
    FeatureHealthSummary,
    FeatureRegistryEntry,
    RunArtifactRef,
    RunManifest,
)
from qsys.trader.database import TradeLedger


class ResearchCockpitRepository:
    """Build stable research-ui contracts from current SysQ artifacts."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root)
        self.daily_root = self.project_root / "daily"
        self.experiments_root = self.project_root / "experiments"
        self.reports_root = self.experiments_root / "reports"
        self.trade_ledger = TradeLedger(self.project_root / "data" / "trade.db")
        self.real_account = RealAccount(db_path=self.project_root / "data" / "meta" / "real_account.db")
        self.store = StockDataStore()
        self.research_view = ResearchDataView(n_jobs=1)
        self.qlib_adapter = QlibAdapter()
        self._stock_list_cache: pd.DataFrame | None = None
        self._instrument_index: dict[str, dict[str, Any]] | None = None
        self._feature_registry_cache: list[FeatureRegistryEntry] | None = None
        self._backtest_runs_cache: dict[int, list[BacktestRunSummary]] = {}
        self._backtest_summary_cache: dict[str, BacktestRunSummary] = {}
        self._backtest_daily_cache: dict[str, list[BacktestDailyPoint]] = {}
        self._backtest_report_paths_cache: list[Path] | None = None
        self._backtest_report_index: dict[str, Path] = {}
        self._json_cache: dict[Path, dict[str, Any]] = {}
        self._model_meta_cache: dict[Path, dict[str, Any]] = {}
        self._qlib_ready = False

    def list_instruments(self, *, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        frame = self._get_stock_list_frame()
        if frame is None or frame.empty:
            return []
        if query:
            q = str(query).strip().lower()
            mask = pd.Series(False, index=frame.index)
            for column in ["ts_code", "symbol", "name", "industry", "market"]:
                if column in frame.columns:
                    mask = mask | frame[column].astype(str).str.lower().str.contains(q, na=False)
            frame = frame[mask]
        frame = frame.sort_values([col for col in ["ts_code", "symbol"] if col in frame.columns]).head(limit)
        return [{key: self._normalize_scalar(value) for key, value in row.items()} for row in frame.to_dict(orient="records")]

    def get_instrument(self, instrument_id: str) -> dict[str, Any] | None:
        return self._get_instrument_index().get(instrument_id)

    def list_feature_registry(self) -> list[FeatureRegistryEntry]:
        if self._feature_registry_cache is not None:
            return self._feature_registry_cache
        entries: dict[str, FeatureRegistryEntry] = {}
        registry_tags: dict[str, set[str]] = {}
        model_feature_sources = self._load_model_feature_configs()
        active_feature_names = {
            self._normalize_registry_feature_name(feature_name)
            for source_name, features in model_feature_sources.items()
            if source_name.startswith("model:")
            for feature_name in features
        }

        def merge_entry(entry: FeatureRegistryEntry) -> None:
            existing = entries.get(entry.feature_name)
            if existing is None:
                entries[entry.feature_name] = entry
                registry_tags[entry.feature_name] = set(entry.tags)
                return
            merged_tags = registry_tags.setdefault(entry.feature_name, set(existing.tags))
            merged_tags.update(existing.tags)
            merged_tags.update(entry.tags)
            source_layer = existing.source_layer
            if source_layer == "raw" and entry.source_layer != "raw":
                source_layer = entry.source_layer
            description = existing.description
            if description.startswith("Adapter field available") and entry.description:
                description = entry.description
            dependencies = existing.dependencies or entry.dependencies
            if len(entry.dependencies) > len(dependencies):
                dependencies = entry.dependencies
            entries[entry.feature_name] = FeatureRegistryEntry(
                feature_id=existing.feature_id,
                feature_name=existing.feature_name,
                display_name=existing.display_name or entry.display_name,
                group_name=entry.group_name if existing.group_name == self._classify_raw_field_group(existing.feature_name) and entry.group_name else existing.group_name,
                source_layer=source_layer,
                dtype=existing.dtype or entry.dtype,
                value_kind=existing.value_kind,
                description=description,
                formula=existing.formula or entry.formula,
                dependencies=dependencies,
                supports_snapshot=existing.supports_snapshot or entry.supports_snapshot,
                tags=sorted(merged_tags),
                status=existing.status,
            )

        for field_name in self._load_adapter_qlib_fields():
            if field_name not in active_feature_names:
                continue
            merge_entry(
                FeatureRegistryEntry(
                    feature_id=field_name,
                    feature_name=field_name,
                    display_name=field_name,
                    group_name=self._classify_raw_field_group(field_name),
                    source_layer="raw",
                    dtype="float",
                    value_kind="scalar",
                    description=f"Adapter field available through qlib layer: {field_name}",
                    supports_snapshot=True,
                    tags=["adapter_field", "research_ui"],
                )
            )

        for group_name, payload in sorted(list_feature_groups().items()):
            for feature_name in payload.get("features", []):
                if feature_name not in active_feature_names:
                    continue
                merge_entry(
                    FeatureRegistryEntry(
                        feature_id=feature_name,
                        feature_name=feature_name,
                        display_name=feature_name,
                        group_name=group_name,
                        source_layer="semantic_derived",
                        dtype="float",
                        value_kind="scalar",
                        description=f"{group_name} feature: {feature_name}",
                        dependencies=[],
                        supports_snapshot=True,
                        tags=[group_name, "semantic", "research_ui"],
                    )
                )

        for source_name, features in model_feature_sources.items():
            if not source_name.startswith("model:"):
                continue
            for feature_name in features:
                field_name = self._normalize_registry_feature_name(feature_name)
                merge_entry(
                    FeatureRegistryEntry(
                        feature_id=field_name,
                        feature_name=field_name,
                        display_name=field_name,
                        group_name=self._classify_registry_group(field_name),
                        source_layer=self._classify_feature_source(field_name),
                        dtype="float",
                        value_kind="scalar",
                        description=self._describe_feature(field_name),
                        formula=feature_name if feature_name != field_name else "",
                        dependencies=self._extract_feature_dependencies(feature_name),
                        supports_snapshot=True,
                        tags=[source_name, "research_ui"],
                    )
                )

        self._feature_registry_cache = sorted(entries.values(), key=lambda item: (item.group_name, item.feature_name))
        return self._feature_registry_cache

    def get_bar_series(self, *, instrument_id: str, start: str, end: str, price_mode: str = "fq") -> list[dict[str, Any]]:
        return self._load_bars(instrument_id=instrument_id, trade_date=end, price_mode=price_mode, start_date=start, end_date=end)

    def get_feature_snapshot(self, *, trade_date: str, instrument_id: str, feature_names: list[str] | None = None) -> dict[str, Any]:
        return self._load_feature_snapshot(trade_date=trade_date, instrument_id=instrument_id, feature_names=feature_names)

    def get_feature_series(self, *, instrument_id: str, start: str, end: str, feature_names: list[str]) -> list[dict[str, Any]]:
        qlib_fields = self._normalize_feature_fields(feature_names)
        frame = self._load_qlib_features_batched([instrument_id], qlib_fields, start, end)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        reset = frame.reset_index()
        date_key = "datetime" if "datetime" in reset.columns else "trade_date"
        instrument_key = "instrument" if "instrument" in reset.columns else "ts_code"
        for _, row in reset.iterrows():
            item = {
                "trade_date": str(row.get(date_key)),
                "instrument_id": str(row.get(instrument_key)),
            }
            for field in qlib_fields:
                item[self._normalize_registry_feature_name(field)] = self._normalize_scalar(row.get(field))
            rows.append(item)
        return rows

    def list_feature_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for manifest_path in sorted(self.daily_root.glob("*/pre_open/manifests/daily_ops_manifest_*.json"), reverse=True)[:limit]:
            manifest = load_manifest(manifest_path)
            signal_date = manifest.get("signal_date") or manifest.get("execution_date")
            runs.append(
                {
                    "run_id": f"feature-health:{signal_date}:csi300",
                    "execution_date": manifest.get("execution_date"),
                    "trade_date": signal_date,
                    "signal_date": signal_date,
                    "universe": "csi300",
                    "model_info": manifest.get("model_info") or {},
                    "data_status": manifest.get("data_status") or {},
                    "manifest_ref": str(manifest_path.relative_to(self.project_root)),
                }
            )
        return runs

    def build_feature_health_summary(self, *, trade_date: str, feature_names: list[str], universe: str = "csi300") -> FeatureHealthSummary:
        qlib_fields = self._normalize_feature_fields(feature_names)
        report = inspect_qlib_data_health(trade_date, qlib_fields, universe=universe)

        # Health should reflect the same final feature values that snapshot/model-facing
        # reads use, instead of only raw probe field availability.
        feature_frame = self._load_qlib_features_batched(universe, qlib_fields, trade_date, trade_date)
        feature_rows = len(feature_frame)
        overall_missing_ratio = float(feature_frame.isna().mean().mean()) if not feature_frame.empty else 1.0

        entries: list[FeatureHealthEntry] = []
        for field in qlib_fields:
            miss = 1.0
            if not feature_frame.empty and field in feature_frame.columns:
                miss = float(feature_frame[field].isna().mean())
            entries.append(
                FeatureHealthEntry(
                    feature_name=self._normalize_registry_feature_name(field),
                    coverage_ratio=max(0.0, 1.0 - miss),
                    nan_ratio=miss,
                    inf_ratio=0.0,
                    status="ok" if miss <= 0.2 else "warning",
                )
            )
        return FeatureHealthSummary(
            run_id=f"feature-health:{trade_date}:{universe}",
            trade_date=trade_date,
            universe=universe,
            price_mode_context="fq",
            feature_count=len(entries),
            instrument_count=feature_rows,
            overall_missing_ratio=overall_missing_ratio,
            features=entries,
            warnings=list(report.warnings),
            blockers=list(report.blocking_issues),
            manifest_ref=f"daily:{trade_date}",
        )

    def build_daily_run_manifest(self, execution_date: str) -> RunManifest:
        manifest_path = self.daily_root / execution_date / "pre_open" / "manifests" / f"daily_ops_manifest_{execution_date}.json"
        manifest = load_manifest(manifest_path)
        artifacts = [
            RunArtifactRef(
                artifact_id=name,
                kind=self._artifact_kind(name),
                logical_path=str(self._logicalize_path(path)),
                title=name,
                stage="pre_open",
            )
            for name, path in sorted((manifest.get("artifacts") or {}).items())
        ]
        return RunManifest(
            run_id=f"daily:{execution_date}",
            run_type="daily_ops",
            status=((manifest.get("stages") or {}).get("pre_open") or {}).get("status", "unknown"),
            signal_date=manifest.get("signal_date"),
            execution_date=manifest.get("execution_date") or execution_date,
            trade_date=execution_date,
            updated_at=manifest.get("updated_at"),
            model_info=dict(manifest.get("model_info") or {}),
            data_status=dict(manifest.get("data_status") or {}),
            scope={"stage": "pre_open"},
            warnings=list((manifest.get("data_status") or {}).get("warnings") or []),
            blockers=list(manifest.get("blockers") or []),
            notes=list(manifest.get("notes") or []),
            artifacts=artifacts,
            links={"daily_digest": f"/api/runs/daily/{execution_date}"},
        )

    def _iter_preferred_formal_report_paths(self) -> list[Path]:
        preferred_paths: list[Path] = []
        preferred_roots = [
            self.project_root / "scratch" / "formal_173_fixed" / "experiments" / "reports",
            self.project_root / "scratch" / "formal_254_fixed" / "experiments" / "reports",
            self.project_root / "scratch" / "formal_173_compare" / "experiments" / "reports",
            self.project_root / "scratch" / "formal_254_compare" / "experiments" / "reports",
        ]
        for report_root in preferred_roots:
            if not report_root.exists():
                continue
            latest = next(iter(sorted(report_root.glob("backtest_*.json"), reverse=True)), None)
            if latest is not None:
                preferred_paths.append(latest)
        return preferred_paths

    def _iter_backtest_report_paths(self) -> list[Path]:
        if self._backtest_report_paths_cache is not None:
            return self._backtest_report_paths_cache
        candidates: list[Path] = []
        seen: set[Path] = set()
        for path in self._iter_preferred_formal_report_paths():
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(path)
        report_roots = [self.reports_root]
        scratch_root = self.project_root / "scratch"
        if scratch_root.exists():
            report_roots.extend(scratch_root.glob("**/experiments/reports"))
        for root in report_roots:
            if not root.exists():
                continue
            for path in sorted(root.glob("backtest_*.json"), reverse=True):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                candidates.append(path)
        self._backtest_report_paths_cache = candidates
        return candidates

    def list_backtest_runs(self, limit: int = 50) -> list[BacktestRunSummary]:
        cached = self._backtest_runs_cache.get(limit)
        if cached is not None:
            return cached
        grouped_runs: dict[str, BacktestRunSummary] = {}
        for path in self._iter_backtest_report_paths():
            summary = self._build_backtest_summary(path)
            self._backtest_summary_cache[summary.run_id] = summary
            self._backtest_report_index[summary.run_id] = path
            source_key = str((summary.parameter_summary or {}).get("source_key") or summary.feature_set or summary.run_id)
            existing = grouped_runs.get(source_key)
            if existing is None or self._backtest_version_rank(summary) > self._backtest_version_rank(existing):
                grouped_runs[source_key] = summary
        runs = sorted(grouped_runs.values(), key=self._backtest_version_rank, reverse=True)[:limit]
        self._backtest_runs_cache[limit] = runs
        for item in runs:
            self._backtest_summary_cache[item.run_id] = item
        return runs

    def get_backtest_summary(self, run_id: str) -> BacktestRunSummary:
        cached = self._backtest_summary_cache.get(run_id)
        if cached is not None:
            return cached
        report_path = self._resolve_backtest_report(run_id)
        summary = self._build_backtest_summary(report_path)
        self._backtest_summary_cache[run_id] = summary
        self._backtest_report_index[summary.run_id] = report_path
        return summary

    def get_backtest_daily_points(self, run_id: str) -> list[BacktestDailyPoint]:
        cached = self._backtest_daily_cache.get(run_id)
        if cached is not None:
            return cached
        report_path = self._resolve_backtest_report(run_id)
        payload = self._load_json(report_path)
        daily_path = (payload.get("artifacts") or {}).get("daily_result")
        if not daily_path:
            return []
        csv_path = self._resolve_project_artifact_path(daily_path)
        if not csv_path.exists():
            return []
        frame = pd.read_csv(csv_path)
        if frame.empty:
            return []
        points: list[BacktestDailyPoint] = []
        if "total_assets" in frame.columns:
            equity = pd.to_numeric(frame["total_assets"], errors="coerce")
            cummax = equity.cummax()
            drawdown = (equity / cummax) - 1.0
            frame = frame.copy()
            frame["drawdown"] = drawdown
        benchmark_points = self._load_benchmark_points(
            start_date=str(frame.iloc[0].get("date") or frame.iloc[0].get("trade_date") or ""),
            end_date=str(frame.iloc[-1].get("date") or frame.iloc[-1].get("trade_date") or ""),
            benchmark_code="000300.SH",
            benchmark_name="CSI300",
        )
        benchmark2_points = self._load_benchmark_points(
            start_date=str(frame.iloc[0].get("date") or frame.iloc[0].get("trade_date") or ""),
            end_date=str(frame.iloc[-1].get("date") or frame.iloc[-1].get("trade_date") or ""),
            benchmark_code="000001.SH",
            benchmark_name="SSE",
        )
        benchmark_map = {item["trade_date"]: item for item in benchmark_points}
        benchmark2_map = {item["trade_date"]: item for item in benchmark2_points}
        benchmark_base = self._to_float(benchmark_points[0].get("close")) if benchmark_points else None
        benchmark2_base = self._to_float(benchmark2_points[0].get("close")) if benchmark2_points else None
        equity_base = self._to_float(frame.iloc[0].get("total_assets")) if not frame.empty else None
        previous_benchmark_close = benchmark_base
        previous_benchmark2_close = benchmark2_base
        for _, row in frame.iterrows():
            trade_date = str(row.get("date") or row.get("trade_date") or "")
            benchmark_row = benchmark_map.get(trade_date, {})
            benchmark_close = self._to_float(benchmark_row.get("close"))
            benchmark2_row = benchmark2_map.get(trade_date, {})
            benchmark2_close = self._to_float(benchmark2_row.get("close"))
            benchmark_equity = None
            benchmark_daily_return = None
            benchmark2_equity = None
            benchmark2_daily_return = None
            if benchmark_base and equity_base and benchmark_close is not None:
                benchmark_equity = equity_base * (benchmark_close / benchmark_base)
            if benchmark2_base and equity_base and benchmark2_close is not None:
                benchmark2_equity = equity_base * (benchmark2_close / benchmark2_base)
            if previous_benchmark_close and benchmark_close is not None and previous_benchmark_close != 0:
                benchmark_daily_return = (benchmark_close / previous_benchmark_close) - 1.0
            if previous_benchmark2_close and benchmark2_close is not None and previous_benchmark2_close != 0:
                benchmark2_daily_return = (benchmark2_close / previous_benchmark2_close) - 1.0
            if benchmark_close is not None:
                previous_benchmark_close = benchmark_close
            if benchmark2_close is not None:
                previous_benchmark2_close = benchmark2_close
            points.append(
                BacktestDailyPoint(
                    trade_date=trade_date,
                    equity=self._to_float(row.get("total_assets")),
                    zero_cost_equity=self._to_float(row.get("zero_cost_total_assets")),
                    daily_return=self._to_float(row.get("daily_return")),
                    drawdown=self._to_float(row.get("drawdown")),
                    benchmark_equity=benchmark_equity,
                    benchmark_daily_return=benchmark_daily_return,
                    benchmark2_equity=benchmark2_equity,
                    benchmark2_daily_return=benchmark2_daily_return,
                    turnover=self._to_float(row.get("turnover") or row.get("daily_turnover")),
                    ic=self._to_float(row.get("ic")),
                    rank_ic=self._to_float(row.get("rank_ic")),
                    trade_count=self._to_int(row.get("trade_count")),
                )
            )
        self._backtest_daily_cache[run_id] = points
        return points

    def get_backtest_orders(
        self,
        run_id: str,
        *,
        trade_date: str | None = None,
        instrument_id: str | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        csv_path = self._resolve_backtest_trades_path(run_id)
        if not csv_path.exists():
            return []
        frame = pd.read_csv(csv_path)
        if frame.empty:
            return []
        if "date" in frame.columns and trade_date:
            frame = frame[frame["date"].astype(str) == str(trade_date)]
        if "symbol" in frame.columns and instrument_id:
            frame = frame[frame["symbol"].astype(str) == str(instrument_id)]
        frame = frame.head(limit)
        rows: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            rows.append({key: self._normalize_scalar(value) for key, value in row.items()})
        return rows

    def build_decision_replay(self, *, execution_date: str, account_name: str) -> DecisionReplay:
        manifest = self.build_daily_run_manifest(execution_date)
        intent_path = self.daily_root / execution_date / "pre_open" / "order_intents" / f"order_intents_{execution_date}_{account_name}.json"
        payload = self._load_json(intent_path) if intent_path.exists() else {}
        intents = payload.get("intents") or []
        signal_date = payload.get("signal_date") or manifest.signal_date or execution_date
        signal_basket = self._load_signal_basket(execution_date)
        previous_positions = self._load_previous_positions(execution_date, account_name)

        scored_candidates: list[DecisionCandidate] = []
        final_orders: list[DecisionOrder] = []
        candidate_pool: list[str] = []
        selected_targets: list[str] = []
        exclusions: list[dict[str, Any]] = []

        intent_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for item in intents:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            intent_by_symbol.setdefault(symbol, []).append(item)
            final_orders.append(
                DecisionOrder(
                    instrument_id=symbol,
                    side=str(item.get("side") or "review"),
                    quantity=int(item.get("amount") or 0),
                    price=self._to_float(item.get("price")),
                    est_value=self._to_float(item.get("est_value")),
                    status=str(item.get("status") or "planned"),
                    note=str(item.get("note") or ""),
                )
            )
            if str(item.get("side") or "").lower() == "buy" and symbol not in selected_targets:
                selected_targets.append(symbol)

        for row in signal_basket:
            symbol = str(row.get("symbol") or row.get("instrument_id") or "")
            if not symbol:
                continue
            if symbol not in candidate_pool:
                candidate_pool.append(symbol)
            linked_intents = intent_by_symbol.get(symbol, [])
            buy_intents = [item for item in linked_intents if str(item.get("side") or "").lower() == "buy"]
            exclusion_reasons = [] if buy_intents else ["not_selected_into_buy_orders"]
            constraint_status = {}
            if buy_intents:
                first_intent = buy_intents[0]
                constraint_status = {
                    "execution_bucket": first_intent.get("execution_bucket"),
                    "cash_dependency": first_intent.get("cash_dependency"),
                    "t1_rule": first_intent.get("t1_rule"),
                }
            scored_candidates.append(
                DecisionCandidate(
                    instrument_id=symbol,
                    raw_score=self._to_float(row.get("score") or row.get("raw_score")),
                    adjusted_score=self._to_float(row.get("adjusted_score") or row.get("score") or row.get("raw_score")),
                    rank=self._to_int(row.get("score_rank") or row.get("rank")),
                    selected=bool(buy_intents),
                    exclusion_reasons=exclusion_reasons,
                    constraint_status=constraint_status,
                )
            )
            if exclusion_reasons:
                exclusions.append({
                    "instrument_id": symbol,
                    "reasons": exclusion_reasons,
                    "signal_rank": self._to_int(row.get("score_rank") or row.get("rank")),
                })

        for symbol, linked_intents in intent_by_symbol.items():
            if symbol in candidate_pool:
                continue
            candidate_pool.append(symbol)
            primary = linked_intents[0]
            scored_candidates.append(
                DecisionCandidate(
                    instrument_id=symbol,
                    raw_score=self._to_float(primary.get("score")),
                    adjusted_score=self._to_float(primary.get("score")),
                    rank=None,
                    selected=str(primary.get("side") or "").lower() == "buy",
                    exclusion_reasons=["existing_position_rotation"] if str(primary.get("side") or "").lower() == "sell" else [],
                    constraint_status={
                        "execution_bucket": primary.get("execution_bucket"),
                        "cash_dependency": primary.get("cash_dependency"),
                        "t1_rule": primary.get("t1_rule"),
                    },
                )
            )

        return DecisionReplay(
            run_id=manifest.run_id,
            trade_date=execution_date,
            signal_date=signal_date,
            execution_date=execution_date,
            account_name=account_name,
            previous_positions=previous_positions,
            candidate_pool=candidate_pool,
            scored_candidates=scored_candidates,
            constraints=dict(payload.get("assumptions") or {}),
            selected_targets=selected_targets,
            final_orders=final_orders,
            exclusions=exclusions,
            summary={
                "intent_count": int(payload.get("intent_count") or len(final_orders)),
                "signal_candidate_count": len(signal_basket),
                "model_info": payload.get("model_info") or {},
            },
            manifest_ref=manifest.run_id,
        )

    def build_case_bundle(self, *, execution_date: str, instrument_id: str, price_mode: str = "fq") -> CaseBundle:
        manifest = self.build_daily_run_manifest(execution_date)
        signal_date = manifest.signal_date or execution_date
        bars = self._load_bars(instrument_id=instrument_id, trade_date=execution_date, price_mode=price_mode)
        benchmark_bars = self._load_benchmark_points(
            start_date=bars[0].get("trade_date") if bars else signal_date,
            end_date=bars[-1].get("trade_date") if bars else execution_date,
            benchmark_code="000300.SH",
            benchmark_name="CSI300",
        )
        secondary_benchmark_bars = self._load_benchmark_points(
            start_date=bars[0].get("trade_date") if bars else signal_date,
            end_date=bars[-1].get("trade_date") if bars else execution_date,
            benchmark_code="000001.SH",
            benchmark_name="SSE",
        )
        signal_snapshot = self._load_signal_snapshot(execution_date=execution_date, instrument_id=instrument_id)
        feature_snapshot = self._load_feature_snapshot(trade_date=signal_date, instrument_id=instrument_id)
        replay = self.build_decision_replay(execution_date=execution_date, account_name="shadow")
        related_orders = [item.to_dict() for item in replay.final_orders if item.instrument_id == instrument_id]
        positions = [item for item in replay.previous_positions if item.get("instrument_id") == instrument_id or item.get("symbol") == instrument_id]
        annotations = [
            {
                "type": "signal_date",
                "trade_date": signal_date,
                "label": "Signal",
                "note": f"signal generated on {signal_date}",
            },
            {
                "type": "execution_date",
                "trade_date": execution_date,
                "label": "Execution",
                "note": f"orders executed on {execution_date}",
            },
        ]
        return CaseBundle(
            case_id=f"{execution_date}:{instrument_id}:{price_mode}",
            run_id=manifest.run_id,
            instrument_id=instrument_id,
            trade_date=execution_date,
            signal_date=signal_date,
            execution_date=execution_date,
            price_mode=price_mode,
            bars=bars,
            benchmark_bars=benchmark_bars,
            secondary_benchmark_bars=secondary_benchmark_bars,
            signal_snapshot=signal_snapshot,
            feature_snapshot=feature_snapshot,
            orders=related_orders,
            positions=positions,
            annotations=annotations,
            links=[
                CaseBundleLink(label="decision_replay", target=f"/api/decision-replay?execution_date={execution_date}&account_name=shadow"),
                CaseBundleLink(label="daily_run", target=f"/api/runs/daily/{execution_date}"),
            ],
        )

    def get_case_bundle_by_id(self, case_id: str) -> CaseBundle:
        try:
            execution_date, instrument_id, price_mode = case_id.split(":", 2)
        except ValueError as exc:
            raise FileNotFoundError(f"Unknown case_id: {case_id}") from exc
        return self.build_case_bundle(execution_date=execution_date, instrument_id=instrument_id, price_mode=price_mode)

    def _load_bars(self, *, instrument_id: str, trade_date: str, price_mode: str, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        fields = ["open", "high", "low", "close", "volume", "adj_factor"]
        if price_mode == "fq":
            fields = ["adj_open", "adj_high", "adj_low", "adj_close", "volume", "adj_factor"]
        raw_daily = self.store.load_daily(instrument_id)
        if start_date is None:
            if raw_daily is not None and not raw_daily.empty and "trade_date" in raw_daily.columns:
                start_date = str(raw_daily["trade_date"].astype(str).min())
            else:
                start_date = (pd.Timestamp(trade_date) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        if end_date is None:
            if raw_daily is not None and not raw_daily.empty and "trade_date" in raw_daily.columns:
                end_date = str(raw_daily["trade_date"].astype(str).max())
            else:
                end_date = trade_date
        frame = self.research_view.get_feature([instrument_id], fields, start_date, end_date)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for (dt, code), row in frame.reset_index().set_index(["trade_date", "ts_code"]).iterrows():
            normalized_date = self._normalize_trade_date_value(dt)
            if not normalized_date:
                continue
            item = {"trade_date": normalized_date, "instrument_id": str(code), "price_mode": price_mode}
            for col, value in row.items():
                item[str(col)] = self._normalize_scalar(value)
            rows.append(item)
        return rows

    def _load_signal_snapshot(self, *, execution_date: str, instrument_id: str) -> dict[str, Any]:
        frame = self._load_signal_basket_frame(execution_date)
        if frame.empty or "symbol" not in frame.columns:
            return {}
        matched = frame[frame["symbol"].astype(str) == instrument_id]
        if matched.empty:
            return {}
        return {key: self._normalize_scalar(value) for key, value in matched.iloc[0].to_dict().items()}

    def _load_signal_basket(self, execution_date: str) -> list[dict[str, Any]]:
        frame = self._load_signal_basket_frame(execution_date)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            rows.append({key: self._normalize_scalar(value) for key, value in row.items()})
        return rows

    def _load_signal_basket_frame(self, execution_date: str) -> pd.DataFrame:
        signal_dir = self.daily_root / execution_date / "pre_open" / "signals"
        files = sorted(signal_dir.glob("signal_basket_*.csv"))
        if not files:
            return pd.DataFrame()
        frame = pd.read_csv(files[-1])
        if frame.empty:
            return frame
        sort_candidates = [col for col in ["score_rank", "rank", "score"] if col in frame.columns]
        if sort_candidates:
            ascending = [col not in {"score"} for col in sort_candidates]
            frame = frame.sort_values(sort_candidates, ascending=ascending, na_position="last")
        return frame.reset_index(drop=True)

    def _load_feature_snapshot(self, *, trade_date: str, instrument_id: str, feature_names: list[str] | None = None) -> dict[str, Any]:
        features = feature_names or self._list_snapshot_feature_names()
        qlib_fields = self._normalize_feature_fields(features)
        try:
            frame = self._load_qlib_features_batched([instrument_id], qlib_fields, trade_date, trade_date)
        except Exception:
            return {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
        if frame.empty:
            return {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
        row = frame.reset_index().iloc[-1].to_dict()
        payload = {}
        for key, value in row.items():
            if key in ("trade_date", "ts_code", "datetime", "instrument"):
                continue
            payload[self._normalize_registry_feature_name(str(key))] = self._normalize_scalar(value)
        return {"trade_date": trade_date, "instrument_id": instrument_id, "features": payload}

    def _normalize_trade_date_value(self, value: Any) -> str:
        if value is None:
            return ""
        parsed = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(parsed):
            text = str(value).strip()
            if len(text) == 8 and text.isdigit():
                parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
        if pd.isna(parsed):
            return str(value)
        return parsed.strftime("%Y-%m-%d")

    def _load_previous_positions(self, execution_date: str, account_name: str) -> list[dict[str, Any]]:
        latest_date = self.real_account.get_latest_date(account_name=account_name, before_date=execution_date)
        if not latest_date:
            return []
        state = self.real_account.get_state(latest_date, account_name=account_name) or {}
        positions = []
        for symbol, item in sorted((state.get("positions") or {}).items()):
            positions.append(
                {
                    "instrument_id": symbol,
                    "symbol": symbol,
                    "quantity": int(item.get("amount", item.get("total_amount", 0)) or 0),
                    "price": self._to_float(item.get("price")),
                    "cost_basis": self._to_float(item.get("cost_basis")),
                    "as_of_date": latest_date,
                }
            )
        return positions

    def _resolve_backtest_report(self, run_id: str) -> Path:
        indexed = self._backtest_report_index.get(run_id)
        if indexed is not None:
            return indexed
        path = self.reports_root / f"backtest_{run_id}.json"
        if path.exists():
            self._backtest_report_index[run_id] = path
            return path
        for candidate in self._iter_backtest_report_paths():
            payload = self._load_json(candidate)
            candidate_run_id = str(payload.get("run_id") or "")
            if candidate_run_id:
                self._backtest_report_index[candidate_run_id] = candidate
            if candidate_run_id == run_id or candidate.stem == f"backtest_{run_id}":
                self._backtest_report_index[run_id] = candidate
                return candidate
        raise FileNotFoundError(f"Unknown backtest run_id: {run_id}")

    def _resolve_backtest_trades_path(self, run_id: str) -> Path:
        report_path = self._resolve_backtest_report(run_id)
        payload = self._load_json(report_path)
        trade_path = (payload.get("artifacts") or {}).get("trades")
        if trade_path:
            return self._resolve_project_artifact_path(trade_path)
        default_path = self.project_root / "experiments" / "backtest_trades.csv"
        return default_path

    def _infer_backtest_version(self, payload: dict[str, Any], report_path: Path) -> tuple[str, str, int | None]:
        model_info = payload.get("model_info") or {}
        model_path_value = model_info.get("model_path")
        feature_count: int | None = None
        match = re.search(r"formal_(\d+)_compare", str(report_path))
        if match:
            feature_count = self._to_int(match.group(1))
        if feature_count is None and model_path_value:
            meta_path = self.project_root / str(model_path_value) / "meta.yaml"
            feature_count = self._load_model_feature_count(meta_path)
        if feature_count is None and "semantic_all_features" in str(model_path_value or ""):
            feature_count = 254
        if feature_count is None and "extended" in str(model_path_value or ""):
            feature_count = 173
        if feature_count:
            return f"feature_{feature_count}", f"feature {feature_count}", feature_count
        model_name = str(model_info.get("model_name") or model_path_value or "unknown")
        return model_name, model_name, feature_count

    def _backtest_version_rank(self, summary: BacktestRunSummary) -> tuple[int, int, str, int]:
        manifest_ref = str(summary.manifest_ref or "")
        params = summary.parameter_summary or {}
        notes = [str(item) for item in (params.get("notes") or [])]
        execution_end = str(summary.test_range.get("end") or "")
        version_pinned = 1 if any(item.startswith("version=") for item in notes) else 0
        preferred_root = 0
        if any(token in manifest_ref for token in ["scratch/formal_173_fixed/", "scratch/formal_254_fixed/"]):
            preferred_root = 2
        elif any(token in manifest_ref for token in ["scratch/formal_173_compare/", "scratch/formal_254_compare/"]):
            preferred_root = 1
        if manifest_ref.startswith("scratch/formal_feature"):
            preferred_root = -1
        feature_count = int(params.get("feature_count") or 0)
        return (version_pinned, preferred_root, execution_end, feature_count)

    def _build_backtest_summary(self, report_path: Path) -> BacktestRunSummary:
        payload = self._load_json(report_path)
        model_info = payload.get("model_info") or {}
        metrics = self._extract_backtest_metrics(payload)
        notes = [str(item) for item in (payload.get("notes") or [])]
        version_key, version_label, feature_count = self._infer_backtest_version(payload, report_path)
        report_logical = str(report_path.relative_to(self.project_root))
        source_key = "other"
        source_label = "other"
        if "scratch/formal_173_compare/" in report_logical:
            source_key = "formal_173_compare"
            source_label = "173 compare"
        elif "scratch/formal_254_compare/" in report_logical:
            source_key = "formal_254_compare"
            source_label = "254 compare"
        elif "scratch/formal_173_fixed/" in report_logical:
            source_key = "formal_173_fixed"
            source_label = "173 fixed"
        elif "scratch/formal_254_fixed/" in report_logical:
            source_key = "formal_254_fixed"
            source_label = "254 fixed"
        daily_path = (payload.get("artifacts") or {}).get("daily_result")
        artifacts = [
            RunArtifactRef(
                artifact_id="backtest_report",
                kind="report",
                logical_path=str(report_path.relative_to(self.project_root)),
                title=report_path.name,
            )
        ]
        if daily_path:
            artifacts.append(
                RunArtifactRef(
                    artifact_id="daily_result",
                    kind="backtest_daily",
                    logical_path=str(self._logicalize_path(daily_path)),
                    title="daily_result",
                    media_type="text/csv",
                )
            )
        return BacktestRunSummary(
            run_id=str(payload.get("run_id") or report_path.stem),
            run_type=str(payload.get("workflow") or "backtest"),
            model_name=str(model_info.get("model_name") or model_info.get("model_path") or "unknown"),
            feature_set=version_key,
            universe=str(model_info.get("universe") or "csi300"),
            train_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
            test_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
            top_k=self._to_int(model_info.get("top_k")),
            price_mode="fq",
            display_label=f"{version_label} · {source_label}",
            parameter_summary={
                "version_key": version_key,
                "version_label": version_label,
                "source_key": source_key,
                "source_label": source_label,
                "feature_count": feature_count,
                "model_path": model_info.get("model_path"),
                "top_k": self._to_int(model_info.get("top_k")),
                "universe": model_info.get("universe") or "csi300",
                "price_mode": "fq",
                "signal_date": payload.get("signal_date"),
                "execution_date": payload.get("execution_date"),
                "internal_run_id": str(payload.get("run_id") or report_path.stem),
                "notes": notes,
            },
            metrics=metrics,
            artifacts=artifacts,
            manifest_ref=report_logical,
        )

    def _extract_backtest_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections = payload.get("sections") or []
        for section in sections:
            if section.get("name") == "Performance":
                return dict(section.get("metrics") or {})
        return {}

    def _artifact_kind(self, name: str) -> str:
        mapping = {
            "signal_basket": "signal_basket",
            "shadow_order_intents": "order_intents",
            "real_order_intents": "order_intents",
            "shadow_plan": "plan",
            "real_plan": "plan",
            "report": "report",
            "manifest": "manifest",
        }
        return mapping.get(name, "other")

    def _load_json(self, path: str | Path) -> dict[str, Any]:
        path_obj = Path(path).resolve()
        cached = self._json_cache.get(path_obj)
        if cached is not None:
            return cached
        with open(path_obj, "r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
        self._json_cache[path_obj] = payload
        return payload

    def _load_model_feature_count(self, meta_path: Path) -> int | None:
        meta_path = meta_path.resolve()
        cached = self._model_meta_cache.get(meta_path)
        if cached is None:
            if not meta_path.exists():
                return None
            cached = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            self._model_meta_cache[meta_path] = cached
        training_summary = cached.get("training_summary") or {}
        return self._to_int(training_summary.get("feature_count"))

    def _logicalize_path(self, path: str | Path) -> Path:
        path_obj = Path(path)
        if not path_obj.is_absolute():
            return path_obj
        try:
            return path_obj.relative_to(self.project_root)
        except ValueError:
            return path_obj

    def _resolve_project_artifact_path(self, path: str | Path) -> Path:
        path_obj = Path(path)
        candidates: list[Path] = []
        if path_obj.is_absolute():
            candidates.append(path_obj)
            try:
                relative = path_obj.relative_to(self.project_root)
                candidates.append(self.project_root / relative)
            except ValueError:
                pass
            # Historical reports sometimes persisted paths under data/experiments while files now live in experiments/.
            parts = list(path_obj.parts)
            if "data" in parts and "experiments" in parts:
                idx = parts.index("data")
                rebased = Path(*parts[:idx], *parts[idx + 1 :])
                candidates.append(rebased)
        else:
            candidates.append(self.project_root / path_obj)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _ensure_qlib_ready(self) -> None:
        if self._qlib_ready:
            return
        self.qlib_adapter.init_qlib()
        self._qlib_ready = True

    def _load_qlib_features(self, instruments: list[str] | str, fields: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        self._ensure_qlib_ready()
        frame = self.qlib_adapter.get_features(instruments, fields, start_time=start_date, end_time=end_date)
        if frame is None:
            return pd.DataFrame()
        return frame

    def _load_qlib_features_batched(
        self,
        instruments: list[str] | str,
        fields: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = 120,
    ) -> pd.DataFrame:
        if not fields:
            return pd.DataFrame()
        if len(fields) <= batch_size:
            return self._load_qlib_features(instruments, fields, start_date, end_date)
        merged: pd.DataFrame | None = None
        for offset in range(0, len(fields), batch_size):
            batch = fields[offset : offset + batch_size]
            chunk = self._load_qlib_features(instruments, batch, start_date, end_date)
            if chunk.empty:
                continue
            merged = chunk if merged is None else merged.join(chunk, how="outer")
        return merged if merged is not None else pd.DataFrame()

    def _list_snapshot_feature_names(self) -> list[str]:
        names: list[str] = []
        for entry in self.list_feature_registry():
            if not entry.supports_snapshot:
                continue
            candidate = entry.formula or entry.feature_name
            if candidate not in names:
                names.append(candidate)
        return names

    def _load_adapter_qlib_fields(self) -> list[str]:
        config = cfg.get_tushare_feature_config().get("adapter", {})
        fields = []
        for field in config.get("qlib_fields", []):
            if field in {"date", "trade_date"}:
                continue
            normalized = self._normalize_registry_feature_name(str(field))
            if normalized not in fields:
                fields.append(normalized)
        return fields

    def _load_model_feature_configs(self) -> dict[str, list[str]]:
        sources: dict[str, list[str]] = {}
        feature_loaders = {
            "feature_set:alpha158": FeatureLibrary.get_alpha158_config,
            "feature_set:alpha158_extended": FeatureLibrary.get_alpha158_extended_config,
            "feature_set:margin_extended": FeatureLibrary.get_alpha158_margin_extended_config,
            "feature_set:research_phase1": FeatureLibrary.get_research_phase1_config,
            "feature_set:research_phase12": FeatureLibrary.get_research_phase12_config,
            "feature_set:research_phase123": FeatureLibrary.get_research_phase123_config,
            "feature_set:semantic_all_features": FeatureLibrary.get_semantic_all_features_config,
        }
        for source_name, loader in feature_loaders.items():
            try:
                features = loader()
            except Exception:
                continue
            if isinstance(features, list) and features:
                sources[source_name] = [str(item) for item in features if str(item).strip()]

        models_root = self.project_root / "data" / "models"
        if not models_root.exists():
            return sources

        for meta_path in sorted(models_root.glob("**/meta.yaml")):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    payload = yaml.safe_load(handle) or {}
            except Exception:
                continue
            feature_config = payload.get("feature_config") or payload.get("features") or []
            if isinstance(feature_config, list) and feature_config:
                key = f"model:{meta_path.parent.name}"
                sources[key] = [str(item) for item in feature_config if str(item).strip()]

        for selection_path in sorted(models_root.glob("**/feature_selection.yaml")):
            try:
                with open(selection_path, "r", encoding="utf-8") as handle:
                    payload = yaml.safe_load(handle) or {}
            except Exception:
                continue
            selected = payload.get("selected_features") or payload.get("feature_names") or []
            if isinstance(selected, list) and selected:
                key = f"selection:{selection_path.parent.name}"
                sources[key] = [str(item) for item in selected if str(item).strip()]
        return sources

    def _normalize_feature_fields(self, feature_names: list[str]) -> list[str]:
        semantic_features = {
            feature_name
            for payload in list_feature_groups().values()
            for feature_name in payload.get("features", [])
        }
        normalized: list[str] = []
        for feature_name in feature_names:
            name = str(feature_name).strip()
            if not name:
                continue
            if name in semantic_features:
                normalized.append(name)
                continue
            if re.match(r"^\$[A-Za-z_][A-Za-z0-9_]*$", name):
                normalized.append(name)
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                normalized.append(f"${name}")
                continue
            normalized.append(name)
        return normalized

    def _normalize_registry_feature_name(self, feature_name: str) -> str:
        name = str(feature_name).strip()
        if re.match(r"^\$[A-Za-z_][A-Za-z0-9_]*$", name):
            return name[1:]
        return name

    def _classify_feature_source(self, feature_name: str) -> str:
        normalized = self._normalize_registry_feature_name(feature_name)
        semantic_features = {
            item
            for payload in list_feature_groups().values()
            for item in payload.get("features", [])
        }
        if normalized in semantic_features:
            return "semantic_derived"
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", normalized):
            return "raw"
        return "qlib_native"

    def _classify_registry_group(self, feature_name: str) -> str:
        normalized = self._normalize_registry_feature_name(feature_name)
        for group_name, payload in list_feature_groups().items():
            if normalized in payload.get("features", []):
                return group_name
        return self._classify_raw_field_group(normalized)

    def _classify_raw_field_group(self, field_name: str) -> str:
        field = str(field_name).lower()
        if field in {"open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close", "vwap", "factor", "adj_factor"}:
            return "price"
        if field in {"volume", "amount", "turnover_rate", "net_inflow", "big_inflow", "l1_buy_amount", "l1_sell_amount", "l1_net_amount"}:
            return "liquidity"
        if field in {"paused", "high_limit", "low_limit"}:
            return "tradability"
        if field.startswith("margin_") or field.startswith("lend_"):
            return "margin"
        if field in {"pe", "pb", "ps_ttm", "roe", "grossprofit_margin", "debt_to_assets", "current_ratio", "total_mv", "circ_mv", "net_income", "revenue", "total_assets", "equity", "op_cashflow", "inventory", "accounts_receiv", "inventory_yoy", "ar_yoy"}:
            return "fundamental"
        return "qlib_native"

    def _describe_feature(self, feature_name: str) -> str:
        normalized = self._normalize_registry_feature_name(feature_name)
        source_layer = self._classify_feature_source(normalized)
        if source_layer == "semantic_derived":
            return f"Semantic research feature: {normalized}"
        if source_layer == "raw":
            return f"Native market or fundamental field: {normalized}"
        return f"Qlib expression feature available to research/model layer: {feature_name}"

    def _extract_feature_dependencies(self, feature_name: str) -> list[str]:
        deps = {
            self._normalize_registry_feature_name(match)
            for match in re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", str(feature_name))
        }
        return sorted(deps)

    def _load_benchmark_points(self, *, start_date: str | None, end_date: str | None, benchmark_code: str = "000300.SH", benchmark_name: str = "CSI300") -> list[dict[str, Any]]:
        if not start_date or not end_date:
            return []
        start = pd.Timestamp(start_date).strftime("%Y-%m-%d")
        end = pd.Timestamp(end_date).strftime("%Y-%m-%d")
        candidates = [
            cfg.get_path("raw") / "index" / f"{benchmark_code}.csv",
            self.project_root / "data" / "raw" / "index" / f"{benchmark_code}.csv",
        ]
        frame = pd.DataFrame()
        for candidate in candidates:
            if candidate.exists():
                frame = pd.read_csv(candidate)
                break

        if frame.empty:
            try:
                frame = self.research_view.get_feature([benchmark_code], ["open", "high", "low", "close", "volume"], start, end)
            except Exception:
                frame = pd.DataFrame()
            if not frame.empty:
                frame = frame.reset_index().rename(columns={"ts_code": "instrument_id"})

        if frame.empty:
            return []

        rename_map = {
            "vol": "volume",
            "trade_date": "trade_date",
            "datetime": "trade_date",
            "ts_code": "instrument_id",
            "instrument": "instrument_id",
        }
        frame = frame.rename(columns=rename_map)
        if "trade_date" not in frame.columns:
            return []
        frame = frame.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
        frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
        frame = frame.sort_values("trade_date")
        frame["instrument_id"] = frame.get("instrument_id", pd.Series([benchmark_code] * len(frame))).astype(str)

        rows: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            rows.append(
                {
                    "trade_date": row.get("trade_date"),
                    "instrument_id": row.get("instrument_id") or benchmark_code,
                    "benchmark_name": benchmark_name,
                    "open": self._to_float(row.get("open")),
                    "high": self._to_float(row.get("high")),
                    "low": self._to_float(row.get("low")),
                    "close": self._to_float(row.get("close")),
                    "volume": self._to_float(row.get("volume")),
                }
            )
        return rows

    def _get_stock_list_frame(self) -> pd.DataFrame:
        if self._stock_list_cache is None:
            frame = self.store.get_stock_list()
            self._stock_list_cache = frame if frame is not None else pd.DataFrame()
        return self._stock_list_cache

    def _get_instrument_index(self) -> dict[str, dict[str, Any]]:
        if self._instrument_index is not None:
            return self._instrument_index
        index: dict[str, dict[str, Any]] = {}
        frame = self._get_stock_list_frame()
        if frame is None or frame.empty:
            self._instrument_index = index
            return self._instrument_index
        for row in frame.to_dict(orient="records"):
            item = {key: self._normalize_scalar(value) for key, value in row.items()}
            ts_code = item.get("ts_code")
            if ts_code:
                index[str(ts_code)] = item
        self._instrument_index = index
        return self._instrument_index

    def _normalize_scalar(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            return value.item()
        return value

    def _to_float(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        try:
            return float(value)
        except Exception:
            return None

    def _to_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        try:
            return int(value)
        except Exception:
            return None
