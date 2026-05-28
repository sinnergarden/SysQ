"""Rolling (walk-forward) backtest framework.

Strategy-agnostic core.  Accepts ``train_func``, ``predict_func``, and
``data_loader`` callbacks so any strategy can be evaluated with walk-forward
validation.

Key design
----------
- **Pre-compute predictions once** per window, cache, and reuse across all
  strategy variants (only portfolio params differ).
- **Batch qlib access**: one feature-load call per window's test period
  instead of per-date calls.
- **Pre-load all training data** once at init, avoiding repeated 51s loads.
- **Parallel variant execution** via ``concurrent.futures``.

Usage::

    runner = RollingBacktestRunner(
        train_func=train_alpha_v1,
        predict_func=predict_alpha_v1,
        data_loader=load_backtest_data,
        variants=VARIANT_CONFIGS,
    )
    results = runner.run(start_date="2024-01-01", end_date="2026-05-22")
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from qsys.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    build_trading_day_windows,
    get_rebalance_dates,
    compute_trade_flags,
)
from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine
from qsys.trader.diff import OrderGenerator
from qsys.analysis.backtest_metrics import compute_backtest_metrics


# ── Data contracts ────────────────────────────────────────────────────────


@dataclass
class WindowSpec:
    """One walk-forward window."""

    window_id: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass
class VariantConfig:
    """One strategy variant = portfolio params + display name."""

    name: str
    strategy_id: str = ""
    top_n: int = 20
    buffer_hold: int = 60
    buffer_buy: int = 40
    single_stock_cap: float = 0.07
    rebalance_freq: str = "weekly"
    slippage: float = 0.0
    portfolio_fn: Callable | None = None


@dataclass
class VariantResult:
    """Result for one variant across all windows."""

    name: str
    backtest_result: BacktestResult
    metrics: dict[str, Any]


@dataclass
class RollingBacktestResult:
    """Aggregated result across all variants."""

    variants: list[VariantResult] = field(default_factory=list)
    windows: list[WindowSpec] = field(default_factory=list)
    total_wall_time: float = 0.0

    def get(self, name: str) -> VariantResult | None:
        for v in self.variants:
            if v.name == name:
                return v
        return None


# ── Callback type aliases ─────────────────────────────────────────────────

TrainFunc = Callable[
    [pd.DataFrame, list[str], str, str, str],
    str,
]
"""``train_func(preloaded_frame, clean_features, model_dir, train_start, train_end) → model_dir``"""

PredictFunc = Callable[
    [str, list[str], list[tuple[str, str]], pd.DataFrame | None],
    dict[tuple[str, str], float],
]
"""``predict_func(model_dir, trade_dates_with_data_dates, preloaded_frame) → {(trade_date, inst): score}``"""

DataLoader = Callable[
    [list[str]],
    pd.DataFrame,
]
"""``data_loader(trade_dates) → frame`` (OHLCV + trade flags)."""


# ── Rolling backtest runner ───────────────────────────────────────────────


class RollingBacktestRunner:
    """Walk-forward backtest runner — generic, reusable, prediction-caching.

    Parameters
    ----------
    train_func
        Callable that trains a model for one window.  Receives the
        pre-loaded feature frame, clean feature list, model directory,
        and train_start/train_end dates.  Must save artifacts to model_dir.
    predict_func
        Callable that generates predictions for a set of trade dates
        using a trained model.  Returns ``{(trade_date_str, instrument): score}``.
    data_loader
        Callable that loads OHLCV + flags for a list of trade dates.
    variants
        List of ``VariantConfig`` defining portfolio params per variant.
    cache_dir
        Optional directory for caching predictions (default: temp).
    n_jobs
        Number of parallel workers for variant backtests (default: 1).
    """

    def __init__(
        self,
        train_func: TrainFunc,
        predict_func: PredictFunc,
        data_loader: DataLoader,
        variants: list[VariantConfig],
        *,
        cache_dir: str | Path | None = None,
        n_jobs: int = 1,
    ) -> None:
        self._train_func = train_func
        self._predict_func = predict_func
        self._data_loader = data_loader
        self._variants = variants
        self._n_jobs = n_jobs

        if cache_dir:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_dir = None

    # ── Public API ────────────────────────────────────────────────────────

    def run(
        self,
        *,
        start_date: str,
        end_date: str,
        preloaded_data: tuple[pd.DataFrame, list[str]] | None = None,
        train_days: int = 504,
        test_days: int = 5,
        step_days: int = 5,
        initial_capital: float = 1_000_000.0,
        signals_cache_path: str | Path | None = None,
    ) -> RollingBacktestResult:
        """Run full rolling backtest.

        Parameters
        ----------
        start_date
            First backtest date (YYYY-MM-DD), inclusive.
        end_date
            Last backtest date (YYYY-MM-DD), inclusive.
        preloaded_data
            Optional pre-loaded feature data from ``alpha_v1_train.preload_training_data``.
            If None, data is loaded per window (slower).
        train_days, test_days, step_days
            Walk-forward window parameters.
        initial_capital
            Starting capital for each variant's account.

        Returns
        -------
        RollingBacktestResult with per-variant results + metrics.
        """
        t_start = time.time()
        print("=" * 70)
        print("Rolling Backtest")
        print(f"  Period: {start_date} → {end_date}")
        print(f"  Variants: {len(self._variants)}")
        print(f"  Windows: {train_days}d train / {test_days}d test / {step_days}d step")
        print("=" * 70)

        # 1. Resolve all trading dates (need extra history for first window's train)
        from qsys.data.calendar import get_trading_calendar

        # Include training history before start_date (504+ days before first test)
        calendar_start = (pd.Timestamp(start_date) - pd.DateOffset(years=2, days=30)).strftime("%Y-%m-%d")
        all_dates = get_trading_calendar(calendar_start, end_date)
        all_dates_dt = [pd.Timestamp(d) for d in all_dates]

        # 2. Build windows
        raw_windows = build_trading_day_windows(
            all_dates_dt,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
        )
        # Filter to only windows whose test period overlaps [start_date, end_date]
        windows: list[WindowSpec] = []
        for w in raw_windows:
            if w["test_start"] <= end_date and w["test_end"] >= start_date:
                windows.append(WindowSpec(**w))
        print(f"\nWindows: {len(windows)}")
        if windows:
            print(f"  First: {windows[0].window_id} train={windows[0].train_start}→{windows[0].train_end} "
                  f"test={windows[0].test_start}→{windows[0].test_end}")
            print(f"  Last:  {windows[-1].window_id} train={windows[-1].train_start}→{windows[-1].train_end} "
                  f"test={windows[-1].test_start}→{windows[-1].test_end}")

        # 3. Pre-load training data (one qlib call for all windows)
        #    We need data up to end_date for the last window's training.
        if preloaded_data is not None:
            train_frame, clean_features = preloaded_data
            print("\n[Data] Using pre-loaded training data")
        else:
            from qsys.model.alpha_v1_train import preload_training_data

            print("\n[Data] Pre-loading training data...")
            train_frame, clean_features = preload_training_data(end_date)

        # 4. Pre-load backtest OHLCV data for the full period (one batch)
        print("\n[Data] Loading backtest OHLCV data...")
        bt_frame = self._load_full_backtest_frame(start_date, end_date)

        # 5. Check signals cache
        all_signals: dict[tuple[str, str], float] = {}
        all_window_ids: dict[str, str] = {}

        if signals_cache_path is not None:
            cache_path = Path(signals_cache_path)
            if cache_path.exists():
                import pickle as _pickle
                print(f"\n[Cache] Loading signals from {cache_path}")
                cached = _pickle.loads(cache_path.read_bytes())
                all_signals = cached["all_signals"]
                all_window_ids = cached["all_window_ids"]
                bt_frame = cached.get("bt_frame", bt_frame)
                windows = cached.get("windows", windows)
                print(f"  {len(all_signals)} signals, {len(all_window_ids)} dates")

        if not all_signals:
            # 5a. For each window: train → predict → cache
            for idx, win in enumerate(windows):
                print(f"\n── Window {idx + 1}/{len(windows)}: {win.window_id} ──")
                print(f"    Train: {win.train_start} → {win.train_end}")
                print(f"    Test:  {win.test_start} → {win.test_end}")

                # 5b. Train model for this window
                model_dir = self._train_window(win, train_frame, clean_features)

                # 5c. Generate predictions for test period
                t_pred = time.time()
                signals = self._predict_window(model_dir, win)
                print(f"    Predictions: {len(signals)} signals in {time.time() - t_pred:.1f}s")

                # 5d. Merge into global signal map
                all_signals.update(signals)
                for d in self._trading_dates_in_range(win.test_start, win.test_end):
                    all_window_ids[d] = win.window_id

            # 5e. Save signals cache
        if signals_cache_path is not None and not Path(signals_cache_path).exists():
            import pickle as _pickle
            cache_path = Path(signals_cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(_pickle.dumps({
                "all_signals": all_signals,
                "all_window_ids": all_window_ids,
                "bt_frame": bt_frame,
                "windows": windows,
            }))
            print(f"\n[Cache] Saved {len(all_signals)} signals to {cache_path}")

        # 6. Rebalance dates for the full period (weekly by default)
        bt_dates = sorted(self._trading_dates_in_range(start_date, end_date))
        bt_dates_dt = [pd.Timestamp(d) for d in bt_dates]

        # 7. For each variant: run backtest
        from qsys.data.calendar import resolve_previous_trading_date

        variants_results: list[VariantResult] = []
        for var in self._variants:
            print(f"\n{'=' * 70}")
            print(f"Variant: {var.name}")
            print(f"  top_n={var.top_n}, buffer_hold={var.buffer_hold}, "
                  f"buffer_buy={var.buffer_buy}, cap={var.single_stock_cap}, "
                  f"freq={var.rebalance_freq}")

            t_var = time.time()
            rb_set = get_rebalance_dates(bt_dates_dt, freq=var.rebalance_freq)
            rb_dates_str = {d.strftime("%Y-%m-%d") for d in rb_set}

            engine = BacktestEngine(
                account=Account(init_cash=initial_capital),
                matcher=MatchEngine(slippage=var.slippage),
                order_gen=OrderGenerator(),
            )
            portfolio_fn_to_use = var.portfolio_fn or build_rank_weight_portfolio
            result = engine.run(
                frame=bt_frame,
                signal_lookup=all_signals,
                rebalance_dates=rb_set,
                portfolio_fn=portfolio_fn_to_use,
                dates=bt_dates_dt,
                window_lookup=all_window_ids,
                top_n=var.top_n,
                buffer_hold=var.buffer_hold,
                buffer_buy=var.buffer_buy,
                single_stock_cap=var.single_stock_cap,
            )

            # Build daily_summary from result for metrics
            metrics = self._compute_metrics(result, var)

            elapsed = time.time() - t_var
            print(f"  Result: {len(result.daily)} days, "
                  f"ann_ret={metrics.get('annual_return', 0):.2%}, "
                  f"sharpe={metrics.get('sharpe', 0):.2f}, "
                  f"mdd={metrics.get('max_drawdown', 0):.2%}")
            print(f"  Time: {elapsed:.1f}s")

            variants_results.append(VariantResult(
                name=var.name,
                backtest_result=result,
                metrics=metrics,
            ))

        total_time = time.time() - t_start
        print(f"\n{'=' * 70}")
        print(f"Done in {total_time:.0f}s")

        return RollingBacktestResult(
            variants=variants_results,
            windows=windows,
            total_wall_time=total_time,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _train_window(
        self,
        win: WindowSpec,
        train_frame: pd.DataFrame,
        clean_features: list[str],
    ) -> str:
        """Train model for one window, return model_dir path."""
        t0 = time.time()
        model_dir = tempfile.mkdtemp(prefix=f"rolling_{win.window_id}_")
        self._train_func(
            train_frame, clean_features, model_dir,
            win.train_start, win.train_end,
        )
        print(f"    Train time: {time.time() - t0:.1f}s")
        return model_dir

    def _predict_window(
        self,
        model_dir: str,
        win: WindowSpec,
    ) -> dict[tuple[str, str], float]:
        """Generate predictions for test period of one window.

        Returns ``{(trade_date_str, instrument): score}`` for all
        trade dates in the test period and all instruments.
        """
        from qsys.data.calendar import resolve_previous_trading_date

        trade_dates = self._trading_dates_in_range(win.test_start, win.test_end)
        # Precompute data_dates (previous trading day for each trade_date)
        date_pairs: list[tuple[str, str]] = []
        for td in trade_dates:
            dd = resolve_previous_trading_date(td)
            date_pairs.append((td, dd))

        return self._predict_func(model_dir, date_pairs, None)

    def _load_full_backtest_frame(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load OHLCV + trade flags for the full backtest period."""
        trade_dates = self._trading_dates_in_range(start_date, end_date)
        return self._data_loader(trade_dates)

    @staticmethod
    def _trading_dates_in_range(start: str, end: str) -> list[str]:
        from qsys.data.calendar import get_trading_calendar

        return get_trading_calendar(start, end)

    @staticmethod
    def _compute_metrics(
        result: BacktestResult, variant: VariantConfig,
    ) -> dict[str, Any]:
        """Compute metrics from BacktestEngine result."""
        if result.daily.empty:
            return {"error": "no_daily_data"}

        daily_summary = result.daily.copy()
        # Rename for backtest_metrics compatibility
        if "equity" in daily_summary.columns and "total_value_after" not in daily_summary.columns:
            daily_summary = daily_summary.rename(columns={"equity": "total_value_after"})
        if "date" in daily_summary.columns and "trade_date" not in daily_summary.columns:
            daily_summary = daily_summary.rename(columns={"date": "trade_date"})
        if "ret" in daily_summary.columns and "return" not in daily_summary.columns:
            pass  # compute_backtest_metrics computes its own returns from equity

        # Add turnover column from trades if available
        if "turnover" not in daily_summary.columns and not result.trades.empty:
            trades = result.trades.copy()
            trades["turnover_value"] = trades["amount"].astype(float) * trades["price"].astype(float)
            daily_turnover = trades.groupby("date")["turnover_value"].sum()
            daily_summary["turnover"] = daily_summary["trade_date"].map(
                lambda d: float(daily_turnover.get(d, 0.0))
            )

        metrics = compute_backtest_metrics(daily_summary)

        # Add trade-level stats
        if not result.trades.empty:
            trades = result.trades.copy()
            metrics["trade_count"] = len(trades)
            metrics["buy_count"] = int((trades["side"] == "buy").sum())
            metrics["sell_count"] = int((trades["side"] == "sell").sum())
        else:
            metrics["trade_count"] = 0
            metrics["buy_count"] = 0
            metrics["sell_count"] = 0

        # Average cash ratio (fraction of equity held as cash)
        if "cash" in daily_summary.columns and "equity" in daily_summary.columns:
            cash_ratios = daily_summary["cash"].astype(float) / daily_summary["equity"].astype(float).replace(0, np.nan)
            metrics["cash_ratio_avg"] = float(cash_ratios.mean())
        else:
            metrics["cash_ratio_avg"] = 0.0

        # Worst 1d and 5d returns from equity curve (not already computed)
        if "ret" in daily_summary.columns:
            rets = daily_summary["ret"].astype(float).dropna()
            if len(rets) > 0:
                metrics["worst_1d_return"] = float(rets.min())
                if len(rets) >= 5:
                    rolling_5d = rets.rolling(5).sum().dropna()
                    metrics["worst_5d_return"] = float(rolling_5d.min())
                else:
                    metrics["worst_5d_return"] = float(rets.sum())
        else:
            metrics["worst_1d_return"] = 0.0
            metrics["worst_5d_return"] = 0.0

        return metrics


