from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from qsys.research_ui import ResearchCockpitRepository
from qsys.research_ui.schema import schema_to_dict


def create_app(project_root: str | Path = ".") -> FastAPI:
    app = FastAPI(title="Qsys Research UI API", version="0.1.0")
    root = Path(project_root).resolve()

    def get_repo() -> ResearchCockpitRepository:
        return ResearchCockpitRepository(project_root=root)

    web_root = root / "qsys" / "research_ui" / "web"
    app.mount("/ui", StaticFiles(directory=web_root), name="research-ui-static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/api/instruments")
    def list_instruments(
        q: str | None = None,
        limit: int = Query(200, ge=1, le=5000),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        items = repo.list_instruments(query=q, limit=limit)
        return {"items": items, "count": len(items)}

    @app.get("/api/search")
    def search_instruments(
        q: str = Query(..., min_length=1),
        limit: int = Query(50, ge=1, le=500),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        items = repo.list_instruments(query=q, limit=limit)
        return {"items": items, "count": len(items), "query": q}

    @app.get("/api/instruments/{instrument_id}")
    def get_instrument(
        instrument_id: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        item = repo.get_instrument(instrument_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"Unknown instrument_id: {instrument_id}")
        return item

    @app.get("/api/bars")
    def get_bars(
        instrument_id: str,
        start: str,
        end: str,
        price_mode: str = Query("fq", pattern="^(raw|fq)$"),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        if not repo.get_instrument(instrument_id):
            raise HTTPException(status_code=404, detail=f"Unknown instrument_id: {instrument_id}")
        items = repo.get_bar_series(instrument_id=instrument_id, start=start, end=end, price_mode=price_mode)
        if not items:
            raise HTTPException(status_code=404, detail=f"No bars found for instrument_id={instrument_id}, range={start}..{end}")
        return {
            "instrument_id": instrument_id,
            "start": start,
            "end": end,
            "price_mode": price_mode,
            "items": items,
        }

    @app.get("/api/feature-runs")
    def list_feature_runs(
        limit: int = Query(100, ge=1, le=500),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        items = repo.list_feature_runs(limit=limit)
        return {"items": items, "count": len(items)}

    @app.get("/api/feature-registry")
    def get_feature_registry(repo: ResearchCockpitRepository = Depends(get_repo)) -> dict:
        items = [schema_to_dict(item) for item in repo.list_feature_registry()]
        return {"items": items, "count": len(items)}

    @app.get("/api/features")
    def get_features(
        instrument_id: str,
        start: str,
        end: str,
        feature_names: list[str] = Query(...),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        if not repo.get_instrument(instrument_id):
            raise HTTPException(status_code=404, detail=f"Unknown instrument_id: {instrument_id}")
        items = repo.get_feature_series(instrument_id=instrument_id, start=start, end=end, feature_names=feature_names)
        if not items:
            raise HTTPException(status_code=404, detail=f"No features found for instrument_id={instrument_id}, range={start}..{end}")
        return {
            "instrument_id": instrument_id,
            "start": start,
            "end": end,
            "feature_names": feature_names,
            "items": items,
        }

    @app.get("/api/feature-health")
    def get_feature_health(
        trade_date: str,
        feature_names: list[str] = Query(...),
        universe: str = "csi300",
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        summary = repo.build_feature_health_summary(trade_date=trade_date, feature_names=feature_names, universe=universe)
        return schema_to_dict(summary)

    @app.get("/api/feature-snapshot")
    def get_feature_snapshot(
        instrument_id: str,
        trade_date: str,
        feature_names: list[str] | None = Query(None),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        if not repo.get_instrument(instrument_id):
            raise HTTPException(status_code=404, detail=f"Unknown instrument_id: {instrument_id}")
        payload = repo.get_feature_snapshot(trade_date=trade_date, instrument_id=instrument_id, feature_names=feature_names)
        if not payload.get("features"):
            raise HTTPException(status_code=404, detail=f"No feature snapshot found for instrument_id={instrument_id}, trade_date={trade_date}")
        return payload

    @app.get("/api/runs/daily/{execution_date}")
    def get_daily_run(
        execution_date: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            return schema_to_dict(repo.build_daily_run_manifest(execution_date))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/backtest-runs")
    def list_backtest_runs(
        limit: int = Query(50, ge=1, le=500),
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        items = [schema_to_dict(item) for item in repo.list_backtest_runs(limit=limit)]
        return {"items": items, "count": len(items)}

    @app.get("/api/backtest-runs/{run_id}/summary")
    def get_backtest_summary(
        run_id: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            return schema_to_dict(repo.get_backtest_summary(run_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/backtest-runs/{run_id}/metrics")
    def get_backtest_metrics(
        run_id: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            summary = repo.get_backtest_summary(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"run_id": run_id, "metrics": summary.metrics}

    @app.get("/api/backtest-runs/{run_id}/daily")
    def get_backtest_daily(
        run_id: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            items = [schema_to_dict(item) for item in repo.get_backtest_daily_points(run_id)]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not items:
            raise HTTPException(status_code=404, detail=f"No daily backtest payload found for run_id={run_id}")
        return {"run_id": run_id, "items": items}

    @app.get("/api/decision-replay")
    def get_decision_replay(
        execution_date: str,
        account_name: str = "shadow",
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            replay = repo.build_decision_replay(execution_date=execution_date, account_name=account_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return schema_to_dict(replay)

    @app.get("/api/cases/{case_id}")
    def get_case(
        case_id: str,
        repo: ResearchCockpitRepository = Depends(get_repo),
    ) -> dict:
        try:
            bundle = repo.get_case_bundle_by_id(case_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        has_payload = bool(bundle.bars or bundle.feature_snapshot.get("features") or bundle.signal_snapshot or bundle.orders or bundle.positions)
        if not has_payload:
            raise HTTPException(status_code=404, detail=f"No case payload found for case_id={case_id}")
        return schema_to_dict(bundle)

    return app


app = create_app()
