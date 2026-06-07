"""Ops API repository — read-only access to daily ops artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.ops_api.schema import DailyOpsSummary, SignalBasketRow, PortfolioSnapshot


class OpsRepository:
    """Read-only data access for daily ops artifacts.

    All methods are read-only; no writes to ledger, state, or strategy config.
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        if project_root is None:
            from pathlib import Path as _Path
            project_root = _Path(__file__).resolve().parents[2]
        self.root = Path(project_root)

    # ── Daily summary ──────────────────────────────────────────────────

    def get_daily_summary(self, date: str) -> DailyOpsSummary | None:
        """Assemble a structured summary for *date* from on-disk artifacts."""
        summary = DailyOpsSummary(execution_date=date)

        # Pre-open manifest
        manifest = self._read_json(
            self.root / "daily" / date / "pre_open" / "manifests",
            pattern="daily_ops_manifest_*.json",
        )
        if manifest:
            summary.pre_open_status = manifest.get("stages", {}).get("pre_open", {}).get("status")
            summary.artifact_paths["pre_open_manifest"] = str(
                list((self.root / "daily" / date / "pre_open" / "manifests").glob("daily_ops_manifest_*.json"))[0]
            )

        # Signal basket
        signal_dir = self.root / "daily" / date / "pre_open" / "signals"
        if signal_dir.exists():
            baskets = list(signal_dir.glob("signal_basket_*.csv"))
            if baskets:
                df = pd.read_csv(baskets[0])
                summary.signal_count = len(df)
                summary.artifact_paths["signal_basket"] = str(baskets[0])

        # Post-close reconciliation
        rec_dir = self.root / "daily" / date / "post_close"
        rec_result = rec_dir / "reconciliation_result.json"
        if rec_result.exists():
            rec_data = self._safe_load_json(rec_result)
            summary.reconciliation_status = rec_data.get("status")
            summary.post_close_status = "completed"
        else:
            # Try run_root path
            run_candidates = list((self.root / "experiments").glob(f"*_daily/{date}/reconciliation/reconciliation_result.json"))
            if run_candidates:
                rec_data = self._safe_load_json(run_candidates[0])
                summary.reconciliation_status = rec_data.get("status")
                summary.post_close_status = "completed"
            else:
                summary.post_close_status = "not_found"

        # Digest
        digest = self._read_json(
            self.root / "daily" / date / "post_close",
            pattern="daily_ops_digest_*.json",
        )
        if digest:
            summary.overall_status = digest.get("status") or digest.get("overall_status")

        return summary

    # ── Signal basket ──────────────────────────────────────────────────

    def get_signal_basket(self, date: str, top_n: int | None = None) -> list[SignalBasketRow] | None:
        """Return signal basket rows for *date*, optionally limited to *top_n*."""
        signal_dir = self.root / "daily" / date / "pre_open" / "signals"
        if not signal_dir.exists():
            return None
        baskets = list(signal_dir.glob("signal_basket_*.csv"))
        if not baskets:
            return None
        df = pd.read_csv(baskets[0])
        if top_n and top_n < len(df):
            df = df.head(top_n)
        return [SignalBasketRow.from_csv_row(row) for _, row in df.iterrows()]

    # ── Portfolio snapshot ─────────────────────────────────────────────

    def get_portfolio(self, account_id: str, date: str) -> PortfolioSnapshot | None:
        """Read portfolio snapshot from LedgerService for *account_id* on *date*."""
        try:
            from qsys.ledger.service import LedgerService
        except ImportError:
            return None

        db_path = self.root / "data" / "trade.db"
        if not db_path.exists():
            return None

        svc = LedgerService(db_path)
        try:
            snapshot = svc.get_portfolio_snapshot(account_id, date)
            if snapshot is None:
                return None

            positions = svc.get_positions(account_id)
            pos_list = []
            for p in positions:
                if int(p.get("quantity", 0)) > 0:
                    pos_list.append({
                        "symbol": p.get("symbol", ""),
                        "quantity": int(p.get("quantity", 0)),
                        "available_quantity": int(p.get("available_quantity", 0)),
                        "avg_cost": float(p.get("avg_cost", 0.0)),
                        "last_price": float(p.get("last_price", 0.0)),
                        "market_value": float(p.get("market_value", 0.0)),
                        "unrealized_pnl": float(p.get("unrealized_pnl", 0.0)) if p.get("unrealized_pnl") else None,
                    })

            return PortfolioSnapshot(
                account_id=account_id,
                trade_date=date,
                cash=float(snapshot.get("cash", 0.0)),
                total_market_value=float(snapshot.get("total_market_value", 0.0)),
                total_asset=float(snapshot.get("total_asset", 0.0)),
                daily_pnl=float(snapshot["daily_pnl"]) if snapshot.get("daily_pnl") is not None else None,
                daily_return=float(snapshot["daily_return"]) if snapshot.get("daily_return") is not None else None,
                position_count=len(pos_list),
                positions=pos_list,
            )
        finally:
            svc.close()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _safe_load_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _read_json(directory: Path, pattern: str) -> dict[str, Any] | None:
        if not directory.exists():
            return None
        matches = list(directory.glob(pattern))
        if not matches:
            return None
        try:
            return json.loads(matches[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