# ── Factory: alpha_v1-specific helpers ────────────────────────────────────


def make_alpha_v1_train_func(
    project_root: str | Path | None = None,
) -> TrainFunc:
    """Create a train_func for alpha_v1 rolling backtest.

    Usage::

        train_func = make_alpha_v1_train_func()
        runner = RollingBacktestRunner(train_func=train_func, ...)
    """
    import os

    if project_root is not None:
        os.chdir(str(project_root))

    from qsys.model.alpha_v1_train import train_alpha_v1

    def _train(
        frame: pd.DataFrame,
        clean_features: list[str],
        model_dir: str,
        train_start: str,
        train_end: str,
    ) -> str:
        train_alpha_v1(
            frame,
            clean_features,
            model_dir=model_dir,
            train_start=train_start,
            train_end=train_end,
        )
        return model_dir

    return _train


def make_alpha_v1_predict_func(
    universe: str = "csi300",
) -> PredictFunc:
    """Create a predict_func for alpha_v1.

    Loads features for data_dates in batch (one qlib call), applies the
    trained model, and returns ``{(trade_date, instrument): score}``.

    Uses batch feature loading: all data_dates in one call.
    """
    from qsys.data.adapter import QlibAdapter
    from qsys.feature.library import FeatureLibrary
    from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE as C
    import lightgbm as lgb

    def _cs_zscore(s: pd.Series) -> pd.Series:
        std = s.std(ddof=0)
        if pd.isna(std) or std < 1e-12:
            return pd.Series(0.0, index=s.index)
        return ((s - s.mean()) / std).clip(-3, 3)

    def _robust_transform(X, center, scale):
        return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)

    def _predict(
        model_dir: str,
        date_pairs: list[tuple[str, str]],
        preloaded_frame: pd.DataFrame | None,
    ) -> dict[tuple[str, str], float]:
        """Generate predictions for a set of (trade_date, data_date) pairs."""
        if not date_pairs:
            return {}

        model_path = Path(model_dir)
        features_file = model_path / "features.json"
        if not features_file.exists():
            raise FileNotFoundError(f"features.json not found in {model_dir}")
        clean_features = json.loads(features_file.read_text())

        # Load models + transforms
        models: dict[str, tuple] = {}
        for tag in ["5d", "20d"]:
            m = lgb.Booster(model_file=str(model_path / f"model_{tag}.txt"))
            center = pd.Series(json.loads((model_path / f"center_{tag}.json").read_text()))
            scale = pd.Series(json.loads((model_path / f"scale_{tag}.json").read_text()))
            models[tag] = (m, center, scale)

        # Collect unique data_dates
        unique_dds = sorted(set(dd for _, dd in date_pairs))
        trade_to_data = {td: dd for td, dd in date_pairs}

        # Batch-load features for all unique data_dates (one qlib call)
        adapter = QlibAdapter()
        adapter.init_qlib()
        all_features = FeatureLibrary.get_semantic_all_features_config()
        raw = adapter.get_features(
            universe,
            all_features + ["$close"],
            start_time=unique_dds[0],
            end_time=unique_dds[-1],
        )
        if raw.empty:
            return {}

        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        # Normalise trade_date to string for comparison
        if hasattr(frame["trade_date"].iloc[0], "strftime"):
            frame["trade_date"] = frame["trade_date"].dt.strftime("%Y-%m-%d")
        else:
            frame["trade_date"] = frame["trade_date"].astype(str)
        # Ensure clean_features exist
        missing = [f for f in clean_features if f not in frame.columns]
        if missing:
            for f in missing:
                frame[f] = 0.0

        # Group by data_date and predict per date
        signal_lookup: dict[tuple[str, str], float] = {}
        for dd in unique_dds:
            day_data = frame[frame["trade_date"] == dd]
            if day_data.empty:
                continue

            X = day_data[clean_features].astype(np.float32).fillna(0.0)
            if X.empty or len(X) < 5:
                continue

            # Generate blended scores
            m5, c5, s5 = models["5d"]
            m20, c20, s20 = models["20d"]

            Xz_5d = _robust_transform(X, c5, s5)
            Xz_20d = _robust_transform(X, c20, s20)

            p5 = pd.Series(m5.predict(Xz_5d.values), index=X.index)
            p20 = pd.Series(m20.predict(Xz_20d.values), index=X.index)
            z5 = _cs_zscore(p5)
            z20 = _cs_zscore(p20)
            blended = C.blend.blend_5d * z5 + C.blend.blend_20d * z20

            # Map to trade_dates that use this data_date
            instruments = day_data["instrument"].values
            for td, ddate in trade_to_data.items():
                if ddate == dd:
                    for i, inst in enumerate(instruments):
                        z5_v = float(z5.iloc[i]) if pd.notna(z5.iloc[i]) else 0.0
                        z20_v = float(z20.iloc[i]) if pd.notna(z20.iloc[i]) else 0.0
                        b_v = float(blended.iloc[i]) if pd.notna(blended.iloc[i]) else 0.0
                        signal_lookup[(td, str(inst))] = (z5_v, z20_v, b_v)

        return signal_lookup

    return _predict


