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
    finalization never read prices after the last calendar date.  Callers pass
    the immutable daily_summary trade dates so results stay deterministic and
    inside the backtest window.  When omitted, the union of execution dates and
    per-symbol price dates is used as a best-effort fallback.
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
                if d and inst and sc is not None and not pd.isna(sc):
                    score_map[(d, inst)] = float(sc)

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
        "giveback": None,
        "recovery_from_mae": None,
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
            if side != "buy" and qty <= 0:
                continue
            fqty = float(r.get("filled_qty") or 0)
            price = float(r.get("deal_price") or 0)
            fee = float(r.get("total_fee") or 0)
            if fqty <= 0:
                continue
            if side == "buy":
                if qty == 0:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                    opened_today = True
                buy_cost += fqty * price + fee
                buy_qty += fqty
                qty += fqty
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                ep["buy_cost"] += fqty * price + fee
            else:  # sell
                if ep is None:
                    ep = _new_episode(symbol, date, score_on(date, symbol))
                sold = min(fqty, qty)
                ep["sell_proceeds"] += sold * price - fee
                if buy_qty > 0:
                    removed = min(sold, buy_qty)
                    buy_cost = max(0.0, buy_cost - avg_cost * removed)
                    buy_qty -= removed
                qty = max(0.0, qty - fqty)
                avg_cost = buy_cost / buy_qty if buy_qty > 0 else 0.0
                if qty == 0:
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


def _return_metrics(final_return: float | None, mfe: float | None, mae: float | None) -> tuple[float | None, float | None, float | None]:
    """Derive capture / giveback / recovery from a final return vs its excursions.

    - ``capture_ratio`` — final return as a fraction of peak favorable excursion
      (only defined when MFE > 0; >1 means the close beat the measured peak).
    - ``giveback`` — fraction of the peak profit given back (1 - capture).
    - ``recovery_from_mae`` — how far the position recovered above its worst
      drawdown, ``(1+final)/(1+MAE) - 1`` (only defined when MAE < 0).
    """
    final = _finite(final_return)
    mfe = _finite(mfe)
    mae = _finite(mae)
    capture = giveback = recovery = None
    if final is not None:
        if mfe is not None and mfe > 0:
            capture = final / mfe
            giveback = 1.0 - capture
        if mae is not None and mae < 0 and (1.0 + mae) > 0:
            recovery = (1.0 + final) / (1.0 + mae) - 1.0
    return capture, giveback, recovery


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
    cap, gb, rec = _return_metrics(ep["realized_return"], ep["MFE"], ep["MAE"])
    ep["capture_ratio"], ep["giveback"], ep["recovery_from_mae"] = cap, gb, rec
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
    cap, gb, rec = _return_metrics(ep["unrealized_return"], ep["MFE"], ep["MAE"])
    ep["capture_ratio"], ep["giveback"], ep["recovery_from_mae"] = cap, gb, rec
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


def _bucketed_exit_reason_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate diagnostics for one exit-reason bucket (None values excluded)."""
    returns = [v for e in items if (v := _finite(e.get("realized_return"))) is not None]
    mfes = [v for e in items if (v := _finite(e.get("MFE"))) is not None]
    maes = [v for e in items if (v := _finite(e.get("MAE"))) is not None]
    gives = [v for e in items if (v := _finite(e.get("giveback"))) is not None]
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
        "giveback_count": len(gives),
        "avg_giveback": _safe_mean(gives),
        "median_giveback": _median(gives),
        "capture_count": len(caps),
        "avg_capture": _safe_mean(caps),
        "median_capture": _median(caps),
        "post_exit_20d_count": len(pe20),
        "avg_post_exit_20d": _safe_mean(pe20),
        "post_exit_60d_count": len(pe60),
        "avg_post_exit_60d": _safe_mean(pe60),
    }


def _holding_horizons(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [{"bucket": name, "count": 0, "exit_reasons": {}} for name, _, _ in HOLDING_BUCKETS]
    for e in episodes:
        h = _finite(e.get("holding_days"))
        if h is None:
            continue
        for i, (name, lo, hi) in enumerate(HOLDING_BUCKETS):
            if h >= lo and (hi is None or h <= hi):
                result[i]["count"] += 1
                reason = str(e.get("exit_reason") or "open")
                result[i]["exit_reasons"][reason] = result[i]["exit_reasons"].get(reason, 0) + 1
                break
    return result


def _winner_capture_buckets(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket winners (MFE > 10%) by how much of the peak they captured."""
    result = [{"bucket": name, "count": 0, "avg_return": None, "win_rate": None} for name, _, _ in CAPTURE_BUCKETS]
    bucket_returns: dict[str, list[float]] = {name: [] for name, _, _ in CAPTURE_BUCKETS}
    for e in episodes:
        mfe = _finite(e.get("MFE"))
        if mfe is None or mfe <= 0.10:
            continue
        capture = _finite(e.get("capture_ratio"))
        if capture is None:
            continue
        final = _finite(e.get("realized_return"))
        if final is None:
            final = _finite(e.get("unrealized_return"))
        for i, (name, lo, hi) in enumerate(CAPTURE_BUCKETS):
            if (lo is None or capture >= lo) and (hi is None or capture < hi):
                result[i]["count"] += 1
                if final is not None:
                    bucket_returns[name].append(final)
                break
    for row, name in zip(result, [name for name, _, _ in CAPTURE_BUCKETS]):
        vals = bucket_returns[name]
        row["avg_return"] = _safe_mean(vals)
        row["win_rate"] = _fraction(sum(1 for v in vals if v > 0), len(vals))
    return result


def _stop_quality(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """hard_stop diagnostics, incl. false-stop rates (price recovered post-exit)."""
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
        "false_stop_rate_20d": _fraction(sum(1 for v in pe20 if v > 0), len(pe20)),
        "post_exit_60d_count": len(pe60),
        "avg_post_exit_60d": _safe_mean(pe60),
        "false_stop_rate_60d": _fraction(sum(1 for v in pe60 if v > 0), len(pe60)),
    }


def _pnl_concentration(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Fat-tail PnL concentration over realized (closed) episodes.

    Episodes are sorted by episode_pnl descending; the cumulative curve tracks
    each rank's contribution to the total *positive* PnL (so a strategy whose
    total is near zero still gets a meaningful tail curve).  The curve is
    capped at 500 points for the UI.
    """
    pnls = sorted((v for e in closed if (v := _finite(e.get("episode_pnl"))) is not None), reverse=True)
    positive_total = sum(p for p in pnls if p > 0)
    total = sum(pnls) if pnls else None

    def top_share(pct: float) -> float | None:
        if not pnls or positive_total <= 0:
            return None
        k = max(1, math.ceil(len(pnls) * pct))
        return sum(pnls[:k]) / positive_total

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
        "n": len(pnls),
        "total_pnl": total,
        "positive_pnl_total": positive_total or None,
        "top_1pct_share": top_share(0.01),
        "top_5pct_share": top_share(0.05),
        "top_10pct_share": top_share(0.10),
        "cumulative_curve": curve[:500],
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
        "winner_capture_buckets": _winner_capture_buckets(episodes),
        "stop_quality": _stop_quality(episodes),
        "pnl_concentration": _pnl_concentration(closed),
    }
