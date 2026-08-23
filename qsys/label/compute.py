"""Label computation functions — forward return, cs_zscore, coverage.

Sunk from scripts/research/compute_labels.py.  CLI entrypoint remains
in scripts/; all business logic lives here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional zscore, clip, handle constant/all-NaN."""
    clean = s.dropna()
    if len(clean) == 0:
        return pd.Series(float("nan"), index=s.index)
    std = clean.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((clean - clean.mean()) / std).clip(-clip, clip).reindex(s.index)


def _resolve_pit_registry(adapter, universe: str):
    """Resolve a multi-span PIT registry to (instrument_list, spans).

    A PIT registry is a qlib instrument file (``qlib_dir/instruments/
    <name>.txt``) where at least one instrument appears on more than one
    line — i.e. its membership has gaps.  For such registries the naive
    span-clip-then-shift label computation is wrong (shift crosses the gap),
    so callers fetch CONTINUOUS history via the instrument list and filter to
    membership spans only after the label has matured.

    Returns ``(None, None)`` when ``universe`` is not a multi-span registry —
    a single-span registry (csi800, all), a plain instrument list, or an
    individual code.  Callers then use the legacy string/registry path which
    is already correct for contiguous membership.
    """
    if not isinstance(universe, str):
        return None, None
    low = universe.lower()
    qlib_dir = getattr(adapter, "qlib_dir", None)
    if qlib_dir is None:
        return None, None
    reg_path = qlib_dir / "instruments" / f"{low}.txt"
    if not reg_path.exists():
        return None, None
    rows = []
    for line in reg_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    if not rows:
        return None, None
    spans = pd.DataFrame(rows, columns=["instrument", "effective_from", "effective_to"])
    # A PIT registry only has non-trivial membership semantics when some
    # instrument holds more than one (non-contiguous) span.
    if not (spans.groupby("instrument").size() > 1).any():
        return None, None
    instruments = sorted(spans["instrument"].unique().tolist())
    return instruments, spans


def _resolve_pit_artifact(artifact: str | None):
    """Resolve an explicit immutable PIT artifact to instruments and spans."""
    if not artifact:
        return None, None
    from qsys.research.pit_universe import PitUniverseStore

    store = PitUniverseStore(artifact)
    spans = store.spans[["instrument", "effective_from", "effective_to"]].copy()
    for column in ("effective_from", "effective_to"):
        spans[column] = pd.to_datetime(
            spans[column], format="%Y%m%d", errors="raise"
        ).dt.strftime("%Y-%m-%d")
    return store.instruments, spans


