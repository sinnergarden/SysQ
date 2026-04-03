from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter


SIGNAL_BASKET_COLUMNS = [
    "symbol",
    "score",
    "score_rank",
    "weight",
    "price",
    "signal_date",
    "execution_date",
    "price_basis_date",
    "price_basis_field",
    "price_basis_label",
    "model_name",
    "model_path",
    "universe",
]


@dataclass
class SignalQualitySnapshot:
    summary: dict
    observations: pd.DataFrame


def _classify_missing_price_reason(*, missing_start_count: int, missing_end_count: int) -> str:
    if missing_start_count and missing_end_count:
        return "missing_start_and_end_price"
    if missing_end_count:
        return "missing_end_price"
    if missing_start_count:
        return "missing_start_price"
    return "ok"


def save_signal_basket(basket_df: pd.DataFrame, *, output_dir: str | Path, signal_date: str) -> str:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized = basket_df.copy()
    if normalized.empty:
        normalized = pd.DataFrame(columns=SIGNAL_BASKET_COLUMNS)
    else:
        for column in SIGNAL_BASKET_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = pd.NA
        normalized = normalized[SIGNAL_BASKET_COLUMNS]

    path = output_dir / f"signal_basket_{signal_date}.csv"
    normalized.to_csv(path, index=False)
    return str(path)


def list_signal_basket_files(signal_dir: str | Path, *, limit: int | None = None) -> list[Path]:
    signal_dir = Path(signal_dir)
    files = sorted(signal_dir.glob("signal_basket_*.csv"), reverse=True)
    return files[:limit] if limit is not None else files


def _default_price_loader(symbols: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["date", "symbol", "close"])
    adapter = QlibAdapter()
    adapter.init_qlib()
    frame = adapter.get_features(symbols, ["$close"], start_time=start_date, end_time=end_date)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "close"])
    if isinstance(frame.index, pd.MultiIndex):
        frame = frame.reset_index()
    frame = frame.rename(columns={"datetime": "date", "instrument": "symbol", "$close": "close"})
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["symbol"] = frame["symbol"].astype(str)
    frame["close"] = frame["close"].astype(float)
    return frame[["date", "symbol", "close"]]


def _default_benchmark_loader(universe: str, start_date: str, end_date: str) -> float | None:
    symbols = D.instruments(universe)
    frame = _default_price_loader(symbols, start_date, end_date)
    if frame.empty:
        return None

    start_prices = frame[frame["date"] == start_date].set_index("symbol")["close"]
    end_prices = frame[frame["date"] == end_date].set_index("symbol")["close"]
    joined = pd.concat([start_prices.rename("start"), end_prices.rename("end")], axis=1).dropna()
    if joined.empty:
        return None
    returns = joined["end"] / joined["start"] - 1.0
    return float(returns.mean())


def _trading_days_between(signal_date: str, as_of_date: str) -> int:
    start = pd.Timestamp(signal_date)
    end = pd.Timestamp(as_of_date)
    if end <= start:
        return 0
    calendar = D.calendar(start_time=start, end_time=end)
    trading_days = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) > start and pd.Timestamp(x) <= end]
    return len(trading_days)


def _normalize_weights(weights: pd.Series) -> pd.Series:
    total = float(weights.sum())
    if total <= 0:
        return pd.Series(1.0 / len(weights), index=weights.index) if len(weights) else weights
    return weights / total


