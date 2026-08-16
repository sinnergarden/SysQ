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

SPECIAL_REASONS = {"hard_stop", "score_delta", "winner_trailing", "stale_replacement"}
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
    """Close-to-close return over `horizon` trading days after ref_date."""
    if ref_date not in calendar:
        return None
    i = calendar.index(ref_date)
    j = i + horizon
    if j >= len(calendar):
        return None
    end_date = calendar[j]
    p0 = prices.get(ref_date)
    # Last available close at or before end_date (handles suspensions/delists).
    p1 = None
    for k in range(j, i, -1):
        p1 = prices.get(calendar[k])
        if p1 is not None:
            break
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def build_events(run_dir: Path, episodes_env: dict) -> list[dict]:
    """Pair special exits with replacement entries via greedy FIFO simulation."""
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

    for f in fills:
        side = str(f.get("side") or "")
        reason = str(f.get("trade_reason") or "")
        sym = str(f.get("instrument") or f.get("symbol") or "")
        qty = float(f.get("filled_qty") or 0.0)
        date = str(f.get("trade_date") or "")
        if not sym or qty <= 0:
            continue

        if side == "sell":
            positions[sym] = positions.get(sym, 0.0) - qty
            if positions.get(sym, 0.0) <= 1e-9:
                positions.pop(sym, None)
            if reason in SPECIAL_REASONS:
                pending_exits.append(f)
        elif side == "buy" and reason in ENTRY_REASONS:
            # A genuinely new entry fills the oldest freed slot.
            if pending_exits and sym not in positions:
                exit_fill = pending_exits.pop(0)
                event = _build_event(exit_fill, f, ep_by_exit, store, price_cache, calendar)
                events.append(event)
            positions[sym] = positions.get(sym, 0.0) + qty
        else:
            # non-entry buys (e.g. top-ups within holding) still update positions
            positions[sym] = positions.get(sym, 0.0) + qty

    # Exits with no replacement found (e.g. window end) recorded with null new side.
    for exit_fill in pending_exits:
        events.append(_build_event(exit_fill, None, ep_by_exit, store, price_cache, calendar))

    events.sort(key=lambda e: e["exit_date"])
    return events


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

    old20, old60 = _horizons(old_px, exit_date)

    new20 = new60 = None
    gap_days = None
    new_score = None
    if new_sym is not None:
        if new_sym not in price_cache:
            price_cache[new_sym] = close_prices(store, new_sym)
        new20, new60 = _horizons(price_cache[new_sym], entry_date)
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
        events = build_events(run_dir, episodes_env)
        by_reason = {}
        for e in events:
            by_reason.setdefault(e["exit_reason"], []).append(e)
        out[name] = {"n_events": len(events), "by_reason": by_reason}
        print(f"[layer4] {name}: {len(events)} special exits "
              f"({ {k: len(v) for k, v in by_reason.items()} })", flush=True)

    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"-> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
