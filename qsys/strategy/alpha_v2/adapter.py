"""AlphaV2StrategyAdapter — rule-based momentum smoke strategy.

This is a **framework compatibility test**, not a profitable strategy.
It exists to prove the strategy-agnostic framework (``StrategyCandidate``
protocol + ``DailyRunner``) supports a second strategy with different
prediction logic, config, account_id, and paths.

Design decisions
----------------
- No LightGBM, no dual 5d/20d model, no zscore blend.
- Momentum signal: ``close_today / close_{lookback_days} - 1``.
- ``build_plan`` uses ``build_plan_from_predictions`` (``qsys.ops.plan_builder``),
  the public composite API for plan construction.
- ``execute_plan`` uses ``execute_shadow_plan`` (``qsys.ops.shadow_execution``)
  with a generic ``run_id`` derived from the strategy and execution date.
- No ``run_alpha_v2_daily.py`` — only ``run_daily.py --strategy alpha_v2``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.model.training import TrainingResult
from qsys.ops.notify_format import (
    format_postclose_message,
    format_preopen_message,
)
from qsys.strategy.runtime_base import BaseStrategyAdapter


class AlphaV2StrategyAdapter(BaseStrategyAdapter):
    """Rule-based momentum strategy for framework validation."""

    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__()
        self._project_root = project_root or Path(__file__).resolve().parents[3]

        # Config overrides (set by from_config)
        self._config_display_name: str | None = None
        self._config_predictions_dir: Path | None = None
        self._config_ledger_db_path: str | None = None

        # Portfolio params (config-driven, no frozen spec)
        self._top_n = 10
        self._buffer_hold = 30
        self._buffer_buy = 20
        self._single_stock_cap = 0.10
        self._rebalance_freq = "weekly"

        # Training params
        self._lookback_days = 20

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: dict | None = None,
        project_root: Path | None = None,
    ) -> "AlphaV2StrategyAdapter":
        """Construct from YAML config dict."""
        self = cls(project_root=project_root)
        cfg = config or {}
        self._config_display_name = cfg.get("display_name")

        # Path overrides
        paths = cfg.get("paths", {})
        pr = self._project_root

        raw_model_dir = paths.get("model_dir")
        if raw_model_dir:
            raise ValueError(
                "paths.model_dir is forbidden for candidate/shadow runtime"
            )

        raw_pred_dir = paths.get("predictions_dir")
        if raw_pred_dir:
            p = Path(raw_pred_dir)
            self._config_predictions_dir = p if p.is_absolute() else pr / raw_pred_dir

        raw_ledger = paths.get("ledger_db")
        if raw_ledger:
            p = Path(raw_ledger)
            self._config_ledger_db_path = str(p if p.is_absolute() else pr / raw_ledger)

        # Portfolio params
        portfolio = cfg.get("portfolio", {})
        self._top_n = portfolio.get("top_n", 10)
        self._buffer_hold = portfolio.get("buffer_hold", 30)
        self._buffer_buy = portfolio.get("buffer_buy", 20)
        self._single_stock_cap = portfolio.get("single_stock_cap", 0.10)
        self._rebalance_freq = portfolio.get("rebalance_freq", "weekly")

        # Training params
        training = cfg.get("training", {})
        self._lookback_days = training.get("lookback_days", 20)

        return self

    # ── Derived paths ──────────────────────────────────────────────────

    @property
    def _model_dir(self) -> Path:
        # Alpha V2 is a rule-based smoke adapter and has no learned model.
        # Keep its local metadata in an explicit versioned directory; it must
        # never participate in approved model-pointer resolution.
        return self._project_root / "experiments/alpha_v2_models/rule_based_smoke_v1"

    @property
    def _predictions_dir(self) -> Path:
        if self._config_predictions_dir is not None:
            return self._config_predictions_dir
        return self._project_root / "experiments/alpha_v2_shadow_predictions"

    @property
    def _ledger_db_path(self) -> str:
        if self._config_ledger_db_path is not None:
            return self._config_ledger_db_path
        return str(self._project_root / "data" / "trade.db")

    @property
    def _shadow_base_dir(self) -> Path:
        """Shadow root for alpha_v2 (separate from alpha_v1's ``shadow/``).

        ``execute_alpha_v1_plan`` appends ``/shadow`` to this when reading
        account state, so execution reads from ``shadow_alpha_v2/shadow/``.
        """
        return self._project_root / "shadow_alpha_v2"

    @property
    def _shadow_state_dir(self) -> Path:
        """Actual account state directory under ``_shadow_base_dir``.

        ``execute_alpha_v1_plan`` resolves ``_shadow_base_dir / "shadow"``
        internally, so plan and execution read from the same directory:
        ``shadow_alpha_v2/shadow/``.
        """
        return self._shadow_base_dir / "shadow"

    # ── Identity ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "alpha_v2"

    @property
    def account_id(self) -> str:
        return "shadow_alpha_v2"

    @property
    def display_name(self) -> str:
        return self._config_display_name or "Alpha V2 Smoke"

    # ── Configuration ──────────────────────────────────────────────────

    @property
    def universe(self) -> str:
        return "csi300"

    @property
    def feature_set(self) -> str:
        return "alpha_v2_smoke"

    @property
    def model_version(self) -> str:
        return "alpha_v2_smoke_202606"

    @property
    def signal_version(self) -> str:
        return "momentum_20d_rank"

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        return {
            "top_n": self._top_n,
            "buffer_hold": self._buffer_hold,
            "buffer_buy": self._buffer_buy,
            "rebalance_freq": self._rebalance_freq,
            "single_stock_cap": self._single_stock_cap,
        }

    # ── Data ────────────────────────────────────────────────────────────

    def load_model(self) -> None:
        """Rule-based momentum — no ML model to load."""
        print(f"  Alpha V2 Smoke: rule-based momentum (lookback={self._lookback_days}d)")
        # Ensure model_dir exists for convention (no actual model files)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Model dir: {self._model_dir}")

    def fetch_data(self, data_date: str) -> Any:
        """Fetch close-price history for momentum computation.

        Returns a DataFrame with columns ``[trade_date, instrument, $close]``
        covering ``[data_date - lookback_days, data_date]``.
        """
        from qlib.data import D as qlib_D

        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        adapter.init_qlib()

        cal = qlib_D.calendar(start_time="2020-01-01", end_time=data_date)
        if cal is None or len(cal) == 0:
            print(f"  ⚠ No qlib calendar data up to {data_date}")
            return pd.DataFrame()

        lookback = min(self._lookback_days + 1, len(cal))
        start_date = pd.Timestamp(cal[-lookback]).strftime("%Y-%m-%d")

        raw = adapter.get_features(
            self.universe,
            ["$close"],
            start_time=start_date,
            end_time=data_date,
        )
        if raw is None or raw.empty:
            print(f"  ⚠ No close data for {start_date} ~ {data_date}")
            return pd.DataFrame()

        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        print(f"  {self.universe}: {len(frame)} rows ({start_date} ~ {data_date})")
        return frame

    # ── Predict + Plan ──────────────────────────────────────────────────

    def generate_predictions(self, data: Any) -> Any:
        """Compute 20-day momentum from close-price history.

        Score = close[t] / close[t-N] - 1, ranked descending.
        """
        df: pd.DataFrame = data
        if df is None or df.empty:
            raise ValueError("No data for prediction")

        # Sort by instrument + trade_date
        df = df.sort_values(["instrument", "trade_date"])

        # Compute momentum per instrument: close[first] vs close[last]
        first_close = df.groupby("instrument")["$close"].transform("first")
        last_close = df.groupby("instrument")["$close"].transform("last")
        # Take one row per instrument
        deduped = df.drop_duplicates(subset=["instrument"]).copy()
        deduped["score"] = 0.0
        for idx, row in deduped.iterrows():
            inst = row["instrument"]
            mask = df["instrument"] == inst
            closes = df.loc[mask, "$close"].values
            if len(closes) >= 2 and float(closes[0]) > 0 and float(closes[-1]) > 0:
                deduped.loc[idx, "score"] = float(closes[-1]) / float(closes[0]) - 1.0

        trade_date = df["trade_date"].iloc[-1]
        if isinstance(trade_date, pd.Timestamp):
            trade_date = trade_date.strftime("%Y-%m-%d")

        rows = []
        for _, row in deduped.iterrows():
            rows.append({
                "trade_date": trade_date,
                "instrument": str(row["instrument"]),
                "score": float(row["score"]),
                "model_name": "alpha_v2_smoke_momentum",
                "mainline_object_name": "alpha_v2_smoke",
            })
        result = pd.DataFrame(rows)
        print(f"  Predictions: {len(result)} instruments")
        return result

    def should_rebalance(self, trade_date: str) -> bool:
        """Weekly rebalance check — independent implementation."""
        if self._rebalance_freq != "weekly":
            return True
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()

        last_trade_date: str | None = None
        if Path(self._ledger_db_path).exists():
            try:
                from qsys.ledger.service import LedgerService

                service = LedgerService(self._ledger_db_path)
                last_trade_date = service.get_latest_trade_date(self.account_id)
                service.close()
            except Exception:
                pass
        if not last_trade_date:
            shadow_account_path = self._shadow_state_dir / "account.json"
            if shadow_account_path.exists():
                try:
                    acct = json.loads(shadow_account_path.read_text())
                    last_trade_date = acct.get("trade_date", "")
                except (json.JSONDecodeError, OSError):
                    pass
        if last_trade_date:
            last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
            cur = trade_dt.isocalendar()
            last = last_dt.isocalendar()
            if last[0] == cur[0] and last[1] == cur[1]:
                return False
        return True

    # ── Execute + MTM ──────────────────────────────────────────────────

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        """Build trading plan using config-driven portfolio parameters."""
        from qsys.backtest.portfolio import build_rank_weight_portfolio
        from qsys.ops.plan_builder import build_plan_from_predictions

        build_plan_from_predictions(
            shadow_dir=self._shadow_state_dir,
            trade_date=str(predictions["trade_date"].iloc[0]),
            predictions=predictions,
            output_dir=Path(target_dir).parent,
            portfolio_fn=build_rank_weight_portfolio,
            top_n=self._top_n,
            buffer_hold=self._buffer_hold,
            buffer_buy=self._buffer_buy,
            single_stock_cap=self._single_stock_cap,
            strategy_id=self.strategy_id,
            strategy_version=self.model_version,
            portfolio_method="equal_weight_momentum",
        )
        return True

    def execute_plan(self, context: Any) -> Any:
        """Execute plan via ``execute_shadow_plan`` with generic run_id."""
        from qsys.ops.shadow_execution import execute_shadow_plan

        plan_dir = context.run_root / "plan"
        staging_exec_dir = context.run_root / "execution" / "staging"

        artifacts = execute_shadow_plan(
            base_dir=str(self._shadow_base_dir),
            plan_dir=str(plan_dir),
            execution_date=context.trade_date,
            output_dir=str(staging_exec_dir),
            debug_run=context.debug_run,
            db_path=self._ledger_db_path if not context.debug_run else None,
        )
        return artifacts

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        from qsys.ops.shadow_execution import commit_execution_artifacts

        commit_execution_artifacts(
            run_root=context.run_root,
            staging_dir=staging_dir,
            db_path=self._ledger_db_path,
            trade_date=context.trade_date,
            strategy_id=self.strategy_id,
            debug_run=context.debug_run,
        )

    def mark_to_market(self, context: Any) -> dict | None:
        from qsys.ops.mtm import try_mark_to_market

        staging_exec_dir = context.run_root / "execution" / "staging"

        if context.debug_run:
            mtm = try_mark_to_market(
                context.trade_date,
                output_dir=context.run_root,
                account_path=staging_exec_dir / "account_after.json"
                if (staging_exec_dir / "account_after.json").exists() else None,
                positions_path=staging_exec_dir / "positions_after.csv"
                if (staging_exec_dir / "positions_after.csv").exists() else None,
                project_root=context.project_root,
                shadow_account_id=self.account_id,
                get_stock_name_fn=self.get_stock_name,
            )
        else:
            mtm = try_mark_to_market(
                context.trade_date,
                output_dir=context.run_root,
                account_path=self._shadow_state_dir / "account.json",
                positions_path=self._shadow_state_dir / "positions.csv",
                db_path=self._ledger_db_path,
                project_root=context.project_root,
                shadow_account_id=self.account_id,
                get_stock_name_fn=self.get_stock_name,
            )
        return mtm

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        from types import SimpleNamespace

        exec_dir = context.run_root / "execution"
        summary_path = exec_dir / "execution_summary.json"
        if not summary_path.exists():
            return None
        try:
            summary = json.loads(summary_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return SimpleNamespace(
            trade_date=context.trade_date,
            order_count=summary.get("order_count", 0),
            filled_count=summary.get("filled_count", 0),
            rejected_count=summary.get("rejected_count", 0),
            turnover=summary.get("turnover", 0.0),
            cash_after=summary.get("cash_after", 0.0),
            total_value_after=summary.get("total_value_after", 0.0),
        )

    # ── Notifications ──────────────────────────────────────────────────

    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any,
    ) -> str:
        predictions = pd.DataFrame(predictions)
        return format_preopen_message(
            display_name=self.display_name,
            trade_date=context.trade_date,
            predictions_df=predictions,
            plan_dir=Path(context.run_root) / "plan",
            rebalance_skipped=rebalance_skipped,
            universe=self.universe,
            prediction_count=len(predictions),
            rebalance_freq=self._rebalance_freq,
            get_stock_name=self.get_stock_name,
        )

    def build_postclose_message(
        self,
        context: Any,
        mtm: dict | None = None,
        artifacts: Any = None,
        stale_check: dict | None = None,
        execution_committed: bool = False,
        execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        self.get_stock_name("")  # ensure cache loaded
        return format_postclose_message(
            display_name=self.display_name,
            trade_date=context.trade_date,
            debug_run=context.debug_run,
            execution_committed=execution_committed,
            execution_skipped=execution_skipped,
            idempotent_skip=idempotent_skip,
            stale_check=stale_check,
            artifacts=artifacts,
            mtm=mtm,
            get_stock_name=self.get_stock_name,
        )

    # ── Training ──────────────────────────────────────────────────────────

    def train(self, context: Any) -> Any:
        """Rule-based momentum: no model training required."""
        print(f"  Alpha V2 Smoke: no training required (rule-based momentum)")
        return TrainingResult(
            strategy_id=self.strategy_id,
            model_version=self.model_version,
            model_dir=str(self._model_dir),
            status="success",
            metrics={},
            artifacts={},
            message="rule_based_momentum: no training required",
        )
