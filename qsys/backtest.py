from __future__ import annotations

from pathlib import Path

import pandas as pd
from tqdm import tqdm
from qlib.data import D

from qsys.analysis.tearsheet import PerformanceAnalyzer
from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.evaluation.signal_metrics import compute_group_returns, compute_signal_metrics
from qsys.research.signal import to_signal_frame
from qsys.strategy.engine import DEFAULT_TOP_K, StrategyEngine
from qsys.strategy.generator import SignalGenerator
from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine
from qsys.utils.logger import log


class BacktestEngine:
    def __init__(
        self,
        model_path=None,
        universe="csi300",
        start_date="2022-01-01",
        end_date="2022-12-31",
        account=None,
        daily_predictions=None,
        top_k=DEFAULT_TOP_K,
        n_drop=0,
        strategy_type="rank_topk",
        label_horizon="1d_fixed_in_v1_impl1",
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.universe = universe
        self.signal_gen = SignalGenerator(model_path) if model_path else None
        self.daily_predictions = daily_predictions
        self.strategy = StrategyEngine(top_k=top_k, method="equal_weight", strategy_type=strategy_type)
        self.account = account if account else Account(init_cash=1_000_000)
        self.order_gen = OrderGenerator()
        self.matcher = MatchEngine()
        self.trade_dates = []
        self.last_trades = pd.DataFrame()
        self.last_summary = None
        self.last_signal_metrics = {}
        self.last_group_returns = pd.DataFrame()
        self.label_horizon = label_horizon

    def prepare(self):
        log.info("Preparing Backtest...")
        QlibAdapter().init_qlib()
        cal = D.calendar(start_time=self.start_date, end_time=self.end_date)
        self.trade_dates = [pd.Timestamp(x).strftime("%Y-%m-%d") for x in cal]
        log.info(f"Backtest Range: {self.start_date} to {self.end_date}, Total Days: {len(self.trade_dates)}")

    def validate_data(self, df, name="Data"):
        if df.empty:
            log.error(f"{name} is empty!")
            return False
        nan_count = df.isna().sum().sum()
        if nan_count > 0:
            log.warning(f"{name} contains {nan_count} NaNs. Filling with 0/ffill...")
            df.ffill(inplace=True)
            df.fillna(0, inplace=True)
        return True

    def run(self):
        self.prepare()
        log.info("Phase 1: Batch Data Fetching & Inference (Pre-loading)...")
        instruments = D.instruments(self.universe)

        all_scores = pd.DataFrame()
        if self.daily_predictions is not None:
            log.info("Using provided daily predictions...")
            all_scores = self.daily_predictions
        else:
            log.info(f"Fetching features for {self.start_date} - {self.end_date}...")
            if not self.signal_gen:
                log.error("No predictions provided and no model loaded!")
                return pd.DataFrame()

            try:
                all_features = QlibAdapter().get_features(
                    instruments=instruments,
                    fields=self.signal_gen.model.feature_config,
                    start_time=self.start_date,
                    end_time=self.end_date,
                )
            except Exception as e:
                log.error(f"Failed to fetch batch features: {e}")
                return pd.DataFrame()

            if all_features is None or all_features.empty:
                log.warning("No features found for the specified range.")
                return pd.DataFrame()

            self.validate_data(all_features, "Features")
            log.info("Running Batch Prediction...")
            all_scores = self.signal_gen.predict(all_features)

        if not all_scores.empty and all_scores.index.names == ["instrument", "datetime"]:
            all_scores = all_scores.swaplevel().sort_index()

        log.info("Fetching Market Data...")
        price_fields = ["$close", "$open", "$factor", "$paused", "$high_limit", "$low_limit"]
        all_market_data = QlibAdapter().get_features(
            instruments,
            price_fields,
            start_time=self.start_date,
            end_time=self.end_date,
        )
        all_market_data.columns = ["close", "open", "factor", "is_suspended", "limit_up", "limit_down"]
        self.validate_data(all_market_data, "Market Data")
        all_market_data["is_suspended"] = all_market_data["is_suspended"].fillna(0).astype(bool)
        all_market_data["is_limit_up"] = False
        all_market_data["is_limit_down"] = False

        mask_valid_limit = all_market_data["limit_up"] > 0.01
        if mask_valid_limit.any():
            all_market_data.loc[mask_valid_limit, "is_limit_up"] = (
                all_market_data.loc[mask_valid_limit, "close"] >= all_market_data.loc[mask_valid_limit, "limit_up"]
            )
        mask_valid_down = all_market_data["limit_down"] > 0.01
        if mask_valid_down.any():
            all_market_data.loc[mask_valid_down, "is_limit_down"] = (
                all_market_data.loc[mask_valid_down, "close"] <= all_market_data.loc[mask_valid_down, "limit_down"]
            )
        if all_market_data.index.names == ["instrument", "datetime"]:
            all_market_data = all_market_data.swaplevel().sort_index()

        log.info("Phase 2: Event-Driven Loop...")
        history = []
        trade_logs = []

        for date in tqdm(self.trade_dates, desc="Backtesting"):
            try:
                ts_date = pd.Timestamp(date)
                try:
                    scores = all_scores.loc[ts_date]
                    market_data = all_market_data.loc[ts_date]
                except KeyError:
                    continue
                if not isinstance(scores, (pd.Series, pd.DataFrame)) or not isinstance(market_data, pd.DataFrame):
                    continue
                if scores.empty or market_data.empty:
                    continue

                signal_frame = to_signal_frame(scores)
                current_prices = market_data["close"].to_dict()
                target_weights = self.strategy.generate_target_weights(signal_frame, market_data)
                orders = self.order_gen.generate_orders(target_weights, self.account, current_prices)
                trades = self.matcher.match(orders, self.account, market_data, current_prices)

                for t in trades:
                    order = t.get("order", {})
                    trade_logs.append(
                        {
                            "date": date,
                            "symbol": order.get("symbol"),
                            "side": order.get("side"),
                            "target_weight": target_weights.get(order.get("symbol"), 0.0),
                            "filled_amount": t.get("filled_amount", 0),
                            "deal_price": t.get("deal_price", 0.0),
                            "fee": t.get("fee", 0.0),
                            "status": t.get("status"),
                            "reason": t.get("reason", ""),
                        }
                    )

                daily_fee = sum(t.get("fee", 0.0) for t in trades if t.get("status") == "filled")
                daily_turnover = sum(
                    t.get("filled_amount", 0) * t.get("deal_price", 0.0)
                    for t in trades
                    if t.get("status") == "filled"
                )

                self.account.settlement()
                total_assets = self.account.get_total_equity(current_prices)
                self.account.record_daily(date, total_assets)
                history.append(
                    {
                        "date": date,
                        "total_assets": total_assets,
                        "cash": self.account.cash,
                        "position_count": len(self.account.positions),
                        "trade_count": len([t for t in trades if t.get("status") == "filled"]),
                        "daily_fee": daily_fee,
                        "daily_turnover": daily_turnover,
                    }
                )
            except Exception as e:
                log.error(f"Error on {date}: {e}")

        df_result = pd.DataFrame(history)
        df_trades = pd.DataFrame(trade_logs)
        self.last_trades = df_trades

        signal_panel = self._build_signal_panel(all_scores, all_market_data)
        self.last_signal_metrics = compute_signal_metrics(signal_panel, label_horizon=self.label_horizon)
        self.last_group_returns = compute_group_returns(signal_panel, label_horizon=self.label_horizon)

        if not df_result.empty:
            log.info(f"Backtest finished. Final Assets: {df_result.iloc[-1]['total_assets']:.2f}")
            self.last_summary = PerformanceAnalyzer.show(df_result)

        return df_result

    def _build_signal_panel(self, all_scores: pd.DataFrame | pd.Series, all_market_data: pd.DataFrame) -> pd.DataFrame:
        if all_market_data.empty:
            return pd.DataFrame(columns=["date", "instrument", "signal_value", "forward_return"])
        close_panel = all_market_data["close"].copy().sort_index()
        if isinstance(close_panel, pd.Series):
            close_panel = close_panel.to_frame(name="close")
        forward = close_panel.groupby(level=1).shift(-1)
        forward_return = (forward["close"] / close_panel["close"] - 1).rename("forward_return")

        if isinstance(all_scores, pd.Series):
            score_frame = all_scores.rename("signal_value").to_frame()
        else:
            score_frame = to_signal_frame(all_scores)[["signal_value"]]
        panel = score_frame.join(forward_return, how="inner")
        if panel.empty:
            return pd.DataFrame(columns=["date", "instrument", "signal_value", "forward_return"])
        panel = panel.reset_index()
        panel.columns = ["date", "instrument", "signal_value", "forward_return"]
        return panel

    def save_report(self, output_dir: str | Path, prefix: str = "backtest") -> dict[str, str]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written = {}

        if hasattr(self.account, "history") and self.account.history:
            history_path = output_dir / f"{prefix}_daily.csv"
            pd.DataFrame(self.account.history).to_csv(history_path, index=False)
            written["daily"] = str(history_path)

        if isinstance(self.last_trades, pd.DataFrame) and not self.last_trades.empty:
            trades_path = output_dir / f"{prefix}_trades.csv"
            self.last_trades.to_csv(trades_path, index=False)
            written["trades"] = str(trades_path)

        if self.last_summary is not None:
            summary_path = output_dir / f"{prefix}_summary.csv"
            pd.DataFrame([self.last_summary]).to_csv(summary_path, index=False)
            written["summary"] = str(summary_path)

        if self.last_signal_metrics:
            signal_metrics_path = output_dir / f"{prefix}_signal_metrics.json"
            pd.Series(self.last_signal_metrics).to_json(signal_metrics_path, force_ascii=False, indent=2)
            written["signal_metrics"] = str(signal_metrics_path)

        if isinstance(self.last_group_returns, pd.DataFrame) and not self.last_group_returns.empty:
            group_returns_path = output_dir / f"{prefix}_group_returns.csv"
            self.last_group_returns.to_csv(group_returns_path, index=False)
            written["group_returns"] = str(group_returns_path)

        return written
