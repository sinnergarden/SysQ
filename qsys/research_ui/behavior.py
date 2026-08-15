from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _norm_date(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _is_filled(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "filled").lower() == "filled"


def _finite(value: Any) -> float | None:
    """Coerce to float; None for null / non-finite (NaN, ±inf).

    All numeric outputs flow through this helper so a bad bar or a degenerate
    price can never leak NaN/inf into the diagnostics layer.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _price_lookup(frame: pd.DataFrame | None) -> tuple[dict[str, dict[str, float | None]], list[str]]:
    """Normalize a raw daily frame into {date: {high, low, close}} + sorted dates.

    Vectorized: the per-row ``iterrows`` path was ~30s over 200+ symbol frames
    (one pandas ``Series`` constructed per row), so columns are extracted once
    with ``to_numeric`` and zipped.
    """
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return {}, []
    f = frame.copy()
    if "close" not in f.columns:
        return {}, []
    f["d"] = f["trade_date"].map(_norm_date)
    f = f[f["d"] != ""]
    if f.empty:
        return {}, []
    f = f.sort_values("d")
    dates = f["d"].tolist()
    close_vals = pd.to_numeric(f["close"], errors="coerce").tolist()
    high_vals = pd.to_numeric(f["high"], errors="coerce").tolist() if "high" in f.columns else [None] * len(dates)
    low_vals = pd.to_numeric(f["low"], errors="coerce").tolist() if "low" in f.columns else [None] * len(dates)
    rows: dict[str, dict[str, float | None]] = {}
    for d, h, l, c in zip(dates, high_vals, low_vals, close_vals):
        rows[d] = {
            "high": _finite(h),
            "low": _finite(l),
            "close": _finite(c),
        }
    return rows, dates


def derive_episodes(
    executions_rows: list[dict[str, Any]],
    *,
    prices_by_symbol: dict[str, pd.DataFrame] | None = None,
    scores_frame: pd.DataFrame | None = None,
    calendar: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct contiguous holding episodes per symbol from exact fills.

    A new episode opens when a buy moves qty 0→>0; buys add to the open
    episode, non-closing sells stay in it, the episode closes when qty →0, and
    a later buy after a close starts a fresh episode.  All prices are RAW
    (unadjusted) to match execution deal prices.  Read-only; never touches
    backtest engine code.

    ``calendar`` is the backtest's trading-day calendar (YYYY-MM-DD strings).
    It defines ``holding_days``, the ``score_delta_*`` lookbacks, post-exit
    return horizons, and the window bound: excursions and open-episode
    finalization never read prices after the last calendar date.  Canonical
    callers (the assembler) MUST pass a window-bounded calendar — the raw price
    store can extend past the backtest end — or fail closed; the ``union``
    fallback below exists only for pure-function tests where every price date is
    deliberately in-scope.
    """
    rows = [r for r in executions_rows if _is_filled(r)]
    if not rows:
        return []
    ordered = sorted(
        rows,
        key=lambda r: (_norm_date(r.get("trade_date") or r.get("date")), r.get("sequence") or 0),
    )
    fill_dates = {_norm_date(r.get("trade_date") or r.get("date")) for r in ordered}
    prices_by_symbol = prices_by_symbol or {}
    if calendar:
        calendar = sorted({_norm_date(d) for d in calendar if d})
    else:
        all_price_dates: set[str] = set()
        for frame in prices_by_symbol.values():
            _, price_dates = _price_lookup(frame)
            all_price_dates.update(price_dates)
        calendar = sorted(fill_dates | all_price_dates)
    cal_index = {d: i for i, d in enumerate(calendar)}
    max_cal_date = calendar[-1] if calendar else ""

    score_map: dict[tuple[str, str], float] = {}
    if scores_frame is not None and not scores_frame.empty:
        sf = scores_frame.copy()
        inst_col = "instrument" if "instrument" in sf.columns else "symbol"
        if "score" not in sf.columns:
            sf = sf.rename(columns={"score_raw": "score"})
        # Vectorized: the per-row iterrows path was ~30s over ~1.25M prediction
        # rows (a pandas Series per row); build effective date + score columns
        # once and zip the underlying lists instead.  A frame with no usable
        # score column simply yields an empty map (matches the old per-row
        # KeyError swallow) and episode derivation continues.
        if "score" in sf.columns:
            date_col = "trade_date" if "trade_date" in sf.columns else "date"
            raw_dates = sf[date_col]
            if date_col == "trade_date" and "date" in sf.columns:
                blank = raw_dates.isna() | (raw_dates.astype(str).str.strip() == "")
                raw_dates = raw_dates.where(~blank, sf["date"])
            inst_vals = sf[inst_col].map(lambda v: str(v or ""))
            score_vals = pd.to_numeric(sf["score"], errors="coerce")
            for d, inst, sc in zip(raw_dates.map(_norm_date), inst_vals, score_vals):
                f = _finite(sc)
                if d and inst and f is not None:
                    score_map[(d, inst)] = f

    def score_on(date: str, symbol: str) -> float | None:
        return score_map.get((date, symbol))

    fills_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for r in ordered:
        symbol = str(r.get("instrument") or r.get("symbol") or "")
        if not symbol:
            continue
        fills_by_symbol.setdefault(symbol, []).append(r)

    episodes: list[dict[str, Any]] = []
    for symbol, fills in fills_by_symbol.items():
        price_rows, price_dates = _price_lookup(prices_by_symbol.get(symbol))
        episodes.extend(
            _simulate_symbol(symbol, fills, price_rows, price_dates, calendar, cal_index, max_cal_date, score_on)
        )
    return episodes


def _new_episode(symbol: str, entry_date: str, entry_score: float | None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "exit_date": None,
        "entry_score": entry_score,
        "exit_score": None,
        "score_delta_5d": None,
        "score_delta_20d": None,
        "realized_return": None,
        "unrealized_return": None,
        "MFE": None,
        "MAE": None,
        "max_drawdown_from_peak": 0.0,
        "exit_reason": "open",
        "post_exit_return_20d": None,
        "post_exit_return_60d": None,
        # P0.1 — PnL / cashflow / horizon fields.  ``buy_cost`` / ``sell_proceeds``
        # stay as internal accumulation and are renamed at finalize time.
        "episode_pnl": None,
        "gross_buy_return": None,
        "total_buy_cashflow": 0.0,
        "total_sell_proceeds": 0.0,
        "episode_end_date": None,
        "valuation_date": None,
        "holding_days": None,
        # P1.8 — excursion-derived return diagnostics.
        "capture_ratio": None,
        "giveback_return": None,
        "giveback_ratio": None,
        "recovery_from_mae": None,
        # Semantic gate: MFE/MAE are price excursions against the *dynamic*
        # avg_cost, while realized_return is cashflow-based
        # (episode_pnl / total_buy_cashflow).  The two are only comparable on a
        # simple round trip (one buy, one full-close sell).  Complex episodes —
        # any mid-hold partial sell, re-add buy, or never-closed open position —
        # keep the capture/giveback/recovery fields None.
        "is_simple_round_trip": None,
        "capture_eligible": None,
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "peak_close": None,
    }


def _simulate_symbol(
    symbol: str,
    fills: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float | None]],
    price_dates: list[str],
    calendar: list[str],
    cal_index: dict[str, int],
    max_cal_date: str,
    score_on,
) -> list[dict[str, Any]]:
    qty = 0.0
    buy_qty = 0.0
    buy_cost = 0.0
    avg_cost = 0.0
    ep: dict[str, Any] | None = None
    episodes: list[dict[str, Any]] = []

    fill_dates = {_norm_date(r.get("trade_date") or r.get("date")) for r in fills}
    walk_dates = fill_dates | set(price_rows.keys())
    if max_cal_date:
        walk_dates = {d for d in walk_dates if d <= max_cal_date}
    walk = sorted(walk_dates)
    day_fills: dict[str, list[dict[str, Any]]] = {}
    for r in fills:
        day_fills.setdefault(_norm_date(r.get("trade_date") or r.get("date")), []).append(r)

    for date in walk:
        opened_today = False
        closed_today = False
        for r in day_fills.get(date, []):
            side = str(r.get("side") or "").lower()
            # An unknown/invalid side is not a trade event.  Guard before the
            # sell branch so a bogus side can never be mistaken for a sell —
            # even while holding a position — and silently mutate qty/cashflow.
            if side not in {"buy", "sell"}:
                continue
            if side != "buy" and qty <= 0:
                continue
            fqty = _finite(r.get("filled_qty"))
            price = _finite(r.get("deal_price"))
            fee = _finite(r.get("total_fee"))
            # Fee policy: the canonical executions contract always carries a
            # total_fee column, so a NaN/missing fee reads as zero (one bad fee
            # must not poison an episode).  A malformed fill (NaN/±inf/zero
            # qty or price) is dropped outright — it never enters the state
            # machine, so a corrupt row cannot corrupt the holding walk.
            if fee is None:
                fee = 0.0
            if fqty is None or fqty <= 0 or price is None or price <= 0:
                continue
            if side == "buy":
                if qty == 0:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                    ep_buy_fills = 1
                    ep_sell_fills = 0
                    ep_saw_partial_sell = False
                    opened_today = True
                else:
                    ep_buy_fills += 1
                buy_cost += fqty * price + fee
                buy_qty += fqty
                qty += fqty
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                ep["buy_cost"] += fqty * price + fee
            else:  # sell
                if ep is None:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                    ep_buy_fills = 0
                    ep_sell_fills = 0
                    ep_saw_partial_sell = False
                sold = min(fqty, qty)
                ep_sell_fills += 1
                if sold < qty:
                    # A sell that leaves the position open splits the holding
                    # into two cashflow regimes: MFE/MAE track the *dynamic*
                    # avg_cost excursion while realized_return is
                    # cashflow-based, so the two are not comparable any more.
                    ep_saw_partial_sell = True
                ep["sell_proceeds"] += sold * price - fee
                if buy_qty > 0:
                    removed = min(sold, buy_qty)
                    buy_cost = max(0.0, buy_cost - avg_cost * removed)
                    buy_qty -= removed
                qty = max(0.0, qty - fqty)
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                if qty == 0:
                    is_simple = not ep_saw_partial_sell and ep_buy_fills == 1 and ep_sell_fills == 1
                    ep["is_simple_round_trip"] = is_simple
                    ep["capture_eligible"] = is_simple
                    _close_episode(ep, date, r, episodes, price_rows, calendar, cal_index, score_on)
                    ep = None
                    closed_today = True
        # excursion update after the day's fills (only while holding).
        # On a same-day close-then-reopen the day's high/low may precede the
        # new entry, so the new episode's excursion starts the following day.
        if ep is not None and qty > 0 and avg_cost > 0 and not (opened_today and closed_today):
            prow = price_rows.get(date)
            if prow:
                high = _finite(prow.get("high"))
                low = _finite(prow.get("low"))
                close = _finite(prow.get("close"))
                if high is not None:
                    mfe = high / avg_cost - 1.0
                    ep["MFE"] = mfe if ep["MFE"] is None else max(ep["MFE"], mfe)
                if low is not None:
                    mae = low / avg_cost - 1.0
                    ep["MAE"] = mae if ep["MAE"] is None else min(ep["MAE"], mae)
                if close is not None:
                    ep["peak_close"] = close if ep["peak_close"] is None else max(ep["peak_close"], close)
                    if ep["peak_close"]:
                        dd = (ep["peak_close"] - close) / ep["peak_close"]
                        ep["max_drawdown_from_peak"] = max(ep["max_drawdown_from_peak"], dd)

    # finalize open episodes (bounded to the calendar window)
    if ep is not None and qty > 0:
        _finalize_open(ep, avg_cost, qty, price_rows, cal_index, max_cal_date)
        episodes.append(ep)
    return episodes


