"""AlphaV2StrategyAdapter — rule-based momentum smoke strategy.

This is a **framework compatibility test**, not a profitable strategy.
It exists to prove the strategy-agnostic framework (``StrategyCandidate``
protocol + ``DailyRunner``) supports a second strategy with different
prediction logic, config, account_id, and paths.

Design decisions
----------------
- No LightGBM, no dual 5d/20d model, no zscore blend.
- Momentum signal: ``close_today / close_{lookback_days} - 1``.
- ``build_plan`` reuses private helpers from ``qsys.ops.shadow_rebalance``
  (``_build_target_weights``, ``_build_order_intents``, ``_fetch_market_snapshot``,
  ``_load_shadow_account``).  These are strategy-agnostic implementation
  details — they accept all parameters explicitly.
- ``execute_plan`` reuses ``execute_alpha_v1_plan`` which is structurally
  generic over ``plan_dir`` (reads ``strategy_id``/``version`` from
  ``plan_meta.json``).  The cosmetic ``alpha_v1_execute_`` run_id prefix
  in staging metadata is a known cosmetic issue — it does not affect
  correctness (the ledger commit uses a generic ``run_id`` via
  ``_write_execution_to_ledger``).  A follow-up should extract a generic
  execution helper from ``execute_alpha_v1_plan``.
- No ``run_alpha_v2_daily.py`` — only ``run_daily.py --strategy alpha_v2``.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.model.training import TrainingResult


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt(amount: float) -> str:
    return f"¥{amount / 1000:.2f}k"


class AlphaV2StrategyAdapter:
    """Rule-based momentum strategy for framework validation."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path(__file__).resolve().parents[3]
        self._stock_names: dict[str, str] = {}
        self._stock_names_loaded = False

        # Config overrides (set by from_config)
        self._config_display_name: str | None = None
        self._config_model_dir: Path | None = None
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
            p = Path(raw_model_dir)
            self._config_model_dir = p if p.is_absolute() else pr / raw_model_dir

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
        if self._config_model_dir is not None:
            return self._config_model_dir
        return self._project_root / "experiments/alpha_v2_models/latest"

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

    def resolve_data_date(self, trade_date: str) -> str:
        from qlib.data import D as qlib_D

        from qsys.data.adapter import QlibAdapter

        QlibAdapter().init_qlib()
        cal = qlib_D.calendar(start_time="2020-01-01", end_time=trade_date)
        if cal is None or len(cal) == 0:
            print(f"  ⚠ qlib calendar has no trading day <= {trade_date}")
            return trade_date
        data_date = pd.Timestamp(cal[-1]).strftime("%Y-%m-%d")
        if data_date != trade_date:
            print(f"  ⚠ {trade_date} not a trading day, using {data_date}")
        return data_date

    def get_stock_name(self, ts_code: str) -> str:
        if not self._stock_names_loaded:
            self._load_stock_names()
        return self._stock_names.get(ts_code, ts_code)

    def _load_stock_names(self) -> None:
        path = self._project_root / "data" / "stock_names.csv"
        if path.exists():
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                self._stock_names[str(row["ts_code"])] = str(row["name"])
        self._stock_names_loaded = True

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

    def print_predictions_summary(self, predictions: Any) -> None:
        top = predictions.sort_values("score", ascending=False).head(5)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            print(f"    #{i} {row['instrument']}  score={row['score']:.4f}")

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

    def save_predictions(self, predictions: Any, run_root: Any, trade_date: str) -> None:
        """Save predictions to the alpha_v2 shared predictions directory."""
        shared_dir = self._predictions_dir
        shared_dir.mkdir(parents=True, exist_ok=True)
        path = shared_dir / f"predictions_{trade_date}.csv"
        predictions.to_csv(path, index=False)
        print(f"  → {len(predictions)} predictions saved: {path}")

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        """Build trading plan using config-driven portfolio parameters.

        Reuses strategy-agnostic helpers from ``qsys.ops.shadow_rebalance``
        (``_build_target_weights``, ``_build_order_intents``, etc.).  These
        accept all parameters explicitly and have no alpha_v1 assumptions.
        """
        from qsys.ops.shadow_rebalance import (
            _build_order_intents,
            _build_target_weights,
            _fetch_market_snapshot,
            _load_shadow_account,
            _read_predictions,
        )
        from qsys.utils.json_io import write_json

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Persist predictions so _read_predictions can load them
        pred_path = target_dir / "predictions_for_plan.csv"
        predictions.to_csv(pred_path, index=False)

        trade_date = str(predictions["trade_date"].iloc[0])

        account, prior_account, _ = _load_shadow_account(self._shadow_state_dir)
        instruments = sorted(
            set(predictions["instrument"].astype(str))
            | set(account.positions.keys())
        )
        current_prices, _ = _fetch_market_snapshot(trade_date, instruments)

        from qsys.backtest.portfolio import build_rank_weight_portfolio

        target_weights, target_frame = _build_target_weights(
            predictions, current_prices, account,
            portfolio_fn=build_rank_weight_portfolio,
            top_n=self._top_n,
            buffer_hold=self._buffer_hold,
            buffer_buy=self._buffer_buy,
            single_stock_cap=self._single_stock_cap,
            strategy_id=self.strategy_id,
            strategy_version=self.model_version,
            portfolio_method="equal_weight_momentum",
        )
        orders, order_intents, rebalance_audit, cash_before, mv_before, tv_before = (
            _build_order_intents(
                account, predictions, target_weights, current_prices, trade_date,
            )
        )

        target_frame.to_csv(target_dir / "target_weights.csv", index=False)
        order_intents.to_csv(target_dir / "order_intents.csv", index=False)
        rebalance_audit.to_csv(target_dir / "rebalance_audit.csv", index=False)
        write_json(target_dir / "plan_meta.json", {
            "trade_date": trade_date,
            "reference_date": trade_date,
            "strategy_id": self.strategy_id,
            "strategy_version": self.model_version,
            "portfolio_method": "equal_weight_momentum",
            "top_n": self._top_n,
            "buffer_hold": self._buffer_hold,
            "buffer_buy": self._buffer_buy,
            "single_stock_cap": self._single_stock_cap,
            "cash_before": cash_before,
            "market_value_before": mv_before,
            "total_value_before": tv_before,
            "buy_count": len([o for o in orders if o["side"] == "buy"]),
            "sell_count": len([o for o in orders if o["side"] == "sell"]),
            "total_orders": len(orders),
            "build_ts": datetime.now().isoformat(),
        })

        print(f"  ✅ Plan built: {len(orders)} orders "
              f"({len([o for o in orders if o['side'] == 'buy'])} buy / "
              f"{len([o for o in orders if o['side'] == 'sell'])} sell)")
        return True

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        intents_path = Path(plan_dir) / "order_intents.csv"
        if not intents_path.exists():
            return []
        try:
            df = pd.read_csv(intents_path)
            return sorted(set(df["instrument"].astype(str)))
        except Exception:
            return []

    def fetch_open_prices(self, trade_date: str, instruments: list[str]) -> dict[str, float]:
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        adapter.init_qlib()
        market = adapter.get_features(
            instruments, ["$open"],
            start_time=trade_date, end_time=trade_date,
        )
        if market is None or market.empty:
            return {}
        if isinstance(market.index, pd.MultiIndex):
            market = market.swaplevel().sort_index()
        frame = market.reset_index()
        frame = frame[frame["datetime"].astype(str).str.startswith(trade_date)]
        if frame.empty:
            return {}
        frame = frame.sort_values(["instrument", "datetime"]).drop_duplicates(
            subset=["instrument"], keep="last"
        )
        return frame.set_index("instrument")["$open"].astype(float).to_dict()

    # ── Execute + MTM ──────────────────────────────────────────────────

    def execute_plan(self, context: Any) -> Any:
        """Execute plan via ``execute_alpha_v1_plan``.

        Uses ``self._shadow_base_dir`` so the function resolves
        ``base_dir / "shadow"`` internally, reading from
        ``shadow_alpha_v2/shadow/`` (not alpha_v1's ``shadow/``).
        Plan side (``build_plan``) also uses ``_shadow_state_dir``
        (which IS ``_shadow_base_dir / "shadow"``), so both sides
        read the same account state.
        """
        from qsys.ops.shadow_rebalance import execute_alpha_v1_plan

        plan_dir = context.run_root / "plan"
        staging_exec_dir = context.run_root / "execution" / "staging"

        artifacts = execute_alpha_v1_plan(
            base_dir=str(self._shadow_base_dir),
            plan_dir=str(plan_dir),
            execution_date=context.trade_date,
            output_dir=str(staging_exec_dir),
            debug_run=context.debug_run,
            db_path=self._ledger_db_path if not context.debug_run else None,
        )
        return artifacts

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        from qsys.ops.commit_guard import (
            cleanup_committing,
            committed_marker,
            committing_marker,
        )
        from qsys.ops.shadow_rebalance import _write_execution_to_ledger

        exec_dir = context.run_root / "execution"
        staging_dir = Path(staging_dir)
        exec_dir.mkdir(parents=True, exist_ok=True)

        committing_path = committing_marker(context.run_root)
        if not committing_path.exists():
            print(f"  ❌ COMMITTING marker not found — commit order error")
            sys.exit(1)

        ledger_written = False
        try:
            if not context.debug_run:
                payload_path = staging_dir / "ledger_payload.json"
                if payload_path.exists():
                    payload = json.loads(payload_path.read_text())
                    positions_df = pd.DataFrame()
                    pos_csv = staging_dir / "positions_after.csv"
                    if pos_csv.exists():
                        positions_df = pd.read_csv(pos_csv)

                    summary_path = staging_dir / "execution_summary.json"
                    if summary_path.exists():
                        summary = json.loads(summary_path.read_text())
                        cash_after = summary.get("cash_after", 0.0)
                        market_value_after = summary.get("market_value_after", 0.0)
                        total_value_after = summary.get("total_value_after", 0.0)
                    else:
                        cash_after = market_value_after = total_value_after = 0.0

                    _write_execution_to_ledger(
                        db_path=self._ledger_db_path,
                        execution_date=context.trade_date,
                        strategy_id=self.strategy_id,
                        orders=payload["orders"],
                        ledger_rows=[],
                        results=payload["results"],
                        close_prices=payload["close_prices"],
                        cash_after=cash_after,
                        market_value_after=market_value_after,
                        total_value_after=total_value_after,
                        positions_after=positions_df,
                        initial_capital=payload.get("initial_capital", 1_000_000.0),
                    )
                    ledger_written = True
                else:
                    print(f"  ⚠ ledger_payload.json not found in {staging_dir}")

            for fname in [
                "account_after.json", "positions_after.csv", "execution_summary.json",
                "account_before.json", "positions_before.csv", "ledger_rows.csv",
                "ledger_payload.json",
            ]:
                src = staging_dir / fname
                if src.exists():
                    shutil.copy2(str(src), str(exec_dir / fname))

            committing_path.rename(committed_marker(context.run_root))
            print(f"  ✅ Execution committed (COMMITTED): {exec_dir}")

        except BaseException:
            if ledger_written:
                print(f"  ❌ Ledger written but artifact commit failed — COMMITTING preserved")
            else:
                cleanup_committing(context.run_root)
            raise

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
        top = predictions.sort_values("score", ascending=False).head(5)
        lines = [
            f"✅ {self.display_name} Pre-open {context.trade_date}",
            f"Time: {_now_str()}",
            "",
            "📈 Top Picks (Momentum)",
        ]
        for i, (_, row) in enumerate(top.iterrows(), 1):
            name = self.get_stock_name(row["instrument"])
            lines.append(f"  {i}. {row['instrument']} {name}  score={row['score']:.4f}")
        lines += [
            "",
            f"Strategy: {self.display_name} | Universe: {self.universe}",
            f"Signal: {self.signal_version} | Top {self._top_n}",
        ]
        if rebalance_skipped:
            lines.append("⏭  Weekly rebalance already done — skip")
        return "\n".join(lines)

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
        lines = [
            f"📊 {self.display_name} Post-close {context.trade_date}",
            f"Time: {_now_str()}",
            "",
        ]
        if context.debug_run:
            lines.append("🔧 Debug mode — shadow account unchanged")
            lines.append("")
        if execution_committed and not execution_skipped:
            lines.append("✅ Execution: completed\n")
        elif execution_committed and execution_skipped:
            lines.append("✅ Execution: no plan to execute\n")
        elif context.debug_run:
            lines.append("🔧 Execution: debug mode, not committed\n")
        if artifacts:
            lines.append(f"🏦 Execution Summary (at OPEN)")
            mv = artifacts.total_value_after - artifacts.cash_after
            lines.append(
                f"  Turnover: {_fmt(artifacts.turnover)}  "
                f"Filled: {artifacts.filled_count}/{artifacts.order_count}  "
                f"Total: {_fmt(artifacts.total_value_after)}  "
                f"Cash: {_fmt(artifacts.cash_after)}  MV: {_fmt(mv)}"
            )
            lines.append("")
        if mtm:
            cum = mtm["cumulative_pnl"]
            daily = mtm["daily_pnl"]
            lines.append(f"💰 Mark-to-Market (at CLOSE)")
            lines.append(f"  Cumulative PnL: {_fmt(cum)} ({mtm['cumulative_pnl_pct']:+.2f}%)")
            lines.append(f"  Daily PnL: {_fmt(daily)}")
            lines.append(f"  Total: {_fmt(mtm['total_value'])}  "
                         f"Cash: {_fmt(mtm['cash'])}  "
                         f"Holdings: {mtm.get('priced_count', 0)} stocks")
        else:
            lines.append("⚠ MTM unavailable")
        return "\n".join(lines)

    def send_notification(self, text: str) -> None:
        from qsys.ops.telegram import send_telegram_message

        print(f"\n{'─' * 50}")
        print("📱 Telegram notification:")
        print(text)
        print(f"{'─' * 50}\n")
        result = send_telegram_message(text)
        status = result.get("status", "unknown")
        if status == "skipped":
            print(f"  ⚠ Telegram not configured: {result.get('message', '')}")
        elif status == "failed":
            print(f"  ❌ Telegram send failed: {result.get('error', '')}")
        else:
            print(f"  ✅ Telegram sent")

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
