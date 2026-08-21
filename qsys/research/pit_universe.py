"""Point-in-time index membership store (csi800_pit_v1 artifact).

The ``csi800_pit_v1`` artifact is a set of membership spans reconstructed from
Tushare ``index_weight`` monthly snapshots.  Each span ``[effective_from,
effective_to]`` is an inclusive interval during which ``instrument`` was a
constituent of ``index_code``.  Membership intervals for one instrument are
disjoint: a name can leave the index and later rejoin, producing gapped spans.

This module provides a read-only accessor over that artifact.  Every query is
bound to the artifact's provenance: ``membership_sha256`` is the sha256 of the
``membership.parquet`` file bytes, recomputed on load and compared against the
manifest, so any research result that cites this store can be re-verified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ARTIFACT_DIR = Path("data") / "research" / "universes" / "csi800_pit_v1"
SPAN_COLUMNS = [
    "index_code",
    "instrument",
    "effective_from",
    "effective_to",
    "source",
    "source_date",
    "source_version",
]


def _normalize_date(value: str | pd.Timestamp) -> str:
    """Coerce a date-like value to an ``YYYYMMDD`` string."""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) == 8 and text.isdigit():
        return text
    raise ValueError(f"cannot normalize date: {value!r}")


def _in_interval(date_int: int, start_int: int, end_int: int) -> bool:
    return start_int <= date_int <= end_int


@dataclass(frozen=True)
class PitUniverseProvenance:
    """Immutable provenance record bound to the membership artifact."""

    universe_id: str
    membership_sha256: str
    raw_source_hash: str
    source: str
    source_date: str
    n_snapshots: int
    snapshot_date_range: list[str]
    n_unique_instruments: int
    n_membership_spans: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "membership_sha256": self.membership_sha256,
            "raw_source_hash": self.raw_source_hash,
            "source": self.source,
            "source_date": self.source_date,
            "n_snapshots": self.n_snapshots,
            "snapshot_date_range": list(self.snapshot_date_range),
            "n_unique_instruments": self.n_unique_instruments,
            "n_membership_spans": self.n_membership_spans,
            "description": self.description,
        }


class PitUniverseStore:
    """Read-only accessor over a point-in-time index membership artifact."""

    def __init__(
        self,
        artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
        *,
        verify_hash: bool = True,
    ) -> None:
        # Accept a bare dirname ("csi1800_pit_v1") as well as an explicit
        # path; resolve bare names under data/research/universes/.
        raw = Path(artifact_dir)
        if not raw.is_absolute() and not (raw / "manifest.json").is_file():
            candidate = Path("data") / "research" / "universes" / artifact_dir
            if (candidate / "manifest.json").is_file():
                raw = candidate
        self.artifact_dir = raw
        self._manifest = self._load_manifest()
        self._spans = self._load_membership(verify_hash=verify_hash)
        self._validate()

    # -- loading and provenance ------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        path = self.artifact_dir / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"PIT universe manifest not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_membership(self, *, verify_hash: bool) -> pd.DataFrame:
        path = self.artifact_dir / "membership.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"PIT universe membership not found: {path}")
        frame = pd.read_parquet(path)
        missing = [c for c in ("instrument", "effective_from", "effective_to") if c not in frame.columns]
        if missing:
            raise ValueError(f"membership.parquet missing columns: {missing}")
        if verify_hash:
            expected = self._manifest.get("membership_sha256")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected and actual != expected:
                raise ValueError(
                    f"PIT membership hash mismatch: expected {expected}, got {actual} "
                    f"(artifact changed since build)"
                )
        frame = frame.copy()
        frame["instrument"] = frame["instrument"].astype(str).str.strip().str.upper()
        frame["effective_from"] = frame["effective_from"].astype(str).str.strip()
        frame["effective_to"] = frame["effective_to"].astype(str).str.strip()
        return frame

    def _validate(self) -> None:
        if self._spans.empty:
            raise ValueError("PIT membership is empty")
        bad = self._spans[self._spans["effective_from"] > self._spans["effective_to"]]
        if not bad.empty:
            raise ValueError(
                f"{len(bad)} spans have effective_from > effective_to "
                f"(first: {bad.iloc[0]['instrument']})"
            )
        for name in ("instrument", "effective_from", "effective_to"):
            if self._spans[name].isna().any():
                raise ValueError(f"PIT membership has NaN in column {name}")

    # -- public accessors -------------------------------------------------

    @property
    def provenance(self) -> PitUniverseProvenance:
        return PitUniverseProvenance(
            universe_id=str(self._manifest.get("universe_id")),
            membership_sha256=str(self._manifest.get("membership_sha256")),
            raw_source_hash=str(self._manifest.get("raw_source_hash")),
            source=str(self._manifest.get("source")),
            source_date=str(self._manifest.get("source_date")),
            n_snapshots=int(self._manifest.get("n_snapshots", 0)),
            snapshot_date_range=list(self._manifest.get("snapshot_date_range", [])),
            n_unique_instruments=int(self._manifest.get("n_unique_instruments", 0)),
            n_membership_spans=int(self._manifest.get("n_membership_spans", 0)),
            description=str(self._manifest.get("description", "")),
        )

    @property
    def spans(self) -> pd.DataFrame:
        """The full membership-span frame (copy)."""
        return self._spans.copy()

    @property
    def snapshot_dates(self) -> list[str]:
        """Sorted membership snapshot dates (``YYYYMMDD``), all unique."""
        return sorted(self._spans["effective_from"].unique().tolist())

    @property
    def instruments(self) -> list[str]:
        """Every instrument that ever appeared in the index (PIT union)."""
        return sorted(self._spans["instrument"].unique().tolist())

    def is_member(self, instrument: str, as_of_date: str | pd.Timestamp) -> bool:
        """True if ``instrument`` was a constituent on ``as_of_date``."""
        date_int = int(_normalize_date(as_of_date))
        mask = self._spans["instrument"] == str(instrument).strip().upper()
        if not mask.any():
            return False
        start = self._spans.loc[mask, "effective_from"].astype(int)
        end = self._spans.loc[mask, "effective_to"].astype(int)
        return bool(((start <= date_int) & (date_int <= end)).any())

    def membership_as_of(self, as_of_date: str | pd.Timestamp) -> list[str]:
        """Sorted constituents of the index on ``as_of_date`` (point-in-time)."""
        date_int = int(_normalize_date(as_of_date))
        start = self._spans["effective_from"].astype(int)
        end = self._spans["effective_to"].astype(int)
        mask = (start <= date_int) & (date_int <= end)
        return sorted(self._spans.loc[mask, "instrument"].unique().tolist())

    def ever_membership_as_of(self, as_of_date: str | pd.Timestamp) -> list[str]:
        """Ever-member instruments whose first membership started by a date.

        ``instrument`` is included iff at least one span has
        ``effective_from <= as_of_date`` (i.e. the stock had already entered
        the index at or before ``as_of_date``).  Unlike :meth:`membership_as_of`
        this ignores ``effective_to`` — a stock that entered, left and stayed
        out is still in the ever-member set.  Monotonic and idempotent: the set
        only grows with ``as_of_date``.
        """
        date_int = int(_normalize_date(as_of_date))
        eff_from = self._spans["effective_from"].astype(int)
        return sorted(self._spans.loc[eff_from <= date_int, "instrument"].unique().tolist())

    def membership_window(self, start_date: str, end_date: str) -> list[str]:
        """PIT union of constituents over the inclusive window ``[start, end]``.

        Every instrument that was a member on at least one day in the window.
        """
        start_int = int(_normalize_date(start_date))
        end_int = int(_normalize_date(end_date))
        if start_int > end_int:
            raise ValueError(f"start_date {start_date} > end_date {end_date}")
        start = self._spans["effective_from"].astype(int)
        end = self._spans["effective_to"].astype(int)
        mask = (start <= end_int) & (end >= start_int)
        return sorted(self._spans.loc[mask, "instrument"].unique().tolist())

    def membership_periods(self, instrument: str) -> pd.DataFrame:
        """All membership spans for one instrument, sorted by effective_from."""
        mask = self._spans["instrument"] == str(instrument).strip().upper()
        return self._spans.loc[mask].sort_values("effective_from").reset_index(drop=True)

    def latest_membership(self) -> list[str]:
        """Constituents at the most recent snapshot date."""
        latest = self.snapshot_dates[-1] if self.snapshot_dates else None
        if latest is None:
            return []
        return self.membership_as_of(latest)

    def to_registry_frame(
        self,
        start_date: str,
        end_date: str,
        *,
        date_format: str = "%Y-%m-%d",
    ) -> pd.DataFrame:
        """Per-instrument membership spans clipped to a window.

        Used to build a qlib instrument registry over a window's PIT union:
        each row is ``(instrument, start_date, end_date)`` for the overlap of
        every membership period with ``[start_date, end_date]``.  Output dates
        use ``date_format`` (default ISO).  This is the static symbol set for
        feature materialization; point-in-time membership filtering is applied
        separately via :meth:`membership_as_of`.
        """
        start_int = int(_normalize_date(start_date))
        end_int = int(_normalize_date(end_date))
        if start_int > end_int:
            raise ValueError(f"start_date {start_date} > end_date {end_date}")
        start = self._spans["effective_from"].astype(int)
        end = self._spans["effective_to"].astype(int)
        mask = (start <= end_int) & (end >= start_int)
        rows: list[dict[str, str]] = []
        for _, span in self._spans.loc[mask].iterrows():
            rows.append(
                {
                    "instrument": span["instrument"],
                    "start_date": pd.to_datetime(
                        str(max(start_int, int(span["effective_from"]))),
                        format="%Y%m%d",
                    ).strftime(date_format),
                    "end_date": pd.to_datetime(
                        str(min(end_int, int(span["effective_to"]))),
                        format="%Y%m%d",
                    ).strftime(date_format),
                }
            )
        frame = pd.DataFrame(rows, columns=["instrument", "start_date", "end_date"])
        return (
            frame.drop_duplicates(subset=["instrument", "start_date", "end_date"])
            .sort_values(["instrument", "start_date"])
            .reset_index(drop=True)
        )