def _return_metrics(final_return: float | None, mfe: float | None, mae: float | None) -> tuple[float | None, float | None, float | None, float | None]:
    """Derive capture / giveback / recovery from a final return vs its excursions.

    - ``capture_ratio`` — final return as a fraction of peak favorable excursion
      (only defined when MFE > 0; >1 means the close beat the measured peak).
    - ``giveback_return`` — absolute return points given back, ``MFE - final``
      (negative when the close beat the peak: nothing was given back).
    - ``giveback_ratio`` — fraction of the peak profit given back, ``1 - capture``.
    - ``recovery_from_mae`` — how far the position recovered above its worst
      drawdown, ``(1+final)/(1+MAE) - 1`` (only defined when MAE < 0).
    """
    final = _finite(final_return)
    mfe = _finite(mfe)
    mae = _finite(mae)
    capture = giveback_return = giveback_ratio = recovery = None
    if final is not None:
        if mfe is not None and mfe > 0:
            capture = final / mfe
            giveback_return = mfe - final
            giveback_ratio = 1.0 - capture
        if mae is not None and mae < 0 and (1.0 + mae) > 0:
            recovery = (1.0 + final) / (1.0 + mae) - 1.0
    return capture, giveback_return, giveback_ratio, recovery


def _close_episode(
    ep: dict[str, Any],
    exit_date: str,
    row: dict[str, Any],
    episodes: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float | None]],
    calendar: list[str],
    cal_index: dict[str, int],
    score_on,
) -> None:
    symbol = ep["symbol"]
    ep["exit_date"] = exit_date
    ep["exit_reason"] = str(row.get("trade_reason") or "exit")
    ep["exit_score"] = score_on(exit_date, symbol)
    buy_cost = _finite(ep["buy_cost"]) or 0.0
    sell_proceeds = _finite(ep["sell_proceeds"]) or 0.0
    ep["total_buy_cashflow"] = buy_cost
    ep["total_sell_proceeds"] = sell_proceeds
    ep["episode_pnl"] = sell_proceeds - buy_cost
    ep["gross_buy_return"] = (ep["episode_pnl"] / buy_cost) if buy_cost > 0 else None
    ep["realized_return"] = ep["gross_buy_return"]
    ep["unrealized_return"] = None
    if ep["entry_score"] is not None and ep["exit_score"] is not None:
        i = cal_index.get(exit_date)
        if i is not None:
            if i >= 5:
                s5 = score_on(calendar[i - 5], symbol)
                ep["score_delta_5d"] = ep["exit_score"] - s5 if s5 is not None else None
            if i >= 20:
                s20 = score_on(calendar[i - 20], symbol)
                ep["score_delta_20d"] = ep["exit_score"] - s20 if s20 is not None else None
    # Post-exit horizons are measured on the market calendar (exit + N trading
    # days), not the symbol's own (possibly sparse) price dates.  If the symbol
    # has no bar on the target calendar day the value stays None.
    ep["post_exit_return_20d"], ep["post_exit_return_60d"] = _post_exit_returns(exit_date, price_rows, calendar, cal_index)
    entry_i = cal_index.get(ep["entry_date"])
    exit_i = cal_index.get(exit_date)
    ep["holding_days"] = (exit_i - entry_i + 1) if (entry_i is not None and exit_i is not None) else None
    ep["episode_end_date"] = exit_date
    ep["valuation_date"] = None
    # Capture/giveback/recovery compare the episode's *cashflow* return against
    # its *avg_cost* excursions.  That comparison is only well-defined on a
    # simple round trip; complex episodes keep these fields None.
    if ep.get("capture_eligible"):
        cap, gb_ret, gb_ratio, rec = _return_metrics(ep["realized_return"], ep["MFE"], ep["MAE"])
        ep["capture_ratio"], ep["giveback_return"], ep["giveback_ratio"], ep["recovery_from_mae"] = cap, gb_ret, gb_ratio, rec
    else:
        ep["capture_ratio"] = None
        ep["giveback_return"] = None
        ep["giveback_ratio"] = None
        ep["recovery_from_mae"] = None
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)
    episodes.append(ep)


