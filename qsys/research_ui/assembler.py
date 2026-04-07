from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.health import inspect_qlib_data_health
from qsys.data.storage import StockDataStore
from qsys.dataview.research import ResearchDataView
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

    def list_instruments(self, *, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        frame = self.store.get_stock_list()
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
        items = [item for item in self.list_instruments(limit=5000) if item.get("ts_code") == instrument_id]
        return items[0] if items else None

    def list_feature_registry(self) -> list[FeatureRegistryEntry]:
        entries: list[FeatureRegistryEntry] = []
        for group_name, payload in sorted(list_feature_groups().items()):
            for feature_name in payload.get("features", []):
                entries.append(
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
                        tags=[group_name, "research_ui"],
                    )
                )
        return entries

    def get_bar_series(self, *, instrument_id: str, start: str, end: str, price_mode: str = "fq") -> list[dict[str, Any]]:
        return self._load_bars(instrument_id=instrument_id, trade_date=end, price_mode=price_mode, start_date=start, end_date=end)

    def get_feature_snapshot(self, *, trade_date: str, instrument_id: str, feature_names: list[str] | None = None) -> dict[str, Any]:
        return self._load_feature_snapshot(trade_date=trade_date, instrument_id=instrument_id, feature_names=feature_names)

    def get_feature_series(self, *, instrument_id: str, start: str, end: str, feature_names: list[str]) -> list[dict[str, Any]]:
        qlib_fields = [name if name.startswith("$") else f"${name}" for name in feature_names]
        frame = self.research_view.get_feature([instrument_id], qlib_fields, start, end)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for _, row in frame.reset_index().iterrows():
            item = {
                "trade_date": str(row.get("trade_date")),
                "instrument_id": str(row.get("ts_code")),
            }
            for field in qlib_fields:
                item[field.lstrip("$")] = self._normalize_scalar(row.get(field))
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
        qlib_fields = [field if field.startswith("$") else f"${field}" for field in feature_names]
        report = inspect_qlib_data_health(trade_date, qlib_fields, universe=universe)
        entries: list[FeatureHealthEntry] = []
        for field in qlib_fields:
            miss = float(report.column_missing_ratio.get(field, 1.0))
            entries.append(
                FeatureHealthEntry(
                    feature_name=field.lstrip("$"),
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
            instrument_count=report.feature_rows,
            overall_missing_ratio=float(report.missing_ratio),
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

    def list_backtest_runs(self, limit: int = 50) -> list[BacktestRunSummary]:
        runs: list[BacktestRunSummary] = []
        for path in sorted(self.reports_root.glob("backtest_*.json"), reverse=True)[:limit]:
            payload = self._load_json(path)
            model_info = payload.get("model_info") or {}
            metrics = self._extract_backtest_metrics(payload)
            daily_path = (payload.get("artifacts") or {}).get("daily_result")
            artifacts = [
                RunArtifactRef(
                    artifact_id="backtest_report",
                    kind="report",
                    logical_path=str(path.relative_to(self.project_root)),
                    title=path.name,
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
            runs.append(
                BacktestRunSummary(
                    run_id=str(payload.get("run_id") or path.stem),
                    run_type=str(payload.get("workflow") or "backtest"),
                    model_name=str(model_info.get("model_name") or model_info.get("model_path") or "unknown"),
                    feature_set=str(model_info.get("feature_set") or "unknown"),
                    universe=str(model_info.get("universe") or "csi300"),
                    train_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
                    test_range={"start": payload.get("signal_date"), "end": payload.get("execution_date")},
                    top_k=self._to_int(model_info.get("top_k")),
                    price_mode="fq",
                    metrics=metrics,
                    artifacts=artifacts,
                    manifest_ref=str(path.relative_to(self.project_root)),
                )
            )
        return runs

    def get_backtest_summary(self, run_id: str) -> BacktestRunSummary:
        for item in self.list_backtest_runs(limit=500):
            if item.run_id == run_id:
                return item
        raise FileNotFoundError(f"Unknown backtest run_id: {run_id}")

    def get_backtest_daily_points(self, run_id: str) -> list[BacktestDailyPoint]:
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
        for _, row in frame.iterrows():
            points.append(
                BacktestDailyPoint(
                    trade_date=str(row.get("date") or row.get("trade_date") or ""),
                    equity=self._to_float(row.get("total_assets")),
                    daily_return=self._to_float(row.get("daily_return")),
                    drawdown=self._to_float(row.get("drawdown")),
                    turnover=self._to_float(row.get("turnover") or row.get("daily_turnover")),
                    ic=self._to_float(row.get("ic")),
                    rank_ic=self._to_float(row.get("rank_ic")),
                    trade_count=self._to_int(row.get("trade_count")),
                )
            )
        return points

    def build_decision_replay(self, *, execution_date: str, account_name: str) -> DecisionReplay:
        manifest = self.build_daily_run_manifest(execution_date)
        intent_path = self.daily_root / execution_date / "pre_open" / "order_intents" / f"order_intents_{execution_date}_{account_name}.json"
        payload = self._load_json(intent_path) if intent_path.exists() else {}
        intents = payload.get("intents") or []
        previous_positions = self._load_previous_positions(execution_date, account_name)
        scored_candidates: list[DecisionCandidate] = []
        final_orders: list[DecisionOrder] = []
        candidate_pool: list[str] = []
        selected_targets: list[str] = []
        exclusions: list[dict[str, Any]] = []
        for index, item in enumerate(intents, start=1):
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            candidate_pool.append(symbol)
            selected_targets.append(symbol)
            scored_candidates.append(
                DecisionCandidate(
                    instrument_id=symbol,
                    raw_score=self._to_float(item.get("score")),
                    adjusted_score=self._to_float(item.get("score")),
                    rank=self._to_int(item.get("score_rank")) or index,
                    selected=True,
                    exclusion_reasons=[],
                    constraint_status={
                        "execution_bucket": item.get("execution_bucket"),
                        "cash_dependency": item.get("cash_dependency"),
                        "t1_rule": item.get("t1_rule"),
                    },
                )
            )
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
        return DecisionReplay(
            run_id=manifest.run_id,
            trade_date=execution_date,
            signal_date=payload.get("signal_date") or manifest.signal_date,
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
                "model_info": payload.get("model_info") or {},
            },
            manifest_ref=manifest.run_id,
        )

    def build_case_bundle(self, *, execution_date: str, instrument_id: str, price_mode: str = "fq") -> CaseBundle:
        manifest = self.build_daily_run_manifest(execution_date)
        signal_date = manifest.signal_date or execution_date
        bars = self._load_bars(instrument_id=instrument_id, trade_date=execution_date, price_mode=price_mode)
        signal_snapshot = self._load_signal_snapshot(execution_date=execution_date, instrument_id=instrument_id)
        feature_snapshot = self._load_feature_snapshot(trade_date=signal_date, instrument_id=instrument_id)
        replay = self.build_decision_replay(execution_date=execution_date, account_name="shadow")
        related_orders = [item.to_dict() for item in replay.final_orders if item.instrument_id == instrument_id]
        positions = [item for item in replay.previous_positions if item.get("instrument_id") == instrument_id or item.get("symbol") == instrument_id]
        return CaseBundle(
            case_id=f"{execution_date}:{instrument_id}:{price_mode}",
            run_id=manifest.run_id,
            instrument_id=instrument_id,
            trade_date=execution_date,
            signal_date=signal_date,
            execution_date=execution_date,
            price_mode=price_mode,
            bars=bars,
            signal_snapshot=signal_snapshot,
            feature_snapshot=feature_snapshot,
            orders=related_orders,
            positions=positions,
            annotations=[],
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
        start_date = start_date or (pd.Timestamp(trade_date) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        end_date = end_date or trade_date
        frame = self.research_view.get_feature([instrument_id], fields, start_date, end_date)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for (dt, code), row in frame.reset_index().set_index(["trade_date", "ts_code"]).iterrows():
            item = {"trade_date": str(dt), "instrument_id": str(code), "price_mode": price_mode}
            for col, value in row.items():
                item[str(col)] = self._normalize_scalar(value)
            rows.append(item)
        return rows

    def _load_signal_snapshot(self, *, execution_date: str, instrument_id: str) -> dict[str, Any]:
        signal_dir = self.daily_root / execution_date / "pre_open" / "signals"
        files = sorted(signal_dir.glob("signal_basket_*.csv"))
        if not files:
            return {}
        frame = pd.read_csv(files[-1])
        if "symbol" not in frame.columns:
            return {}
        matched = frame[frame["symbol"].astype(str) == instrument_id]
        if matched.empty:
            return {}
        return {key: self._normalize_scalar(value) for key, value in matched.iloc[0].to_dict().items()}

    def _load_feature_snapshot(self, *, trade_date: str, instrument_id: str, feature_names: list[str] | None = None) -> dict[str, Any]:
        features = feature_names or [item.feature_name for item in self.list_feature_registry()[:20]]
        field_names = [str(item).lstrip("$") for item in features]
        qlib_fields = [f"${name}" for name in field_names]
        try:
            frame = self.research_view.get_feature([instrument_id], qlib_fields, trade_date, trade_date)
        except Exception:
            return {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
        if frame.empty:
            return {"trade_date": trade_date, "instrument_id": instrument_id, "features": {}}
        row = frame.reset_index().iloc[-1].to_dict()
        payload = {}
        for key, value in row.items():
            if key in ("trade_date", "ts_code"):
                continue
            payload[str(key).lstrip("$")] = self._normalize_scalar(value)
        return {"trade_date": trade_date, "instrument_id": instrument_id, "features": payload}

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
        path = self.reports_root / f"backtest_{run_id}.json"
        if path.exists():
            return path
        for candidate in self.reports_root.glob("backtest_*.json"):
            payload = self._load_json(candidate)
            if str(payload.get("run_id") or "") == run_id:
                return candidate
        raise FileNotFoundError(f"Unknown backtest run_id: {run_id}")

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
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}

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
