"""Label computation functions — forward return, cs_zscore, coverage.

Sunk from scripts/research/compute_labels.py.  CLI entrypoint remains
in scripts/; all business logic lives here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd


EXECUTABLE_ENTRY_ELIGIBILITY_CONTRACT = (
    "canonical_open_snapshot_buyable_v1"
)
EXECUTABLE_SIGNAL_CUTOFF_CONTRACT = (
    "previous_trading_session_close_before_entry_open_v1"
)
EXECUTABLE_EXIT_OBSERVATION_CONTRACT = (
    "target_session_price_no_future_tradability_filter_v1"
)
ADJUSTED_PRICE_CONTRACT = (
    "tushare_adj_factor_adjusted_price_total_return_v1"
)


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


def _load_executable_price_panel(
    *,
    universe: str,
    start: str,
    end: str,
    pit_universe_artifact: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Load one shared PIT market panel for an executable label suite."""
    from qsys.data.adapter import QlibAdapter
    from qsys.data.calendar import get_trading_calendar

    adapter = QlibAdapter()
    adapter.init_qlib()
    instruments, spans = _resolve_pit_artifact(pit_universe_artifact)
    if instruments is None or spans is None:
        raise ValueError("executable label suite requires an immutable PIT artifact")
    spans = spans[
        (spans["effective_to"] >= start)
        & (spans["effective_from"] <= end)
    ].copy()
    end_membership = spans[
        (spans["effective_from"] <= end) & (spans["effective_to"] >= end)
    ]
    if spans.empty or end_membership.empty:
        raise ValueError(
            "PIT membership does not cover the requested data cutoff: "
            f"{end}"
        )
    instruments = sorted(spans["instrument"].unique().tolist())
    fields = [
        "$open", "$close", "$factor", "$paused", "$high_limit", "$low_limit"
    ]
    raw = adapter.get_features(
        instruments,
        fields,
        start_time=start,
        end_time=end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    if frame.columns.duplicated().any():
        duplicated = sorted(set(frame.columns[frame.columns.duplicated()]))
        raise ValueError(
            "executable label source has duplicate columns: "
            + ", ".join(duplicated)
        )
    missing = [field for field in fields if field not in frame.columns]
    if missing:
        raise ValueError(
            "executable label source lacks canonical market fields: "
            + ", ".join(missing)
        )
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["instrument"] = frame["instrument"].astype(str).str.upper()
    if frame.duplicated(["trade_date", "instrument"]).any():
        raise ValueError("executable label source has duplicate instrument/date rows")
    if frame.empty:
        raise ValueError("executable label source panel is empty")
    calendar_start = (
        pd.Timestamp(start) - pd.Timedelta(days=14)
    ).strftime("%Y-%m-%d")
    calendar = get_trading_calendar(calendar_start, end)
    if not calendar or start not in calendar:
        raise ValueError(
            f"executable label start must be a trading session: {start}"
        )
    if end not in calendar:
        raise ValueError(
            f"executable label data cutoff must be a trading session: {end}"
        )
    return frame, spans, calendar


def _entry_eligibility(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    open_price = pd.to_numeric(frame["$open"], errors="coerce")
    paused = pd.to_numeric(frame["$paused"], errors="coerce")
    high_limit = pd.to_numeric(frame["$high_limit"], errors="coerce")
    low_limit = pd.to_numeric(frame["$low_limit"], errors="coerce")
    has_open = open_price.notna() & np.isfinite(open_price) & open_price.gt(0)
    constraints_known = (
        paused.notna()
        & high_limit.notna()
        & low_limit.notna()
        & high_limit.gt(0)
        & low_limit.gt(0)
    )
    eligible = has_open & constraints_known & paused.eq(0) & open_price.lt(high_limit)
    reason = pd.Series("", index=frame.index, dtype="string")
    reason.loc[~has_open] = "missing_or_nonpositive_entry_open"
    reason.loc[has_open & ~constraints_known] = "entry_constraints_unknown"
    reason.loc[has_open & constraints_known & paused.ne(0)] = "entry_suspended"
    reason.loc[
        has_open & constraints_known & paused.eq(0) & open_price.ge(high_limit)
    ] = "entry_limit_up"
    return eligible.astype(bool), reason


def _exit_status(frame: pd.DataFrame) -> pd.Series:
    open_price = pd.to_numeric(frame["$open_end"], errors="coerce")
    paused = pd.to_numeric(frame["$paused_end"], errors="coerce")
    high_limit = pd.to_numeric(frame["$high_limit_end"], errors="coerce")
    low_limit = pd.to_numeric(frame["$low_limit_end"], errors="coerce")
    status = pd.Series("executable", index=frame.index, dtype="string")
    status.loc[frame["return_end_date"].isna()] = "immature"
    target = frame["return_end_date"].notna()
    observed = open_price.notna() & np.isfinite(open_price) & open_price.gt(0)
    status.loc[target & ~observed] = "target_open_unobserved"
    known = (
        paused.notna()
        & high_limit.notna()
        & low_limit.notna()
        & high_limit.gt(0)
        & low_limit.gt(0)
    )
    status.loc[target & observed & ~known] = "target_constraints_unknown"
    status.loc[target & observed & known & paused.ne(0)] = "target_suspended"
    status.loc[
        target & observed & known & paused.eq(0) & open_price.ge(high_limit)
    ] = "target_limit_up"
    status.loc[
        target & observed & known & paused.eq(0) & open_price.le(low_limit)
    ] = "target_limit_down"
    return status


def iter_executable_forward_returns(
    *,
    universe: str,
    horizons: list[int],
    start: str,
    end: str,
    pit_universe_artifact: str,
    label_templates: dict[str, str],
) -> Iterator[tuple[str, pd.DataFrame, dict[str, Any]]]:
    """Yield independently named open/open and close/close PIT labels.

    The shared panel is loaded once.  Entry eligibility uses only the entry
    session's canonical open snapshot.  Target-session tradability is
    recorded but never filters a row or changes ``is_valid``.
    """
    supported = {"open_to_open": "$open", "close_to_close": "$close"}
    unknown = sorted(set(label_templates) - set(supported))
    if unknown:
        raise ValueError(f"unsupported executable label intervals: {unknown}")
    clean_horizons = sorted(set(int(value) for value in horizons))
    if not clean_horizons or clean_horizons[0] <= 0:
        raise ValueError("executable label horizons must be positive")

    panel, spans, calendar = _load_executable_price_panel(
        universe=universe,
        start=start,
        end=end,
        pit_universe_artifact=pit_universe_artifact,
    )
    positions = {date: idx for idx, date in enumerate(calendar)}
    previous = {
        date: calendar[idx - 1] if idx > 0 else None
        for idx, date in enumerate(calendar)
    }
    base = panel[panel["trade_date"].between(start, end)].copy()
    base["signal_data_cutoff"] = base["trade_date"].map(previous)
    if base["signal_data_cutoff"].isna().any():
        raise ValueError("could not resolve a prior-session signal cutoff")
    entry_eligible, entry_reason = _entry_eligibility(base)
    base["entry_eligible"] = entry_eligible
    base["entry_invalid_reason"] = entry_reason

    end_columns = [
        "trade_date", "instrument", "$open", "$close", "$factor", "$paused",
        "$high_limit", "$low_limit",
    ]
    end_panel = panel[end_columns].rename(columns={
        "trade_date": "return_end_date",
        **{column: f"{column}_end" for column in end_columns[2:]},
    })
    for horizon in clean_horizons:
        working = base.copy()
        working["return_end_date"] = working["trade_date"].map(
            lambda date: (
                calendar[positions[date] + horizon]
                if date in positions and positions[date] + horizon < len(calendar)
                else None
            )
        )
        working = working.merge(
            end_panel,
            on=["return_end_date", "instrument"],
            how="left",
            # Several forward-tail rows can share a null target date.  The
            # target market panel must remain unique, while the left side is
            # legitimately many-to-one for those immature rows.
            validate="many_to_one",
        )
        working["maturity_date"] = working["return_end_date"]
        working["is_mature"] = working["return_end_date"].notna()
        working["exit_execution_status"] = _exit_status(working)
        working = _filter_membership(working, spans)

        for return_type, price_column in label_templates.items():
            label_id = str(price_column).format(horizon=horizon)
            field = supported[return_type]
            start_raw = pd.to_numeric(working[field], errors="coerce")
            end_raw = pd.to_numeric(
                working[f"{field}_end"], errors="coerce"
            )
            start_factor = pd.to_numeric(working["$factor"], errors="coerce")
            end_factor = pd.to_numeric(
                working["$factor_end"], errors="coerce"
            )
            start_price = start_raw * start_factor
            end_price = end_raw * end_factor
            label_value = end_price / start_price - 1.0
            finite_start = np.isfinite(start_price) & start_price.gt(0)
            finite_end = np.isfinite(end_price) & end_price.gt(0)
            finite_label = (
                np.isfinite(label_value) & finite_start & finite_end
            )
            label_missing_reason = np.select(
                [
                    working["return_end_date"].isna(),
                    ~finite_start,
                    ~finite_end,
                    ~finite_label,
                ],
                [
                    "immature",
                    "entry_price_unobserved",
                    "target_price_unobserved",
                    "nonfinite_return",
                ],
                default="",
            )
            result = pd.DataFrame({
                "trade_date": working["trade_date"],
                "label_date": working["trade_date"],
                "instrument": working["instrument"],
                "label_id": label_id,
                "horizon": horizon,
                "shift": horizon,
                "return_type": return_type,
                "price_basis": f"adjusted_{field[1:]}",
                "signal_data_cutoff": working["signal_data_cutoff"],
                "return_start_date": working["trade_date"],
                "return_start_price": start_price,
                "return_end_date": working["return_end_date"],
                "return_end_price": end_price,
                "maturity_date": working["maturity_date"],
                "is_mature": working["is_mature"],
                "entry_eligible": working["entry_eligible"],
                "exit_execution_status": working["exit_execution_status"],
                "label_value": label_value.where(finite_label).astype(np.float32),
                "universe": universe,
                "is_valid": working["entry_eligible"] & working["is_mature"],
                "invalid_reason": working["entry_invalid_reason"],
                "label_missing_reason": label_missing_reason,
            }).reset_index(drop=True)
            metadata = {
                "horizon": horizon,
                "return_type": return_type,
                "price_basis": f"adjusted_{field[1:]}",
                "signal_cutoff_contract": EXECUTABLE_SIGNAL_CUTOFF_CONTRACT,
                "entry_eligibility_contract": (
                    EXECUTABLE_ENTRY_ELIGIBILITY_CONTRACT
                ),
                "exit_observation_contract": EXECUTABLE_EXIT_OBSERVATION_CONTRACT,
                "exit_status_basis": "canonical_open_snapshot",
                "future_exit_status_used_for_filter": False,
                "corporate_action_adjustment_contract": ADJUSTED_PRICE_CONTRACT,
                "data_cutoff": end,
                "mature_row_count": int(result["is_mature"].sum()),
                "entry_eligible_row_count": int(result["entry_eligible"].sum()),
                "valid_observed_row_count": int(
                    (result["is_valid"] & result["label_value"].notna()).sum()
                ),
                "missing_target_price_row_count": int(
                    result["label_missing_reason"].eq(
                        "target_price_unobserved"
                    ).sum()
                ),
                "missing_entry_price_row_count": int(
                    result["label_missing_reason"].eq(
                        "entry_price_unobserved"
                    ).sum()
                ),
            }
            yield label_id, result, metadata


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