def inspect_signal_basket_price_readiness(
    basket_df: pd.DataFrame,
    *,
    as_of_date: str,
    price_loader: Callable[[list[str], str, str], pd.DataFrame] | None = None,
) -> dict:
    price_loader = price_loader or _default_price_loader

    if basket_df is None or basket_df.empty:
        return {
            "signal_date": None,
            "execution_date": None,
            "as_of_date": as_of_date,
            "status": "missing_plan",
            "reason": "empty_signal_basket",
            "basket_size": 0,
            "ready_count": 0,
            "ready_ratio": 0.0,
            "missing_start_count": 0,
            "missing_end_count": 0,
            "missing_start_symbols": [],
            "missing_end_symbols": [],
        }

    normalized = basket_df.copy()
    normalized["symbol"] = normalized["symbol"].astype(str)
    signal_date = str(normalized["signal_date"].iloc[0])
    execution_date = str(normalized.get("execution_date", pd.Series([signal_date])).iloc[0])
    price_basis_date = str(normalized.get("price_basis_date", pd.Series([signal_date])).iloc[0])
    symbols = normalized["symbol"].tolist()

    prices = price_loader(symbols, price_basis_date, as_of_date)
    if prices is None or prices.empty:
        missing_reason = _classify_missing_price_reason(
            missing_start_count=len(symbols),
            missing_end_count=len(symbols),
        )
        return {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "as_of_date": as_of_date,
            "status": "failed",
            "reason": missing_reason,
            "basket_size": int(len(symbols)),
            "ready_count": 0,
            "ready_ratio": 0.0,
            "missing_start_count": int(len(symbols)),
            "missing_end_count": int(len(symbols)),
            "missing_start_symbols": symbols,
            "missing_end_symbols": symbols,
        }

    start_prices = prices[prices["date"] == price_basis_date].set_index("symbol")["close"]
    end_prices = prices[prices["date"] == as_of_date].set_index("symbol")["close"]
    frame = normalized.set_index("symbol")
    frame["start_price"] = start_prices
    frame["end_price"] = end_prices

    missing_start_symbols = [str(sym) for sym in frame.index[frame["start_price"].isna()].tolist()]
    missing_end_symbols = [str(sym) for sym in frame.index[frame["end_price"].isna()].tolist()]
    ready_count = int((frame["start_price"].notna() & frame["end_price"].notna()).sum())
    basket_size = int(len(frame))
    ready_ratio = float(ready_count / basket_size) if basket_size else 0.0
    status = "success" if ready_count == basket_size else ("failed" if ready_count == 0 else "partial")
    reason = _classify_missing_price_reason(
        missing_start_count=len(missing_start_symbols),
        missing_end_count=len(missing_end_symbols),
    )
    if status == "success":
        reason = "ok"

    return {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "as_of_date": as_of_date,
        "status": status,
        "reason": reason,
        "basket_size": basket_size,
        "ready_count": ready_count,
        "ready_ratio": ready_ratio,
        "missing_start_count": int(len(missing_start_symbols)),
        "missing_end_count": int(len(missing_end_symbols)),
        "missing_start_symbols": missing_start_symbols,
        "missing_end_symbols": missing_end_symbols,
    }