def _filter_membership(frame: pd.DataFrame, spans: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows whose (instrument, trade_date) is inside a membership span.

    ``spans`` has columns instrument / effective_from / effective_to (all
    ``YYYY-MM-DD`` strings, inclusive on both ends).  A date falling in the
    membership gap is dropped; a member date survives even when its forward
    label was computed from prices beyond the span end (correct — the label
    is the realized forward return of holding the member, independent of the
    later membership exit).
    """
    if spans is None or spans.empty:
        return frame
    f = frame[["trade_date", "instrument"]].copy()
    f["_row"] = np.arange(len(f))
    merged = f.merge(spans, on="instrument", how="left")
    ok = (merged["trade_date"] >= merged["effective_from"]) & (
        merged["trade_date"] <= merged["effective_to"]
    )
    member_rows = merged.loc[ok, "_row"].unique()
    return frame.iloc[np.sort(member_rows)]


def compute_forward_return(
    universe: str,
    horizon: int,
    start: str,
    end: str,
    price_field: str = "close",
    norm_type: str = "cs_zscore",
    clip_val: float | None = 3.0,
    label_id_override: str | None = None,
    pit_universe_artifact: str | None = None,
) -> pd.DataFrame:
    """Compute forward return label.

    Price basis is adjusted close (``$close * $factor``) so that dividends,
    stock splits, and rights issues do not distort the return calculation:

        adjusted_close = close * factor
        forward_return = shift(-horizon, adjusted_close) / adjusted_close - 1

    ``$close`` is the raw (unadjusted) close from the Tushare ``daily`` API.
    ``$factor`` is the cumulative adjustment factor from the Tushare
    ``adj_factor`` API, stored as an independent qlib field.

    The ``raw`` suffix in the label ID means *no normalization*, not
    *unadjusted price*. All forward return labels use adjusted prices
    regardless of normalization.

    Parameters
    ----------
    norm_type: "" for raw, "cs_zscore" for cross-sectional normalization.
    clip_val: clip threshold (None = no clip).

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    label_id = ``fwd_ret_{horizon}d_{suffix}`` unless ``label_id_override``
    is given (used for PIT-namespaced label stores, e.g.
    ``fwd_ret_180d_raw_pit``).
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    price_col = f"${price_field}"
    # Resolve multi-span PIT registries: for those, fetch CONTINUOUS trading
    # history (list path — no span clipping) so that ``shift(-horizon)`` is a
    # trading-day offset, not a row offset across membership gaps.  PIT
    # membership filtering is applied only AFTER the label has matured, so a
    # member's label at T is the realized forward return of holding it for
    # ``horizon`` trading days — independent of a later membership exit.
    instruments, spans = _resolve_pit_artifact(pit_universe_artifact)
    if instruments is None:
        instruments, spans = _resolve_pit_registry(adapter, universe)
    if instruments is not None:
        raw = adapter.get_features(instruments, [price_col, "$factor"],
                                   start_time=start, end_time=end)
    else:
        # Fetch both price and adjustment factor — factor is the cumulative
        # adjustment factor from Tushare (1.0 = no adjustment).
        raw = adapter.get_features(universe, [price_col, "$factor"], start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    # Forward-adjusted close — essential for correct long-horizon returns
    frame["_adj_price"] = frame[price_col] * frame["$factor"]

    shifted = frame.groupby("instrument")["_adj_price"].transform(lambda s: s.shift(-horizon))
    fwd = shifted / frame["_adj_price"] - 1.0
    frame["_fwd"] = fwd

    # PIT membership filtering happens AFTER label maturity (the shift above).
    # Rows inside a membership gap are dropped; member rows keep the label
    # computed from continuous history (which may cross the span boundary).
    if spans is not None:
        frame = _filter_membership(frame, spans)

    suffix = "raw"
    if norm_type == "cs_zscore":
        suffix = "cs_zscore"
        if clip_val is not None:
            suffix += f"_clip{int(clip_val)}"
        valid = frame.dropna(subset=["_fwd"]).copy()
        valid["label_value"] = valid.groupby("trade_date")["_fwd"].transform(
            lambda g: cs_zscore(g.astype(float), clip=clip_val or 3.0)
        )
        label_value = valid["label_value"].astype(np.float32)
        frame = valid
    else:
        label_value = frame["_fwd"].astype(np.float32)

    label_id = label_id_override or f"fwd_ret_{horizon}d_{suffix}"
    result = pd.DataFrame({
        "trade_date": frame["trade_date"],
        "instrument": frame["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": label_value,
    })
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def compute_raw_forward_return(
    universe: str,
    horizon: int,
    start: str,
    end: str,
    price_field: str = "close",
    label_id_override: str | None = None,
    pit_universe_artifact: str | None = None,
) -> pd.DataFrame:
    """Compute raw (un-normalized) forward return label.
    Delegates to ``compute_forward_return`` with no normalization.
    """
    return compute_forward_return(
        universe, horizon, start, end,
        price_field=price_field, norm_type="", clip_val=None,
        label_id_override=label_id_override,
        pit_universe_artifact=pit_universe_artifact,
    )


def compute_future_max_drawdown(
    universe: str,
    horizon: int = 5,
    start: str = "2020-01-01",
    end: str = "2026-01-01",
    price_field: str = "close",
) -> pd.DataFrame:
    """Compute forward window peak-to-trough max drawdown label.

    For each feature date T, measures the worst peak-to-trough drawdown
    within the forward window [T+1, T+horizon]:

        adj_price_i = close_i * factor_i
        for i in [T+1, T+horizon]:
            cummax_i = max(adj_price_{T+1}, ..., adj_price_i)
            drawdown_i = adj_price_i / cummax_i - 1   (always <= 0)
        label_T = min(drawdown_i)

    Important: drawdown is computed INSIDE the forward window only.
    It does NOT anchor to the T close price.  This label is designed
    for T-date features predicting T+1 entry risk.

    A more negative value means a deeper drawdown.  Binary thresholding
    is done separately via :func:`compute_binary_max_drawdown`.

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    label_id = ``fwd_maxdd_{horizon}d_raw``.
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    price_col = f"${price_field}"

    # Extend fetch end by ~2× horizon so forward labels can be computed
    from datetime import datetime, timedelta
    end_buf = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=horizon * 2)).strftime("%Y-%m-%d")

    raw = adapter.get_features(universe, [price_col, "$factor"],
                               start_time=start, end_time=end_buf)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["_adj_price"] = frame[price_col] * frame["$factor"]

    def _max_dd_series(vals: np.ndarray, h: int) -> np.ndarray:
        n = len(vals)
        result = np.full(n, np.nan, dtype=np.float32)
        for i in range(n - h):
            win = vals[i + 1:i + 1 + h]
            if len(win) < h:
                continue
            cmax = np.maximum.accumulate(win)
            dd = win / cmax - 1.0
            result[i] = np.min(dd)
        return result

    frame["_maxdd"] = frame.groupby("instrument")["_adj_price"].transform(
        lambda s: _max_dd_series(s.values, horizon)
    )

    # Trim to requested range (exclude the buffer we added for forward lookahead)
    trimmed = frame[frame["trade_date"].between(start, end)].copy()
    label_id = f"fwd_maxdd_{horizon}d_raw"
    result = pd.DataFrame({
        "trade_date": trimmed["trade_date"],
        "instrument": trimmed["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": trimmed["_maxdd"].astype(np.float32),
    })
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def compute_binary_max_drawdown(
    universe: str,
    horizon: int = 5,
    start: str = "2020-01-01",
    end: str = "2026-01-01",
    threshold: float = -0.05,
    price_field: str = "close",
) -> pd.DataFrame:
    """Binary version of future max drawdown — for stop-loss classification.

    1 = future max drawdown is WORSE than *threshold* (i.e. deeper loss).
    0 = no drawdown beyond threshold.
    NaN = label not yet observable (forward tail).

    label_id = ``fwd_maxdd_{horizon}d_binary_{pct}pct``.
    """
    continuous = compute_future_max_drawdown(universe, horizon, start, end, price_field)
    label_value = continuous["label_value"].apply(
        lambda v: 1.0 if pd.notna(v) and v < threshold else (0.0 if pd.notna(v) else np.nan)
    )
    pct = int(abs(threshold) * 100)
    result = continuous.copy()
    result["label_id"] = f"fwd_maxdd_{horizon}d_binary_{pct}pct"
    result["label_value"] = label_value.astype(np.float32)
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def coverage(row_count: int, expected: int) -> float:
    """Coverage ratio: actual rows / expected (dates x universe)."""
    if expected <= 0:
        return 0.0
    return min(row_count / expected, 1.0)
