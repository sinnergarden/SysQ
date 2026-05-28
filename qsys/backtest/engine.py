"""Generic rolling backtest engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine


@dataclass
class BacktestResult:
    """Result of a BacktestEngine run."""

    daily: pd.DataFrame
    trades: pd.DataFrame


def build_trading_day_windows(
    all_dates_dt, *, train_days: int = 504, test_days: int = 5, step_days: int = 5
) -> list[dict]:
    """Build non-overlapping rolling windows from sorted trading dates."""
    dates = sorted(all_dates_dt)
    windows = []
    for i in range(0, len(dates), step_days):
        test_end_idx = i + test_days - 1
        if test_end_idx >= len(dates):
            break
        test_start = dates[i]
        test_end = dates[test_end_idx]
        train_start_idx = i - train_days
        if train_start_idx < 0:
            continue
        train_start = dates[train_start_idx]
        train_end = dates[i - 1] if i > 0 else dates[0]
        windows.append(
            {
                "window_id": f"w{i//step_days:04d}",
                "train_start": train_start.strftime("%Y-%m-%d"),
                "train_end": train_end.strftime("%Y-%m-%d"),
                "test_start": test_start.strftime("%Y-%m-%d"),
                "test_end": test_end.strftime("%Y-%m-%d"),
            }
        )
    return windows


def get_rebalance_dates(dates, freq: str = "weekly") -> set:
    """Determine rebalance days from a sorted list of dates."""
    dates = sorted(dates)
    if freq == "weekly":
        rb = {d for d in dates if d.weekday() == 4}
        if not rb:
            seen = set()
            for d in reversed(dates):
                w = d.isocalendar()[1]
                if w not in seen:
                    seen.add(w)
                    rb.add(d)
        return rb
    if freq in ("daily_full", "daily_partial"):
        return set(dates)
    return set(dates)


def compute_trade_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute suspension and limit-up/down flags."""
    frame = frame.copy()
    pc = frame.groupby("instrument")["$close"].shift(1)
    frame["is_suspended"] = (
        (frame["$volume"].fillna(0) <= 0) | frame["$close"].isna()
    ).astype(int)
    pct = frame["$open"] / pc.replace(0, np.nan) - 1.0
    frame["is_limit_up"] = (pct >= 0.095).fillna(False).astype(int)
    frame["is_limit_down"] = (pct <= -0.095).fillna(False).astype(int)
    return frame


