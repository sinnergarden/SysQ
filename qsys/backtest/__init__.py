from qsys.backtest.engine import BacktestEngine, BacktestResult, build_trading_day_windows, compute_trade_flags, get_rebalance_dates
from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.backtest.rolling_runner import RollingBacktestRunner, VariantConfig, VariantResult
from qsys.backtest.strategy_variants import (
    compute_split_5d20d_adjusted_scores,
    precompute_crash_features,
    get_crash_risk_stocks,
    make_dynamic_topn_portfolio_fn,
    make_split_5d20d_portfolio_fn,
    make_regime_exposure_portfolio_fn,
    make_turnover_budget_portfolio_fn,
    make_rank_stability_portfolio_fn,
    make_two_book_portfolio_fn,
    make_crash_filter_portfolio_fn,
    make_split_5d20d_regime_exposure_portfolio_fn,
    make_split_5d20d_crash_filter_portfolio_fn,
)

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "build_rank_weight_portfolio",
    "build_trading_day_windows",
    "compute_trade_flags",
    "get_rebalance_dates",
    "RollingBacktestRunner",
    "VariantConfig",
    "VariantResult",
    "compute_split_5d20d_adjusted_scores",
    "precompute_crash_features",
    "get_crash_risk_stocks",
    "make_dynamic_topn_portfolio_fn",
    "make_split_5d20d_portfolio_fn",
    "make_regime_exposure_portfolio_fn",
    "make_turnover_budget_portfolio_fn",
    "make_rank_stability_portfolio_fn",
    "make_two_book_portfolio_fn",
    "make_crash_filter_portfolio_fn",
    "make_split_5d20d_regime_exposure_portfolio_fn",
    "make_split_5d20d_crash_filter_portfolio_fn",
]
