"""Reusable backtest loop: signals → BacktestEngine → results."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from qsys.backtest import (  # noqa: E402
    BacktestEngine,
    build_rank_weight_portfolio,
    build_trading_day_windows,
    compute_trade_flags,
    get_rebalance_dates,
)
from qsys.trader.account import Account  # noqa: E402
from qsys.trader.matcher import MatchEngine  # noqa: E402


def run_backtest(
    ohlcv_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    config: dict,
    score_col: str,
    output_dir: Path,
    label: str = "kronos",
) -> dict:
    """Run a rolling backtest using pre-computed signals.

    Parameters
    ----------
    ohlcv_df : pd.DataFrame
        OHLCV data with trade_date, instrument, fq_close, volume, amount.
    signals_df : pd.DataFrame
        Signal dataframe with trade_date, instrument, ``score_col``.
    config : dict
        Pipeline config (backtest.portfolio and backtest.cost sections).
    score_col : str
        Column name in signals_df to use as the signal score.
    output_dir : Path
        Directory for backtest output files.
    label : str
        Label for this backtest (used in progress messages).

    Returns
    -------
    dict with keys: result, metrics, equity_curve, trades
    """
    print(f"\n{'='*70}")
    print(f"Backtest: {label} (score_col={score_col})")
    print(f"{'='*70}")

    bt_cfg = config.get("backtest", {})
    pf = bt_cfg.get("portfolio", {})
    cost = bt_cfg.get("cost", {})

    # Build signal_lookup (normalise date to str to match engine lookup)
    sig = signals_df[["trade_date", "instrument", score_col]].dropna(subset=[score_col])
    signal_lookup: dict[tuple[str, str], float] = {}
    for _, row in sig.iterrows():
        date_str = str(row["trade_date"])[:10] if not isinstance(row["trade_date"], str) else row["trade_date"]
        signal_lookup[(date_str, row["instrument"])] = float(row[score_col])
    print(f"  Signal lookup: {len(signal_lookup)} entries")

    # Prepare main dataframe for backtest
    frame = ohlcv_df.copy()
    frame = frame.rename(columns={
        "fq_open": "$open", "fq_high": "$high", "fq_low": "$low",
        "fq_close": "$close", "volume": "$volume", "amount": "$amount",
    })
    frame = compute_trade_flags(frame)
    frame["daily_ret"] = frame.groupby("instrument")["$close"].pct_change()

    # Build date windows (for window tracking, not model training)
    all_dates_dt = [pd.Timestamp(d) for d in sorted(frame["trade_date"].unique())]
    windows = build_trading_day_windows(
        all_dates_dt, train_days=min(504, max(50, len(all_dates_dt) // 2)),
        test_days=5, step_days=5,
    )
    # Filter to signal date range
    signal_dates = sorted(sig["trade_date"].unique())
    if signal_dates:
        signal_start = str(signal_dates[0])[:10]
        windows = [w for w in windows if str(w["test_end"])[:10] >= signal_start]
    if windows:
        print(f"  Windows: {len(windows)} ({windows[0]['test_start']} ~ {windows[-1]['test_end']})")
    else:
        print("  Windows: 0 (signal period too short)")

    # Build date index
    all_dates = sorted(frame["trade_date"].unique())
    rebal_dates = get_rebalance_dates(all_dates, pf.get("rebalance_freq", "weekly"))

    # Set up rebalance/test dates (normalise date types for safe comparison)
    signal_date_objs = set()
    for d in sig["trade_date"].unique():
        signal_date_objs.add(pd.Timestamp(str(d)[:10]))
    test_dates = sorted(d for d in all_dates if d in signal_date_objs)

    window_lookup = {}
    for w in windows:
        start_str = str(w["test_start"])[:10]
        end_str = str(w["test_end"])[:10]
        for d in pd.date_range(start_str, end_str, freq="D"):
            window_lookup[d.strftime("%Y-%m-%d")] = w["window_id"]

    # Backtest engine
    account = Account(init_cash=10_000_000.0)
    zc_account = Account(init_cash=10_000_000.0)
    matcher = MatchEngine(
        commission=cost.get("commission", 0.0003),
        stamp_duty=cost.get("stamp_duty", 0.001),
        min_commission=cost.get("min_commission", 5.0),
        slippage=cost.get("slippage", 0.001),
    )
    zc_matcher = MatchEngine(commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0)

    engine = BacktestEngine(account, matcher, zc_account=zc_account, zc_matcher=zc_matcher)
    result = engine.run(
        frame, signal_lookup, rebal_dates, build_rank_weight_portfolio,
        dates=test_dates, window_lookup=window_lookup,
        top_n=pf.get("top_n", 20),
        buffer_hold=pf.get("buffer_hold", 60),
        buffer_buy=pf.get("buffer_buy", 40),
        single_stock_cap=pf.get("single_stock_cap", 0.07),
    )

    daily_df = result.daily
    trade_df = result.trades

    # Compute metrics
    metrics = _compute_metrics(daily_df, trade_df)

    # Save outputs
    (output_dir / "backtest").mkdir(parents=True, exist_ok=True)
    daily_df.to_csv(output_dir / "backtest" / f"daily_equity_{label}.csv", index=False)
    if not trade_df.empty:
        trade_df.to_csv(output_dir / "backtest" / f"trade_log_{label}.csv", index=False)

    print(f"  Returns: {metrics.get('total_ret', 0)*100:.2f}% / {metrics.get('ann_ret', 0)*100:.2f}% ann")
    print(f"  Sharpe: {metrics.get('sharpe', 0):.4f}, MaxDD: {metrics.get('max_dd', 0)*100:.2f}%")

    return {
        "result": result,
        "metrics": metrics,
        "equity_curve": daily_df,
        "trades": trade_df,
        "label": label,
        "score_col": score_col,
    }


def _compute_metrics(daily_rows, trade_rows=None, nwindows=0):
    """Compute performance metrics from backtest results."""
    ddf = daily_rows if isinstance(daily_rows, pd.DataFrame) else pd.DataFrame(daily_rows) if daily_rows else pd.DataFrame()
    tdf = trade_rows if isinstance(trade_rows, pd.DataFrame) else pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()
    if ddf.empty or len(ddf) < 5:
        return {"error": "insufficient_data"}

    eq = ddf["equity"].values
    tr = eq[-1] / eq[0] - 1.0
    nd = len(ddf)
    ann = (1 + tr) ** (252 / nd) - 1 if nd > 0 else 0.0
    dr = ddf["ret"].values
    ds = max(np.nanstd(dr), 1e-10)
    sp = (np.nanmean(dr) / ds) * np.sqrt(252)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    mdd = float(np.min(dd))
    cal = ann / abs(mdd) if mdd < 0 else 0.0

    ttf = float(tdf["fee"].sum()) if not tdf.empty and "fee" in tdf.columns else 0.0
    ae = eq.mean()
    ato = 0.0
    if not tdf.empty and "amount" in tdf.columns and "price" in tdf.columns:
        ato = ((tdf["amount"] * tdf["price"]).sum() / max(ae, 1)) * (252 / max(nd, 1))

    wd = int(np.sum(dr > 0))
    ld = int(np.sum(dr < 0))
    wr = wd / (wd + ld) if wd + ld > 0 else 0.0

    return {
        "total_ret": round(tr, 6), "ann_ret": round(ann, 6),
        "ann_vol": round(ds * np.sqrt(252), 6),
        "sharpe": round(sp, 4), "max_dd": round(mdd, 6),
        "calmar": round(cal, 4), "win_rate": round(wr, 4),
        "avg_pos": round(float(ddf["npos"].mean()), 1),
        "ann_to": round(ato, 4), "cost": round(ttf, 2), "ndays": nd,
    }
