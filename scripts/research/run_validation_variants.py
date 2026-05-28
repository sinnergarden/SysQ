#!/usr/bin/env python3
"""Focused validation: 5 strategies × 3 slippage levels (15 variants).

Reuses signals.cache + previous results where possible.  Runs only 6 new
combo variants.  Produces full attribution analysis.

Usage
-----
    cd /home/liuming/.openclaw/workspace/SysQ
    python scripts/research/run_validation_variants.py

Output
------
    experiments/research/strategy_variants/<timestamp>/
        strategy_comparison_net.csv
        period_breakdown_annual.csv
        rank_bucket_attribution.csv
        split_5d20d_state_attribution.csv
        drawdown_attribution.csv
        experiment_summary.md
        signals.cache  (symlink)
        <variant>/daily_summary.csv, trades.csv, metrics.json
"""

from __future__ import annotations

import json
import pickle
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.engine import BacktestEngine, BacktestResult
from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.backtest.rolling_runner import (
    VariantConfig, VariantResult, RollingBacktestResult,
    get_rebalance_dates, make_alpha_v1_data_loader,
)
from qsys.backtest.strategy_variants import (
    compute_split_5d20d_adjusted_scores,
    precompute_crash_features,
    get_crash_risk_stocks,
    make_split_5d20d_portfolio_fn,
    make_regime_exposure_portfolio_fn,
    make_crash_filter_portfolio_fn,
    make_split_5d20d_regime_exposure_portfolio_fn,
    make_split_5d20d_crash_filter_portfolio_fn,
    compute_index_regime,
)
from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine
from qsys.analysis.backtest_metrics import compute_backtest_metrics

# ── Constants ───────────────────────────────────────────────────────────────

SLIPPAGE_LEVELS = [0.0, 0.001, 0.002]
PREV_RUN_DIR = _PROJECT_ROOT / "experiments" / "research" / "strategy_variants" / "20260527_230733"
CACHE_MAP = {
    0.0: "alpha_v1_s0",
    0.001: "alpha_v1_s1e-03",
    0.002: "alpha_v1_s2e-03",
}
START_DATE = "2015-01-01"
END_DATE = "2026-05-22"

# ── Attribution capture ────────────────────────────────────────────────────


