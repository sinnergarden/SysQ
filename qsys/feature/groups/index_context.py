"""Index daily data loader — CSV-based, OHLCV + volume.

Reads from ``data/raw/index/<ts_code>.csv`` — maintained by
daily sync pipeline and backfill_index_daily.py.

Compatible columns (all optional except trade_date, close):
  ts_code, trade_date, open, high, low, close, pre_close,
  change, pct_chg, vol, amount

See Also
--------
scripts/ops/backfill_index_daily.py — initial historical backfill
scripts/ops/sync_csi800_daily.py — daily incremental update
docs/ARCHITECTURE.md §4.1 — data layer: Canonical SOT (Index)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.config import cfg

INDEX_CODE_MAP = {
    "sse": "000001.SH",
    "hs300": "000300.SH",
    "zz500": "000905.SH",
    "zz1000": "000852.SH",
    "cyb": "399006.SZ",
    "kc50": "000688.SH",
    "csi800": "000906.SH",
}

# Order used by load_multi_index — matches ARCHITECTURE §4.1 coverage
DEFAULT_BENCHMARK_ORDER = [
    "000300.SH",  # CSI 300 — 大盘基准
    "000905.SH",  # CSI 500 — 中盘基准
    "000852.SH",  # CSI 1000 — 小盘基准
    "000906.SH",  # CSI 800 — csi800 策略基准
    "000001.SH",  # 上证综指 — regime
    "000688.SH",  # 科创50 — regime
    "399006.SZ",  # 创业板指 — regime
]


def _read_index_csv(path: Path) -> pd.DataFrame:
    """Read a single index CSV, parse dates, return sorted by trade_date."""
    df = pd.read_csv(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def load_index_daily(
    index_name: str = "hs300",
    root: str | None = None,
) -> pd.DataFrame:
    """Load index daily data, returning **all available columns**.

    Parameters
    ----------
    index_name:
        Key into ``INDEX_CODE_MAP`` (e.g. ``"hs300"``, ``"csi800"``).
    root:
        Path to ``data/raw/index/``.  Detected from config when ``None``.

    Returns
    -------
    pd.DataFrame
        Columns include at least ``trade_date``, ``close``, and any
        other columns present in the CSV (open, high, low, vol, …).
        Sorted by trade_date ascending.

    Examples
    --------
    >>> df = load_index_daily("hs300")
    >>> df.columns
    Index(['ts_code', 'trade_date', 'close', 'open', 'high', 'low', ...])
    """
    if root is None:
        root = str(Path(cfg.get_path("root")) / "raw" / "index")
    code = INDEX_CODE_MAP[index_name]
    path = Path(root) / f"{code}.csv"
    return _read_index_csv(path)


def load_index_close(
    index_name: str = "hs300",
    root: str | None = None,
) -> pd.DataFrame:
    """Load only ``trade_date`` and ``close`` — legacy close-only contract.

    Useful for callers that do not need OHLCV and want a predictable
    two-column result regardless of what columns the underlying CSV has.
    """
    df = load_index_daily(index_name, root=root)
    result = df[["trade_date", "close"]].copy()
    return result


def load_index_ohlcv(
    index_name: str = "hs300",
    root: str | None = None,
) -> pd.DataFrame:
    """Load index with standard OHLCV columns, in fixed order.

    Returns columns ``trade_date, open, high, low, close, volume``
    in that order.  ``volume`` is sourced from ``vol`` when the
    ``volume`` column is absent.
    """
    df = load_index_daily(index_name, root=root)
    result = pd.DataFrame()
    result["trade_date"] = df["trade_date"]
    result["open"] = df.get("open", pd.NA)
    result["high"] = df.get("high", pd.NA)
    result["low"] = df.get("low", pd.NA)
    result["close"] = df.get("close", pd.NA)
    if "volume" in df.columns:
        result["volume"] = df["volume"]
    elif "vol" in df.columns:
        result["volume"] = df["vol"]
    else:
        result["volume"] = pd.NA
    return result


def load_multi_index(
    codes: list[str] | None = None,
    root: str | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Load multiple indices into one wide DataFrame, one column per index.

    Parameters
    ----------
    codes:
        List of index codes (e.g. ``["000300.SH", "000905.SH"]``).
        Defaults to ``DEFAULT_BENCHMARK_ORDER``.
    root:
        Path to ``data/raw/index/``.
    columns:
        Column(s) to pivot wide.  Defaults to ``["close"]``.

    Returns
    -------
    pd.DataFrame
        Indexed by ``trade_date``, with one column per (index, field), e.g.
        ``close_000300.SH``, ``close_000905.SH``.
    """
    if codes is None:
        codes = DEFAULT_BENCHMARK_ORDER
    if columns is None:
        columns = ["close"]
    if root is None:
        root = str(Path(cfg.get_path("root")) / "raw" / "index")

    root_path = Path(root)
    merged = None
    for code in codes:
        path = root_path / f"{code}.csv"
        if not path.exists():
            continue
        df = _read_index_csv(path)
        df = df[["trade_date"] + [c for c in columns if c in df.columns]]
        df = df.rename(columns={c: f"{c}_{code}" for c in columns})
        if merged is None:
            merged = df
        else:
            merged = pd.merge(merged, df, on="trade_date", how="outer")

    if merged is None:
        return pd.DataFrame()
    merged = merged.sort_values("trade_date").reset_index(drop=True)
    return merged


# ── Backward-compatible aliases ────────────────────────────────────────


def attach_index_context(df: pd.DataFrame, index_name: str = "hs300") -> pd.DataFrame:
    """Legacy: attach a single ``index_close`` column.

    Renames before merge to avoid column clash with ``df``'s existing
    ``close`` column.  Kept for callers that do not specify a closer
    contract.  Prefer using ``load_index_daily`` or ``load_multi_index``
    directly.
    """
    idx = load_index_close(index_name=index_name)
    rename_close = idx.rename(columns={"close": f"{index_name}_close"})
    out = df.merge(rename_close, on="trade_date", how="left")
    out["index_close"] = out[f"{index_name}_close"]
    return out
