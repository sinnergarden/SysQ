#!/usr/bin/env python3
"""Layer 4: exit counterfactual for the ablation runs.

For every special exit event (hard_stop / score_delta / winner_trailing /
stale_replacement) records:

  exit_date, old_symbol, exit_reason,
  old_score, old_score_delta20, old_return_at_exit, old_MFE, old_MAE,
  new_symbol, new_score, new_entry_date,
  old_return_next_20d / 60d, new_return_next_20d / 60d,
  swap_edge_20d / 60d  (= new_ret_H - old_ret_H), replacement_gap_days

All forward returns use the run's unified market trading calendar (daily
summary trade dates).  A replacement is the first entry fill (top_n_entry or
stale_replacement_entry) after the exit that buys a symbol not currently held;
pairing is a greedy FIFO over chronologically-sorted fills, which mirrors the
policy's slot-filling on rebalance days.  If no replacement exists, swap_edge
is null.

SwapEdge methodology (corrected): old and new forward returns share a common
start = the replacement's entry date, over the same +20/+60 market-calendar
horizon.  The exit->entry cash gap is reported separately as
`replacement_gap_days`.  A forward return is null (never a stale close) when
the symbol has no valid close on the exact reference or horizon-end date.

Run from the MAIN repo cwd (qsys + data resolve there).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

import pandas as pd

from qsys.data.storage import StockDataStore

SPECIAL_REASONS = {
    "hard_stop", "score_delta", "winner_trailing", "stale_replacement", "rank_exit"
}
ENTRY_REASONS = {"top_n_entry", "stale_replacement_entry"}


def load_calendar(run_dir: Path) -> list[str]:
    daily = pd.read_csv(run_dir / "daily_summary.csv", dtype={"trade_date": str})
    return sorted({str(v).strip() for v in daily["trade_date"].dropna() if str(v).strip()})


def _norm_date(value) -> str:
    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def close_prices(store: StockDataStore, symbol: str) -> dict[str, float]:
    df = store.load_daily(symbol)
    if df is None or df.empty or "trade_date" not in df.columns or "close" not in df.columns:
        return {}
    f = df[["trade_date", "close"]].copy()
    f["trade_date"] = f["trade_date"].map(_norm_date)
    f["close"] = pd.to_numeric(f["close"], errors="coerce")
    f = f.dropna(subset=["close"])
    f = f[f["trade_date"] != ""]
    return dict(zip(f["trade_date"], f["close"]))


def forward_return(
    prices: dict[str, float],
    calendar: list[str],
    ref_date: str,
    horizon: int,
) -> float | None:
    """Close-to-close return over `horizon` trading days after ref_date.

    Strict close lookup: the symbol must have a valid close on BOTH the exact
    reference date and the exact horizon-end date.  If either is missing
    (suspension / delist), the return is null — a stale prior close is never
    substituted.
    """
    if ref_date not in calendar:
        return None
    i = calendar.index(ref_date)
    j = i + horizon
    if j >= len(calendar):
        return None
    end_date = calendar[j]
    p0 = prices.get(ref_date)
    p1 = prices.get(end_date)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def build_events(run_dir: Path, episodes_env: dict) -> dict:
    """Swap analysis.

    Returns {"events": [per-event FIFO pairings, flagged], "day_baskets": [...],
             "pairing_ambiguous_days": n, "n_days_with_exits": n}.

    Two views are produced so FIFO pairing is never taken as a precise causal
    mapping when a day swaps multiple names at once:

      1. events[]   greedy FIFO per-event pairing (exit -> next not-held entry),
                    each flagged multi_exit_day / multi_entry_day / pairing_ambiguous.
      2. day_baskets[]  per exit-day, equal-weight baskets of the symbols exited
                    (old_basket) vs the symbols entered as replacements that day
                    (new_basket), with old_basket_return_20/60, new_basket_return_20/60
                    and basket_swap_edge_20/60 from a common start (the entry
                    day, i.e. same-day refresh means the exit day itself).

    Per P0.2 convention, on multi-swap days the day-level basket outputs are the
    preferred reading; the FIFO pairing is retained only for backward
    compatibility and flagged ambiguous.
    """
    execs = pd.read_csv(run_dir / "executions.csv", dtype={"trade_date": str})
    fills = execs.sort_values(["trade_date", "sequence"]).to_dict("records")

    # Episode lookup by (symbol, exit_date) for old-side metadata.
    ep_by_exit: dict[tuple[str, str], dict] = {}
    for e in episodes_env["data"]["episodes"]:
        if e.get("exit_reason") and e.get("exit_reason") != "open" and e.get("exit_date"):
            ep_by_exit[(str(e["symbol"]), str(e["exit_date"]))] = e

    store = StockDataStore()
    calendar = load_calendar(run_dir)
    price_cache: dict[str, dict[str, float]] = {}

    positions: dict[str, float] = {}
    pending_exits: list[dict] = []
    events: list[dict] = []
    day_baskets: list[dict] = []

    # Per-day accumulation for basket view.
    day_state: dict[str, dict] = {}  # date -> {"exits": {sym: fill}, "entries": {sym: fill}, "consumed": int}
    current_day: str | None = None

    def _flush_day():
        nonlocal current_day
        if current_day is None:
            return
        ds = day_state.get(current_day)
        if ds and ds["exits"]:
            day_baskets.append(_build_day_basket(
                current_day, ds, ep_by_exit, store, price_cache, calendar,
            ))
        current_day = None

    for f in fills:
        side = str(f.get("side") or "")
        reason = str(f.get("trade_reason") or "")
        sym = str(f.get("instrument") or f.get("symbol") or "")
        qty = float(f.get("filled_qty") or 0.0)
        date = str(f.get("trade_date") or "")
        if not sym or qty <= 0:
            continue
        if date != current_day:
            _flush_day()
            current_day = date
            day_state[date] = {"exits": {}, "entries": {}, "consumed": 0}

        if side == "sell":
            positions[sym] = positions.get(sym, 0.0) - qty
            if positions.get(sym, 0.0) <= 1e-9:
                positions.pop(sym, None)
            if reason in SPECIAL_REASONS:
                pending_exits.append(f)
                day_state[date]["exits"][sym] = f
        elif side == "buy" and reason in ENTRY_REASONS:
            if pending_exits and sym not in positions:
                exit_fill = pending_exits.pop(0)
                event = _build_event(exit_fill, f, ep_by_exit, store, price_cache, calendar)
                events.append(event)
                day_state[date]["consumed"] += 1
                day_state[date]["entries"][sym] = f
            positions[sym] = positions.get(sym, 0.0) + qty
        else:
            positions[sym] = positions.get(sym, 0.0) + qty

    _flush_day()

    # Exits with no replacement found (e.g. window end) recorded with null new side.
    for exit_fill in pending_exits:
        events.append(_build_event(exit_fill, None, ep_by_exit, store, price_cache, calendar))

    events.sort(key=lambda e: e["exit_date"])

    # Ambiguity flags derived from per-day exit/entry counts: FIFO order is not
    # a real slot map when a day swaps multiple names at once.
    day_counts = {d: (len(ds["exits"]), len(ds["entries"])) for d, ds in day_state.items()}
    for e in events:
        nx, nnew = day_counts.get(e["exit_date"], (1, 1))
        multi_exit, multi_entry = nx > 1, nnew > 1
        e["multi_exit_day"] = multi_exit
        e["multi_entry_day"] = multi_entry
        e["pairing_ambiguous"] = multi_exit or multi_entry

    n_days = len({b["exit_date"] for b in day_baskets})
    n_ambig = sum(1 for b in day_baskets if b["pairing_ambiguous"])
    return {
        "events": events,
        "day_baskets": day_baskets,
        "n_days_with_exits": n_days,
        "pairing_ambiguous_days": n_ambig,
    }


def _day_multi_flags(day: str, ds: dict) -> tuple[bool, bool]:
    return (len(ds["exits"]) > 1, len(ds["entries"]) > 1)


def _build_day_basket(
    day: str,
    ds: dict,
    ep_by_exit: dict,
    store: StockDataStore,
    price_cache: dict,
    calendar: list[str],
) -> dict:
    """Equal-weight day-level basket swap for an exit day."""
    old_syms = sorted(ds["exits"].keys())
    new_syms = sorted(ds["entries"].keys())
    multi_exit, multi_entry = _day_multi_flags(day, ds)
    common_start = day  # same-day refresh: exits and refill entries share the day
    if not new_syms:
        common_start = day  # unfilled: old side measured from exit day

    for s in old_syms + new_syms:
        if s not in price_cache:
            price_cache[s] = close_prices(store, s)

    def _basket_ret(syms: list[str], h: int) -> float | None:
        vals = [forward_return(price_cache[s], calendar, common_start, h) for s in syms]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    old20, old60 = _basket_ret(old_syms, 20), _basket_ret(old_syms, 60)
    new20, new60 = _basket_ret(new_syms, 20), _basket_ret(new_syms, 60)
    swap20 = (new20 - old20) if (new20 is not None and old20 is not None) else None
    swap60 = (new60 - old60) if (new60 is not None and old60 is not None) else None
    return {
        "exit_date": day,
        "old_basket": old_syms,
        "new_basket": new_syms,
        "multi_exit_day": multi_exit,
        "multi_entry_day": multi_entry,
        "pairing_ambiguous": multi_exit or multi_entry,
        "unfilled_exits": len(old_syms) - len(new_syms),
        "old_basket_return_20d": old20,
        "old_basket_return_60d": old60,
        "new_basket_return_20d": new20,
        "new_basket_return_60d": new60,
        "basket_swap_edge_20d": swap20,
        "basket_swap_edge_60d": swap60,
    }


def _build_event(
    exit_fill: dict,
    entry_fill: dict | None,
    ep_by_exit: dict,
    store: StockDataStore,
    price_cache: dict,
    calendar: list[str],
) -> dict:
    exit_date = str(exit_fill.get("trade_date"))
    reason = str(exit_fill.get("trade_reason"))
    old_sym = str(exit_fill.get("instrument") or exit_fill.get("symbol"))

    ep = ep_by_exit.get((old_sym, exit_date), {})
    old_ret = ep.get("realized_return")

    if entry_fill is not None:
        new_sym = str(entry_fill.get("instrument") or entry_fill.get("symbol"))
        entry_date = str(entry_fill.get("trade_date"))
        new_reason = str(entry_fill.get("trade_reason"))
    else:
        new_sym, entry_date, new_reason = None, None, None

    if old_sym not in price_cache:
        price_cache[old_sym] = close_prices(store, old_sym)
    old_px = price_cache[old_sym]

    def _horizons(px: dict[str, float], ref_date: str) -> tuple[float | None, float | None]:
        return forward_return(px, calendar, ref_date, 20), forward_return(px, calendar, ref_date, 60)

    # Common-start methodology: when a replacement exists, old and new forward
    # returns are both measured from the replacement's entry date over the same
    # +20/+60 horizon.  The exit->entry cash gap is reported separately.
    common_start = entry_date if new_sym is not None else exit_date
    old20, old60 = _horizons(old_px, common_start)

    new20 = new60 = None
    gap_days = None
    new_score = None
    if new_sym is not None:
        assert common_start == entry_date
        if new_sym not in price_cache:
            price_cache[new_sym] = close_prices(store, new_sym)
        new20, new60 = _horizons(price_cache[new_sym], common_start)
        if exit_date in calendar and entry_date in calendar:
            gap_days = calendar.index(entry_date) - calendar.index(exit_date)
        # entry score from the episode that started at entry_date
        for e in ep_by_exit.values():
            if e.get("symbol") == new_sym and e.get("entry_date") == entry_date and e.get("entry_score") is not None:
                new_score = e.get("entry_score")
                break

    swap20 = (new20 - old20) if (new20 is not None and old20 is not None) else None
    swap60 = (new60 - old60) if (new60 is not None and old60 is not None) else None

    return {
        "exit_date": exit_date,
        "old_symbol": old_sym,
        "exit_reason": reason,
        "old_score": ep.get("exit_score"),
        "old_score_delta20": ep.get("score_delta_20d"),
        "old_return_at_exit": old_ret,
        "old_MFE": ep.get("MFE"),
        "old_MAE": ep.get("MAE"),
        "new_symbol": new_sym,
        "new_entry_date": entry_date,
        "new_entry_reason": new_reason,
        "new_score": new_score,
        "replacement_gap_days": gap_days,
        "old_return_next_20d": old20,
        "old_return_next_60d": old60,
        "new_return_next_20d": new20,
        "new_return_next_60d": new60,
        "swap_edge_20d": swap20,
        "swap_edge_60d": swap60,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--episodes-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", default="A5_all")
    args = ap.parse_args()

    runs_root = Path(args.runs_root)
    ep_root = Path(args.episodes_root)
    out = {}
    for name in [s.strip() for s in args.runs.split(",") if s.strip()]:
        run_dir = runs_root / name
        episodes_env = json.loads((ep_root / f"{name}.json").read_text())
        res = build_events(run_dir, episodes_env)
        events = res["events"]
        by_reason = {}
        for e in events:
            by_reason.setdefault(e["exit_reason"], []).append(e)
        out[name] = {
            "n_events": len(events),
            "by_reason": by_reason,
            "day_baskets": res["day_baskets"],
            "n_days_with_exits": res["n_days_with_exits"],
            "pairing_ambiguous_days": res["pairing_ambiguous_days"],
            "n_ambiguous_events": sum(1 for e in events if e["pairing_ambiguous"]),
        }
        print(f"[layer4] {name}: {len(events)} special exits "
              f"({ {k: len(v) for k, v in by_reason.items()} }), "
              f"{len(res['day_baskets'])} day baskets, "
              f"{res['pairing_ambiguous_days']} ambiguous days", flush=True)

    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"-> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
