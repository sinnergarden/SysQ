from __future__ import annotations

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


def _price_lookup(frame: pd.DataFrame | None) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Normalize a raw daily frame into {date: {high, low, close}} + sorted dates."""
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
    rows: dict[str, dict[str, float]] = {}
    for _, r in f.iterrows():
        d = str(r["d"])
        high = r.get("high")
        low = r.get("low")
        rows[d] = {
            "high": float(high) if high is not None and pd.notna(high) else None,
            "low": float(low) if low is not None and pd.notna(low) else None,
            "close": float(r["close"]),
        }
    return rows, sorted(rows.keys())


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
    It defines ``holding_days``, the ``score_delta_*`` lookbacks, and the
    window bound: excursions and open-episode finalization never read prices
    after the last calendar date.  Callers pass the immutable daily_summary
    trade dates so results stay deterministic and inside the backtest window.
    When omitted, the union of execution dates and per-symbol price dates is
    used as a best-effort fallback.
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
        for _, srow in sf.iterrows():
            d = _norm_date(srow.get("trade_date") or srow.get("date"))
            inst = str(srow.get(inst_col) or "")
            if d and inst:
                try:
                    score_map[(d, inst)] = float(srow["score"])
                except (TypeError, ValueError, KeyError):
                    continue

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
        "buy_cost": 0.0,
        "sell_proceeds": 0.0,
        "peak_close": None,
    }


def _simulate_symbol(
    symbol: str,
    fills: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float]],
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
                    _close_episode(ep, date, r, episodes, price_rows, price_dates, calendar, cal_index, score_on)
                    ep = None
                    closed_today = True
        # excursion update after the day's fills (only while holding).
        # On a same-day close-then-reopen the day's high/low may precede the
        # new entry, so the new episode's excursion starts the following day.
        if ep is not None and qty > 0 and avg_cost > 0 and not (opened_today and closed_today):
            prow = price_rows.get(date)
            if prow:
                if prow.get("high") is not None:
                    mfe = prow["high"] / avg_cost - 1.0
                    ep["MFE"] = mfe if ep["MFE"] is None else max(ep["MFE"], mfe)
                if prow.get("low") is not None:
                    mae = prow["low"] / avg_cost - 1.0
                    ep["MAE"] = mae if ep["MAE"] is None else min(ep["MAE"], mae)
                close = prow.get("close")
                if close is not None:
                    ep["peak_close"] = close if ep["peak_close"] is None else max(ep["peak_close"], close)
                    if ep["peak_close"]:
                        dd = (ep["peak_close"] - close) / ep["peak_close"]
                        ep["max_drawdown_from_peak"] = max(ep["max_drawdown_from_peak"], dd)

    # finalize open episodes (bounded to the calendar window)
    if ep is not None and qty > 0:
        exit_date = ep["entry_date"]
        if price_rows:
            bounded = [d for d in price_rows if not max_cal_date or d <= max_cal_date]
            if bounded:
                exit_date = max(bounded)
        _finalize_open(ep, avg_cost, price_rows, cal_index, exit_date)
        episodes.append(ep)
    return episodes


def _close_episode(
    ep: dict[str, Any],
    exit_date: str,
    row: dict[str, Any],
    episodes: list[dict[str, Any]],
    price_rows: dict[str, dict[str, float]],
    price_dates: list[str],
    calendar: list[str],
    cal_index: dict[str, int],
    score_on,
) -> None:
    symbol = ep["symbol"]
    ep["exit_date"] = exit_date
    ep["exit_reason"] = str(row.get("trade_reason") or "exit")
    ep["exit_score"] = score_on(exit_date, symbol)
    ep["realized_return"] = (ep["sell_proceeds"] / ep["buy_cost"] - 1.0) if ep["buy_cost"] > 0 else None
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
    ep["post_exit_return_20d"], ep["post_exit_return_60d"] = _post_exit_returns(exit_date, price_rows, price_dates)
    entry_i = cal_index.get(ep["entry_date"])
    exit_i = cal_index.get(exit_date)
    ep["holding_days"] = (exit_i - entry_i + 1) if (entry_i is not None and exit_i is not None) else None
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)
    episodes.append(ep)


def _finalize_open(
    ep: dict[str, Any],
    avg_cost: float,
    price_rows: dict[str, dict[str, float]],
    cal_index: dict[str, int],
    exit_date: str,
) -> None:
    ep["exit_date"] = exit_date
    last_close = price_rows.get(exit_date, {}).get("close")
    if last_close is not None and avg_cost > 0:
        ep["unrealized_return"] = last_close / avg_cost - 1.0
    ep["exit_score"] = None
    ep["realized_return"] = None
    entry_i = cal_index.get(ep["entry_date"])
    exit_i = cal_index.get(exit_date)
    ep["holding_days"] = (exit_i - entry_i + 1) if (entry_i is not None and exit_i is not None) else None
    ep.pop("buy_cost", None)
    ep.pop("sell_proceeds", None)
    ep.pop("peak_close", None)


def _post_exit_returns(
    exit_date: str,
    price_rows: dict[str, dict[str, float]],
    price_dates: list[str],
) -> tuple[float | None, float | None]:
    if exit_date not in price_dates:
        return None, None
    i = price_dates.index(exit_date)
    exit_close = price_rows[exit_date].get("close")
    if exit_close is None or exit_close == 0:
        return None, None
    out: list[float | None] = [None, None]
    for j, horizon in enumerate((20, 60)):
        k = i + horizon
        if k < len(price_dates):
            c = price_rows[price_dates[k]].get("close")
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


def summarize_episodes(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate episodes into a UI-friendly summary."""
    closed = [e for e in episodes if e.get("exit_reason") != "open"]
    returns = [e["realized_return"] for e in closed if e.get("realized_return") is not None]
    holding = [e["holding_days"] for e in episodes if e.get("holding_days") is not None]
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for e in closed:
        by_reason.setdefault(str(e["exit_reason"]), []).append(e)
    return {
        "total_episodes": len(episodes),
        "closed_episodes": len(closed),
        "open_episodes": len(episodes) - len(closed),
        "win_rate": (sum(1 for r in returns if r > 0) / len(returns)) if returns else None,
        "avg_return": (sum(returns) / len(returns)) if returns else None,
        "median_return": _median(returns),
        "avg_holding_days": (sum(holding) / len(holding)) if holding else None,
        "by_exit_reason": [
            {
                "exit_reason": reason,
                "count": len(items),
                "win_rate": (sum(1 for e in items if (e.get("realized_return") or 0) > 0) / len(items)) if items else None,
                "avg_return": (sum(e["realized_return"] or 0 for e in items) / len(items)) if items else None,
                "median_return": _median([e["realized_return"] or 0 for e in items]),
                "avg_mfe": (sum(e.get("MFE") or 0 for e in items) / len(items)) if items else None,
                "avg_mae": (sum(e.get("MAE") or 0 for e in items) / len(items)) if items else None,
            }
            for reason, items in sorted(by_reason.items())
        ],
    }
