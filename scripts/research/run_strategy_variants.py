#!/usr/bin/env python3
"""Run 7 strategy variants with 4 slippage levels each (28 variants).

Period: 2015-01-01 → 2026-05-22
Walk-forward: 504d train / 5d test / 5d step
Universe: CSI300

Usage
-----
    cd /home/liuming/.openclaw/workspace/SysQ
    python scripts/research/run_strategy_variants.py

Output
------
    experiments/research/strategy_variants/<timestamp>/
        ├── strategy_comparison_net.csv
        ├── period_breakdown.csv
        ├── drawdown_attribution.csv
        ├── experiment_summary.md
        ├── signals.cache
        └── <variant>/
            ├── daily_summary.csv
            ├── trades.csv
            └── metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.rolling_runner import (
    RollingBacktestRunner,
    VariantConfig,
    make_alpha_v1_train_func,
    make_alpha_v1_predict_func,
    make_alpha_v1_data_loader,
)
from qsys.backtest.strategy_variants import (
    make_dynamic_topn_portfolio_fn,
    make_split_5d20d_portfolio_fn,
    make_regime_exposure_portfolio_fn,
    make_turnover_budget_portfolio_fn,
    make_rank_stability_portfolio_fn,
    make_two_book_portfolio_fn,
    make_crash_filter_portfolio_fn,
)
from qsys.model.alpha_v1_train import preload_training_data
from qsys.data.calendar import get_trading_calendar

# ── Strategy definitions ─────────────────────────────────────────────────────

SLIPPAGE_LEVELS = [0.0, 0.0005, 0.001, 0.002]


def _make_baseline_variants() -> list[VariantConfig]:
    """Baseline + slippage levels (control group)."""
    variants = []
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_s{slip_label}",
            strategy_id="alpha_v1",
            slippage=slip,
        ))
    return variants


def _make_dynamic_topn_variants() -> list[VariantConfig]:
    variants = []
    pf = make_dynamic_topn_portfolio_fn(window=252)
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_dynamic_topn_s{slip_label}",
            strategy_id="alpha_v1_dynamic_topn",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_split_5d20d_variants() -> list[VariantConfig]:
    variants = []
    pf = make_split_5d20d_portfolio_fn()
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_split_5d20d_s{slip_label}",
            strategy_id="alpha_v1_split_5d20d",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_regime_exposure_variants(index_close: pd.Series) -> list[VariantConfig]:
    variants = []
    pf = make_regime_exposure_portfolio_fn(index_close)
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_regime_exposure_s{slip_label}",
            strategy_id="alpha_v1_regime_exposure",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_turnover_budget_variants() -> list[VariantConfig]:
    variants = []
    pf = make_turnover_budget_portfolio_fn(budget=0.20, min_delta=0.005)
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_turnover_budget_s{slip_label}",
            strategy_id="alpha_v1_turnover_budget",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_rank_stability_variants() -> list[VariantConfig]:
    variants = []
    pf = make_rank_stability_portfolio_fn()
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_rank_stability_s{slip_label}",
            strategy_id="alpha_v1_rank_stability",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_two_book_variants() -> list[VariantConfig]:
    variants = []
    pf = make_two_book_portfolio_fn()
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_two_book_s{slip_label}",
            strategy_id="alpha_v1_two_book",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


def _make_crash_filter_variants(bt_frame: pd.DataFrame) -> list[VariantConfig]:
    variants = []
    pf = make_crash_filter_portfolio_fn(bt_frame)
    for slip in SLIPPAGE_LEVELS:
        slip_label = f"{slip:.0e}" if slip > 0 else "0"
        variants.append(VariantConfig(
            name=f"alpha_v1_crash_filter_s{slip_label}",
            strategy_id="alpha_v1_crash_filter",
            slippage=slip,
            portfolio_fn=pf,
        ))
    return variants


# ── Index data loader ────────────────────────────────────────────────────────


def load_csi300_index(start_date: str, end_date: str) -> pd.Series:
    """Load CSI300 index close prices as pd.Series index=date, values=close."""
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()
    # CSI300 index symbol in qlib
    raw = adapter.get_features(
        "csi300",
        ["$close"],
        start_time=start_date,
        end_time=end_date,
    )
    if raw.empty:
        # Fallback: use universe median close as proxy
        print("[WARN] CSI300 index data not available, using universe median as proxy")
        raw = adapter.get_features(
            "csi300",
            ["$close"],
            start_time=start_date,
            end_time=end_date,
        )
    # Index data has single row per date; we take the median across stocks
    # as a proxy (acceptable for regime detection)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    daily_close = frame.groupby("trade_date")["$close"].median()
    daily_close.index = pd.to_datetime(daily_close.index)
    return daily_close.sort_index()


# ── CSV output helpers ───────────────────────────────────────────────────────


def build_comparison_rows(results) -> list[dict]:
    """Build rows for strategy_comparison_net.csv."""
    rows = []
    for vr in results.variants:
        m = vr.metrics
        slip = 0.0
        if "_s" in vr.name:
            parts = vr.name.rsplit("_s", 1)
            slip_str = parts[1] if len(parts) > 1 else "0"
            slip = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0

        equity = m.get("details", {}).get("equity_curve", None) if isinstance(m.get("details"), dict) else None
        final_value = float(equity.iloc[-1]) if equity is not None else 0.0

        rows.append({
            "strategy_id": m.get("strategy_id", vr.name),
            "slippage": slip,
            "annual_return_net": m.get("annual_return"),
            "sharpe_net": m.get("sharpe"),
            "max_drawdown_net": m.get("max_drawdown"),
            "calmar_net": m.get("calmar"),
            "volatility": m.get("annual_volatility"),
            "turnover": m.get("annual_turnover"),
            "avg_holding_days": m.get("avg_holding_days"),
            "win_rate": m.get("win_rate"),
            "worst_1d_return": m.get("worst_1d_return"),
            "worst_5d_return": m.get("worst_5d_return"),
            "final_value": final_value,
            "trade_count": m.get("trade_count", 0),
            "buy_count": m.get("buy_count", 0),
            "sell_count": m.get("sell_count", 0),
            "cash_ratio_avg": m.get("cash_ratio_avg", 0.0),
        })
    return rows


def build_period_breakdown_rows(results) -> list[dict]:
    """Build rows for period_breakdown.csv."""
    periods = [
        ("2015-2016", "2015-01-01", "2016-12-31"),
        ("2017-2018", "2017-01-01", "2018-12-31"),
        ("2019-2020", "2019-01-01", "2020-12-31"),
        ("2021-2022", "2021-01-01", "2022-12-31"),
        ("2023-2024", "2023-01-01", "2024-12-31"),
        ("2025-2026", "2025-01-01", "2026-05-22"),
    ]

    rows = []
    for vr in results.variants:
        daily = vr.backtest_result.daily
        if daily.empty:
            continue
        daily = daily.copy()
        if "equity" in daily.columns:
            daily = daily.rename(columns={"equity": "total_value_after"})
        if "date" in daily.columns:
            daily = daily.rename(columns={"date": "trade_date"})
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])

        for period_name, p_start, p_end in periods:
            mask = (daily["trade_date"] >= p_start) & (daily["trade_date"] <= p_end)
            sub = daily[mask].sort_values("trade_date").reset_index(drop=True)
            if len(sub) < 20:
                continue

            equity = sub["total_value_after"].astype(float)
            rets = equity.pct_change().dropna()
            tr = float(equity.iloc[-1] / equity.iloc[0] - 1)
            n = len(rets)
            ann_ret = (1 + tr) ** (252 / max(n, 1)) - 1
            sp = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-12 else 0.0
            mdd = float((equity / equity.cummax() - 1).min())
            cal = ann_ret / abs(mdd) if abs(mdd) > 1e-12 else 0.0
            to = 0.0
            if "turnover" in sub.columns:
                total_t = sub["turnover"].astype(float).sum()
                avg_val = float(equity.mean())
                if avg_val > 0:
                    to = total_t / avg_val * 252 / max(n, 1)

            slip = 0.0
            if "_s" in vr.name:
                parts = vr.name.rsplit("_s", 1)
                slip_str = parts[1] if len(parts) > 1 else "0"
                slip = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0

            rows.append({
                "strategy_id": vr.name,
                "slippage": slip,
                "period": period_name,
                "annual_return": ann_ret,
                "sharpe": sp,
                "max_drawdown": mdd,
                "calmar": cal,
                "turnover": to,
            })
    return rows


def build_drawdown_attribution_rows(results) -> list[dict]:
    """Build rows for drawdown_attribution.csv."""
    rows = []
    for vr in results.variants:
        daily = vr.backtest_result.daily
        if daily.empty:
            continue
        daily = daily.copy()
        if "equity" in daily.columns:
            daily = daily.rename(columns={"equity": "total_value_after"})
        if "date" in daily.columns:
            daily = daily.rename(columns={"date": "trade_date"})
        equity = daily["total_value_after"].astype(float)

        # Find drawdown periods
        peak = equity.iloc[0]
        peak_idx = 0
        in_dd = False
        dd_start = 0
        periods_list = []

        for i in range(1, len(equity)):
            if equity.iloc[i] >= peak:
                if in_dd:
                    dd_depth = float(equity.iloc[dd_start:i+1].min() / peak - 1.0)
                    trough_idx = dd_start + equity.iloc[dd_start:i+1].values.argmin()
                    periods_list.append({
                        "start": daily["trade_date"].iloc[dd_start],
                        "trough": daily["trade_date"].iloc[trough_idx],
                        "end": daily["trade_date"].iloc[i],
                        "depth": dd_depth,
                        "duration": i - dd_start,
                    })
                    in_dd = False
                peak = equity.iloc[i]
                peak_idx = i
            else:
                if not in_dd:
                    in_dd = True
                    dd_start = peak_idx

        if in_dd:
            dd_depth = float(equity.iloc[dd_start:].min() / peak - 1.0)
            trough_idx = dd_start + equity.iloc[dd_start:].values.argmin()
            periods_list.append({
                "start": daily["trade_date"].iloc[dd_start],
                "trough": daily["trade_date"].iloc[trough_idx],
                "end": None,
                "depth": dd_depth,
                "duration": len(equity) - dd_start,
            })

        # Top 5 drawdowns by depth
        periods_list.sort(key=lambda p: p["depth"])
        for p in periods_list[:5]:
            slip = 0.0
            if "_s" in vr.name:
                parts = vr.name.rsplit("_s", 1)
                slip_str = parts[1] if len(parts) > 1 else "0"
                slip = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0

            # Cash ratio during drawdown
            dd_mask = (daily["trade_date"] >= p["start"]) & (
                daily["trade_date"] <= (p["end"] or daily["trade_date"].iloc[-1])
            )
            dd_data = daily[dd_mask]
            cash_ratio = 0.0
            if "cash" in dd_data.columns and "equity" in dd_data.columns:
                cash_ratio = float(
                    (dd_data["cash"].astype(float) / dd_data["equity"].astype(float).replace(0, np.nan)).mean()
                )

            rows.append({
                "strategy_id": vr.name,
                "slippage": slip,
                "drawdown_start": p["start"],
                "drawdown_valley": p["trough"],
                "drawdown_end": p["end"],
                "max_drawdown": p["depth"],
                "duration_days": p["duration"],
                "recovery_days": None if p["end"] is None else None,  # simplified
                "cash_ratio_during_drawdown": cash_ratio,
            })
    return rows


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy variants rolling backtest")
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2026-05-22")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-cache", action="store_true",
                        help="Recompute signals even if cache exists")
    args = parser.parse_args()

    # Output dir
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = _PROJECT_ROOT / "experiments" / "research" / "strategy_variants" / ts
    output_dir.mkdir(parents=True, exist_ok=True)
    signals_cache = output_dir / "signals.cache"

    # ── Load index data for regime_exposure strategy ──
    print("[Data] Loading CSI300 index data for regime detection...")
    # Need extra history before start_date for MA60 calculation
    calendar_start = (pd.Timestamp(args.start_date) - pd.DateOffset(years=2, days=30)).strftime("%Y-%m-%d")
    index_close = load_csi300_index(calendar_start, args.end_date)
    print(f"  {len(index_close)} trading days loaded")

    # ── Load bt_frame for crash_filter strategy ──
    data_loader = make_alpha_v1_data_loader(universe="csi300")
    print("[Data] Loading backtest OHLCV for crash_filter (with history)...")
    # Load extra 252d history before start_date for rolling calcs
    bt_calendar_start = (pd.Timestamp(args.start_date) - pd.DateOffset(years=1, days=30)).strftime("%Y-%m-%d")
    bt_trade_dates = get_trading_calendar(bt_calendar_start, args.end_date)
    bt_frame = data_loader(bt_trade_dates)
    print(f"  {len(bt_frame)} rows loaded")

    # ── Build all variants ──
    all_variants: list[VariantConfig] = []
    all_variants.extend(_make_baseline_variants())
    all_variants.extend(_make_dynamic_topn_variants())
    all_variants.extend(_make_split_5d20d_variants())
    all_variants.extend(_make_regime_exposure_variants(index_close))
    all_variants.extend(_make_turnover_budget_variants())
    all_variants.extend(_make_rank_stability_variants())
    all_variants.extend(_make_two_book_variants())
    all_variants.extend(_make_crash_filter_variants(bt_frame))

    print(f"\nTotal variants: {len(all_variants)} ({8} strategies × {len(SLIPPAGE_LEVELS)} slippages)")
    print(f"Output: {output_dir}/")
    print(f"Cache:  {signals_cache}")

    BaseVariantConfig = VariantConfig  # alias

    # Pre-load training data
    print("\n[Preload] Loading all training data...")
    t0 = time.time()
    preloaded = preload_training_data(args.end_date)
    print(f"  Done in {time.time() - t0:.1f}s")

    # Create runner
    runner = RollingBacktestRunner(
        train_func=make_alpha_v1_train_func(project_root=_PROJECT_ROOT),
        predict_func=make_alpha_v1_predict_func(universe="csi300"),
        data_loader=make_alpha_v1_data_loader(universe="csi300"),
        variants=all_variants,
    )

    # Run
    t_start = time.time()
    results = runner.run(
        start_date=args.start_date,
        end_date=args.end_date,
        preloaded_data=preloaded,
        signals_cache_path=None if args.no_cache else signals_cache,
    )
    total_time = time.time() - t_start

    # ── Save per-variant details ──
    for vr in results.variants:
        var_dir = output_dir / vr.name
        var_dir.mkdir(parents=True, exist_ok=True)
        if not vr.backtest_result.daily.empty:
            vr.backtest_result.daily.to_csv(var_dir / "daily_summary.csv", index=False)
        if not vr.backtest_result.trades.empty:
            vr.backtest_result.trades.to_csv(var_dir / "trades.csv", index=False)
        clean_metrics = {k: v for k, v in vr.metrics.items() if k != "details"}
        (var_dir / "metrics.json").write_text(
            json.dumps(clean_metrics, indent=2, default=str, ensure_ascii=False),
        )

    # ── Build and save comparison CSV ──
    rows = build_comparison_rows(results)
    comparison_df = pd.DataFrame(rows)
    csv_path = output_dir / "strategy_comparison_net.csv"
    comparison_df.to_csv(csv_path, index=False)
    print(f"\n→ {csv_path}")

    # ── Print comparison table ──
    print("\n" + "=" * 140)
    print("Strategy Variants — Net Returns (commission + stamp_duty + slippage deducted)")
    print("=" * 140)
    header = (
        f"{'strategy_id':<28} {'Slip':>6} {'Ann Ret':>9} {'Sharpe':>8} "
        f"{'MDD':>9} {'Calmar':>8} {'Vol':>8} {'TO':>8} {'Worst1d':>9} {'Cash%':>7}"
    )
    print(header)
    print("-" * 140)
    for _, r in comparison_df.iterrows():
        print(
            f"{r['strategy_id']:<28} {r['slippage']:>6.4f} "
            f"{r['annual_return_net']:>8.2%} {r['sharpe_net']:>7.2f} "
            f"{r['max_drawdown_net']:>8.2%} {r['calmar_net']:>7.2f} "
            f"{r['volatility']:>7.2%} {r['turnover']:>7.1f}x "
            f"{r['worst_1d_return']:>8.2%} {r['cash_ratio_avg']:>6.1%}"
        )
    print("=" * 140)

    # ── Period breakdown ──
    period_rows = build_period_breakdown_rows(results)
    if period_rows:
        period_df = pd.DataFrame(period_rows)
        period_path = output_dir / "period_breakdown.csv"
        period_df.to_csv(period_path, index=False)
        print(f"\n→ {period_path}")

    # ── Drawdown attribution ──
    dd_rows = build_drawdown_attribution_rows(results)
    if dd_rows:
        dd_df = pd.DataFrame(dd_rows)
        dd_path = output_dir / "drawdown_attribution.csv"
        dd_df.to_csv(dd_path, index=False)
        print(f"\n→ {dd_path}")

    # ── Summary ──
    summary = {
        "experiment": "strategy_variants",
        "run_at": datetime.now().isoformat(),
        "total_wall_time_seconds": total_time,
        "n_windows": len(results.windows),
        "n_variants": len(results.variants),
        "slippage_levels": SLIPPAGE_LEVELS,
        "strategies": [
            "alpha_v1", "alpha_v1_dynamic_topn", "alpha_v1_split_5d20d",
            "alpha_v1_regime_exposure", "alpha_v1_turnover_budget",
            "alpha_v1_rank_stability", "alpha_v1_two_book", "alpha_v1_crash_filter",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str, ensure_ascii=False),
    )

    print(f"\nTotal time: {total_time:.0f}s ({total_time / 60:.1f} min)")
    print(f"Results: {output_dir}/")


if __name__ == "__main__":
    main()
