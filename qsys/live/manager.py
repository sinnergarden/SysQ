from __future__ import annotations

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter
from qsys.live.account import RealAccount
from qsys.live.reconciliation import export_plan_bundle
from qsys.strategy.engine import StrategyEngine
from qsys.trader.plan import PlanGenerator
from qsys.utils.logger import log


class LiveManager:
    """
    Orchestrates the Daily Live Trading Process.

    signal_date: the market data date used to generate signals.
    execution_date: the date the human/shadow account is expected to execute the plan.
    """

    def __init__(self, model_path, db_path="data/real_account.db", output_dir="data"):
        self.real_account = RealAccount(db_path)
        self.strategy = StrategyEngine(top_k=30)
        self.planner = PlanGenerator()
        self.model_path = model_path
        self.model = None
        self.output_dir = output_dir

    def load_model(self):
        if self.model is None:
            from qsys.strategy.generator import SignalGenerator

            self.model = SignalGenerator(self.model_path)
            log.info(f"Model loaded from {self.model_path}")

    @staticmethod
    def _normalize_score_series(scores: pd.Series | pd.DataFrame) -> pd.Series:
        if isinstance(scores, pd.DataFrame):
            if "score" in scores.columns:
                scores = scores["score"]
            else:
                scores = scores.iloc[:, 0]
        if isinstance(scores.index, pd.MultiIndex):
            if scores.index.nlevels >= 2:
                scores.index = scores.index.get_level_values(0)
        return pd.Series(scores).groupby(level=0).last().sort_values(ascending=False)

    @staticmethod
    def _normalize_price_lookup(prices_df: pd.DataFrame) -> dict:
        normalized = prices_df.copy()
        if isinstance(normalized.index, pd.MultiIndex):
            normalized.index = normalized.index.get_level_values(0)
        normalized = normalized.groupby(level=0).last()
        return normalized["close"].to_dict()

    def run_daily_plan(self, date, market_data=None, account_name="real", execution_date=None):
        self.load_model()
        if self.model is None:
            log.error("Model not loaded.")
            return None

        signal_date = pd.Timestamp(date).strftime("%Y-%m-%d")
        execution_date = pd.Timestamp(execution_date or date).strftime("%Y-%m-%d")

        state = self.real_account.get_state(signal_date, account_name=account_name)
        if state is None:
            latest_date = self.real_account.get_latest_date(account_name=account_name, before_date=signal_date)
            if not latest_date:
                log.error("No account state date found! Please sync broker state first.")
                return None
            state = self.real_account.get_state(latest_date, account_name=account_name)
        if not state:
            log.error("No account state found! Please sync broker state first.")
            return None

        log.info(f"Generating plan using signal_date={signal_date}, execution_date={execution_date}")
        log.info(f"Using account state from {state['date']}")
        log.info(f"Total Assets: {state['total_assets']:,.2f}, Cash: {state['cash']:,.2f}")

        try:
            instruments = D.instruments("csi300")
            if market_data is None:
                features = QlibAdapter().get_features(
                    instruments,
                    self.model.model.feature_config,
                    start_time=signal_date,
                    end_time=signal_date,
                )
            else:
                features = market_data

            if features is None or features.empty:
                log.error(f"No features found for signal_date={signal_date}!")
                return None

            raw_scores = self.model.predict(features)
            scores = self._normalize_score_series(raw_scores)
            ranked_scores = scores.reset_index()
            ranked_scores.columns = ["symbol", "score"]
            ranked_scores["score_rank"] = ranked_scores["score"].rank(ascending=False, method="first").astype(int)

            price_fields = ["$close", "$factor"]
            prices_df = QlibAdapter().get_features(
                instruments,
                price_fields,
                start_time=signal_date,
                end_time=signal_date,
            )
            prices_df = prices_df.rename(columns={"$close": "close", "$factor": "factor"})

            current_prices = self._normalize_price_lookup(prices_df)
            target_weights = self.strategy.generate_target_weights(scores, market_status=None)

            current_positions = state["positions"]
            total_assets = state["total_assets"]
            plan_df = self.planner.generate_plan(
                target_weights,
                current_positions,
                total_assets,
                current_prices,
                score_lookup=ranked_scores.set_index("symbol")["score"].to_dict(),
                score_rank_lookup=ranked_scores.set_index("symbol")["score_rank"].to_dict(),
                weight_method=self.strategy.method,
            )

            outputs = export_plan_bundle(
                plan_df,
                output_dir=self.output_dir,
                signal_date=signal_date,
                plan_date=signal_date,
                account_name=account_name,
                execution_date=execution_date,
            )
            if plan_df is None:
                return None
            plan_df = plan_df.copy()
            plan_df["account_name"] = account_name
            plan_df["signal_date"] = signal_date
            plan_df["plan_date"] = signal_date
            plan_df["execution_date"] = execution_date
            plan_df["price_basis_date"] = signal_date
            plan_df["price_basis_field"] = "close"
            plan_df["price_basis_label"] = f"close@{signal_date} -> next-session execution plan"
            plan_df["plan_role"] = "target_portfolio_delta"
            log.info(f"Plan saved to {outputs['plan']}")
            log.info(f"Real sync template saved to {outputs['real_sync_template']}")
            log.info(f"\n{self.planner.to_markdown(plan_df)}")
            return plan_df
        except Exception as e:
            log.error(f"Failed to run live plan: {e}")
            import traceback
            traceback.print_exc()
            return None
