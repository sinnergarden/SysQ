#!/usr/bin/env python3
"""Unified backtest runner — prediction-caching strategy variant comparison.

All variants share the same ML model and data — only portfolio parameters
differ.  Predictions are generated **once** (cached to disk), then each
variant replays plan + execution from the cached predictions.

Two-phase execution:
  1. Precache phase — generate predictions for all trading dates (slow, ~2h)
  2. Replay phase — each variant runs from cached predictions (~5 min each)

Usage
-----
    python scripts/research/run_backtest_variants.py
    python scripts/research/run_backtest_variants.py \\
        --start-date 2025-01-01 --end-date 2026-05-22 \\
        --initial-capital 10000000

    # Re-run from existing prediction cache (skip precache):
    python scripts/research/run_backtest_variants.py --cache-only
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Suppress noisy pandas FutureWarnings from feature computation
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*Downcasting behavior.*")
warnings.filterwarnings("ignore", message=".*fill_method.*ffill.*")
warnings.filterwarnings("ignore", message=".*DataFrameGroupBy.apply operated.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.analysis.backtest_metrics import compute_backtest_metrics
from qsys.backtest.strategy_runner import BacktestRunner
from qsys.strategy.predictions_cache import PredictionsCacheProxy
from qsys.strategy.registry import create_strategy
from qsys.strategy.spec import spec_from_config


# ── Variant definitions ─────────────────────────────────────────────────────

VariantDef = dict[str, Any]

VARIANTS: list[VariantDef] = [
    {
        "id": "baseline",
        "display_name": "Baseline (top20_buf60_7pct_weekly)",
        "portfolio": {
            "top_n": 20,
            "buffer_hold": 60,
            "buffer_buy": 40,
            "single_stock_cap": 0.07,
            "rebalance_freq": "weekly",
        },
    },
    {
        "id": "no_buffer",
        "display_name": "No Buffer (strict top20)",
        "portfolio": {
            "top_n": 20,
            "buffer_hold": 20,
            "buffer_buy": 20,
            "single_stock_cap": 0.07,
            "rebalance_freq": "weekly",
        },
    },
    {
        "id": "no_cap",
        "display_name": "No Cap (equal weight, no single-stock cap)",
        "portfolio": {
            "top_n": 20,
            "buffer_hold": 60,
            "buffer_buy": 40,
            "single_stock_cap": 1.0,
            "rebalance_freq": "weekly",
        },
    },
    {
        "id": "concentrated",
        "display_name": "Concentrated (top10_buf15_15pct)",
        "portfolio": {
            "top_n": 10,
            "buffer_hold": 15,
            "buffer_buy": 10,
            "single_stock_cap": 0.15,
            "rebalance_freq": "weekly",
        },
    },
    {
        "id": "diversified",
        "display_name": "Diversified (top50_buf100_4pct)",
        "portfolio": {
            "top_n": 50,
            "buffer_hold": 100,
            "buffer_buy": 80,
            "single_stock_cap": 0.04,
            "rebalance_freq": "weekly",
        },
    },
    {
        "id": "daily_rebalance",
        "display_name": "Daily Rebalance (top20_buf60_7pct)",
        "portfolio": {
            "top_n": 20,
            "buffer_hold": 60,
            "buffer_buy": 40,
            "single_stock_cap": 0.07,
            "rebalance_freq": "daily",
        },
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_base_config() -> dict[str, Any]:
    """Load the alpha_v1_research YAML as a base config dict."""
    import yaml

    path = PROJECT_ROOT / "configs/strategies/alpha_v1_research.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _get_trading_dates(start_date: str, end_date: str) -> list[str]:
    """Resolve trading calendar."""
    from qsys.data.calendar import get_trading_calendar

    return get_trading_calendar(start_date, end_date)


def _fetch_benchmark_returns() -> pd.Series:
    """Fetch CSI300 close prices → daily returns (index = trade_date)."""
    from qlib.data import D as qlib_D
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()
    raw = adapter.get_features(
        "csi300", ["$close"],
        start_time="2022-12-01", end_time="2026-05-22",
    )
    if raw is None or raw.empty:
        print("  ⚠ No CSI300 data for benchmark, skipping excess return")
        return pd.Series()

    df = raw.reset_index().rename(columns={"datetime": "trade_date"})
    df = df.sort_values("trade_date")
    rets = df.groupby("trade_date")["$close"].last().pct_change().dropna()
    rets.index = rets.index.astype(str)
    return rets


def _fetch_price_data() -> pd.DataFrame:
    """Fetch CSI300 close prices for all instruments (bucket analysis)."""
    from qlib.data import D as qlib_D
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()
    raw = adapter.get_features(
        "csi300", ["$close"],
        start_time="2022-12-01", end_time="2026-05-22",
    )
    if raw is None or raw.empty:
        print("  ⚠ No price data, skipping bucket contribution analysis")
        return pd.DataFrame()

    df = raw.reset_index().rename(columns={"datetime": "trade_date"})
    df = df.loc[:, ~df.columns.duplicated()]
    df["trade_date"] = df["trade_date"].astype(str)
    return df[["trade_date", "instrument", "$close"]]


# ── Phase 1: Precache predictions ──────────────────────────────────────────


def _precache_predictions(
    base_config: dict[str, Any],
    trading_dates: list[str],
    cache_dir: Path,
) -> None:
    """Generate predictions for all trading dates and cache to disk.

    Each variant shares the same predictions (same ML model + data),
    so this only needs to run once.
    """
    existing = len(list(cache_dir.glob("*.csv")))
    needed = len(trading_dates)
    if existing >= needed:
        print(f"  Predictions cache already full ({existing}/{needed}), skipping")
        return

    print(f"\n{'=' * 60}")
    print(f"  Phase 1: Precomputing predictions ({needed} trading dates)")
    print(f"  Cache: {cache_dir}")
    print(f"{'=' * 60}")

    strategy = create_strategy(
        "alpha_v1_research", base_config, project_root=PROJECT_ROOT,
    )
    cached = PredictionsCacheProxy(strategy, cache_dir)

    n_total = len(trading_dates)
    t0 = time.time()
    last_log = time.time()

    for i, trade_date in enumerate(trading_dates):
        cached.generate_predictions_for_date(trade_date)

        # Log progress every 30s
        now = time.time()
        if now - last_log >= 30:
            elapsed = now - t0
            done = i + 1
            rate = done / elapsed
            remaining = (n_total - done) / rate
            print(f"  [{done}/{n_total}] {trade_date}  "
                  f"({rate:.1f} days/s, ~{remaining/60:.0f} min remaining)")
            last_log = now

    elapsed = time.time() - t0
    print(f"\n  ✅ Predictions cached: {n_total} dates in {elapsed/60:.1f} min "
          f"({n_total/elapsed:.2f} days/s)")


# ── Phase 2: Run variants from cache ───────────────────────────────────────


def _run_variant_from_cache(
    variant: VariantDef,
    base_config: dict[str, Any],
    trading_dates: list[str],
    cache_dir: Path,
    *,
    initial_capital: float,
    output_dir: Path,
    benchmark_returns: pd.Series | None,
    price_data: pd.DataFrame | None,
) -> dict[str, Any]:
    """Run a single variant's backtest using cached predictions."""
    vid = variant["id"]
    vout = output_dir / vid
    vout.mkdir(parents=True, exist_ok=True)

    cfg = copy.deepcopy(base_config)
    cfg["display_name"] = variant["display_name"]
    cfg["portfolio"] = copy.deepcopy(variant["portfolio"])

    print(f"\n{'─' * 60}")
    print(f"  Variant: {vid} — {variant['display_name']}")
    print(f"  Portfolio: {cfg['portfolio']}")
    print(f"{'─' * 60}")

    # Create spec + adapter + cache proxy
    spec = spec_from_config(
        cfg, path=str(PROJECT_ROOT / "configs/strategies/alpha_v1_research.yaml"),
    )
    strategy = create_strategy("alpha_v1_research", cfg, project_root=PROJECT_ROOT)
    cached = PredictionsCacheProxy(strategy, cache_dir)

    # Warm the in-memory cache before the run
    cached.warm_cache(trading_dates)

    # Run backtest
    runner = BacktestRunner(
        mode="cached_daily_equivalent",
        artifact_mode="debug",
        execution_price_mode="open",
    )
    t0 = time.time()
    result = runner.run_range(
        cached, spec,
        start_date=trading_dates[0],
        end_date=trading_dates[-1],
        initial_capital=initial_capital,
        output_dir=vout,
    )
    elapsed = time.time() - t0

    daily_df = pd.DataFrame(result.daily_summary)
    daily_df.to_csv(vout / "daily_summary.csv", index=False)

    # Compute metrics
    metrics = compute_backtest_metrics(
        daily_df,
        debug_artifacts_dir=vout / "daily" if (vout / "daily").exists() else None,
        benchmark_returns=benchmark_returns,
        price_data=price_data,
        top_n=variant["portfolio"]["top_n"],
        buffer_hold=variant["portfolio"]["buffer_hold"],
    )

    display_metrics = {k: v for k, v in metrics.items() if k != "details"}

    print(f"\n  ✅ {vid} done — {len(daily_df)} trading days in {elapsed:.1f}s")
    tr = display_metrics.get("total_return", 0)
    sp = display_metrics.get("sharpe", 0)
    dd = display_metrics.get("max_drawdown", 0)
    print(f"     Return: {tr:>7.2%}  Sharpe: {sp:>7.4f}  MaxDD: {dd:>7.2%}")

    return {
        "variant_id": vid,
        "display_name": variant["display_name"],
        "portfolio": variant["portfolio"],
        "n_trading_days": len(daily_df),
        "elapsed_seconds": round(elapsed, 1),
        "status": result.status,
        "metrics": display_metrics,
    }


