from __future__ import annotations

import json
import os
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



class ResearchCockpitRepository:
    """Build stable research-ui contracts from current SysQ artifacts."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root)
        self.daily_root = self.project_root / "daily"
        self.experiments_root = self.project_root / "experiments"
        self.reports_root = self.experiments_root / "reports"
        self.canonical_backtests_root = self.project_root / "data" / "research" / "backtests"
        # F05: removed unused TradeLedger(self.project_root / "data" / "trade.db")
        # — it pointed TradeLedger at the LedgerService SOT with a conflicting
        # schema and was never read.
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
        self._backtest_group_returns_cache: dict[str, list[dict[str, Any]]] = {}
        self._backtest_report_paths_cache: list[Path] | None = None
        self._backtest_report_index: dict[str, Path] = {}
        self._synthetic_backtest_index: dict[str, dict[str, Any]] = {}
        self._canonical_backtest_index: dict[str, dict[str, Any]] = {}
        self._json_cache: dict[Path, dict[str, Any]] = {}
        self._model_meta_cache: dict[Path, dict[str, Any]] = {}
        self._universe_cache: dict[str, set[str]] = {}
        self._feature_snapshot_cache: dict[str, dict[str, Any]] = {}
        self._qlib_ready = False

    def _load_universe_set(self, universe: str) -> set[str]:
        if universe in self._universe_cache:
            return self._universe_cache[universe]
        universe_path = self.project_root / "data" / "qlib_bin" / "instruments" / f"{universe}.txt"
        symbols: set[str] = set()
        if universe_path.exists():
            df = pd.read_csv(universe_path, sep="\t", names=["symbol", "start_date", "end_date"])
            symbols = set(df["symbol"].astype(str))
        self._universe_cache[universe] = symbols
        return symbols

    def list_instruments(self, *, query: str | None = None, limit: int = 200, universe: str | None = None) -> list[dict[str, Any]]:
        frame = self._get_stock_list_frame()
        if frame is None or frame.empty:
            return []
        if universe and universe != "all" and "ts_code" in frame.columns:
            universe_set = self._load_universe_set(universe)
            if universe_set:
                frame = frame[frame["ts_code"].astype(str).isin(universe_set)]
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

    def _iter_live_rolling_run_sources(self) -> list[dict[str, Any]]:
        rolling_root = self.experiments_root / "mainline_rolling_runs"
        if not rolling_root.exists():
            return []
        sources: list[dict[str, Any]] = []
        for run_dir in sorted(rolling_root.glob("*/*"), reverse=True):
            if not run_dir.is_dir():
                continue
            metrics_path = run_dir / "rolling_metrics.csv"
            windows_path = run_dir / "rolling_windows.csv"
            if not metrics_path.exists() or not windows_path.exists():
                continue
            cohort = run_dir.parent.name
            object_name = run_dir.name
            run_id = f"live_rolling__{cohort}__{object_name}"
            source = {
                "run_id": run_id,
                "cohort": cohort,
                "object_name": object_name,
                "run_dir": run_dir,
                "metrics_path": metrics_path,
                "windows_path": windows_path,
                "summary_path": run_dir / "rolling_summary.json",
            }
            self._synthetic_backtest_index[run_id] = source
            sources.append(source)
        return sources

    def _get_synthetic_backtest_source(self, run_id: str) -> dict[str, Any] | None:
        synthetic_source = self._synthetic_backtest_index.get(run_id)
        if synthetic_source is not None:
            return synthetic_source
        if run_id.startswith("live_rolling__"):
            self._iter_live_rolling_run_sources()
            return self._synthetic_backtest_index.get(run_id)
        return None

    @staticmethod
    def _canonical_backtest_run_id(payload: dict[str, Any]) -> str:
        strategy_run_id = str(payload.get("strategy_run_id") or "").strip()
        backtest_id = str(payload.get("backtest_id") or "").strip()
        if not strategy_run_id or not backtest_id:
            return ""
        return f"canonical__{strategy_run_id}__{backtest_id}"

    def _iter_canonical_backtest_sources(self) -> list[dict[str, Any]]:
        """Index immutable backtest manifests by their explicit artifact identity."""
        if not self.canonical_backtests_root.exists():
            return []
        sources: list[dict[str, Any]] = []
        for manifest_path in sorted(self.canonical_backtests_root.glob("*/*/manifest.json")):
            payload = self._load_json(manifest_path)
            if payload.get("artifact_type") != "backtest_run":
                continue
            run_id = self._canonical_backtest_run_id(payload)
            if (
                manifest_path.parent.name != str(payload.get("backtest_id") or "")
                or manifest_path.parent.parent.name != str(payload.get("strategy_run_id") or "")
            ):
                # The directory identity is part of the canonical contract.  A
                # mismatched manifest must not be published under another run.
                continue
            if not run_id or run_id in self._canonical_backtest_index:
                continue
            source = {
                "run_id": run_id,
                "run_dir": manifest_path.parent,
                "manifest_path": manifest_path,
                "manifest": payload,
                "daily_path": manifest_path.parent / "daily_summary.csv",
                "metrics_path": manifest_path.parent / "metrics.json",
                "result_path": manifest_path.parent / "backtest_result.json",
            }
            self._canonical_backtest_index[run_id] = source
            sources.append(source)
        return sources

    def _get_canonical_backtest_source(self, run_id: str) -> dict[str, Any] | None:
        source = self._canonical_backtest_index.get(run_id)
        if source is not None:
            return source
        if run_id.startswith("canonical__"):
            self._iter_canonical_backtest_sources()
            return self._canonical_backtest_index.get(run_id)
        return None

    def _canonical_executions_path(self, source: dict[str, Any]) -> Path | None:
        """Resolve the immutable executions artifact declared by a canonical manifest.

        ``artifacts.executions.path`` is relative to the run-id directory that
        holds the manifest.  Absolute paths (or paths that escape the run dir)
        are rejected so a tampered manifest cannot redirect reads outside the
        backtest artifact tree.  Returns None when the run recorded no
        executions artifact.
        """
        payload = source["manifest"]
        executions = (payload.get("artifacts") or {}).get("executions") or {}
        exec_path = executions.get("path")
        if not exec_path or os.path.isabs(str(exec_path)):
            return None
        candidate = (source["run_dir"] / str(exec_path)).resolve()
        run_dir = source["run_dir"].resolve()
        if not candidate.is_file() or not candidate.is_relative_to(run_dir):
            return None
        return candidate

    def list_backtest_runs(self, limit: int = 50) -> list[BacktestRunSummary]:
        cached = self._backtest_runs_cache.get(limit)
        if cached is not None:
            return cached
        grouped_runs: dict[str, BacktestRunSummary] = {}
        for source in self._iter_canonical_backtest_sources():
            summary = self._build_canonical_backtest_summary(source)
            self._backtest_summary_cache[summary.run_id] = summary
            # Canonical runs are separately addressable artifacts.  Do not collapse
            # Top-N sensitivity runs or distinct signal/config identities.
            grouped_runs[f"canonical:{summary.run_id}"] = summary
        for path in self._iter_backtest_report_paths():
            summary = self._build_backtest_summary(path)
            self._backtest_summary_cache[summary.run_id] = summary
            self._backtest_report_index[summary.run_id] = path
            source_key = str((summary.parameter_summary or {}).get("source_key") or summary.feature_set or summary.run_id)
            universe_key = str(summary.universe or "")
            dedup_key = f"{source_key}__{universe_key}"
            existing = grouped_runs.get(dedup_key)
            if existing is None or self._backtest_version_rank(summary) > self._backtest_version_rank(existing):
                grouped_runs[dedup_key] = summary
        for source in self._iter_live_rolling_run_sources():
            summary = self._build_live_rolling_backtest_summary(source)
            self._backtest_summary_cache[summary.run_id] = summary
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
        synthetic_source = self._get_synthetic_backtest_source(run_id)
        if synthetic_source is not None:
            summary = self._build_live_rolling_backtest_summary(synthetic_source)
            self._backtest_summary_cache[run_id] = summary
            return summary
        canonical_source = self._get_canonical_backtest_source(run_id)
        if canonical_source is not None:
            summary = self._build_canonical_backtest_summary(canonical_source)
            self._backtest_summary_cache[run_id] = summary
            return summary
        report_path = self._resolve_backtest_report(run_id)
        summary = self._build_backtest_summary(report_path)
        self._backtest_summary_cache[run_id] = summary
        self._backtest_report_index[summary.run_id] = report_path
        return summary

    def get_backtest_daily_points(self, run_id: str) -> list[BacktestDailyPoint]:
        cached = self._backtest_daily_cache.get(run_id)
        if cached is not None:
            return cached
        synthetic_source = self._get_synthetic_backtest_source(run_id)
        if synthetic_source is not None:
            points = self._build_live_rolling_daily_points(synthetic_source)
            self._backtest_daily_cache[run_id] = points
            return points
        canonical_source = self._get_canonical_backtest_source(run_id)
        if canonical_source is not None:
            csv_path = canonical_source["daily_path"]
        else:
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
        # Support both total_assets and equity column names
        if "total_assets" in frame.columns:
            equity_col = "total_assets"
        elif "equity" in frame.columns:
            equity_col = "equity"
        else:
            equity_col = "total_value_after"
        if equity_col in frame.columns:
            eq_vals = pd.to_numeric(frame[equity_col], errors="coerce")
            cummax = eq_vals.cummax()
            if canonical_source is not None:
                initial_capital = self._to_float(
                    canonical_source["manifest"].get("initial_capital")
                )
                if initial_capital is not None:
                    cummax = cummax.clip(lower=initial_capital)
            frame = frame.copy()
            frame["drawdown"] = (eq_vals / cummax) - 1.0
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
        equity_base = self._to_float(frame.iloc[0].get(equity_col)) if not frame.empty else None
        previous_benchmark_close = benchmark_base
        previous_benchmark2_close = benchmark2_base
        previous_equity = None
        points: list[BacktestDailyPoint] = []
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
            # Fall back to CSV-column benchmark if index data isn't available
            csv_benchmark = self._to_float(row.get("benchmark_equity"))
            csv_drawdown = self._to_float(row.get("drawdown"))
            equity_value = self._to_float(row.get(equity_col))
            daily_return = self._to_float(row.get("daily_return"))
            if daily_return is None and previous_equity and equity_value is not None:
                daily_return = (equity_value / previous_equity) - 1.0
            if equity_value is not None:
                previous_equity = equity_value
            turnover_value = row.get("turnover") if "turnover" in frame.columns else row.get("daily_turnover")
            trade_count_value = row.get("trade_count") if "trade_count" in frame.columns else row.get("order_count")
            points.append(
                BacktestDailyPoint(
                    trade_date=trade_date,
                    equity=equity_value,
                    zero_cost_equity=self._to_float(row.get("zero_cost_total_assets")),
                    daily_return=daily_return,
                    drawdown=csv_drawdown if csv_drawdown is not None else self._to_float(row.get("drawdown")),
                    benchmark_equity=benchmark_equity if benchmark_equity is not None else csv_benchmark,
                    benchmark_daily_return=benchmark_daily_return,
                    benchmark2_equity=benchmark2_equity,
                    benchmark2_daily_return=benchmark2_daily_return,
                    turnover=self._to_float(turnover_value),
                    ic=self._to_float(row.get("ic")),
                    rank_ic=self._to_float(row.get("rank_ic")),
                    trade_count=self._to_int(trade_count_value),
                )
            )
        self._backtest_daily_cache[run_id] = points
        return points

    def get_backtest_group_returns(self, run_id: str) -> list[dict[str, Any]]:
        cached = self._backtest_group_returns_cache.get(run_id)
        if cached is not None:
            return cached
        synthetic_source = self._get_synthetic_backtest_source(run_id)
        if synthetic_source is not None:
            self._backtest_group_returns_cache[run_id] = []
            return []
        if self._get_canonical_backtest_source(run_id) is not None:
            self._backtest_group_returns_cache[run_id] = []
            return []
        report_path = self._resolve_backtest_report(run_id)
        payload = self._load_json(report_path)
        group_path = (payload.get("artifacts") or {}).get("group_returns")
        if not group_path:
            self._backtest_group_returns_cache[run_id] = []
            return []
        csv_path = self._resolve_project_artifact_path(group_path)
        if not csv_path.exists():
            self._backtest_group_returns_cache[run_id] = []
            return []
        frame = pd.read_csv(csv_path)
        if frame.empty:
            self._backtest_group_returns_cache[run_id] = []
            return []
        rows = [{key: self._normalize_scalar(value) for key, value in row.items()} for row in frame.to_dict(orient="records")]
        self._backtest_group_returns_cache[run_id] = rows
        return rows

    def get_backtest_orders(
        self,
        run_id: str,
        *,
        trade_date: str | None = None,
        instrument_id: str | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        if self._get_synthetic_backtest_source(run_id) is not None:
            return []
        canonical_source = self._get_canonical_backtest_source(run_id)
        if canonical_source is not None:
            return self._load_canonical_executions_orders(
                canonical_source,
                trade_date=trade_date,
                instrument_id=instrument_id,
                limit=limit,
            )
        csv_path = self._resolve_backtest_trades_path(run_id)
        if not csv_path.exists():
            return []
        frame = pd.read_csv(csv_path)
        if frame.empty:
            return []
        if "date" in frame.columns and trade_date:
            frame = frame[frame["date"].astype(str).str[:10] == str(trade_date)]
        if "symbol" in frame.columns and instrument_id:
            frame = frame[frame["symbol"].astype(str) == str(instrument_id)]
        frame = frame.head(limit)
        rows: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            rows.append({key: self._normalize_scalar(value) for key, value in row.items()})
        return rows

    def _read_canonical_executions(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        """Read the raw executions rows of a canonical backtest artifact.

        Rows keep their artifact column names (``instrument`` / ``trade_date`` /
        ``filled_qty`` / ``side`` / ``deal_price`` / ``total_fee`` ...) so
        callers can normalize for the orders table or derive holdings.
        """
        csv_path = self._canonical_executions_path(source)
        if csv_path is None:
            return []
        frame = self._read_csv_safe(csv_path)
        if frame.empty:
            return []
        return [
            {key: self._normalize_scalar(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]

    def _load_canonical_executions_orders(
        self,
        source: dict[str, Any],
        *,
        trade_date: str | None,
        instrument_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read order rows from a canonical backtest's executions artifact.

        The front-end order table consumes ``date`` / ``symbol`` /
        ``filled_amount`` / ``deal_price`` / ``side`` / ``status``.  Canonical
        executions record these under ``trade_date`` / ``instrument`` /
        ``filled_qty`` so the rows are normalized here, keeping the web layer
        schema stable across artifact formats.
        """
        rows = self._read_canonical_executions(source)
        if not rows:
            return []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if "date" not in item and "trade_date" in item:
                item["date"] = str(item["trade_date"])[:10]
            if "symbol" not in item and "instrument" in item:
                item["symbol"] = item["instrument"]
            if "filled_amount" not in item and "filled_qty" in item:
                item["filled_amount"] = item["filled_qty"]
            if trade_date and str(item.get("trade_date") or item.get("date") or "")[:10] != str(trade_date):
                continue
            if instrument_id and str(item.get("instrument") or item.get("symbol") or "") != str(instrument_id):
                continue
            normalized.append(item)
            if len(normalized) >= limit:
                break
        return normalized

    def _derive_positions_from_executions(self, rows: list[dict[str, Any]], *, as_of_date: str | None = None) -> dict[str, dict[str, Any]]:
        """Derive per-instrument holdings as of a date from exact backtest fills.

        Buys add, sells remove.  Cost basis is the average buy cost including
        fees; sells realize ``(sell_price - avg_cost) * qty - fee`` and reduce
        the cost basis proportionally.  Holdings are derived read-only from the
        immutable executions artifact (the canonical runs carry no positions
        file), so they are an exact reconstruction, not an estimate.
        """
        positions: dict[str, dict[str, Any]] = {}
        ordered = sorted(
            rows,
            key=lambda r: (str(r.get("trade_date") or r.get("date") or "")[:10], r.get("sequence") or 0),
        )
        for row in ordered:
            date = str(row.get("trade_date") or row.get("date") or "")[:10]
            if as_of_date and date > str(as_of_date):
                continue
            symbol = str(row.get("instrument") or row.get("symbol") or "")
            if not symbol:
                continue
            side = str(row.get("side") or "").lower()
            qty = float(row.get("filled_qty") or row.get("filled_amount") or 0)
            price = float(row.get("deal_price") or 0)
            fee = float(row.get("total_fee") or 0)
            if qty <= 0:
                continue
            position = positions.setdefault(
                symbol,
                {
                    "instrument": symbol,
                    "qty": 0.0,
                    "buy_qty": 0.0,
                    "buy_cost": 0.0,
                    "avg_cost": 0.0,
                    "realized_pnl": 0.0,
                    "first_trade_date": date,
                    "last_trade_date": date,
                },
            )
            position["last_trade_date"] = date
            if side == "sell":
                if position["qty"] > 0:
                    position["realized_pnl"] += (price - position["avg_cost"]) * qty - fee
                reduce_qty = min(qty, position["buy_qty"])
                if position["buy_qty"] > 0:
                    position["buy_qty"] -= reduce_qty
                    position["buy_cost"] = max(0.0, position["buy_cost"] - position["avg_cost"] * reduce_qty)
                position["qty"] = max(0.0, position["qty"] - qty)
                position["avg_cost"] = position["buy_cost"] / position["buy_qty"] if position["buy_qty"] > 0 else 0.0
            else:
                position["buy_qty"] += qty
                position["buy_cost"] += qty * price + fee
                position["qty"] += qty
                position["avg_cost"] = position["buy_cost"] / position["buy_qty"] if position["buy_qty"] > 0 else 0.0
        return positions

    def _last_close_on_or_before(self, instrument_id: str, as_of_date: str | None) -> float | None:
        df = self.store.load_daily(instrument_id)
        if df is None or df.empty or "trade_date" not in df.columns or "close" not in df.columns:
            return None
        df = df.copy()
        df["trade_date"] = df["trade_date"].map(self._normalize_trade_date_value)
        if as_of_date:
            df = df[df["trade_date"] <= str(as_of_date)]
        if df.empty:
            return None
        return self._normalize_scalar(float(df["close"].iloc[-1]))

    def get_backtest_positions(
        self,
        run_id: str,
        *,
        trade_date: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Holdings of a canonical backtest as of a date, derived from executions.

        Only currently-held instruments are returned (qty > 0).  When a close
        price is available on/before ``trade_date`` the row is enriched with
        ``last_close`` / ``market_value`` / ``unrealized_pnl``.
        """
        source = self._get_canonical_backtest_source(run_id)
        if source is None:
            # Unknown run_ids must 404 like every other backtest endpoint.
            # _resolve_backtest_report raises FileNotFoundError for unknown
            # runs; a legacy (non-canonical) run resolves a report but carries
            # no executions artifact, so there is nothing to derive from.
            self._resolve_backtest_report(run_id)
            return []
        rows = self._read_canonical_executions(source)
        if not rows:
            return []
        positions = self._derive_positions_from_executions(rows, as_of_date=trade_date)
        result: list[dict[str, Any]] = []
        for symbol, position in positions.items():
            if position["qty"] <= 0:
                continue
            item = {key: self._normalize_scalar(value) for key, value in position.items()}
            last_close = self._last_close_on_or_before(symbol, trade_date)
            if last_close is not None:
                item["last_close"] = last_close
                item["market_value"] = round(last_close * position["qty"], 2)
                item["unrealized_pnl"] = round((last_close - position["avg_cost"]) * position["qty"], 2)
            result.append(item)
        result.sort(key=lambda item: abs(item.get("market_value") or 0), reverse=True)
        return result[:limit]

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
                start_date = self._normalize_trade_date_value(start_date)
            else:
                start_date = (pd.Timestamp(trade_date) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        if end_date is None:
            if raw_daily is not None and not raw_daily.empty and "trade_date" in raw_daily.columns:
                end_date = str(raw_daily["trade_date"].astype(str).max())
                end_date = self._normalize_trade_date_value(end_date)
            else:
                end_date = trade_date
        frame = self.research_view.get_feature([instrument_id], fields, start_date, end_date)
        if not frame.empty:
            rows: list[dict[str, Any]] = []
            for (dt, code), row in frame.reset_index().set_index(["trade_date", "ts_code"]).iterrows():
                normalized_date = self._normalize_trade_date_value(dt)
                if not normalized_date:
                    continue
                item = {"trade_date": normalized_date, "instrument_id": str(code), "price_mode": price_mode}
                for col, value in row.items():
                    item[str(col)] = self._normalize_scalar(value)
                rows.append(item)
            ohlc_fields = ["adj_open", "adj_high", "adj_low", "adj_close"] if price_mode == "fq" else ["open", "high", "low", "close"]
            if any(row.get(field) is not None for row in rows for field in ohlc_fields):
                return rows
        # The qlib bin for some instruments only carries `volume` (no OHLC /
        # adj_* fields).  Fall back to the raw daily store, which is the
        # authoritative open/high/low/close source, and forward-adjust it the
        # same way ResearchDataView does (latest-factor ratio) for fq mode.
        return self._load_bars_from_raw_store(raw_daily, instrument_id, price_mode, start_date, end_date)

    def _load_bars_from_raw_store(
        self,
        raw_daily: pd.DataFrame | None,
        instrument_id: str,
        price_mode: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        if raw_daily is None or raw_daily.empty:
            return []
        df = raw_daily.copy()
        if "trade_date" not in df.columns or "close" not in df.columns:
            return []
        df["trade_date"] = df["trade_date"].map(self._normalize_trade_date_value)
        df = df[df["trade_date"] != ""]
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask &= df["trade_date"] >= str(start_date)
        if end_date:
            mask &= df["trade_date"] <= str(end_date)
        df = df[mask]
        if df.empty:
            return []
        if price_mode == "fq" and "factor" in df.columns:
            latest_factor = float(df["factor"].iloc[-1]) if df["factor"].notna().any() else 1.0
            if not latest_factor or latest_factor == 0:
                latest_factor = 1.0
            ratio = df["factor"].astype(float) / latest_factor
            df = df.assign(
                adj_open=df["open"].astype(float) * ratio,
                adj_high=df["high"].astype(float) * ratio,
                adj_low=df["low"].astype(float) * ratio,
                adj_close=df["close"].astype(float) * ratio,
            )
        rows: list[dict[str, Any]] = []
        ohlc_fields = ["adj_open", "adj_high", "adj_low", "adj_close"] if price_mode == "fq" else ["open", "high", "low", "close"]
        for _, row in df.iterrows():
            item = {"trade_date": str(row["trade_date"]), "instrument_id": instrument_id, "price_mode": price_mode}
            for field in ohlc_fields + ["volume"]:
                if field in df.columns:
                    item[field] = self._normalize_scalar(row.get(field))
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
        cache_key = f"{trade_date}:{instrument_id}:{','.join(sorted(feature_names)) if feature_names else '_all'}"
        cached = self._feature_snapshot_cache.get(cache_key)
        if cached is not None:
            return cached
        features = feature_names or self._list_snapshot_feature_names()
        qlib_fields = self._normalize_feature_fields(features)
        try:
            frame = self._load_qlib_features_batched([instrument_id], qlib_fields, trade_date, trade_date)
        except Exception:
            empty = {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
            self._feature_snapshot_cache[cache_key] = empty
            return empty
        if frame.empty:
            empty = {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
            self._feature_snapshot_cache[cache_key] = empty
            return empty
        row = frame.reset_index().iloc[-1].to_dict()
        payload = {}
        for key, value in row.items():
            if key in ("trade_date", "ts_code", "datetime", "instrument"):
                continue
            payload[self._normalize_registry_feature_name(str(key))] = self._normalize_scalar(value)
        result = {"trade_date": trade_date, "instrument_id": instrument_id, "features": payload}
        self._feature_snapshot_cache[cache_key] = result
        return result

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
        if self._get_synthetic_backtest_source(run_id) is not None:
            raise FileNotFoundError(f"Synthetic rolling run has no published backtest report: {run_id}")
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
        mainline_object_name = str(model_info.get("mainline_object_name") or "").strip()
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
        elif mainline_object_name:
            source_key = f"rolling:{mainline_object_name}"
            source_label = f"rolling {mainline_object_name}"
        payload_artifacts = payload.get("artifacts") or {}
        daily_path = payload_artifacts.get("daily_result")
        training_summary = {}
        training_summary_path = payload_artifacts.get("training_summary")
        if training_summary_path:
            resolved_training_summary = self._resolve_project_artifact_path(training_summary_path)
            if resolved_training_summary.exists():
                training_summary = self._load_json(resolved_training_summary)
        metrics_payload = {}
        metrics_path = payload_artifacts.get("metrics")
        if metrics_path:
            resolved_metrics = self._resolve_project_artifact_path(metrics_path)
            if resolved_metrics.exists():
                metrics_payload = self._load_json(resolved_metrics)
        signal_metrics = self._load_backtest_signal_metrics(payload_artifacts)
        group_returns_summary = self._load_backtest_group_returns_summary(payload_artifacts)
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
        if payload_artifacts.get("signal_metrics"):
            artifacts.append(
                RunArtifactRef(
                    artifact_id="signal_metrics",
                    kind="other",
                    logical_path=str(self._logicalize_path(payload_artifacts["signal_metrics"])),
                    title="signal_metrics",
                )
            )
        if payload_artifacts.get("group_returns"):
            artifacts.append(
                RunArtifactRef(
                    artifact_id="group_returns",
                    kind="other",
                    logical_path=str(self._logicalize_path(payload_artifacts["group_returns"])),
                    title="group_returns",
                    media_type="text/csv",
                )
            )
        return BacktestRunSummary(
            run_id=str(payload.get("run_id") or report_path.stem),
            run_type=str(payload.get("workflow") or "backtest"),
            model_name=str(model_info.get("model_name") or model_info.get("model_path") or "unknown"),
            feature_set=mainline_object_name or version_key,
            universe=str(model_info.get("universe") or "csi300"),
            train_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
            test_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
            top_k=self._to_int(model_info.get("top_k")),
            price_mode="fq",
            display_label=f"{(mainline_object_name or version_label)} · {source_label}",
            parameter_summary={
                "version_key": version_key,
                "version_label": version_label,
                "source_key": source_key,
                "source_label": source_label,
                "feature_count": feature_count,
                "model_path": model_info.get("model_path"),
                "feature_set": model_info.get("feature_set") or mainline_object_name or version_key,
                "model_type": model_info.get("model_type"),
                "label_type": model_info.get("label_type"),
                "strategy_type": model_info.get("strategy_type"),
                "rebalance_mode": model_info.get("rebalance_mode"),
                "rebalance_freq": model_info.get("rebalance_freq"),
                "inference_freq": model_info.get("inference_freq"),
                "retrain_freq": model_info.get("retrain_freq"),
                "top_k": self._to_int(model_info.get("top_k")),
                "universe": model_info.get("universe") or "csi300",
                "price_mode": "fq",
                "signal_date": payload.get("signal_date"),
                "execution_date": payload.get("execution_date"),
                "mainline_object_name": mainline_object_name or None,
                "bundle_id": model_info.get("bundle_id"),
                "legacy_feature_set_alias": model_info.get("legacy_feature_set_alias"),
                "internal_run_id": str(payload.get("run_id") or report_path.stem),
                "notes": notes,
                "training_mode": training_summary.get("training_mode") or model_info.get("training_mode"),
                "train_end_requested": training_summary.get("train_end_requested"),
                "train_end_effective": training_summary.get("train_end_effective"),
                "infer_date": training_summary.get("infer_date"),
                "last_train_sample_date": training_summary.get("last_train_sample_date"),
                "max_label_date_used": training_summary.get("max_label_date_used"),
                "is_label_mature_at_infer_time": training_summary.get("is_label_mature_at_infer_time"),
                "shadow_reject_count": metrics_payload.get("shadow_reject_count"),
                "suspicious_trade_count": metrics_payload.get("suspicious_trade_count"),
            },
            metrics=metrics,
            signal_metrics=signal_metrics,
            group_returns_summary=group_returns_summary,
            artifacts=artifacts,
            manifest_ref=report_logical,
        )

    def _build_canonical_backtest_summary(self, source: dict[str, Any]) -> BacktestRunSummary:
        payload = source["manifest"]
        metrics_payload = (
            self._load_json(source["metrics_path"])
            if source["metrics_path"].exists()
            else {}
        )
        daily = self._read_csv_safe(source["daily_path"])
        max_drawdown = None
        if not daily.empty and "total_value_after" in daily.columns:
            equity = pd.to_numeric(daily["total_value_after"], errors="coerce")
            peak = equity.cummax()
            initial_capital = self._to_float(payload.get("initial_capital"))
            if initial_capital is not None:
                peak = peak.clip(lower=initial_capital)
            max_drawdown = self._to_float((equity / peak - 1.0).min())
        top_n = self._to_int((payload.get("allocation_params") or {}).get("top_n"))
        signal_id = str(payload.get("signal_id") or "unknown")
        strategy_template = str(payload.get("strategy_template_id") or "backtest")
        logical_manifest = str(source["manifest_path"].relative_to(self.project_root))
        artifacts = [
            RunArtifactRef(
                artifact_id="manifest",
                kind="manifest",
                logical_path=logical_manifest,
                title="canonical backtest manifest",
            )
        ]
        for artifact_id, kind, media_type in [
            ("daily_summary", "backtest_daily", "text/csv"),
            ("metrics", "other", "application/json"),
            ("backtest_result", "report", "application/json"),
        ]:
            path = source[f"{artifact_id.replace('daily_summary', 'daily').replace('backtest_result', 'result')}_path"]
            if path.exists():
                artifacts.append(
                    RunArtifactRef(
                        artifact_id=artifact_id,
                        kind=kind,
                        logical_path=str(path.relative_to(self.project_root)),
                        title=path.name,
                        media_type=media_type,
                    )
                )
        return BacktestRunSummary(
            run_id=source["run_id"],
            run_type="canonical_backtest",
            model_name=signal_id,
            feature_set=signal_id,
            universe=str(payload.get("universe") or "unknown"),
            train_range={"start": None, "end": None},
            test_range={
                "start": payload.get("effective_start_date") or payload.get("start_date"),
                "end": payload.get("effective_end_date") or payload.get("end_date"),
            },
            top_k=top_n,
            price_mode="fq" if payload.get("use_adjusted_price") else "raw",
            display_label=f"{signal_id} · Top{top_n or '?'} · {strategy_template}",
            parameter_summary={
                "source_key": f"canonical:{source['run_id']}",
                "source_label": "canonical signal-cache backtest",
                "artifact_identity": source["run_id"],
                "strategy_run_id": payload.get("strategy_run_id"),
                "backtest_id": payload.get("backtest_id"),
                "signal_id": signal_id,
                "signal_run_id": payload.get("signal_run_id"),
                "strategy_type": strategy_template,
                "rebalance_freq": payload.get("rebalance_freq"),
                "top_k": top_n,
                "execution_price": payload.get("execution_price"),
                "mtm_price": payload.get("mtm_price"),
                "git_commit": payload.get("git_commit"),
                "signal_date": payload.get("effective_start_date") or payload.get("start_date"),
                "execution_date": payload.get("effective_end_date") or payload.get("end_date"),
                "notes": ["version=canonical_backtest_v1"],
            },
            metrics={
                "total_return": self._fmt_pct(metrics_payload.get("total_return", payload.get("total_return"))),
                "sharpe": self._fmt_num(metrics_payload.get("sharpe")),
                "max_drawdown": self._fmt_pct(max_drawdown),
                "trade_count": metrics_payload.get("order_count_total"),
                "days": metrics_payload.get("trading_day_count", payload.get("trading_day_count")),
            },
            signal_metrics={"status": "not_available"},
            group_returns_summary={"status": "not_available"},
            artifacts=artifacts,
            manifest_ref=logical_manifest,
        )

    def _build_live_rolling_backtest_summary(self, source: dict[str, Any]) -> BacktestRunSummary:
        metrics_frame = self._read_csv_safe(source["metrics_path"])
        summary_payload = self._load_json(source["summary_path"]) if source["summary_path"].exists() else {}
        object_name = str(source["object_name"])
        cohort = str(source["cohort"])
        signal_metrics = self._build_live_rolling_signal_metrics(metrics_frame)
        total_return_mean = self._series_numeric_stat(metrics_frame, "total_return", "mean")
        max_drawdown_worst = self._series_numeric_stat(metrics_frame, "max_drawdown", "min")
        turnover_mean = self._series_numeric_stat(metrics_frame, "turnover", "mean")
        first_test_start = self._frame_first_value(metrics_frame, "test_start")
        last_test_end = self._frame_last_value(metrics_frame, "test_end")
        completed = int(len(metrics_frame))
        planned = self._read_csv_safe(source["windows_path"]).shape[0]
        source_label = cohort.replace("_", " ")
        parameter_summary = {
            "version_key": f"live_rolling:{cohort}:{object_name}",
            "version_label": object_name,
            "source_key": f"live_rolling:{cohort}:{object_name}",
            "source_label": source_label,
            "feature_set": object_name,
            "mainline_object_name": metrics_frame.iloc[0]["mainline_object_name"] if not metrics_frame.empty and "mainline_object_name" in metrics_frame.columns else object_name,
            "bundle_id": metrics_frame.iloc[0]["bundle_id"] if not metrics_frame.empty and "bundle_id" in metrics_frame.columns else summary_payload.get("bundle_id"),
            "legacy_feature_set_alias": metrics_frame.iloc[0]["legacy_feature_set_alias"] if not metrics_frame.empty and "legacy_feature_set_alias" in metrics_frame.columns else summary_payload.get("legacy_feature_set_alias"),
            "signal_date": first_test_start,
            "execution_date": last_test_end,
            "price_mode": "fq",
            "universe": (summary_payload.get("defaults") or {}).get("universe") or "csi300",
            "top_k": (summary_payload.get("defaults") or {}).get("top_k") or 5,
            "strategy_type": (summary_payload.get("defaults") or {}).get("strategy_type") or "rank_topk",
            "label_type": (summary_payload.get("lineage") or {}).get("label_type"),
            "retrain_freq": "weekly_rolling",
            "rebalance_freq": (summary_payload.get("defaults") or {}).get("step_days"),
            "inference_freq": (summary_payload.get("defaults") or {}).get("step_days"),
            "window_count_completed": completed,
            "window_count_planned": planned,
            "progress_pct": round(completed / planned, 4) if planned else None,
            "run_dir": str(source["run_dir"].relative_to(self.project_root)),
            "internal_run_id": source["run_id"],
            "notes": [
                "version=live_mainline_rolling_v1",
                f"rolling_metrics={source['metrics_path'].relative_to(self.project_root)}",
                f"rolling_windows={source['windows_path'].relative_to(self.project_root)}",
            ],
        }
        artifacts = [
            RunArtifactRef(
                artifact_id="rolling_metrics",
                kind="other",
                logical_path=str(source["metrics_path"].relative_to(self.project_root)),
                title="rolling_metrics",
                media_type="text/csv",
            ),
            RunArtifactRef(
                artifact_id="rolling_windows",
                kind="other",
                logical_path=str(source["windows_path"].relative_to(self.project_root)),
                title="rolling_windows",
                media_type="text/csv",
            ),
        ]
        if source["summary_path"].exists():
            artifacts.append(
                RunArtifactRef(
                    artifact_id="rolling_summary",
                    kind="report",
                    logical_path=str(source["summary_path"].relative_to(self.project_root)),
                    title="rolling_summary",
                )
            )
        return BacktestRunSummary(
            run_id=source["run_id"],
            run_type="backtest",
            model_name=str(summary_payload.get("model_path") or object_name),
            feature_set=object_name,
            universe=str(parameter_summary["universe"]),
            train_range={"start": self._frame_first_value(metrics_frame, "train_start"), "end": self._frame_last_value(metrics_frame, "train_end")},
            test_range={"start": first_test_start, "end": last_test_end},
            top_k=self._to_int(parameter_summary["top_k"]),
            price_mode="fq",
            display_label=f"{object_name} · {source_label}",
            parameter_summary=parameter_summary,
            metrics={
                "total_return": self._fmt_pct(total_return_mean),
                "sharpe": "-",
                "max_drawdown": self._fmt_pct(max_drawdown_worst),
                "trade_count": completed,
                "days": completed,
            },
            signal_metrics=signal_metrics,
            group_returns_summary={"status": "not_available"},
            artifacts=artifacts,
            manifest_ref=str(source["run_dir"].relative_to(self.project_root)),
        )

    def _extract_backtest_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections = payload.get("sections") or []
        for section in sections:
            if section.get("name") == "Performance":
                return dict(section.get("metrics") or {})
        return {}

    def _load_backtest_signal_metrics(self, artifacts: dict[str, Any]) -> dict[str, Any]:
        signal_metrics_path = artifacts.get("signal_metrics")
        if not signal_metrics_path:
            return {"status": "not_available"}
        resolved_path = self._resolve_project_artifact_path(signal_metrics_path)
        if not resolved_path.exists():
            return {"status": "not_available"}
        payload = self._load_json(resolved_path)
        if not payload:
            return {"status": "not_available"}
        payload.setdefault("status", "available")
        return payload

    def _load_backtest_group_returns_summary(self, artifacts: dict[str, Any]) -> dict[str, Any]:
        group_returns_path = artifacts.get("group_returns")
        if not group_returns_path:
            return {"status": "not_available"}
        resolved_path = self._resolve_project_artifact_path(group_returns_path)
        if not resolved_path.exists() or resolved_path.stat().st_size == 0:
            return {"status": "not_available"}
        try:
            frame = pd.read_csv(resolved_path)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            return {"status": "not_available"}
        if frame.empty:
            return {"status": "not_available"}
        group_nav = frame.sort_values(["group", "date"]).groupby("group", observed=False).tail(1)
        return {
            "status": "available",
            "groups": [
                {
                    "group": self._to_int(row.get("group")),
                    "nav": self._to_float(row.get("nav")),
                    "mean_return": self._to_float(row.get("mean_return")),
                    "label_horizon": row.get("label_horizon"),
                }
                for row in group_nav.to_dict(orient="records")
            ],
            "group_count": int(frame["group"].nunique()) if "group" in frame.columns else 0,
            "days": int(frame["date"].nunique()) if "date" in frame.columns else 0,
        }

    def get_backtest_sections(self, run_id: str) -> dict[str, Any]:
        synthetic_source = self._get_synthetic_backtest_source(run_id)
        if synthetic_source is not None:
            metrics_frame = self._read_csv_safe(synthetic_source["metrics_path"])
            windows_frame = self._read_csv_safe(synthetic_source["windows_path"])
            return {
                "sections": self._build_live_rolling_sections(metrics_frame),
                "artifacts": {
                    "rolling_windows": windows_frame.to_dict(orient="records"),
                    "rolling_metrics": metrics_frame.to_dict(orient="records"),
                    "signal_metrics": self._build_live_rolling_signal_metrics(metrics_frame),
                    "rolling_stability": self._build_live_rolling_stability(metrics_frame),
                },
            }
        canonical_source = self._get_canonical_backtest_source(run_id)
        if canonical_source is not None:
            return self._build_canonical_sections(canonical_source)
        report_path = self._resolve_backtest_report(run_id)
        payload = self._load_json(report_path)
        sections = payload.get("sections", [])
        artifacts = {}
        for key in ["rolling_windows", "rolling_metrics", "monthly_returns", "weekly_returns", "signal_metrics", "trade_detail", "trades"]:
            ap = (payload.get("artifacts") or {}).get(key)
            if not ap or not isinstance(ap, str):
                continue
            ap_path = self._resolve_project_artifact_path(ap)
            if ap_path.suffix == ".csv":
                try:
                    df = pd.read_csv(ap_path)
                    artifacts[key] = df.to_dict(orient="records")
                except Exception:
                    pass
            elif ap_path.suffix == ".json":
                try:
                    artifacts[key] = json.loads(ap_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {"sections": sections, "artifacts": artifacts}

    def _build_canonical_sections(self, source: dict[str, Any]) -> dict[str, Any]:
        """Assemble UI sections for a canonical signal-cache backtest.

        The immutable run records metrics.json, daily_summary.csv and (usually)
        executions.csv.  Calendar monthly/weekly returns are derived from the
        equity curve so the front-end heatmap and cost grid have real content;
        per-window IC / rolling metrics do not exist for trade-level canonical
        runs and stay honestly unavailable.
        """
        metrics = (
            self._load_json(source["metrics_path"])
            if source["metrics_path"].exists()
            else {}
        )
        daily = self._read_csv_safe(source["daily_path"])
        monthly_returns = self._derive_canonical_period_returns(daily, "M")
        weekly_returns = self._derive_canonical_period_returns(daily, "W")
        return {
            "sections": [
                self._build_canonical_performance_section(metrics, daily, monthly_returns),
                self._build_canonical_cost_section(metrics, source),
            ],
            "artifacts": {
                "metrics": metrics,
                "monthly_returns": monthly_returns,
                "weekly_returns": weekly_returns,
            },
        }

    def _derive_canonical_period_returns(self, daily: pd.DataFrame, period: str) -> list[dict[str, Any]]:
        """Derive calendar-month or calendar-week returns from the daily equity curve.

        A period's return is the ratio of the last to the first observed
        ``total_value_after`` inside that calendar period.  The first/last
        period of a run may be partial; that matches the run's effective dates
        and is intentional.
        """
        if daily.empty or "trade_date" not in daily.columns or "total_value_after" not in daily.columns:
            return []
        frame = daily.copy()
        frame["date"] = pd.to_datetime(frame["trade_date"].astype(str).str[:10], errors="coerce")
        frame["total_value_after"] = pd.to_numeric(frame["total_value_after"], errors="coerce")
        frame = frame.dropna(subset=["date", "total_value_after"])
        if frame.empty:
            return []
        if period == "W":
            frame["label"] = frame["date"].dt.to_period("W").apply(
                lambda p: p.start_time.strftime("%Y-%m-%d")
            )
        else:
            frame["label"] = frame["date"].dt.strftime("%Y-%m")
        rows: list[dict[str, Any]] = []
        for label, group in frame.groupby("label", sort=True):
            first_val = group["total_value_after"].iloc[0]
            last_val = group["total_value_after"].iloc[-1]
            if not first_val:
                continue
            ret = float(last_val / first_val - 1.0)
            rows.append({"week": label, "return": ret} if period == "W" else {"month": label, "return": ret})
        return rows

    def _build_canonical_performance_section(
        self,
        metrics: dict[str, Any],
        daily: pd.DataFrame,
        monthly_returns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        monthly_values = [row["return"] for row in monthly_returns if row.get("return") is not None]
        win_rate = None
        if monthly_values:
            win_rate = sum(1 for value in monthly_values if value > 0) / len(monthly_values)
        max_drawdown = self._to_float(metrics.get("max_drawdown"))
        if max_drawdown is None and not daily.empty and "total_value_after" in daily.columns:
            equity = pd.to_numeric(daily["total_value_after"], errors="coerce")
            peak = equity.cummax()
            max_drawdown = self._to_float((equity / peak - 1.0).min())
        return {
            "name": "Performance",
            "status": "success",
            "message": "canonical signal-cache backtest artifact (equity curve derived from daily_summary.csv)",
            "metrics": {
                "total_return": self._fmt_pct(self._to_float(metrics.get("total_return"))),
                "max_drawdown": self._fmt_pct(max_drawdown),
                "trading_days": metrics.get("trading_day_count"),
                "filled_orders": metrics.get("filled_count_total"),
                "months": len(monthly_values),
                "month_win_rate": self._fmt_pct(win_rate),
                "best_month": self._fmt_pct(max(monthly_values)) if monthly_values else None,
                "worst_month": self._fmt_pct(min(monthly_values)) if monthly_values else None,
            },
            "details": {},
        }

    def _build_canonical_cost_section(
        self,
        metrics: dict[str, Any],
        source: dict[str, Any],
    ) -> dict[str, Any]:
        """Cost grid for a canonical run: exact fees when executions exist, else estimate.

        ``total_fees`` uses the exact per-order ``total_fee`` column when the
        manifest declares an executions artifact; otherwise it is estimated
        from the configured fee rates applied to total turnover (stamp duty
        assumed on the sell leg only).  Fee rates in the manifest are decimal
        fractions (0.0003 = 3bp) -- they must not be re-scaled.  When neither
        the executions artifact nor the fee config is available the cost
        metrics stay None so the UI renders "not available" instead of a
        misleading zero.
        """
        manifest = source["manifest"]
        initial_capital = self._to_float(manifest.get("initial_capital"))
        turnover_total = self._to_float(metrics.get("turnover_total"))
        filled_orders = self._to_int(metrics.get("filled_count_total"))
        days_with_orders = self._to_int(metrics.get("trading_day_count_with_orders"))
        trading_days = self._to_int(metrics.get("trading_day_count"))
        total_fees: float | None = None
        exec_path = self._canonical_executions_path(source)
        if exec_path is not None:
            exec_frame = self._read_csv_safe(exec_path)
            if "total_fee" in exec_frame.columns:
                total_fees = self._to_float(pd.to_numeric(exec_frame["total_fee"], errors="coerce").sum())
        if total_fees is None and turnover_total:
            commission_bp = self._to_float(manifest.get("commission_bp"))
            stamp_duty_bp = self._to_float(manifest.get("stamp_duty_bp"))
            if commission_bp is not None:
                stamp_rate = stamp_duty_bp * 0.5 if stamp_duty_bp is not None else 0.0
                total_fees = turnover_total * (commission_bp + stamp_rate)
        fee_ratio = (total_fees / turnover_total) if total_fees is not None and turnover_total else None
        fees_pct = (total_fees / initial_capital) if total_fees is not None and initial_capital else None
        avg_daily_fee = (total_fees / days_with_orders) if total_fees is not None and days_with_orders else None
        annualized_turnover = None
        if turnover_total and initial_capital:
            annualized_turnover = turnover_total / initial_capital
            years = (trading_days / 252.0) if trading_days else None
            if years:
                annualized_turnover = annualized_turnover / years
        avg_daily_turnover = (turnover_total / days_with_orders) if turnover_total and days_with_orders else None
        return {
            "name": "Cost Analysis",
            "status": "success",
            "message": "derived from executions.csv + metrics.json + manifest.json",
            "metrics": {
                "total_fees": total_fees,
                "avg_daily_fee": avg_daily_fee,
                "total_turnover": turnover_total,
                "avg_daily_turnover": avg_daily_turnover,
                "annualized_turnover": annualized_turnover,
                "fee_ratio": fee_ratio,
                "fees_as_pct_of_initial": fees_pct,
                "filled_orders": filled_orders,
            },
            "details": {},
        }

    def _build_live_rolling_sections(self, metrics_frame: pd.DataFrame) -> list[dict[str, Any]]:
        positive_returns = self._series_positive_ratio(metrics_frame, "total_return")
        positive_rankic = self._series_positive_ratio(metrics_frame, "RankIC")
        return [
            {
                "name": "Performance",
                "status": "success",
                "message": "live rolling aggregation from rolling_metrics.csv",
                "metrics": {
                    "window_count": str(int(len(metrics_frame))),
                    "positive_windows": str(int(self._series_positive_count(metrics_frame, "total_return"))),
                    "window_win_rate": self._fmt_pct(positive_returns),
                    "mean_window_return": self._fmt_pct(self._series_numeric_stat(metrics_frame, "total_return", "mean")),
                    "median_window_return": self._fmt_pct(self._series_numeric_stat(metrics_frame, "total_return", "median")),
                    "best_window_return": self._fmt_pct(self._series_numeric_stat(metrics_frame, "total_return", "max")),
                    "worst_window_return": self._fmt_pct(self._series_numeric_stat(metrics_frame, "total_return", "min")),
                },
                "details": {},
            },
            {
                "name": "Signal Metrics",
                "status": "success",
                "message": "aggregated from per-window IC metrics",
                "metrics": {
                    "IC_mean": self._fmt_num(self._series_numeric_stat(metrics_frame, "IC", "mean"), 6),
                    "IC_std": self._fmt_num(self._series_numeric_stat(metrics_frame, "IC", "std"), 6),
                    "IC_positive_ratio": self._fmt_pct(self._series_positive_ratio(metrics_frame, "IC")),
                    "RankIC_mean": self._fmt_num(self._series_numeric_stat(metrics_frame, "RankIC", "mean"), 6),
                    "RankIC_std": self._fmt_num(self._series_numeric_stat(metrics_frame, "RankIC", "std"), 6),
                    "RankIC_positive_ratio": self._fmt_pct(positive_rankic),
                    "long_short_spread_mean": self._fmt_num(self._series_numeric_stat(metrics_frame, "long_short_spread", "mean"), 6),
                    "long_short_spread_std": self._fmt_num(self._series_numeric_stat(metrics_frame, "long_short_spread", "std"), 6),
                    "long_short_spread_positive_ratio": self._fmt_pct(self._series_positive_ratio(metrics_frame, "long_short_spread")),
                },
                "details": {},
            },
        ]

    def _build_live_rolling_signal_metrics(self, metrics_frame: pd.DataFrame) -> dict[str, Any]:
        return {
            "status": "available",
            "IC": self._series_numeric_stat(metrics_frame, "IC", "mean"),
            "RankIC": self._series_numeric_stat(metrics_frame, "RankIC", "mean"),
            "ICIR": self._series_information_ratio(metrics_frame, "IC"),
            "RankICIR": self._series_information_ratio(metrics_frame, "RankIC"),
            "long_short_spread": self._series_numeric_stat(metrics_frame, "long_short_spread", "mean"),
            "aggregate": {
                "IC": {"values": self._series_values(metrics_frame, "IC")},
                "RankIC": {"values": self._series_values(metrics_frame, "RankIC")},
                "long_short_spread": {"values": self._series_values(metrics_frame, "long_short_spread")},
                "turnover": {"values": self._series_values(metrics_frame, "turnover")},
                "total_return": {"values": self._series_values(metrics_frame, "total_return")},
            },
        }

    def _build_live_rolling_stability(self, metrics_frame: pd.DataFrame) -> dict[str, Any]:
        if metrics_frame.empty:
            return {"status": "not_available"}
        best_idx = pd.to_numeric(metrics_frame.get("total_return"), errors="coerce").idxmax()
        worst_idx = pd.to_numeric(metrics_frame.get("total_return"), errors="coerce").idxmin()
        best_row = metrics_frame.loc[best_idx].to_dict() if best_idx in metrics_frame.index else {}
        worst_row = metrics_frame.loc[worst_idx].to_dict() if worst_idx in metrics_frame.index else {}
        return {
            "status": "available",
            "positive_return_ratio": self._series_positive_ratio(metrics_frame, "total_return"),
            "positive_rankic_ratio": self._series_positive_ratio(metrics_frame, "RankIC"),
            "positive_ic_ratio": self._series_positive_ratio(metrics_frame, "IC"),
            "return_std": self._series_numeric_stat(metrics_frame, "total_return", "std"),
            "rankic_std": self._series_numeric_stat(metrics_frame, "RankIC", "std"),
            "turnover_mean": self._series_numeric_stat(metrics_frame, "turnover", "mean"),
            "turnover_std": self._series_numeric_stat(metrics_frame, "turnover", "std"),
            "best_window": {
                "window_id": best_row.get("window_id"),
                "test_end": best_row.get("test_end"),
                "total_return": self._to_float(best_row.get("total_return")),
            },
            "worst_window": {
                "window_id": worst_row.get("window_id"),
                "test_end": worst_row.get("test_end"),
                "total_return": self._to_float(worst_row.get("total_return")),
            },
        }

    def _build_live_rolling_daily_points(self, source: dict[str, Any]) -> list[BacktestDailyPoint]:
        frame = self._read_csv_safe(source["metrics_path"])
        if frame.empty:
            return []
        frame = frame.sort_values([col for col in ["test_end", "window_id"] if col in frame.columns]).reset_index(drop=True)
        equity = 1_000_000.0
        points: list[BacktestDailyPoint] = []
        drawdowns: list[float] = []
        equities: list[float] = []
        for _, row in frame.iterrows():
            window_return = self._to_float(row.get("total_return")) or 0.0
            equity = equity * (1.0 + window_return)
            equities.append(equity)
            peak = max(equities)
            drawdowns.append((equity / peak) - 1.0 if peak else 0.0)
            points.append(
                BacktestDailyPoint(
                    trade_date=str(row.get("test_end") or row.get("test_start") or ""),
                    equity=equity,
                    zero_cost_equity=equity,
                    daily_return=window_return,
                    drawdown=drawdowns[-1],
                    benchmark_equity=None,
                    benchmark_daily_return=None,
                    benchmark2_equity=None,
                    benchmark2_daily_return=None,
                    turnover=self._to_float(row.get("turnover")),
                    ic=self._to_float(row.get("IC")),
                    rank_ic=self._to_float(row.get("RankIC")),
                    trade_count=None,
                )
            )
        return points

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
            "feature_set:alpha158_extended_absnorm": FeatureLibrary.get_alpha158_extended_absnorm_config,
            "feature_set:margin_extended": FeatureLibrary.get_alpha158_margin_extended_config,
            "feature_set:margin_extended_absnorm": FeatureLibrary.get_alpha158_margin_extended_absnorm_config,
            "feature_set:research_phase1": FeatureLibrary.get_research_phase1_config,
            "feature_set:research_phase12": FeatureLibrary.get_research_phase12_config,
            "feature_set:research_phase123": FeatureLibrary.get_research_phase123_config,
            "feature_set:research_phase123_absnorm": FeatureLibrary.get_research_phase123_absnorm_config,
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

    def _read_csv_safe(self, path: str | Path) -> pd.DataFrame:
        try:
            return pd.read_csv(path)
        except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
            return pd.DataFrame()

    def _series_numeric_stat(self, frame: pd.DataFrame, column: str, op: str) -> float | None:
        if frame.empty or column not in frame.columns:
            return None
        valid = pd.to_numeric(frame[column], errors="coerce").dropna()
        if valid.empty:
            return None
        if op == "mean":
            result = valid.mean()
        elif op == "median":
            result = valid.median()
        elif op == "std":
            result = valid.std(ddof=0)
        elif op == "min":
            result = valid.min()
        elif op == "max":
            result = valid.max()
        else:
            raise ValueError(f"Unsupported stat op: {op}")
        return round(float(result), 8)

    def _series_information_ratio(self, frame: pd.DataFrame, column: str) -> float | None:
        mean_value = self._series_numeric_stat(frame, column, "mean")
        std_value = self._series_numeric_stat(frame, column, "std")
        if mean_value is None or std_value in (None, 0):
            return None
        return round(float(mean_value / std_value), 8)

    def _series_positive_ratio(self, frame: pd.DataFrame, column: str) -> float | None:
        if frame.empty or column not in frame.columns:
            return None
        valid = pd.to_numeric(frame[column], errors="coerce").dropna()
        if valid.empty:
            return None
        return round(float((valid > 0).mean()), 8)

    def _series_positive_count(self, frame: pd.DataFrame, column: str) -> int:
        if frame.empty or column not in frame.columns:
            return 0
        valid = pd.to_numeric(frame[column], errors="coerce").dropna()
        if valid.empty:
            return 0
        return int((valid > 0).sum())

    def _series_values(self, frame: pd.DataFrame, column: str) -> list[float]:
        if frame.empty or column not in frame.columns:
            return []
        valid = pd.to_numeric(frame[column], errors="coerce")
        return [float(item) for item in valid.dropna().tolist()]

    def _frame_first_value(self, frame: pd.DataFrame, column: str) -> str | None:
        if frame.empty or column not in frame.columns:
            return None
        value = frame.iloc[0].get(column)
        return None if pd.isna(value) else str(value)

    def _frame_last_value(self, frame: pd.DataFrame, column: str) -> str | None:
        if frame.empty or column not in frame.columns:
            return None
        value = frame.iloc[-1].get(column)
        return None if pd.isna(value) else str(value)

    def _fmt_pct(self, value: Any) -> str:
        numeric = self._to_float(value)
        if numeric is None:
            return "-"
        return f"{numeric * 100:.2f}%"

    def _fmt_num(self, value: Any, digits: int = 4) -> str:
        numeric = self._to_float(value)
        if numeric is None:
            return "-"
        return f"{numeric:.{digits}f}"

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
