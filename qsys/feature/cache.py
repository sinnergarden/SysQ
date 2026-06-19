"""Feature cache — persistent parquet cache for derived features.

Problem: each rolling window re-computes derived features from scratch.
67 windows × same span = massive repeated compute (especially neutralized).

Solution: cache ``_build_semantic_features()`` output (MultiIndex format)
after the first call.  Subsequent calls skip the builder and join directly.

Storage: ``data/canonical/features/<universe>/<hash>.parquet``
Key = hash(universe, sorted_fields, start, end).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.utils.logger import log

_CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "canonical" / "features"


def _cache_key(universe: str, fields: list[str], start: str, end: str) -> str:
    raw = f"{universe}::{sorted(fields)}::{start}::{end}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _path(universe: str, fields: list[str], start: str, end: str) -> Path:
    return _CACHE_ROOT / universe / f"{_cache_key(universe, fields, start, end)}.parquet"


def has(universe: str, fields: list[str], start: str, end: str) -> bool:
    return _path(universe, fields, start, end).exists()


def load(universe: str, fields: list[str], start: str, end: str,
         *, request_start: str | None = None) -> pd.DataFrame:
    """Load cached features.

    Returns a DataFrame with the MultiIndex (instrument, datetime)
    matching ``QlibAdapter.get_features()`` native df index convention,
    filtered by *request_start* if given.
    """
    df = pd.read_parquet(_path(universe, fields, start, end))
    if "trade_date" in df.columns and "ts_code" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index(["ts_code", "trade_date"])
        df.index = df.index.rename(["instrument", "datetime"])
        df = df.drop(columns=["ts_code"], errors="ignore")
    if request_start and isinstance(df.index, pd.MultiIndex):
        dt_level = df.index.get_level_values(1)
        df = df[dt_level >= pd.Timestamp(request_start)]
    return df


def save(universe: str, fields: list[str], start: str, end: str,
         df: pd.DataFrame) -> None:
    """Save *df* (MultiIndex) to cache, flat for parquet round-trip."""
    p = _path(universe, fields, start, end)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = df.reset_index().rename(columns={"datetime": "trade_date"})
    out["ts_code"] = out["instrument"]
    out.to_parquet(p, index=False)
    log.info("Cached %d rows × %d cols → %s", len(out), len(out.columns), p)