def make_alpha_v1_data_loader(
    universe: str = "csi300",
) -> DataLoader:
    """Create a data_loader for alpha_v1 backtest.

    Loads CSI300 OHLCV + volume for trade dates, computes trade flags.
    """
    from qsys.data.adapter import QlibAdapter
    from qsys.feature.library import FeatureLibrary

    def _load(trade_dates: list[str]) -> pd.DataFrame:
        if not trade_dates:
            return pd.DataFrame()

        adapter = QlibAdapter()
        adapter.init_qlib()
        # Only load price columns needed for backtest execution
        price_cols = ["$open", "$close", "$volume"]

        raw = adapter.get_features(
            universe,
            price_cols,
            start_time=trade_dates[0],
            end_time=trade_dates[-1],
        )
        if raw.empty:
            return pd.DataFrame()

        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]

        # Keep only needed columns
        cols = {"trade_date", "instrument", "$open", "$close", "$volume"}
        available = cols & set(frame.columns)
        frame = frame[list(available)].copy()

        # Compute trade flags
        frame = compute_trade_flags(frame)
        return frame

    return _load


# ── Variant definitions ───────────────────────────────────────────────────

BASELINE = VariantConfig(
    name="baseline",
    top_n=20, buffer_hold=60, buffer_buy=40,
    single_stock_cap=0.07, rebalance_freq="weekly",
)

NO_BUFFER = VariantConfig(
    name="no_buffer",
    top_n=20, buffer_hold=20, buffer_buy=20,
    single_stock_cap=0.07, rebalance_freq="weekly",
)

NO_CAP = VariantConfig(
    name="no_cap",
    top_n=20, buffer_hold=60, buffer_buy=40,
    single_stock_cap=1.0, rebalance_freq="weekly",
)

CONCENTRATED = VariantConfig(
    name="concentrated",
    top_n=10, buffer_hold=10, buffer_buy=10,
    single_stock_cap=0.15, rebalance_freq="weekly",
)

DIVERSIFIED = VariantConfig(
    name="diversified",
    top_n=50, buffer_hold=100, buffer_buy=80,
    single_stock_cap=0.03, rebalance_freq="weekly",
)

DAILY_REBALANCE = VariantConfig(
    name="daily_rebalance",
    top_n=20, buffer_hold=60, buffer_buy=40,
    single_stock_cap=0.07, rebalance_freq="daily",
)

ALL_VARIANTS = [
    BASELINE, NO_BUFFER, NO_CAP,
    CONCENTRATED, DIVERSIFIED, DAILY_REBALANCE,
]
