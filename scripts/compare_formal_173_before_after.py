#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BEFORE = PROJECT_ROOT / "scratch/formal_173_compare"
AFTER = PROJECT_ROOT / "scratch/formal_173_compare_fixed"
OUT = PROJECT_ROOT / "reports/audits/formal_173_2025_01_fix_compare"
OUT.mkdir(parents=True, exist_ok=True)


def load_backtest_curve(base: Path) -> pd.DataFrame:
    path = base / "experiments" / "backtest_result.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[(df["date"] >= "2025-01-01") & (df["date"] <= "2025-01-31")].copy()


def load_trades(base: Path) -> pd.DataFrame:
    path = base / "experiments" / "backtest_trades.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[(df["date"] >= "2025-01-01") & (df["date"] <= "2025-01-31")].copy()


def load_daily_top_picks(base: Path) -> pd.DataFrame:
    rows = []
    daily_root = base / "daily"
    for day_dir in sorted([p for p in daily_root.iterdir() if p.is_dir() and '2025-01-01' <= p.name <= '2025-01-31']):
        signal_files = sorted((day_dir / "pre_open" / "signals").glob("signal_basket_*.csv"))
        if not signal_files:
            continue
        df = pd.read_csv(signal_files[-1])
        rank_col = "score_rank" if "score_rank" in df.columns else "rank"
        if rank_col in df.columns:
            top = df.sort_values(rank_col).head(5)
        else:
            top = df.head(5)
        rows.append({
            "execution_date": day_dir.name,
            "signal_count": int(len(df)),
            "top_picks": list(top["symbol"].astype(str)),
        })
    return pd.DataFrame(rows)


def main() -> None:
    before_curve = load_backtest_curve(BEFORE)
    after_curve = load_backtest_curve(AFTER)
    before_trades = load_trades(BEFORE)
    after_trades = load_trades(AFTER)
    before_picks = load_daily_top_picks(BEFORE)
    after_picks = load_daily_top_picks(AFTER)

    curve_cmp = before_curve[["date", "total_assets", "daily_return"]].merge(
        after_curve[["date", "total_assets", "daily_return"]], on="date", how="outer", suffixes=("_before", "_after")
    )
    picks_cmp = before_picks.merge(after_picks, on="execution_date", how="outer", suffixes=("_before", "_after"))
    picks_cmp["top_pick_overlap_count"] = picks_cmp.apply(
        lambda r: len(set(r["top_picks_before"] or []) & set(r["top_picks_after"] or [])) if isinstance(r.get("top_picks_before"), list) and isinstance(r.get("top_picks_after"), list) else None,
        axis=1,
    )
    trade_keys = ["date", "symbol", "side", "filled_amount"]
    merged_trades = before_trades[trade_keys].merge(after_trades[trade_keys], on=trade_keys, how="inner")

    summary = {
        "end_assets_before": float(before_curve["total_assets"].iloc[-1]) if not before_curve.empty else None,
        "end_assets_after": float(after_curve["total_assets"].iloc[-1]) if not after_curve.empty else None,
        "return_before": float(before_curve["total_assets"].iloc[-1] / before_curve["total_assets"].iloc[0] - 1) if len(before_curve) >= 1 else None,
        "return_after": float(after_curve["total_assets"].iloc[-1] / after_curve["total_assets"].iloc[0] - 1) if len(after_curve) >= 1 else None,
        "daily_signal_count_before_mean": float(before_picks["signal_count"].mean()) if not before_picks.empty else None,
        "daily_signal_count_after_mean": float(after_picks["signal_count"].mean()) if not after_picks.empty else None,
        "top_picks_overlap_mean": float(picks_cmp["top_pick_overlap_count"].dropna().mean()) if not picks_cmp.empty else None,
        "filled_trade_match_count": int(len(merged_trades)),
        "filled_trade_count_before": int(len(before_trades)),
        "filled_trade_count_after": int(len(after_trades)),
    }

    curve_cmp.to_csv(OUT / "curve_compare.csv", index=False)
    picks_cmp.to_csv(OUT / "signal_compare.csv", index=False)
    merged_trades.to_csv(OUT / "matched_trades.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