class AttributionCapture:
    """Wrap portfolio_fn to capture per-rebalance decision context.

    Usage::

        wrapped = AttributionCapture(pf)
        variant = VariantConfig(name="...", portfolio_fn=wrapped)
        # run backtest...
        df = wrapped.to_dataframe()
    """

    def __init__(self, portfolio_fn):
        self._fn = portfolio_fn
        self.records: list[dict] = []

    def __call__(self, scores, account, *, top_n=20, buffer_hold=60,
                  buffer_buy=40, single_stock_cap=0.07,
                  signal_info=None, **kwargs):
        date_str = scores.name if hasattr(scores, "name") and scores.name else ""

        # 1. Compute deciles for all candidates
        n = len(scores)
        sorted_scores = scores.sort_values(ascending=False)
        decile_map = {}
        for rank, (inst, _) in enumerate(sorted_scores.items()):
            d = min(rank * 10 // n, 9) + 1 if n > 0 else 0
            decile_map[inst] = d

        # 2. Compute split_5d20d state for all candidates
        state_map: dict[str, str] = {}
        if signal_info and n > 0:
            z5_all = np.array([si["z5"] for si in signal_info.values()])
            z20_all = np.array([si["z20"] for si in signal_info.values()])
            for inst in scores.index:
                si = signal_info.get(inst)
                if si is None:
                    state_map[inst] = "other"
                    continue
                z5_pct = (z5_all < si["z5"]).mean()
                z20_pct = (z20_all < si["z20"]).mean()
                z5h = z5_pct <= 0.25
                z20h = z20_pct <= 0.25
                z5l = z5_pct >= 0.60
                z20l = z20_pct >= 0.60
                if z5h and z20h:
                    state_map[inst] = "z5h_z20h"
                elif z5h and z20l:
                    state_map[inst] = "z5h_z20l"
                elif z5l and z20h:
                    state_map[inst] = "z5l_z20h"
                else:
                    state_map[inst] = "other"

        # 3. Call the real portfolio_fn
        result = self._fn(
            scores, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
            signal_info=signal_info, **kwargs,
        )

        # 4. Capture per-holding attribution snapshot
        record: dict[str, Any] = {
            "date": date_str,
            "n_candidates": n,
            "n_held": len(account.positions),
        }
        holdings_list: list[dict] = []
        for inst, w in result.items():
            holdings_list.append({
                "symbol": inst,
                "weight": w,
                "decile": decile_map.get(inst, 0),
                "state": state_map.get(inst, ""),
            })
        record["holdings"] = holdings_list
        self.records.append(record)
        return result

    def to_dataframe(self) -> pd.DataFrame:
        """Flatten all records into a DataFrame (one row per symbol per date)."""
        rows = []
        for r in self.records:
            for h in r["holdings"]:
                rows.append({
                    "date": r["date"],
                    "symbol": h["symbol"],
                    "weight": h["weight"],
                    "decile": h["decile"],
                    "state": h["state"],
                    "n_candidates": r["n_candidates"],
                    "n_held": r["n_held"],
                })
        return pd.DataFrame(rows)


# ── Old-name → new-name mapping ────────────────────────────────────────────

VAR_NAME_MAP = {
    "alpha_v1_s0": "baseline_s0",
    "alpha_v1_s1e-03": "baseline_s1e-03",
    "alpha_v1_s2e-03": "baseline_s2e-03",
    "alpha_v1_split_5d20d_s0": "split_5d20d_s0",
    "alpha_v1_split_5d20d_s1e-03": "split_5d20d_s1e-03",
    "alpha_v1_split_5d20d_s2e-03": "split_5d20d_s2e-03",
    "alpha_v1_regime_exposure_s0": "regime_exposure_s0",
    "alpha_v1_regime_exposure_s1e-03": "regime_exposure_s1e-03",
    "alpha_v1_regime_exposure_s2e-03": "regime_exposure_s2e-03",
    "alpha_v1_crash_filter_s0": "crash_filter_s0",
    "alpha_v1_crash_filter_s1e-03": "crash_filter_s1e-03",
    "alpha_v1_crash_filter_s2e-03": "crash_filter_s2e-03",
}

STRATEGY_ID_MAP = {
    "baseline": "alpha_v1",
    "split_5d20d": "alpha_v1_split_5d20d",
    "split_5d20d_regime_exposure": "alpha_v1_split_5d20d_regime_exposure",
    "split_5d20d_crash_filter": "alpha_v1_split_5d20d_crash_filter",
    "regime_exposure": "alpha_v1_regime_exposure",
}


def _slip_label(s: float) -> str:
    if s == 0.0:
        return "s0"
    return f"s{s:.0e}"


def _parse_slip(name: str) -> float:
    for s in [0.002, 0.001, 0.0]:
        if name.endswith(_slip_label(s)):
            return s
    return 0.0


def _load_signals_cache(cache_path: Path):
    """Load cached signals dict."""
    print(f"[Cache] Loading {cache_path} ...")
    cached = pickle.loads(cache_path.read_bytes())
    print(f"  {len(cached['all_signals'])} signals, "
          f"{len(cached['all_window_ids'])} dates")
    return cached


# ── Copy previous results ──────────────────────────────────────────────────


def copy_previous_results(prev_dir: Path, output_dir: Path) -> dict[str, dict]:
    """Copy already-computed variant results into new output structure.

    Returns dict mapping new_name → metrics dict.
    """
    results_map = {}
    for old_name, new_name in VAR_NAME_MAP.items():
        src = prev_dir / old_name
        dst = output_dir / new_name
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            metrics_path = dst / "metrics.json"
            if metrics_path.exists():
                results_map[new_name] = json.loads(metrics_path.read_text())
            print(f"  {old_name} → {new_name}")
    return results_map


# ── New variant runner ────────────────────────────────────────────────────


def run_variant(
    name: str,
    strategy_id: str,
    slippage: float,
    portfolio_fn,
    bt_frame: pd.DataFrame,
    signals: dict,
    bt_dates_dt: list,
    rebalance_dates: set,
    window_lookup: dict,
    initial_capital: float = 1_000_000.0,
) -> tuple[VariantResult, AttributionCapture]:
    """Run one variant and return result + attribution capture."""
    engine = BacktestEngine(
        account=Account(init_cash=initial_capital),
        matcher=MatchEngine(slippage=slippage),
        order_gen=OrderGenerator(),
    )
    result = engine.run(
        frame=bt_frame,
        signal_lookup=signals,
        rebalance_dates=rebalance_dates,
        portfolio_fn=portfolio_fn,
        dates=bt_dates_dt,
        window_lookup=window_lookup,
    )
    metrics = _compute_metrics(result, strategy_id)
    vr = VariantResult(name=name, backtest_result=result, metrics=metrics)
    return vr, metrics


def _compute_metrics(result: BacktestResult, strategy_id: str) -> dict:
    """Compute metrics from BacktestEngine result (same as rolling_runner)."""
    if result.daily.empty:
        return {"error": "no_daily_data", "strategy_id": strategy_id}

    daily = result.daily.copy()
    col_map = {}
    for c in daily.columns:
        if c == "equity":
            col_map[c] = "total_value_after"
        elif c == "date":
            col_map[c] = "trade_date"
    if col_map:
        daily = daily.rename(columns=col_map)

    # Add turnover from trades
    if "turnover" not in daily.columns and not result.trades.empty:
        trades = result.trades.copy()
        trades["turnover_value"] = trades["amount"].astype(float) * trades["price"].astype(float)
        dt = trades.groupby("date")["turnover_value"].sum()
        daily["turnover"] = daily["trade_date"].map(
            lambda d: float(dt.get(d, 0.0))
        )

    metrics = compute_backtest_metrics(daily)
    metrics["strategy_id"] = strategy_id

    if not result.trades.empty:
        trades = result.trades.copy()
        metrics["trade_count"] = len(trades)
        metrics["buy_count"] = int((trades["side"] == "buy").sum())
        metrics["sell_count"] = int((trades["side"] == "sell").sum())
    else:
        metrics["trade_count"] = 0
        metrics["buy_count"] = 0
        metrics["sell_count"] = 0

    if "cash" in daily.columns and "equity" in daily.columns:
        cr = daily["cash"].astype(float) / daily["equity"].astype(float).replace(0, np.nan)
        metrics["cash_ratio_avg"] = float(cr.mean())
    else:
        metrics["cash_ratio_avg"] = 0.0

    if "ret" in daily.columns:
        rets = daily["ret"].astype(float).dropna()
        if len(rets) > 0:
            metrics["worst_1d_return"] = float(rets.min())
            if len(rets) >= 5:
                metrics["worst_5d_return"] = float(rets.rolling(5).sum().dropna().min())
            else:
                metrics["worst_5d_return"] = float(rets.sum())
    else:
        metrics["worst_1d_return"] = 0.0
        metrics["worst_5d_return"] = 0.0

    return metrics


# ── Batch run new variants ────────────────────────────────────────────────


def run_new_variants(
    output_dir: Path,
    bt_frame: pd.DataFrame,
    signals: dict,
    bt_dates_dt: list,
    rebalance_dates: set,
    window_lookup: dict,
    index_close: pd.Series,
    crash_features: pd.DataFrame,
) -> dict[str, dict]:
    """Run the 6 new combo variants, save results, return metrics."""
    from qsys.data.calendar import get_trading_calendar

    existing_results: dict[str, dict] = {}

    new_variants = [
        ("split_5d20d_regime_exposure", make_split_5d20d_regime_exposure_portfolio_fn(index_close)),
        ("split_5d20d_crash_filter", make_split_5d20d_crash_filter_portfolio_fn(bt_frame, crash_features)),
    ]

    for base_name, pf in new_variants:
        for slip in SLIPPAGE_LEVELS:
            name = f"{base_name}_{_slip_label(slip)}"
            sid = STRATEGY_ID_MAP[base_name]

            wrapped = AttributionCapture(pf)
            var_conf = VariantConfig(
                name=name, strategy_id=sid,
                slippage=slip, portfolio_fn=wrapped,
            )

            print(f"\n── {name} ──")
            t0 = time.time()
            vr, metrics = run_variant(
                name, sid, slip, wrapped,
                bt_frame, signals, bt_dates_dt,
                rebalance_dates, window_lookup,
            )
            elapsed = time.time() - t0

            ann_ret = metrics.get("annual_return", 0)
            sp = metrics.get("sharpe", 0)
            mdd = metrics.get("max_drawdown", 0)
            print(f"  {len(vr.backtest_result.daily)} days, "
                  f"ann_ret={ann_ret:.2%}, sharpe={sp:.2f}, mdd={mdd:.2%}")
            print(f"  Time: {elapsed:.1f}s")

            # Save results
            var_dir = output_dir / name
            var_dir.mkdir(parents=True, exist_ok=True)
            if not vr.backtest_result.daily.empty:
                vr.backtest_result.daily.to_csv(var_dir / "daily_summary.csv", index=False)
            if not vr.backtest_result.trades.empty:
                vr.backtest_result.trades.to_csv(var_dir / "trades.csv", index=False)
            clean = {k: v for k, v in metrics.items() if k != "details"}
            (var_dir / "metrics.json").write_text(
                json.dumps(clean, indent=2, default=str, ensure_ascii=False),
            )

            # Save attribution records
            attrib_df = wrapped.to_dataframe()
            if not attrib_df.empty:
                attrib_df.to_csv(var_dir / "attribution.csv", index=False)

            existing_results[name] = metrics

    return existing_results


# ── Post-processing: comparison CSV ───────────────────────────────────────


def build_comparison_rows(all_metrics: dict[str, dict]) -> pd.DataFrame:
    """Build strategy_comparison_net.csv."""
    rows = []
    for name, m in sorted(all_metrics.items()):
        slip = _parse_slip(name)
        equity = m.get("details", {}).get("equity_curve", None) if isinstance(m.get("details"), dict) else None
        final_value = float(equity.iloc[-1]) if equity is not None else 0.0

        rows.append({
            "strategy_id": name,
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
    return pd.DataFrame(rows)


# ── Post-processing: yearly breakdown ─────────────────────────────────────


def build_yearly_breakdown(output_dir: Path) -> pd.DataFrame:
    """Build period_breakdown_annual.csv."""
    years = [str(y) for y in range(2015, 2027)]
    periods = []
    for y in years:
        if y == "2026":
            periods.append((y, f"{y}-01-01", "2026-05-22"))
        else:
            periods.append((y, f"{y}-01-01", f"{y}-12-31"))

    rows = []
    for var_dir in sorted(output_dir.iterdir()):
        if not var_dir.is_dir() or not var_dir.name.startswith(("baseline", "split", "regime", "crash")):
            continue
        name = var_dir.name
        slip = _parse_slip(name)

        daily_path = var_dir / "daily_summary.csv"
        if not daily_path.exists():
            continue
        daily = pd.read_csv(daily_path)
        if daily.empty:
            continue

        col_map = {}
        for c in daily.columns:
            if c == "equity":
                col_map[c] = "total_value_after"
            elif c == "date":
                col_map[c] = "trade_date"
        if col_map:
            daily = daily.rename(columns=col_map)
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])

        for p_name, p_start, p_end in periods:
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
            vol = float(rets.std() * np.sqrt(252))

            to = 0.0
            if "turnover" in sub.columns:
                total_t = sub["turnover"].astype(float).sum()
                avg_val = float(equity.mean())
                if avg_val > 0:
                    to = total_t / avg_val * 252 / max(n, 1)

            cash_r = 0.0
            if "cash" in sub.columns and "equity" in sub.columns:
                sub_e = sub["equity"].astype(float).replace(0, np.nan)
                cash_r = float((sub["cash"].astype(float) / sub_e).mean())

            rows.append({
                "strategy_id": name,
                "slippage": slip,
                "year": p_name,
                "annual_return": ann_ret,
                "sharpe": sp,
                "max_drawdown": mdd,
                "calmar": cal,
                "volatility": vol,
                "turnover": to,
                "cash_ratio_avg": cash_r,
            })

    return pd.DataFrame(rows)


# ── Post-processing: rank bucket attribution ──────────────────────────────


def compute_rank_bucket_attribution(
    output_dir: Path, signals: dict,
) -> pd.DataFrame:
    """For each variant, compute buy weight % per rank decile.

    Uses trades.csv + signals.cache to determine the rank decile
    of each buy at execution time.
    """
    # Build decile map from signals
    from collections import defaultdict

    date_insts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (d, inst), (z5, z20, b) in signals.items():
        if not np.isnan(b):
            date_insts[d].append((inst, b))

    decile_map: dict[tuple[str, str], int] = {}
    for d, items in date_insts.items():
        items.sort(key=lambda x: x[1], reverse=True)
        n_items = len(items)
        for rank, (inst, _) in enumerate(items):
            decile = min(rank * 10 // n_items, 9) + 1
            decile_map[(d, inst)] = decile

    # Build state map from signals
    state_map: dict[tuple[str, str], str] = {}
    for (d, inst), (z5, z20, b) in signals.items():
        if np.isnan(b):
            continue
        # We don't have percentiles here without the full universe for each date
        # So we compute states separately in the split attribution
        pass

    rows = []
    for var_dir in sorted(output_dir.iterdir()):
        if not var_dir.is_dir() or not var_dir.name.startswith(("baseline", "split", "regime", "crash")):
            continue
        name = var_dir.name
        slip = _parse_slip(name)

        trades_path = var_dir / "trades.csv"
        if not trades_path.exists():
            continue
        trades = pd.read_csv(trades_path)
        buys = trades[trades["side"] == "buy"].copy()
        if buys.empty:
            continue

        # Compute total buy value for weight %
        buys["value"] = buys["amount"].astype(float) * buys["price"].astype(float)
        total_value = buys["value"].sum()
        if total_value <= 0:
            continue

        # Map each buy to decile
        decile_value: dict[int, float] = {d: 0.0 for d in range(1, 11)}
        mapped_value = 0.0
        for _, row in buys.iterrows():
            key = (row["date"], str(row["symbol"]))
            dec = decile_map.get(key)
            if dec is not None:
                decile_value[dec] = decile_value.get(dec, 0.0) + row["value"]
                mapped_value += row["value"]

        row_data = {"strategy_id": name, "slippage": slip}
        for d in range(1, 11):
            row_data[f"decile_{d}_pct"] = decile_value[d] / total_value
        row_data["unmapped_pct"] = (total_value - mapped_value) / total_value
        row_data["total_buy_value"] = total_value
        rows.append(row_data)

    return pd.DataFrame(rows)


# ── Post-processing: split_5d20d state attribution ────────────────────────


def compute_split_state_attribution(
    output_dir: Path, signals: dict,
) -> pd.DataFrame:
    """For split_5d20d variants, compute per-state attribution.

    Only includes variants whose strategy_id contains "split_5d20d".
    Uses signals.cache to determine z5/z20 state at buy time.
    """
    # Pre-compute states per (date, inst) from signals
    from collections import defaultdict

    date_z_all: dict[str, dict] = {}
    for (d, inst), (z5, z20, b) in signals.items():
        if np.isnan(b):
            continue
        if d not in date_z_all:
            date_z_all[d] = {"insts": [], "z5s": [], "z20s": []}
        date_z_all[d]["insts"].append(inst)
        date_z_all[d]["z5s"].append(z5)
        date_z_all[d]["z20s"].append(z20)

    state_map: dict[tuple[str, str], str] = {}
    for d, data in date_z_all.items():
        z5_arr = np.array(data["z5s"])
        z20_arr = np.array(data["z20s"])
        for inst, z5, z20 in zip(data["insts"], data["z5s"], data["z20s"]):
            z5_pct = (z5_arr < z5).mean()
            z20_pct = (z20_arr < z20).mean()
            z5h = z5_pct <= 0.25
            z20h = z20_pct <= 0.25
            z5l = z5_pct >= 0.60
            z20l = z20_pct >= 0.60
            if z5h and z20h:
                state_map[(d, inst)] = "z5h_z20h"
            elif z5h and z20l:
                state_map[(d, inst)] = "z5h_z20l"
            elif z5l and z20h:
                state_map[(d, inst)] = "z5l_z20h"
            else:
                state_map[(d, inst)] = "other"

    rows = []
    for var_dir in sorted(output_dir.iterdir()):
        if not var_dir.is_dir():
            continue
        name = var_dir.name
        if not any(s in name for s in ["split_5d20d", "split"]):
            continue
        slip = _parse_slip(name)

        # Determine strategy_id
        if "regime" in name:
            sid = "alpha_v1_split_5d20d_regime_exposure"
        elif "crash" in name:
            sid = "alpha_v1_split_5d20d_crash_filter"
        else:
            sid = "alpha_v1_split_5d20d"

        trades_path = var_dir / "trades.csv"
        if not trades_path.exists():
            continue
        trades = pd.read_csv(trades_path)
        buys = trades[trades["side"] == "buy"].copy()
        sells = trades[trades["side"] == "sell"].copy()
        if buys.empty:
            continue

        buys["value"] = buys["amount"].astype(float) * buys["price"].astype(float)
        total_buy_value = buys["value"].sum()
        if total_buy_value <= 0:
            continue

        state_buy_value: dict[str, float] = {}
        state_buy_count: dict[str, int] = {}
        for _, row in buys.iterrows():
            key = (row["date"], str(row["symbol"]))
            st = state_map.get(key, "other")
            state_buy_value[st] = state_buy_value.get(st, 0.0) + row["value"]
            state_buy_count[st] = state_buy_count.get(st, 0) + 1

        total_buys = len(buys)
        rows.append({
            "strategy_id": name,
            "slippage": slip,
            "total_buy_value": total_buy_value,
            "total_buys": total_buys,
            "state_counts": json.dumps(state_buy_count, default=str),
            "state_values": json.dumps(state_buy_value, default=str),
        })

    return pd.DataFrame(rows)


# ── Post-processing: drawdown attribution ─────────────────────────────────


def compute_drawdown_attribution(output_dir: Path) -> pd.DataFrame:
    """Enhanced drawdown table with top_losing_symbols for each variant."""
    rows = []
    for var_dir in sorted(output_dir.iterdir()):
        if not var_dir.is_dir() or not var_dir.name.startswith(("baseline", "split", "regime", "crash")):
            continue
        name = var_dir.name
        slip = _parse_slip(name)

        daily_path = var_dir / "daily_summary.csv"
        if not daily_path.exists():
            continue
        daily = pd.read_csv(daily_path)
        if daily.empty:
            continue

        col_map = {}
        for c in daily.columns:
            if c == "equity":
                col_map[c] = "total_value_after"
            elif c == "date":
                col_map[c] = "trade_date"
        if col_map:
            daily = daily.rename(columns=col_map)

        equity = daily["total_value_after"].astype(float)

        # Find drawdown periods
        peak = equity.iloc[0]
        peak_idx = 0
        in_dd = False
        dd_start = 0
        periods = []

        for i in range(1, len(equity)):
            if equity.iloc[i] >= peak:
                if in_dd:
                    trough_idx = dd_start + equity.iloc[dd_start:i+1].values.argmin()
                    dd_depth = float(equity.iloc[trough_idx] / peak - 1.0)
                    periods.append({
                        "start": str(daily["trade_date"].iloc[dd_start].date()
                                    if hasattr(daily["trade_date"].iloc[dd_start], "date")
                                    else daily["trade_date"].iloc[dd_start]),
                        "trough": str(daily["trade_date"].iloc[trough_idx].date()
                                     if hasattr(daily["trade_date"].iloc[trough_idx], "date")
                                     else daily["trade_date"].iloc[trough_idx]),
                        "end": str(daily["trade_date"].iloc[i].date()
                                  if hasattr(daily["trade_date"].iloc[i], "date")
                                  else daily["trade_date"].iloc[i]),
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
            trough_idx = dd_start + equity.iloc[dd_start:].values.argmin()
            dd_depth = float(equity.iloc[trough_idx] / peak - 1.0)
            periods.append({
                "start": str(daily["trade_date"].iloc[dd_start].date()
                            if hasattr(daily["trade_date"].iloc[dd_start], "date")
                            else daily["trade_date"].iloc[dd_start]),
                "trough": str(daily["trade_date"].iloc[trough_idx].date()
                             if hasattr(daily["trade_date"].iloc[trough_idx], "date")
                             else daily["trade_date"].iloc[trough_idx]),
                "end": None,
                "depth": dd_depth,
                "duration": len(equity) - dd_start,
            })

        # Top 5 drawdowns
        periods.sort(key=lambda p: p["depth"])
        for p in periods[:5]:
            # Cash ratio during drawdown
            dd_end = p["end"] or str(daily["trade_date"].iloc[-1].date()
                                     if hasattr(daily["trade_date"].iloc[-1], "date")
                                     else daily["trade_date"].iloc[-1])
            dd_mask = (daily["trade_date"] >= p["start"]) & (daily["trade_date"] <= dd_end)
            dd_data = daily[dd_mask]
            cash_ratio = 0.0
            if "cash" in dd_data.columns and "equity" in dd_data.columns:
                e = dd_data["equity"].astype(float).replace(0, np.nan)
                cash_ratio = float((dd_data["cash"].astype(float) / e).mean())

            rows.append({
                "strategy_id": name,
                "slippage": slip,
                "drawdown_start": p["start"],
                "drawdown_valley": p["trough"],
                "drawdown_end": p["end"],
                "max_drawdown": p["depth"],
                "duration_days": p["duration"],
                "cash_ratio_during_drawdown": cash_ratio,
            })

    return pd.DataFrame(rows)


# ── Stats helpers ──────────────────────────────────────────────────────────


def print_comparison_table(comparison_df: pd.DataFrame) -> None:
    """Pretty-print comparison table to stdout."""
    print("\n" + "=" * 140)
    print("Strategy Variants — Net Returns (s1e-03 = main comparison)")
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


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    t_start = time.time()

    # Output directory
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _PROJECT_ROOT / "experiments" / "research" / "strategy_variants" / ts
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}")

    # ── Load cached signals ──
    cache_src = PREV_RUN_DIR / "signals.cache"
    if not cache_src.exists():
        print(f"[ERROR] Previous signals.cache not found at {cache_src}")
        print("  Run the full experiment first.")
        sys.exit(1)

    cached = _load_signals_cache(cache_src)
    signals = cached["all_signals"]
    window_lookup = cached["all_window_ids"]
    bt_frame = cached["bt_frame"]

    # Symlink signals.cache
    (output_dir / "signals.cache").symlink_to(cache_src)

    # ── Trading calendar & rebalance dates ──
    from qsys.data.calendar import get_trading_calendar
    bt_dates = get_trading_calendar(START_DATE, END_DATE)
    bt_dates_dt = [pd.Timestamp(d) for d in bt_dates]
    rb_set = get_rebalance_dates(bt_dates_dt, freq="weekly")

    # ── Load index data for regime_exposure ──
    print("[Data] Loading CSI300 index data for regime detection...")
    from qsys.data.adapter import QlibAdapter
    adapter = QlibAdapter()
    adapter.init_qlib()
    cal_start = (pd.Timestamp(START_DATE) - pd.DateOffset(years=2, days=30)).strftime("%Y-%m-%d")
    raw = adapter.get_features("csi300", ["$close"], start_time=cal_start, end_time=END_DATE)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    index_close = frame.groupby("trade_date")["$close"].median()
    index_close.index = pd.to_datetime(index_close.index)
    index_close = index_close.sort_index()
    print(f"  {len(index_close)} days")

    # ── Pre-compute crash features ──
    print("[Data] Pre-computing crash features (vectorized)...")
    crash_features = precompute_crash_features(bt_frame)
    print(f"  {len(crash_features)} rows")

    # ── Copy previous results ──
    print(f"\n[Copy] Reusing previous results from {PREV_RUN_DIR.name} ...")
    all_metrics = copy_previous_results(PREV_RUN_DIR, output_dir)
    print(f"  {len(all_metrics)} variants reused")

    # ── Run new variants ──
    print(f"\n[Run] Running 6 new combo variants ...")
    new_metrics = run_new_variants(
        output_dir, bt_frame, signals, bt_dates_dt, rb_set,
        window_lookup, index_close, crash_features,
    )
    all_metrics.update(new_metrics)
    print(f"\n  Total: {len(all_metrics)} variants")

    # ── Build comparison CSV ──
    print("\n[Output] strategy_comparison_net.csv ...")
    comp_df = build_comparison_rows(all_metrics)
    comp_df.to_csv(output_dir / "strategy_comparison_net.csv", index=False)
    print_comparison_table(comp_df)

    # ── Yearly breakdown ──
    print("\n[Output] period_breakdown_annual.csv ...")
    yearly_df = build_yearly_breakdown(output_dir)
    if not yearly_df.empty:
        yearly_df.to_csv(output_dir / "period_breakdown_annual.csv", index=False)

    # ── Rank bucket attribution ──
    print("[Output] rank_bucket_attribution.csv ...")
    rank_df = compute_rank_bucket_attribution(output_dir, signals)
    if not rank_df.empty:
        rank_df.to_csv(output_dir / "rank_bucket_attribution.csv", index=False)

    # ── Split state attribution (split_5d20d variants only) ──
    print("[Output] split_5d20d_state_attribution.csv ...")
    state_df = compute_split_state_attribution(output_dir, signals)
    if not state_df.empty:
        state_df.to_csv(output_dir / "split_5d20d_state_attribution.csv", index=False)

        # Pretty-print state distribution
        print("\n  Split State Attribution (s1e-03 only):")
        for _, r in state_df.iterrows():
            if r["slippage"] != 0.001:
                continue
            counts = json.loads(r["state_counts"])
            total = r["total_buys"]
            parts = [f"{k}: {v/total:.1%}" for k, v in sorted(counts.items())]
            print(f"  {r['strategy_id']:<32} " + " | ".join(parts))

    # ── Drawdown attribution ──
    print("[Output] drawdown_attribution.csv ...")
    dd_df = compute_drawdown_attribution(output_dir)
    if not dd_df.empty:
        dd_df.to_csv(output_dir / "drawdown_attribution.csv", index=False)

    # ── Summary JSON ──
    summary = {
        "experiment": "validation_variants",
        "run_at": datetime.now().isoformat(),
        "total_wall_time_seconds": time.time() - t_start,
        "n_variants": len(all_metrics),
        "slippage_levels": SLIPPAGE_LEVELS,
        "strategies": list(STRATEGY_ID_MAP.values()),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str, ensure_ascii=False),
    )

    total_time = time.time() - t_start
    print(f"\nTotal time: {total_time:.0f}s ({total_time / 60:.1f} min)")
    print(f"Results: {output_dir}/")


if __name__ == "__main__":
    main()