def evaluate_signal_basket(
    basket_df: pd.DataFrame,
    *,
    as_of_date: str,
    price_loader: Callable[[list[str], str, str], pd.DataFrame] | None = None,
    benchmark_loader: Callable[[str, str, str], float | None] | None = None,
) -> dict:
    price_loader = price_loader or _default_price_loader
    benchmark_loader = benchmark_loader or _default_benchmark_loader

    if basket_df is None or basket_df.empty:
        return {
            "signal_date": None,
            "execution_date": None,
            "as_of_date": as_of_date,
            "holding_days": 0,
            "status": "missing_plan",
            "reason": "empty_signal_basket",
            "basket_size": 0,
        }

    normalized = basket_df.copy()
    signal_date = str(normalized["signal_date"].iloc[0])
    execution_date = str(normalized.get("execution_date", pd.Series([signal_date])).iloc[0])
    model_name = str(normalized.get("model_name", pd.Series([""])).iloc[0])
    model_path = str(normalized.get("model_path", pd.Series([""])).iloc[0])
    universe = str(normalized.get("universe", pd.Series(["csi300"])) .iloc[0] or "csi300")
    price_basis_date = str(normalized.get("price_basis_date", pd.Series([signal_date])).iloc[0])

    holding_days = _trading_days_between(signal_date, as_of_date)
    if holding_days <= 0:
        return {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "as_of_date": as_of_date,
            "holding_days": 0,
            "status": "pending",
            "reason": "insufficient_holding_window",
            "basket_size": int(len(normalized)),
            "model_name": model_name,
            "model_path": model_path,
            "universe": universe,
        }

    readiness = inspect_signal_basket_price_readiness(
        normalized,
        as_of_date=as_of_date,
        price_loader=price_loader,
    )
    symbols = normalized["symbol"].astype(str).tolist()
    prices = price_loader(symbols, price_basis_date, as_of_date)
    start_prices = prices[prices["date"] == price_basis_date].set_index("symbol")["close"] if not prices.empty else pd.Series(dtype=float)
    end_prices = prices[prices["date"] == as_of_date].set_index("symbol")["close"] if not prices.empty else pd.Series(dtype=float)

    joined = normalized.copy()
    joined["symbol"] = joined["symbol"].astype(str)
    joined = joined.set_index("symbol")
    joined["start_price"] = start_prices
    joined["end_price"] = end_prices
    joined = joined.dropna(subset=["start_price", "end_price"])

    if joined.empty:
        return {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "as_of_date": as_of_date,
            "holding_days": holding_days,
            "status": "failed",
            "reason": readiness["reason"],
            "basket_size": int(len(normalized)),
            "coverage_count": 0,
            "coverage_ratio": 0.0,
            "ready_count": readiness["ready_count"],
            "ready_ratio": readiness["ready_ratio"],
            "missing_start_count": readiness["missing_start_count"],
            "missing_end_count": readiness["missing_end_count"],
            "missing_start_symbols": readiness["missing_start_symbols"],
            "missing_end_symbols": readiness["missing_end_symbols"],
            "model_name": model_name,
            "model_path": model_path,
            "universe": universe,
        }

    joined["stock_return"] = joined["end_price"] / joined["start_price"] - 1.0
    equal_weight_return = float(joined["stock_return"].mean())
    weights = _normalize_weights(joined["weight"].fillna(0.0).astype(float))
    weighted_return = float((joined["stock_return"] * weights).sum())
    benchmark_return = benchmark_loader(universe, price_basis_date, as_of_date)
    positive_ratio = float((joined["stock_return"] > 0).mean())

    top1_return = None
    top5_mean_return = None
    if "score_rank" in joined.columns:
        ranked = joined.sort_values("score_rank")
        if not ranked.empty:
            top1_return = float(ranked.iloc[0]["stock_return"])
            top5_mean_return = float(ranked.head(5)["stock_return"].mean())

    coverage_count = int(len(joined))
    basket_size = int(len(normalized))
    coverage_ratio = float(coverage_count / basket_size) if basket_size else 0.0
    status = "success" if coverage_count == basket_size else "partial"
    reason = "ok" if status == "success" else readiness["reason"]

    return {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "as_of_date": as_of_date,
        "holding_days": holding_days,
        "status": status,
        "reason": reason,
        "basket_size": basket_size,
        "coverage_count": coverage_count,
        "coverage_ratio": coverage_ratio,
        "ready_count": readiness["ready_count"],
        "ready_ratio": readiness["ready_ratio"],
        "missing_start_count": readiness["missing_start_count"],
        "missing_end_count": readiness["missing_end_count"],
        "missing_start_symbols": readiness["missing_start_symbols"],
        "missing_end_symbols": readiness["missing_end_symbols"],
        "equal_weight_return": equal_weight_return,
        "weighted_return": weighted_return,
        "benchmark_return": benchmark_return,
        "equal_weight_excess_return": None if benchmark_return is None else float(equal_weight_return - benchmark_return),
        "weighted_excess_return": None if benchmark_return is None else float(weighted_return - benchmark_return),
        "positive_ratio": positive_ratio,
        "top1_return": top1_return,
        "top5_mean_return": top5_mean_return,
        "model_name": model_name,
        "model_path": model_path,
        "universe": universe,
    }


