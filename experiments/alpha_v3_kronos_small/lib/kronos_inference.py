"""Kronos-small model inference using the official KronosPredictor API."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Add Kronos model code to path
KRONOS_LIB_DIR = Path(__file__).resolve().parent
if str(KRONOS_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(KRONOS_LIB_DIR))

_HAS_KRONOS = False
try:
    from kronos_model.kronos import Kronos, KronosTokenizer, KronosPredictor
    _HAS_KRONOS = True
except ImportError as e:
    Kronos = None
    KronosTokenizer = None
    KronosPredictor = None
    logger.warning(f"Kronos model code not available: {e}")

_HAS_TORCH = False
try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None


def check_model_available() -> bool:
    """Check if Kronos-small model and tokenizer are cached locally."""
    if not _HAS_TORCH:
        return False
    if not _HAS_KRONOS:
        return False

    # Check both model and tokenizer
    for model_id in ["NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base"]:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        safe_id = model_id.replace("/", "--")
        if not any(cache_dir.glob(f"models--{safe_id}*")):
            logger.warning(f"{model_id} not found in HF cache")
            return False
    return True


def load_model(
    device: str = "cuda",
    max_context: int = 512,
) -> tuple[Any, Any, dict[str, Any]]:
    """Load Kronos-small model and tokenizer.

    Returns
    -------
    (predictor, tokenizer, status_dict)
    On failure: (None, None, {"status": "failed", "reason": "..."})
    """
    if not _HAS_KRONOS:
        return None, None, {"status": "failed", "reason": "Kronos model code not available"}
    if not _HAS_TORCH:
        return None, None, {"status": "failed", "reason": "torch not installed"}

    dev = device if (torch is not None and torch.cuda.is_available() and device == "cuda") else "cpu"
    print(f"[Kronos] Loading model on {dev} (max_context={max_context})")

    try:
        tokenizer = KronosTokenizer.from_pretrained(
            "NeoQuasar/Kronos-Tokenizer-base",
        )
        print("  Tokenizer loaded")

        model = Kronos.from_pretrained(
            "NeoQuasar/Kronos-small",
        )
        model = model.to(dev)
        model.eval()
        n_params = sum(p.numel() for p in model.parameters())
        print(f"  Model loaded: {n_params/1e6:.1f}M params")

        predictor = KronosPredictor(model, tokenizer, device=dev, max_context=max_context)
        print("  Predictor ready")

        status = {
            "status": "success",
            "device": dev,
            "n_params": n_params,
            "max_context": max_context,
        }
        return predictor, tokenizer, status

    except Exception as e:
        print(f"[Kronos] Failed to load: {e}")
        return None, None, {"status": "failed", "reason": str(e)}


def _store_pred_row(
    all_rows: list[dict], pred_df: pd.DataFrame,
    trade_date: str, instrument: str, pred_len: int,
) -> None:
    """Append prediction steps for one (date, instrument) pair."""
    for step in range(1, pred_len + 1):
        if step <= len(pred_df):
            row = pred_df.iloc[step - 1]
            all_rows.append({
                "trade_date": trade_date,
                "instrument": instrument,
                "step": step,
                "pred_open": float(row.get("open", 0)),
                "pred_high": float(row.get("high", 0)),
                "pred_low": float(row.get("low", 0)),
                "pred_close": float(row.get("close", 0)),
                "pred_volume": float(row.get("volume", 0)),
                "pred_amount": float(row.get("amount", 0)),
            })


def run_inference(
    ohlcv_df: pd.DataFrame,
    predictor: Any,
    lookback: int = 90,
    pred_len: int = 20,
    sample_count: int = 1,
    batch_size: int = 128,
    target_dates: list[str] | None = None,
) -> pd.DataFrame:
    """Run Kronos inference on all stocks across given dates.

    Uses ``KronosPredictor.predict_batch`` to parallelise across stocks
    for each prediction date, leveraging GPU efficiently.

    Parameters
    ----------
    ohlcv_df : pd.DataFrame
        OHLCV data with columns: trade_date, instrument, fq_open, fq_high,
        fq_low, fq_close, volume, amount. Sorted by instrument, trade_date.
    predictor : KronosPredictor
        Loaded KronosPredictor instance.
    lookback : int
        Number of past days to use as context.
    pred_len : int
        Number of future steps to predict.
    sample_count : int
        Number of forecast paths to average (1 = deterministic).
    batch_size : int
        Max number of stocks per batch for ``predict_batch``.
    target_dates : list[str] | None
        If set, only predict for these dates (YYYY-MM-DD). Greatly reduces
        computation when you only need predictions on rebalance days.

    Returns
    -------
    pd.DataFrame with columns:
        trade_date, instrument, step, pred_open, pred_high, pred_low,
        pred_close, pred_volume, pred_amount
    """
    import torch
    from collections import defaultdict

    print(f"[Kronos] Running inference: lookback={lookback}, pred_len={pred_len}, "
          f"sample_count={sample_count}, batch_size={batch_size}",
          end="")
    if target_dates is not None:
        print(f", target_dates={len(target_dates)}")
    else:
        print()

    # Pre-filter stocks with enough history
    instruments = ohlcv_df["instrument"].unique()
    stock_data: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        grp = ohlcv_df[ohlcv_df["instrument"] == inst].sort_values("trade_date").reset_index(drop=True)
        if len(grp) >= lookback + 1:
            stock_data[inst] = grp

    n_skipped = len(instruments) - len(stock_data)
    if len(stock_data) == 0:
        print(f"  No instruments have enough history (need >{lookback} days)")
        return pd.DataFrame(columns=[
            "trade_date", "instrument", "step",
            "pred_open", "pred_high", "pred_low", "pred_close",
            "pred_volume", "pred_amount",
        ])

    # Build date‑grouped windows — only for target_dates if provided
    target_set: set[str] | None = set(target_dates) if target_dates is not None else None
    date_groups: dict[str, list[tuple[str, str, pd.DataFrame, pd.Series, pd.Series]]] = defaultdict(list)

    for inst, grp in stock_data.items():
        n = len(grp)
        for i in range(lookback, n):
            current_date = grp.iloc[i]["trade_date"]
            date_key = str(current_date)[:10]

            if target_set is not None and date_key not in target_set:
                continue

            hist = grp.iloc[i - lookback : i]

            x_df = hist[["fq_open", "fq_high", "fq_low", "fq_close", "volume", "amount"]].rename(columns={
                "fq_open": "open", "fq_high": "high", "fq_low": "low", "fq_close": "close",
            })
            x_ts = pd.Series(pd.to_datetime(hist["trade_date"]).values, name="timestamp")
            last_dt = pd.to_datetime(current_date)
            y_ts = pd.Series(
                pd.date_range(start=last_dt + timedelta(days=1), periods=pred_len, freq="D"),
                name="timestamp",
            )
            date_groups[date_key].append((inst, str(current_date)[:10], x_df, x_ts, y_ts))

    if not date_groups:
        print("  No windows to predict (no target_dates in range)")
        return pd.DataFrame(columns=[
            "trade_date", "instrument", "step",
            "pred_open", "pred_high", "pred_low", "pred_close",
            "pred_volume", "pred_amount",
        ])

    # Process grouped by date — batch across stocks per date
    all_rows: list[dict] = []
    n_total_preds = sum(len(v) for v in date_groups.values())
    n_done = 0

    for date_key in sorted(date_groups.keys()):
        entries = date_groups[date_key]

        for batch_start in range(0, len(entries), batch_size):
            batch = entries[batch_start:batch_start + batch_size]
            batch_dfs = [e[2] for e in batch]
            batch_x_ts = [e[3] for e in batch]
            batch_y_ts = [e[4] for e in batch]
            batch_meta = [(e[0], e[1]) for e in batch]

            try:
                with torch.inference_mode():
                    preds = predictor.predict_batch(
                        df_list=batch_dfs,
                        x_timestamp_list=batch_x_ts,
                        y_timestamp_list=batch_y_ts,
                        pred_len=pred_len,
                        T=1.0, top_p=0.9,
                        sample_count=sample_count,
                        verbose=False,
                    )
                for (inst, td), pred_df in zip(batch_meta, preds):
                    _store_pred_row(all_rows, pred_df, td, inst, pred_len)

            except Exception:
                for (inst, td), df, x_ts, y_ts in zip(batch_meta, batch_dfs, batch_x_ts, batch_y_ts):
                    try:
                        with torch.inference_mode():
                            pred_df = predictor.predict(
                                df=df, x_timestamp=x_ts, y_timestamp=y_ts,
                                pred_len=pred_len, T=1.0, top_p=0.9,
                                sample_count=sample_count,
                            )
                        _store_pred_row(all_rows, pred_df, td, inst, pred_len)
                    except Exception:
                        continue

        n_done += len(entries)
        if n_done % 5000 == 0 or n_done == n_total_preds:
            print(f"  [{n_done}/{n_total_preds}] predictions")

    result = pd.DataFrame(all_rows)
    print(f"  Inference done: {len(result)} rows from {len(stock_data)} instruments "
          f"(skipped {n_skipped})")
    return result


def save_raw_predictions(predictions: pd.DataFrame, path: Path) -> str:
    """Save raw predictions to parquet. Returns path string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(path, index=False)
    print(f"  → {path}")
    return str(path)


def load_raw_predictions(path: Path) -> pd.DataFrame:
    """Load raw predictions from parquet."""
    return pd.read_parquet(path)