# ── Comparison table ────────────────────────────────────────────────────────


def _fmt_pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "   —   "
    return f"{v:>7.2%}"


def _fmt_float(v: Any, decimals: int = 4) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "   —   "
    return f"{v:>8.{decimals}f}"


def _print_comparison(results: list[dict[str, Any]]) -> None:
    """Print a side-by-side comparison table."""
    print("\n\n" + "=" * 100)
    print("  STRATEGY VARIANT COMPARISON")
    print("=" * 100)

    header = (
        f"{'Variant':<24} | {'Ann Return':>10} | {'Sharpe':>8} | "
        f"{'Max DD':>9} | {'Calmar':>7} | {'Turnover':>9} | {'Win Rate':>8} | "
        f"{'Days':>5}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    for r in results:
        if r["status"] != "completed":
            print(f"{r['variant_id']:<24} | {'FAILED':>10} | {'':>8} | {'':>9} | "
                  f"{'':>7} | {'':>9} | {'':>8} | {r['n_trading_days']:>5}")
            continue
        m = r["metrics"]
        print(
            f"{r['variant_id']:<24} | "
            f"{_fmt_pct(m.get('annual_return')):>10} | "
            f"{_fmt_float(m.get('sharpe'), 4):>8} | "
            f"{_fmt_pct(m.get('max_drawdown')):>9} | "
            f"{_fmt_float(m.get('calmar'), 2):>7} | "
            f"{_fmt_float(m.get('annual_turnover'), 1):>9} | "
            f"{_fmt_pct(m.get('win_rate')):>8} | "
            f"{m.get('n_trading_days', '—'):>5}"
        )

    print()
    detail_header = (
        f"{'Variant':<24} | {'Excess Ret':>10} | {'Avg Hold':>9} | "
        f"{'Cost Drag':>9} | {'Worst Day':>9}"
    )
    detail_sep = "-" * len(detail_header)
    print(detail_header)
    print(detail_sep)
    for r in results:
        if r["status"] != "completed":
            continue
        m = r["metrics"]
        worst = m.get("worst_5_days", [])
        worst_str = f"{worst[0]['return']:.2%}" if worst else "—"
        ahd = m.get("avg_holding_days")
        ahd_str = f"{ahd:.0f}d" if ahd and ahd != float("inf") else "—"
        print(
            f"{r['variant_id']:<24} | "
            f"{_fmt_pct(m.get('excess_return')):>10} | "
            f"{ahd_str:>9} | "
            f"{_fmt_pct(m.get('cost_drag')):>9} | "
            f"{worst_str:>9}"
        )

    if any("top_contribution" in r.get("metrics", {}) for r in results):
        print()
        bh = f"{'Variant':<24} | {'Top Contrib':>10} | {'Buffer Contrib':>13} | {'Other Contrib':>13}"
        print(bh)
        print("-" * len(bh))
        for r in results:
            if r["status"] != "completed":
                continue
            m = r["metrics"]
            print(
                f"{r['variant_id']:<24} | "
                f"{_fmt_float(m.get('top_contribution', '—'), 4):>10} | "
                f"{_fmt_float(m.get('buffer_contribution', '—'), 4):>13} | "
                f"{_fmt_float(m.get('other_contribution', '—'), 4):>13}"
            )

    print("=" * 100)


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run alpha_v1 research variants with prediction caching"
    )
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2026-05-22")
    parser.add_argument("--initial-capital", type=float, default=10_000_000.0)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "experiments" / "research_backtest"),
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        default=None,
        help="Space-separated variant IDs (default: all)",
    )
    parser.add_argument(
        "--skip-benchmark",
        action="store_true",
        help="Skip benchmark/price data (faster startup)",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Only populate the prediction cache, don't run variants",
    )
    parser.add_argument(
        "--no-precache",
        action="store_true",
        help="Skip precache phase (use existing cache or let first variant populate it)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "predictions_cache"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Base config ────────────────────────────────────────────────────
    base_config = _load_base_config()

    # ── Filter variants ────────────────────────────────────────────────
    variants = VARIANTS
    if args.variants:
        allowed = set(args.variants)
        variants = [v for v in VARIANTS if v["id"] in allowed]
        missing = allowed - {v["id"] for v in variants}
        if missing:
            print(f"  ⚠ Unknown variants: {missing}. Known: {[v['id'] for v in VARIANTS]}")

    # ── Trading dates ──────────────────────────────────────────────────
    trading_dates = _get_trading_dates(args.start_date, args.end_date)
    print(f"\n{'=' * 60}")
    print(f"  Range: {args.start_date} → {args.end_date}")
    print(f"  Trading days: {len(trading_dates)}")
    print(f"  Variants: {[v['id'] for v in variants]}")
    print(f"  Output: {output_dir}")
    print(f"{'=' * 60}")

    # ── Phase 1: Precache predictions ──────────────────────────────────
    if not args.no_precache:
        _precache_predictions(base_config, trading_dates, cache_dir)
    else:
        cached_count = len(list(cache_dir.glob("*.csv")))
        print(f"\n  ⏩ Skipping precache ({cached_count} cached files found)")

    if args.cache_only:
        print("\n  ✅ Cache-only mode — exiting")
        return

    # ── Fetch benchmark & price data once ──────────────────────────────
    benchmark_returns: pd.Series | None = None
    price_data: pd.DataFrame | None = None
    if not args.skip_benchmark:
        print("\n📊 Loading benchmark data (CSI300)...")
        t0 = time.time()
        benchmark_returns = _fetch_benchmark_returns()
        price_data = _fetch_price_data()
        print(f"   Done in {time.time() - t0:.1f}s")
    else:
        print("\n⏩ Skipping benchmark/price data")

    # ── Phase 2: Run variants from cache ───────────────────────────────
    results: list[dict[str, Any]] = []
    for variant in variants:
        try:
            r = _run_variant_from_cache(
                variant, base_config, trading_dates, cache_dir,
                initial_capital=args.initial_capital,
                output_dir=output_dir,
                benchmark_returns=benchmark_returns,
                price_data=price_data,
            )
        except Exception as exc:
            print(f"\n  ❌ {variant['id']} failed: {exc}")
            import traceback
            traceback.print_exc()
            r = {
                "variant_id": variant["id"],
                "display_name": variant["display_name"],
                "portfolio": variant["portfolio"],
                "n_trading_days": 0,
                "elapsed_seconds": 0,
                "status": f"error: {exc}",
                "metrics": {},
            }
        results.append(r)

    # ── Print comparison ───────────────────────────────────────────────
    _print_comparison(results)

    # ── Save aggregated results ────────────────────────────────────────
    serializable = []
    for r in results:
        sr = {
            "variant_id": r["variant_id"],
            "display_name": r["display_name"],
            "portfolio": r["portfolio"],
            "n_trading_days": r["n_trading_days"],
            "elapsed_seconds": r["elapsed_seconds"],
            "status": r["status"],
            "metrics": {
                k: v for k, v in r["metrics"].items()
                if k not in ("worst_5_days", "worst_5_drawdowns")
            },
        }
        if "worst_5_days" in r["metrics"]:
            sr["metrics"]["worst_5_days"] = [dict(d) for d in r["metrics"]["worst_5_days"]]
        if "worst_5_drawdowns" in r["metrics"]:
            sr["metrics"]["worst_5_drawdowns"] = [dict(d) for d in r["metrics"]["worst_5_drawdowns"]]
        serializable.append(sr)

    comp_path = output_dir / "comparison.json"

    class NpEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return float(o)
            if isinstance(o, (np.bool_,)):
                return bool(o)
            if isinstance(o, (np.ndarray,)):
                return o.tolist()
            return super().default(o)

    comp_path.write_text(
        json.dumps(serializable, indent=2, cls=NpEncoder, ensure_ascii=False),
    )
    print(f"\n  → Comparison saved: {comp_path}")


if __name__ == "__main__":
    main()