class BacktestEngine:
    """Generic rolling backtest engine.

    Iterates through dates, executes orders, settles accounts, and calls
    rebalance on schedule.  Signal computation is pre-computed externally
    and passed in as a lookup dict — the engine has no knowledge of models,
    training, or strategy-specific logic.
    """

    def __init__(
        self,
        account: Account,
        matcher: MatchEngine,
        order_gen: OrderGenerator | None = None,
        zc_account: Account | None = None,
        zc_matcher: MatchEngine | None = None,
    ):
        self.account = account
        self.matcher = matcher
        self.order_gen = order_gen or OrderGenerator()
        self.zc_account = zc_account
        self.zc_matcher = zc_matcher

    def run(
        self,
        frame: pd.DataFrame,
        signal_lookup: dict[tuple[str, str], float | tuple],
        rebalance_dates: set,
        portfolio_fn: Callable,
        *,
        dates: list | None = None,
        window_lookup: dict[str, str] | None = None,
        top_n: int = 20,
        buffer_hold: int = 60,
        buffer_buy: int = 40,
        single_stock_cap: float = 0.07,
    ) -> BacktestResult:
        """Run the backtest loop.

        Parameters
        ----------
        frame : pd.DataFrame
            OHLCV data with columns: trade_date, instrument, $open, $close,
            $volume, is_suspended, is_limit_up, is_limit_down.
        signal_lookup : dict
            {(date_str, instrument): score} — pre-computed scores for all
            rebalance dates and instruments.
        rebalance_dates : set
            Dates (pd.Timestamp) on which rebalancing occurs.
        portfolio_fn : callable
            (scores, account, *, top_n, buffer_hold, buffer_buy,
            single_stock_cap) -> dict[str, float]
        dates : list, optional
            Specific dates to iterate. If None, uses all unique dates
            from ``frame`` sorted.  Pass a pre-filtered list (e.g. only
            dates within test-window ranges) to avoid iterating periods
            where no signal exists.
        window_lookup : dict, optional
            {date_str: window_id} — attached to every daily/trade row.
        top_n, buffer_hold, buffer_buy, single_stock_cap
            Portfolio construction parameters, forwarded to portfolio_fn.

        Returns
        -------
        BacktestResult with daily equity curve and trade log.
        """
        all_dates = sorted(dates) if dates is not None else sorted(frame["trade_date"].unique())
        prev_equity = self.account.get_total_equity({})
        daily_rows, trade_rows, pending = [], [], []

        for i, date in enumerate(all_dates):
            mask = frame["trade_date"] == date
            today = frame[mask]
            if today.empty:
                continue

            date_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)
            wid = window_lookup.get(date_str, "") if window_lookup else ""

            # ── Execute pending orders ──
            if pending:
                exec_prices = {
                    r["instrument"]: r["$open"]
                    for _, r in today.iterrows()
                    if pd.notna(r["$open"]) and r["$open"] > 0
                }
                status_df = pd.DataFrame(
                    {
                        "is_suspended": today["is_suspended"].values,
                        "is_limit_up": today["is_limit_up"].values,
                        "is_limit_down": today["is_limit_down"].values,
                    },
                    index=today["instrument"].values,
                )
                results = self.matcher.match(pending, self.account, status_df, exec_prices)
                for r in results:
                    if r["status"] == "filled":
                        o = r["order"]
                        trade_rows.append(
                            {
                                "date": date_str,
                                "window_id": wid,
                                "symbol": o["symbol"],
                                "side": o["side"],
                                "amount": r["filled_amount"],
                                "price": r["deal_price"],
                                "fee": r["fee"],
                            }
                        )
                if self.zc_matcher is not None and self.zc_account is not None:
                    self.zc_matcher.match(pending, self.zc_account, status_df, exec_prices)
                pending = []

            # ── Settlement ──
            self.account.settlement()
            if self.zc_account is not None:
                self.zc_account.settlement()

            # ── MTM ──
            cp = {
                r["instrument"]: r["$close"]
                for _, r in today.iterrows()
                if pd.notna(r["$close"]) and r["$close"] > 0
            }
            equity = self.account.get_total_equity(cp)
            ret = (equity / prev_equity - 1) if prev_equity > 0 else 0.0
            prev_equity = equity

            zc_equity = (
                self.zc_account.get_total_equity(cp)
                if self.zc_account is not None
                else None
            )

            daily_rows.append(
                {
                    "date": date_str,
                    "window_id": wid,
                    "equity": equity,
                    "cash": self.account.cash,
                    "mv": self.account.get_market_value(cp),
                    "npos": len(self.account.positions),
                    "ret": ret,
                    "zc_equity": zc_equity,
                }
            )

            # ── Rebalance? ──
            if date not in rebalance_dates:
                continue

            inst_scores: dict[str, float] = {}
            signal_info: dict[str, dict] = {}
            for _, r in today.iterrows():
                key = (date_str, r["instrument"])
                val = signal_lookup.get(key)
                if val is not None:
                    if isinstance(val, (tuple, list)):
                        z5_v, z20_v, blended_v = val
                        if not np.isnan(blended_v):
                            inst_scores[r["instrument"]] = float(blended_v)
                            signal_info[r["instrument"]] = {"z5": float(z5_v), "z20": float(z20_v)}
                    elif not (isinstance(val, float) and np.isnan(val)):
                        inst_scores[r["instrument"]] = float(val)
            if not inst_scores:
                continue
            scores = pd.Series(inst_scores).dropna()
            scores.name = date_str
            if len(scores) < 5:
                continue

            target_weights = portfolio_fn(
                scores,
                self.account,
                top_n=top_n,
                buffer_hold=buffer_hold,
                buffer_buy=buffer_buy,
                single_stock_cap=single_stock_cap,
                signal_info=signal_info if signal_info else None,
            )
            if not target_weights:
                continue

            orders = self.order_gen.generate_orders(target_weights, self.account, cp)
            pending = orders if orders else []

        daily_df = pd.DataFrame(daily_rows) if daily_rows else pd.DataFrame()
        trade_df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()
        return BacktestResult(daily=daily_df, trades=trade_df)
