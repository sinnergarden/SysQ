#!/usr/bin/env python3
"""Derive behavior episodes for an ablation backtest run dir.

Mirrors the assembler's get_behavior_episodes data loading but reads from an
arbitrary output dir (does NOT touch the canonical backtests index):

  executions.csv          -> fill rows
  daily_summary.csv       -> backtest-window trading calendar
  manifest.json           -> signal_id / signal_run_id
  predictions.parquet     -> point-in-time scores (main repo signals root)
  StockDataStore          -> raw daily bars (main repo data store)

Usage (run from the MAIN repo cwd so qsys + data resolve):
  python <this> --run-dir <ablation/AX_dir> [--out <path.json>]

Output envelope matches the behavior_episodes API shape so downstream
analytics scripts are identical across runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Script runs from the main repo cwd; that repo must be importable.
sys.path.insert(0, os.getcwd())

import pandas as pd

from qsys.data.storage import StockDataStore
from qsys.research_ui.behavior import derive_episodes, summarize_episodes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--signals-root", default=None,
                    help="signals root; default <run-dir>/../../../../../../data/research/signals")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    signal_id = str(manifest.get("signal_id") or "")
    signal_run_id = str(manifest.get("signal_run_id") or "")

    rows_path = run_dir / "executions.csv"
    rows = []
    if rows_path.exists():
        rows = pd.read_csv(rows_path).to_dict("records")

    scores_frame: pd.DataFrame | None = None
    if signal_id and signal_run_id:
        # Default to <cwd>/data/research/signals (script is run from the main
        # repo cwd, which holds the canonical signal artifacts).
        signals_root = Path(args.signals_root) if args.signals_root else (
            Path.cwd() / "data" / "research" / "signals"
        )
        pred = signals_root / signal_id / signal_run_id / "predictions.parquet"
        if pred.exists():
            try:
                scores_frame = pd.read_parquet(pred)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"  warn: could not read predictions: {exc}", file=__import__("sys").stderr)
                scores_frame = None

    store = StockDataStore()
    symbols = sorted({
        str(r.get("instrument") or r.get("symbol") or "")
        for r in rows if r.get("instrument") or r.get("symbol")
    })
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = store.load_daily(symbol)
        if df is not None and not df.empty:
            prices_by_symbol[symbol] = df

    calendar: list[str] | None = None
    daily = run_dir / "daily_summary.csv"
    if daily.exists():
        try:
            daily_df = pd.read_csv(daily, usecols=["trade_date"])
            calendar = sorted({str(v).strip() for v in daily_df["trade_date"].dropna() if str(v).strip()})
        except Exception:
            calendar = None
    if not calendar:
        dates = manifest.get("trading_dates")
        if isinstance(dates, list) and dates:
            calendar = sorted({str(v) for v in dates if str(v).strip()})

    episodes = derive_episodes(
        rows, prices_by_symbol=prices_by_symbol, scores_frame=scores_frame, calendar=calendar
    )
    summary = summarize_episodes(episodes)
    run_id = f"canonical__{manifest.get('strategy_run_id','')}__{manifest.get('backtest_id','')}"
    envelope = {
        "api_version": "v1",
        "meta": {
            "resource": "behavior_episodes",
            "run_id": run_id,
            "limit": args.limit,
            "total_episodes": len(episodes),
            "returned_episodes": min(len(episodes), args.limit),
            "truncated": len(episodes) > args.limit,
        },
        "data": {"episodes": episodes[:args.limit], "summary": summary},
        "run_id": run_id,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(envelope, indent=1, ensure_ascii=False))
    print(f"{manifest.get('backtest_id')}: {len(episodes)} episodes "
          f"(closed {summary.get('closed_episodes')}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