def _finalize_open(
    ep: dict[str, Any],
    avg_cost: float,
    qty: float,
    price_rows: dict[str, dict[str, float | None]],
    cal_index: dict[str, int],
    max_cal_date: str,
) -> None:
    # An open episode ends at the backtest window edge; it is *valued* at the
    # last bar the symbol actually has (a symbol can delist/suspend before the
    # window ends).  holding_days counts to the window end, unrealized/episode_pnl
    # use the valuation close.
    ep["episode_end_date"] = max_cal_date or None
    bounded = sorted(d for d in price_rows if not max_cal_date or d <= max_cal_date)
    exit_date = ep["entry_date"]
    if bounded:
        exit_date = bounded[-1]
    valuation_date = exit_date
    valuation_close = _finite(price_rows.get(valuation_date, {}).get("close"))
    if valuation_close is None:
        for d in reversed(bounded):
            c = _finite(price_rows[d].get("close"))
            if c is not None:
                valuation_date = d
                valuation_close = c
                break
    ep["exit_date"] = exit_date
    ep["valuation_date"] = valuation_date
    buy_cost = _finite(ep["buy_cost"]) or 0.0
    sell_proceeds = _finite(ep["sell_proceeds"]) or 0.0
    ep["total_buy_cashflow"] = buy_cost
    ep["total_sell_proceeds"] = sell_proceeds
    if qty > 0 and valuation_close is not None:
        ep["episode_pnl"] = qty * valuation_close + sell_proceeds - buy_cost
    else:
        ep["episode_pnl"] = sell_proceeds - buy_cost
    ep["gross_buy_return"] = (ep["episode_pnl"] / buy_cost) if buy_cost > 0 else None
    ep["unrealized_return"] = (valuation_close / avg_cost - 1.0) if (valuation_close is not None and avg_cost > 0) else None
    ep["realized_return"] = None
    ep["exit_score"] = None
    entry_i = cal_index.get(ep["entry_date"])
    end_i = cal_index.get(ep["episode_end_date"]) if ep["episode_end_date"] else None
    ep["holding_days"] = (end_i - entry_i + 1) if (entry_i is not None and end_i is not None) else None
    # An open position never completes a full close, so it is never a simple
    # round trip: the cashflow-vs-excursion comparison does not apply and the
    # capture/giveback/recovery fields stay None.
    ep["is_simple_round_trip"] = False
    ep["capture_eligible"] = False
    ep["capture_ratio"] = None
    ep["giveback_return"] = None
    ep["giveback_ratio"] = None
    ep["recovery_from_mae"] = None
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)