def collect_signal_quality_snapshot(
    *,
    as_of_date: str,
    signal_dir: str | Path = "data",
    horizons: tuple[int, ...] = (1, 2, 3),
    recent_window: int = 5,
) -> SignalQualitySnapshot:
    observations: list[dict] = []
    for path in list_signal_basket_files(signal_dir, limit=max(recent_window + len(horizons), 10)):
        basket_df = pd.read_csv(path)
        observation = evaluate_signal_basket(basket_df, as_of_date=as_of_date)
        observation["basket_path"] = str(path)
        observations.append(observation)

    detailed = pd.DataFrame(observations)
    if detailed.empty:
        return SignalQualitySnapshot(
            summary={
                "as_of_date": as_of_date,
                "status": "missing_plan",
                "reason": "no_signal_basket_files",
                "recent_vintage_count": 0,
            },
            observations=pd.DataFrame(),
        )

    detailed = detailed.sort_values("signal_date", ascending=False).reset_index(drop=True)
    horizon_summary = {}
    for horizon in horizons:
        match = detailed[detailed["holding_days"] == horizon]
        if match.empty:
            horizon_summary[f"horizon_{horizon}d"] = {
                "status": "missing",
                "reason": "no_matching_vintage",
            }
            continue
        row = match.iloc[0].to_dict()
        horizon_summary[f"horizon_{horizon}d"] = {
            "status": row.get("status"),
            "reason": row.get("reason"),
            "signal_date": row.get("signal_date"),
            "holding_days": row.get("holding_days"),
            "equal_weight_return": row.get("equal_weight_return"),
            "weighted_return": row.get("weighted_return"),
            "benchmark_return": row.get("benchmark_return"),
            "weighted_excess_return": row.get("weighted_excess_return"),
            "basket_size": row.get("basket_size"),
            "coverage_ratio": row.get("coverage_ratio"),
        }

    completed = detailed[detailed["holding_days"] >= 1].head(recent_window)
    weighted_series = completed["weighted_return"] if "weighted_return" in completed.columns else pd.Series(dtype=float)
    excess_series = completed["weighted_excess_return"] if "weighted_excess_return" in completed.columns else pd.Series(dtype=float)
    recent_summary = {
        "recent_vintage_count": int(len(completed)),
        "recent_vintage_win_rate": float((weighted_series > 0).mean()) if not weighted_series.empty else None,
        "recent_vintage_avg_weighted_return": float(weighted_series.mean()) if not weighted_series.empty else None,
        "recent_vintage_avg_excess_return": float(excess_series.dropna().mean()) if not excess_series.empty and excess_series.notna().any() else None,
    }

    data_quality_status = "success"
    if any(item.get("status") == "failed" for item in horizon_summary.values() if isinstance(item, dict)):
        data_quality_status = "failed"
    elif any(item.get("status") == "partial" for item in horizon_summary.values() if isinstance(item, dict)):
        data_quality_status = "partial"

    summary = {
        "as_of_date": as_of_date,
        "status": "success",
        "data_quality_status": data_quality_status,
        **horizon_summary,
        **recent_summary,
    }
    return SignalQualitySnapshot(summary=summary, observations=detailed)


def build_signal_quality_blockers(summary: dict, *, required_horizons: tuple[int, ...] = (1, 2, 3)) -> list[str]:
    blockers: list[str] = []
    if not summary:
        return ["Signal quality summary missing"]

    for horizon in required_horizons:
        key = f"horizon_{horizon}d"
        horizon_summary = summary.get(key) or {}
        status = horizon_summary.get("status")
        if status in {"failed", "partial"}:
            blockers.append(
                f"Signal basket {key} data quality {status}: reason={horizon_summary.get('reason')} signal_date={horizon_summary.get('signal_date')}"
            )
    return blockers


def write_signal_quality_outputs(
    snapshot: SignalQualitySnapshot,
    *,
    output_dir: str | Path,
    as_of_date: str,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    detailed_path = output_dir / f"signal_quality_vintages_{as_of_date}.csv"
    snapshot.observations.to_csv(detailed_path, index=False)

    summary_path = output_dir / f"signal_quality_summary_{as_of_date}.json"
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot.summary, handle, indent=2, ensure_ascii=False)

    return {
        "signal_quality_vintages": str(detailed_path),
        "signal_quality_summary": str(summary_path),
    }
