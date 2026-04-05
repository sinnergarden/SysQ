from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from qsys.broker.gateway import BrokerGateway
from qsys.trader.database import TradeLedger

STEP_SEQUENCE = [
    "01_sync_data",
    "02_sync_broker",
    "03_retrain",
    "04_inference",
    "05_portfolio",
    "06_order_staging",
    "07_reconcile",
]

STEP_DIRECTORIES = {
    "01_sync_data": "01_data",
    "02_sync_broker": "02_broker",
    "03_retrain": "03_retrain",
    "04_inference": "04_inference",
    "05_portfolio": "05_portfolio",
    "06_order_staging": "06_order_staging",
    "07_reconcile": "07_reconcile",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_run_id(trading_date: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{trading_date}-{timestamp}"


def _build_data_version_hash(trading_date: str, recipe_version: str) -> str:
    payload = f"{trading_date}:{recipe_version}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


class QsysRunner:
    def __init__(
        self,
        *,
        trading_date: str,
        recipe_version: str = "minimal-production-kernel",
        data_version_hash: str | None = None,
        runs_root: str | Path = "runs",
        db_path: str | Path = "data/trade.db",
        broker_gateway: BrokerGateway | None = None,
    ) -> None:
        self.trading_date = trading_date
        self.recipe_version = recipe_version
        self.runs_root = Path(runs_root)
        self.run_root = self.runs_root / trading_date
        self.manifest_path = self.run_root / "manifest.json"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.ledger = TradeLedger(db_path)
        self.broker_gateway = broker_gateway or BrokerGateway()
        self.data_version_hash = data_version_hash or _build_data_version_hash(trading_date, recipe_version)
        self.manifest = self.load_manifest()

    def _default_step_state(self) -> dict[str, Any]:
        return {
            "status": "pending",
            "started_at": None,
            "ended_at": None,
            "error": None,
        }

    def _default_manifest(self) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date,
            "run_id": _build_run_id(self.trading_date),
            "updated_at": utc_now(),
            "recipe_version": self.recipe_version,
            "data_version_hash": self.data_version_hash,
            "steps": {step_name: self._default_step_state() for step_name in STEP_SEQUENCE},
            "artifacts": {},
        }

    def load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            with open(self.manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle) or {}
        else:
            manifest = self._default_manifest()
            self.save_manifest(manifest)

        manifest.setdefault("trading_date", self.trading_date)
        manifest.setdefault("run_id", _build_run_id(self.trading_date))
        manifest.setdefault("updated_at", utc_now())
        manifest.setdefault("recipe_version", self.recipe_version)
        manifest.setdefault("data_version_hash", self.data_version_hash)
        manifest.setdefault("steps", {})
        manifest.setdefault("artifacts", {})
        for step_name in STEP_SEQUENCE:
            manifest["steps"].setdefault(step_name, self._default_step_state())
        self.manifest = manifest
        return manifest

    def save_manifest(self, manifest: dict[str, Any] | None = None) -> None:
        payload = manifest or self.manifest
        payload["updated_at"] = utc_now()
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.manifest_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        temp_path.replace(self.manifest_path)
        self.manifest = payload

    def register_artifact(self, name: str, path: str | Path, *, step_name: str) -> str:
        artifact_path = Path(path)
        self.manifest.setdefault("artifacts", {})[name] = {
            "path": str(artifact_path),
            "step": step_name,
            "updated_at": utc_now(),
        }
        self.save_manifest()
        return str(artifact_path)

    def load_artifact(self, name: str) -> dict[str, Any]:
        artifact = (self.manifest.get("artifacts") or {}).get(name)
        if not artifact:
            raise KeyError(f"artifact_not_found:{name}")
        with open(artifact["path"], "r", encoding="utf-8") as handle:
            return json.load(handle) or {}

    def run_step(self, step_name: str, func: Callable[[], Any], force: bool = False) -> str:
        if step_name not in STEP_SEQUENCE:
            raise ValueError(f"unknown_step:{step_name}")

        step_state = self.manifest["steps"].setdefault(step_name, self._default_step_state())
        if step_state.get("status") == "success" and not force:
            return "skipped"

        step_state.update(
            {
                "status": "running",
                "started_at": utc_now(),
                "ended_at": None,
                "error": None,
            }
        )
        self.save_manifest()

        try:
            func()
        except Exception as exc:
            step_state.update(
                {
                    "status": "failed",
                    "ended_at": utc_now(),
                    "error": str(exc),
                }
            )
            self.save_manifest()
            raise

        step_state.update(
            {
                "status": "success",
                "ended_at": utc_now(),
                "error": None,
            }
        )
        self.save_manifest()
        return "success"

    def run(self, *, from_step: str | None = None, force: bool = False) -> int:
        if from_step and from_step not in STEP_SEQUENCE:
            raise ValueError(f"unknown_from_step:{from_step}")

        step_names = STEP_SEQUENCE[STEP_SEQUENCE.index(from_step) :] if from_step else list(STEP_SEQUENCE)
        handlers = self.build_default_step_handlers()
        self.ledger.start_pipeline_run(
            run_id=self.manifest["run_id"],
            trading_date=self.trading_date,
            recipe_version=self.manifest["recipe_version"],
            status="running",
        )

        try:
            for step_name in step_names:
                self.run_step(step_name, handlers[step_name], force=force)
        except Exception as exc:
            self.ledger.finish_pipeline_run(run_id=self.manifest["run_id"], status="failed", error=str(exc))
            return 1

        self.ledger.finish_pipeline_run(run_id=self.manifest["run_id"], status="success")
        return 0

    def build_default_step_handlers(self) -> dict[str, Callable[[], Any]]:
        return {
            "01_sync_data": lambda: self._run_sync_data_step(),
            "02_sync_broker": lambda: self._run_sync_broker_step(),
            "03_retrain": lambda: self._run_retrain_step(),
            "04_inference": lambda: self._run_inference_step(),
            "05_portfolio": lambda: self._run_portfolio_step(),
            "06_order_staging": lambda: self._run_order_staging_step(),
            "07_reconcile": lambda: self._run_reconcile_step(),
        }

    def _step_root(self, step_name: str) -> Path:
        step_root = self.run_root / STEP_DIRECTORIES[step_name]
        step_root.mkdir(parents=True, exist_ok=True)
        return step_root

    def _write_json_artifact(self, *, step_name: str, filename: str, payload: dict[str, Any]) -> Path:
        output_path = self._step_root(step_name) / filename
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return output_path

    def _run_sync_data_step(self) -> None:
        payload = {
            "artifact_type": "data_sync_report",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "status": "healthy",
            "checks": {
                "date_matches": True,
                "has_gap": False,
                "required_fields": ["open", "high", "low", "close", "volume"],
                "null_ratio": 0.0,
            },
        }
        artifact_path = self._write_json_artifact(
            step_name="01_sync_data",
            filename="data_status.json",
            payload=payload,
        )
        self.register_artifact("data_status", artifact_path, step_name="01_sync_data")

    def _run_sync_broker_step(self) -> None:
        output_path = self._step_root("02_sync_broker") / "broker_snapshot.json"
        snapshot = self.broker_gateway.write_snapshot(trading_date=self.trading_date, output_path=output_path)
        self.register_artifact("broker_snapshot", output_path, step_name="02_sync_broker")
        account_name = snapshot["account_snapshot"].get("account_name", "real")
        self.ledger.replace_position_snapshot(
            run_id=self.manifest["run_id"],
            trading_date=self.trading_date,
            snapshot_type="real",
            positions=snapshot.get("positions") or [],
            account_name=account_name,
        )
        for fill in snapshot.get("fills") or []:
            self.ledger.insert_fill(
                fill_id=str(fill.get("fill_id") or ""),
                order_id=str(fill.get("order_id") or ""),
                run_id=self.manifest["run_id"],
                trading_date=self.trading_date,
                symbol=str(fill.get("symbol") or ""),
                side=str(fill.get("side") or ""),
                quantity=int(fill.get("quantity") or 0),
                price=float(fill.get("price") or 0.0),
                fee=float(fill.get("fee") or 0.0),
                tax=float(fill.get("tax") or 0.0),
                filled_at=str(fill.get("filled_at") or self.trading_date),
                note=str(fill.get("note") or ""),
            )

    def _run_retrain_step(self) -> None:
        payload = {
            "artifact_type": "training_summary",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "model_name": "stub_lgbm",
            "model_path": str(self._step_root("03_retrain") / "model_stub.txt"),
            "metrics": {
                "loss": 0.182,
                "rank_ic": 0.041,
                "auc": 0.613,
                "sample_coverage": 0.98,
            },
        }
        model_path = Path(payload["model_path"])
        model_path.write_text("stub model artifact\n", encoding="utf-8")
        artifact_path = self._write_json_artifact(
            step_name="03_retrain",
            filename="training_summary.json",
            payload=payload,
        )
        self.register_artifact("training_summary", artifact_path, step_name="03_retrain")
        self.register_artifact("model_artifact", model_path, step_name="03_retrain")

    def _run_inference_step(self) -> None:
        predictions = {
            "artifact_type": "inference_predictions",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "predictions": [
                {
                    "symbol": "600000.SH",
                    "model_score": 0.81,
                    "score_rank": 1,
                    "reference_price": 10.5,
                    "weight_hint": 0.55,
                },
                {
                    "symbol": "000001.SZ",
                    "model_score": 0.74,
                    "score_rank": 2,
                    "reference_price": 12.3,
                    "weight_hint": 0.45,
                },
            ],
        }
        artifact_path = self._write_json_artifact(
            step_name="04_inference",
            filename="predictions.json",
            payload=predictions,
        )
        self.register_artifact("inference_predictions", artifact_path, step_name="04_inference")

    def _run_portfolio_step(self) -> None:
        inference_payload = self.load_artifact("inference_predictions")
        positions: list[dict[str, Any]] = []
        plan_rows: list[dict[str, Any]] = []
        for item in inference_payload.get("predictions") or []:
            planned_shares = 500 if item.get("score_rank") == 1 else 300
            weight = float(item.get("weight_hint") or 0.0)
            reference_price = float(item.get("reference_price") or 0.0)
            plan_rows.append(
                {
                    "symbol": item["symbol"],
                    "model_score": float(item.get("model_score") or 0.0),
                    "score_rank": int(item.get("score_rank") or 0),
                    "weight": weight,
                    "planned_shares": planned_shares,
                    "reference_price": reference_price,
                }
            )
            positions.append(
                {
                    "symbol": item["symbol"],
                    "quantity": planned_shares,
                    "sellable_quantity": 0,
                    "price": reference_price,
                    "avg_cost": reference_price,
                    "market_value": planned_shares * reference_price,
                    "account_name": "model_shadow",
                }
            )

        payload = {
            "artifact_type": "portfolio_plan",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "positions": plan_rows,
        }
        artifact_path = self._write_json_artifact(
            step_name="05_portfolio",
            filename="portfolio.json",
            payload=payload,
        )
        self.register_artifact("portfolio_plan", artifact_path, step_name="05_portfolio")
        self.ledger.replace_position_snapshot(
            run_id=self.manifest["run_id"],
            trading_date=self.trading_date,
            snapshot_type="model_shadow",
            positions=positions,
            account_name="model_shadow",
        )

    def _run_order_staging_step(self) -> None:
        portfolio_payload = self.load_artifact("portfolio_plan")
        staged_orders: list[dict[str, Any]] = []
        for index, item in enumerate(portfolio_payload.get("positions") or [], start=1):
            order_id = f"{self.manifest['run_id']}-order-{index:03d}"
            order = {
                "order_id": order_id,
                "run_id": self.manifest["run_id"],
                "trading_date": self.trading_date,
                "symbol": item["symbol"],
                "side": "buy",
                "quantity": int(item.get("planned_shares") or 0),
                "price": float(item.get("reference_price") or 0.0),
                "status": "staged",
            }
            staged_orders.append(order)
            self.ledger.upsert_order(
                order_id=order_id,
                run_id=self.manifest["run_id"],
                trading_date=self.trading_date,
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                price=order["price"],
                status=order["status"],
                account_name="real",
                note="staged_by_runner",
            )

        payload = {
            "artifact_type": "order_staging",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "orders": staged_orders,
        }
        artifact_path = self._write_json_artifact(
            step_name="06_order_staging",
            filename="staged_orders.json",
            payload=payload,
        )
        self.register_artifact("staged_orders", artifact_path, step_name="06_order_staging")

    def _run_reconcile_step(self) -> None:
        staged_orders_payload = self.load_artifact("staged_orders")
        order_count = self.ledger.count_orders(run_id=self.manifest["run_id"])
        fill_count = self.ledger.count_fills(run_id=self.manifest["run_id"], trading_date=self.trading_date)
        fill_rate = round(fill_count / order_count, 4) if order_count else 0.0

        exec_shadow_positions = []
        for order in staged_orders_payload.get("orders") or []:
            exec_shadow_positions.append(
                {
                    "symbol": order["symbol"],
                    "quantity": order["quantity"],
                    "sellable_quantity": 0,
                    "price": order["price"],
                    "avg_cost": order["price"],
                    "market_value": order["quantity"] * order["price"],
                    "account_name": "exec_shadow",
                }
            )
        self.ledger.replace_position_snapshot(
            run_id=self.manifest["run_id"],
            trading_date=self.trading_date,
            snapshot_type="exec_shadow",
            positions=exec_shadow_positions,
            account_name="exec_shadow",
        )

        metrics = {
            "daily_return": 0.0032,
            "turnover": 0.126,
            "fill_rate": fill_rate,
            "order_count": order_count,
            "fill_count": fill_count,
        }
        self.ledger.upsert_daily_metrics(
            run_id=self.manifest["run_id"],
            trading_date=self.trading_date,
            daily_return=metrics["daily_return"],
            turnover=metrics["turnover"],
            fill_rate=metrics["fill_rate"],
            details=metrics,
        )
        payload = {
            "artifact_type": "reconcile_summary",
            "trading_date": self.trading_date,
            "run_id": self.manifest["run_id"],
            "metrics": metrics,
        }
        artifact_path = self._write_json_artifact(
            step_name="07_reconcile",
            filename="reconcile_summary.json",
            payload=payload,
        )
        self.register_artifact("reconcile_summary", artifact_path, step_name="07_reconcile")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the minimal Qsys production kernel")
    parser.add_argument("--date", required=True, help="Trading date, for example 2026-04-07")
    parser.add_argument("--from-step", dest="from_step", choices=STEP_SEQUENCE, help="Resume from a step")
    parser.add_argument("--force", action="store_true", help="Force re-run steps even if they already succeeded")
    parser.add_argument("--runs-root", default="runs", help="Root directory for run artifacts")
    parser.add_argument("--db-path", default="data/trade.db", help="SQLite ledger path")
    parser.add_argument("--recipe-version", default="minimal-production-kernel", help="Recipe version written into the manifest")
    parser.add_argument("--data-version-hash", default=None, help="Explicit data version hash")
    parser.add_argument("--broker-readback", default=None, help="Optional readback JSON for the broker gateway")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    runner = QsysRunner(
        trading_date=args.date,
        recipe_version=args.recipe_version,
        data_version_hash=args.data_version_hash,
        runs_root=args.runs_root,
        db_path=args.db_path,
        broker_gateway=BrokerGateway(readback_path=args.broker_readback) if args.broker_readback else BrokerGateway(),
    )
    return runner.run(from_step=args.from_step, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