def _post_exit_returns(
    exit_date: str,
    price_rows: dict[str, dict[str, float | None]],
    calendar: list[str],
    cal_index: dict[str, int],
) -> tuple[float | None, float | None]:
    """Return +20d / +60d close-to-close returns measured on the market calendar.

    The target day is ``calendar[exit_index + horizon]``; if the symbol has no
    (finite) bar that day the value is None.
    """
    i = cal_index.get(exit_date)
    if i is None:
        return None, None
    exit_close = _finite(price_rows.get(exit_date, {}).get("close"))
    if exit_close is None or exit_close == 0:
        return None, None
    out: list[float | None] = [None, None]
    for j, horizon in enumerate((20, 60)):
        k = i + horizon
        if k < len(calendar):
            c = _finite(price_rows.get(calendar[k], {}).get("close"))
            if c is not None and c != 0:
                out[j] = c / exit_close - 1.0
    return out[0], out[1]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _safe_mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _safe_sum(values: list[float]) -> float | None:
    return sum(values) if values else None


def _fraction(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


HOLDING_BUCKETS = [
    ("0-10", 0, 10),
    ("11-20", 11, 20),
    ("21-40", 21, 40),
    ("41-60", 41, 60),
    ("61-120", 61, 120),
    ("120+", 121, None),
]

CAPTURE_BUCKETS = [
    ("give_back_loss", None, 0.0),
    ("0-10%", 0.0, 0.10),
    ("10-20%", 0.10, 0.20),
    ("20-40%", 0.20, 0.40),
    ("40-80%", 0.40, 0.80),
    ("80-100%", 0.80, 1.0),
    ("over_100%", 1.0, None),
]

MFE_BUCKETS = [
    ("10-20%", 0.10, 0.20),
    ("20-40%", 0.20, 0.40),
    ("40-80%", 0.40, 0.80),
    ("80%+", 0.80, None),
]

# Maximum cumulative-curve points sent to the UI.  The full curve is always
# computed first; only the *rendered* list is downsampled (see _pnl_concentration).
CURVE_CAP = 500


def _snap(value: float) -> float:
    """Round to 9 decimals so bucket-boundary comparisons are fp-robust.

    An MFE computed as ``12.0 / 10.0 - 1`` is ``0.19999999999999996``, which
    would silently fall below the 0.20 lower bound of the 20-40% bucket and
    land in 10-20%.  Snapping to the shared boundary grid (all bucket edges are
    exact multiples of 0.01) restores the intended [lo, hi) semantics.
    """
    return round(value, 9)


def _bucketed_exit_reason_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate diagnostics for one exit-reason bucket (None values excluded)."""
    returns = [v for e in items if (v := _finite(e.get("realized_return"))) is not None]
    mfes = [v for e in items if (v := _finite(e.get("MFE"))) is not None]
    maes = [v for e in items if (v := _finite(e.get("MAE"))) is not None]
    gives_ret = [v for e in items if (v := _finite(e.get("giveback_return"))) is not None]
    gives_ratio = [v for e in items if (v := _finite(e.get("giveback_ratio"))) is not None]
    caps = [v for e in items if (v := _finite(e.get("capture_ratio"))) is not None]
    pe20 = [v for e in items if (v := _finite(e.get("post_exit_return_20d"))) is not None]
    pe60 = [v for e in items if (v := _finite(e.get("post_exit_return_60d"))) is not None]
    return {
        "count": len(items),
        "return_count": len(returns),
        "win_rate": _fraction(sum(1 for r in returns if r > 0), len(returns)),
        "avg_return": _safe_mean(returns),
        "median_return": _median(returns),
        "mfe_count": len(mfes),
        "avg_mfe": _safe_mean(mfes),
        "median_mfe": _median(mfes),
        "mae_count": len(maes),
        "avg_mae": _safe_mean(maes),
        "median_mae": _median(maes),
        "giveback_return_count": len(gives_ret),
        "avg_giveback_return": _safe_mean(gives_ret),
        "median_giveback_return": _median(gives_ret),
        "giveback_ratio_count": len(gives_ratio),
        "avg_giveback_ratio": _safe_mean(gives_ratio),
        "median_giveback_ratio": _median(gives_ratio),
        "capture_count": len(caps),
        "avg_capture": _safe_mean(caps),
        "median_capture": _median(caps),
        "post_exit_20d_count": len(pe20),
        "avg_post_exit_20d": _safe_mean(pe20),
        "post_exit_60d_count": len(pe60),
        "avg_post_exit_60d": _safe_mean(pe60),
    }


def _final_return(e: dict[str, Any]) -> float | None:
    """Realized return for closed episodes, unrealized for open ones."""
    realized = _finite(e.get("realized_return"))
    if realized is not None:
        return realized
    return _finite(e.get("unrealized_return"))


def _holding_horizons(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {
            "bucket": name, "count": 0, "exit_reasons": {},
            "win_rate": None, "median_return": None,
            "median_mfe": None, "median_mae": None,
        }
        for name, _, _ in HOLDING_BUCKETS
    ]
    bucket_returns: dict[str, list[float]] = {name: [] for name, _, _ in HOLDING_BUCKETS}
    bucket_mfes: dict[str, list[float]] = {name: [] for name, _, _ in HOLDING_BUCKETS}
    bucket_maes: dict[str, list[float]] = {name: [] for name, _, _ in HOLDING_BUCKETS}
    for e in episodes:
        h = _finite(e.get("holding_days"))
        if h is None:
            continue
        for i, (name, lo, hi) in enumerate(HOLDING_BUCKETS):
            if h >= lo and (hi is None or h <= hi):
                result[i]["count"] += 1
                reason = str(e.get("exit_reason") or "open")
                result[i]["exit_reasons"][reason] = result[i]["exit_reasons"].get(reason, 0) + 1
                if (r := _finite(e.get("realized_return"))) is not None:
                    bucket_returns[name].append(r)
                if (m := _finite(e.get("MFE"))) is not None:
                    bucket_mfes[name].append(m)
                if (m := _finite(e.get("MAE"))) is not None:
                    bucket_maes[name].append(m)
                break
    for name, _, _ in HOLDING_BUCKETS:
        row = next(r for r in result if r["bucket"] == name)
        returns = bucket_returns[name]
        row["win_rate"] = _fraction(sum(1 for v in returns if v > 0), len(returns))
        row["median_return"] = _median(returns)
        row["median_mfe"] = _median(bucket_mfes[name])
        row["median_mae"] = _median(bucket_maes[name])
    return result


def _capture_ratio_distribution(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket winners (MFE > 10%) by how much of the peak they captured."""
    result = [{"bucket": name, "count": 0, "avg_return": None, "win_rate": None} for name, _, _ in CAPTURE_BUCKETS]
    bucket_returns: dict[str, list[float]] = {name: [] for name, _, _ in CAPTURE_BUCKETS}
    for e in episodes:
        mfe = _finite(e.get("MFE"))
        if mfe is None or _snap(mfe) <= 0.10:
            continue
        capture = _finite(e.get("capture_ratio"))
        if capture is None:
            continue
        cap = _snap(capture)
        final = _final_return(e)
        for i, (name, lo, hi) in enumerate(CAPTURE_BUCKETS):
            if (lo is None or cap >= lo) and (hi is None or cap < hi):
                result[i]["count"] += 1
                if final is not None:
                    bucket_returns[name].append(final)
                break
    for row, name in zip(result, [name for name, _, _ in CAPTURE_BUCKETS]):
        vals = bucket_returns[name]
        row["avg_return"] = _safe_mean(vals)
        row["win_rate"] = _fraction(sum(1 for v in vals if v > 0), len(vals))
    return result


def _mfe_distribution(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket episodes by peak favorable excursion (MFE), 10% and up.

    Each bucket reports the median MFE, final return (realized for closed,
    unrealized for open), giveback return, capture ratio and holding days, so a
    downstream consumer can see how much of a winner's peak was held vs given back.

    ``median_giveback_return`` and ``median_capture_ratio`` are computed over
    capture-eligible episodes only (simple round trips, see
    ``capture_eligible_count``) — complex episodes carry None for those fields
    and are excluded.  ``count`` still covers every episode in the MFE bucket.
    """
    result: list[dict[str, Any]] = []
    for name, lo, hi in MFE_BUCKETS:
        items = [
            e for e in episodes
            if (m := _finite(e.get("MFE"))) is not None
            and (s := _snap(m)) >= lo and (hi is None or s < hi)
        ]
        result.append({
            "bucket": name,
            "count": len(items),
            "median_mfe": _median([v for e in items if (v := _finite(e.get("MFE"))) is not None]),
            "median_final_return": _median([v for e in items if (v := _final_return(e)) is not None]),
            "median_giveback_return": _median([v for e in items if (v := _finite(e.get("giveback_return"))) is not None]),
            "median_capture_ratio": _median([v for e in items if (v := _finite(e.get("capture_ratio"))) is not None]),
            "median_holding_days": _median([v for e in items if (v := _finite(e.get("holding_days"))) is not None]),
        })
    return result


def _stop_quality(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """hard_stop diagnostics, incl. post-exit positive rates.

    ``post_exit_positive_rate_{20d,60d}`` is the fraction of hard_stop episodes
    whose market return over the N-day horizon after exit is positive.  It is a
    *descriptive* measure of the exit-horizon outcome only — NOT a claim that
    the stop rule was wrong.  A true counterfactual (what would have happened
    had the position been kept) belongs to the Exit Rule Ablation chapter.
    """
    stops = [e for e in episodes if str(e.get("exit_reason") or "") == "hard_stop"]
    returns = [v for e in stops if (v := _finite(e.get("realized_return"))) is not None]
    mfes = [v for e in stops if (v := _finite(e.get("MFE"))) is not None]
    maes = [v for e in stops if (v := _finite(e.get("MAE"))) is not None]
    pe20 = [v for e in stops if (v := _finite(e.get("post_exit_return_20d"))) is not None]
    pe60 = [v for e in stops if (v := _finite(e.get("post_exit_return_60d"))) is not None]
    return {
        "hard_stop_count": len(stops),
        "win_rate": _fraction(sum(1 for r in returns if r > 0), len(returns)),
        "avg_return": _safe_mean(returns),
        "avg_mfe": _safe_mean(mfes),
        "avg_mae": _safe_mean(maes),
        "post_exit_20d_count": len(pe20),
        "avg_post_exit_20d": _safe_mean(pe20),
        "post_exit_positive_rate_20d": _fraction(sum(1 for v in pe20 if v > 0), len(pe20)),
        "post_exit_60d_count": len(pe60),
        "avg_post_exit_60d": _safe_mean(pe60),
        "post_exit_positive_rate_60d": _fraction(sum(1 for v in pe60 if v > 0), len(pe60)),
    }


def _downsample_curve(curve: list[dict[str, Any]], cap: int = CURVE_CAP) -> list[dict[str, Any]]:
    """Downsample a sorted curve to at most ``cap`` points for the UI.

    Always keeps the first and last points (the last cumulative therefore
    always equals total_pnl) and samples the interior approximately evenly.
    A curve already at or under the cap is returned untouched.
    """
    n = len(curve)
    if n <= cap:
        return list(curve)
    step = (n - 1) / (cap - 1)
    indices = {0, n - 1}
    for i in range(1, cap - 1):
        indices.add(int(round(i * step)))
    return [curve[i] for i in sorted(indices)]


def _pnl_concentration(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Fat-tail PnL concentration over realized (closed) episodes.

    Episodes are sorted by episode_pnl descending; the cumulative curve tracks
    each rank's contribution to the total *positive* PnL (so a strategy whose
    total is near zero still gets a meaningful tail curve).

    Denominator contract: every ``*_share`` value is
    ``sum(top-k PnL) / positive_pnl_total``, where ``positive_pnl_total`` is the
    sum of positive episode PnL — NOT ``total_pnl`` (losses are not part of the
    share denominator; see the ``share_denominator`` field).  ``top_1pct_share`` /
    ``top_5pct_share`` / ``top_10pct_share`` pick the top ``ceil(n*pct)``
    episodes; ``top_1_episode_share`` / ``top_5_episode_share`` pick a fixed
    count of episodes.  ``pnl_ex_top1`` / ``pnl_ex_top5`` / ``pnl_ex_top10pct``
    are absolute ¥ of ``total_pnl`` remaining after removing those top ranks
    (they run over ALL PnL, losses included).

    The cumulative curve is computed in full, then downsampled to at most
    ``CURVE_CAP`` points for the UI — the first and last points are always kept
    and the last cumulative always equals ``total_pnl``.  ``curve_points``
    reports the full (pre-downsample) length.
    """
    pnls = sorted((v for e in closed if (v := _finite(e.get("episode_pnl"))) is not None), reverse=True)
    n = len(pnls)
    positive_total = sum(p for p in pnls if p > 0)
    total = sum(pnls) if pnls else None

    def top_pct_share(pct: float) -> float | None:
        if not pnls or positive_total <= 0:
            return None
        k = max(1, math.ceil(n * pct))
        return sum(pnls[:k]) / positive_total

    def top_k_share(k: int) -> float | None:
        if not pnls or positive_total <= 0:
            return None
        k = max(1, min(k, n))
        return sum(pnls[:k]) / positive_total

    def pnl_excluding(k: int) -> float | None:
        if not pnls:
            return None
        k = max(1, min(k, n))
        return total - sum(pnls[:k])

    top10pct_k = max(1, math.ceil(n * 0.10)) if pnls else 0
    curve: list[dict[str, Any]] = []
    cum = 0.0
    for rank, p in enumerate(pnls, start=1):
        cum += p
        curve.append({
            "rank": rank,
            "pnl": p,
            "cumulative": cum,
            "share_of_positive": (cum / positive_total) if positive_total > 0 else None,
        })
    return {
        "scope": "closed_realized",
        "n": n,
        "total_pnl": total,
        "positive_pnl_total": positive_total or None,
        "share_denominator": "positive_pnl_total",
        "top_1pct_share": top_pct_share(0.01),
        "top_5pct_share": top_pct_share(0.05),
        "top_10pct_share": top_pct_share(0.10),
        "top_1_episode_share": top_k_share(1),
        "top_5_episode_share": top_k_share(5),
        "pnl_ex_top1": pnl_excluding(1),
        "pnl_ex_top5": pnl_excluding(5),
        "pnl_ex_top10pct": pnl_excluding(top10pct_k),
        "curve_points": n,
        "cumulative_curve": _downsample_curve(curve),
    }


def summarize_episodes(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate episodes into a UI-friendly summary.

    All aggregates exclude None / non-finite values and report the sample size
    they were computed over (``*_count``).  Summary is always computed over the
    FULL episode list; ``limit`` only slices the per-episode detail rows at the
    assembler layer.
    """
    closed = [e for e in episodes if e.get("exit_reason") != "open"]
    returns = [v for e in closed if (v := _finite(e.get("realized_return"))) is not None]
    holding = [v for e in episodes if (v := _finite(e.get("holding_days"))) is not None]

    by_reason: dict[str, list[dict[str, Any]]] = {}
    for e in closed:
        by_reason.setdefault(str(e["exit_reason"]), []).append(e)

    return {
        "total_episodes": len(episodes),
        "closed_episodes": len(closed),
        "open_episodes": len(episodes) - len(closed),
        # Only simple round trips (no partial sell / re-add / never-closed) are
        # eligible for the excursion-vs-cashflow capture/giveback/recovery
        # fields.  Every *_count that feeds those aggregates is a subset of this
        # count, so it makes the aggregate sample denominator explicit.
        "capture_eligible_count": sum(1 for e in episodes if e.get("capture_eligible")),
        "return_count": len(returns),
        "win_rate": _fraction(sum(1 for r in returns if r > 0), len(returns)),
        "avg_return": _safe_mean(returns),
        "median_return": _median(returns),
        "avg_holding_days": _safe_mean(holding),
        "total_pnl": _safe_sum([v for e in closed if (v := _finite(e.get("episode_pnl"))) is not None]),
        "by_exit_reason": [
            {"exit_reason": reason, **_bucketed_exit_reason_summary(items)}
            for reason, items in sorted(by_reason.items())
        ],
        "holding_horizons": _holding_horizons(episodes),
        "capture_ratio_distribution": _capture_ratio_distribution(episodes),
        "mfe_distribution": _mfe_distribution(episodes),
        "stop_quality": _stop_quality(episodes),
        "pnl_concentration": _pnl_concentration(closed),
    }
