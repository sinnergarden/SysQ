"""Raw canonical market-data adapter for the backtest accounting layer.

This module deliberately does not use Qlib, adjusted prices, or a ``latest``
alias.  It reads the immutable per-instrument canonical feather files and
keeps execution observations separate from valuation observations:

* an execution price must be present and legal on the requested date;
* a valuation caller may use :meth:`observed_close` only for that date;
* initial valuation may explicitly request the last legal close as-of a date;
* ADV is calculated from strictly prior dates;
* ``factor_snapshot`` exposes the raw factor for corporate-action checks.

Files are loaded on demand and retained in a process-local cache.  The cache
also records a SHA-256 of the bytes actually read, making ``source_identity``
deterministic and auditable without relying on mtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import pyarrow as pa
import pyarrow.ipc as pa_ipc


_REQUIRED_DATE_COLUMN = "trade_date"
_ACCOUNTING_COLUMNS = (
    "trade_date", "open", "close", "paused", "high_limit", "low_limit",
    "amount", "factor",
)
MARKET_SLICE_SCHEMA_VERSION = "canonical_market_slice_v1"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalise_day(value: Any) -> str:
    """Return an ISO day for common canonical/API date representations."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    # Canonical files commonly store dates as YYYYMMDD strings or integers.
    if text.isdigit() and len(text) == 8:
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    parsed = pd.to_datetime(text, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _paused_state(value: Any) -> bool | None:
    """Return a known paused state, or ``None`` for an unknown source value."""

    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"1", "true", "yes", "paused"}:
            return True
        if normalised in {"0", "false", "no", "trading"}:
            return False
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number != 0


def _is_paused(value: Any) -> bool:
    """Fail closed when the paused source field is unknown."""

    state = _paused_state(value)
    return True if state is None else state


@dataclass(frozen=True)
class _HashedFile:
    instrument: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class _LoadedFile(_HashedFile):
    frame: pd.DataFrame


class MarketDataAdapter:
    """Lazy reader for ``data/canonical/daily/{instrument}.feather``.

    Parameters
    ----------
    root:
        Directory containing per-instrument feather files.  It defaults to
        the repository-relative canonical daily directory.  ``data_root`` is
        accepted as an explicit alias for callers whose configuration uses
        that name.

    Notes
    -----
    A missing file is not an exception during a snapshot: all requested
    instruments still receive a fail-closed status row.  Malformed files do
    raise, because silently treating corrupted source data as suspension
    would hide an accounting input failure.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        if root is not None and data_root is not None and Path(root) != Path(data_root):
            raise ValueError("root and data_root must refer to the same directory")
        configured_root = root if root is not None else data_root
        self.root = Path(configured_root or "data/canonical/daily")
        # Provenance and parsed data intentionally have separate lifetimes.
        # Explicit identity binding hashes files without retaining DataFrames.
        self._digest_cache: dict[str, _HashedFile] = {}
        self._cache: dict[str, _LoadedFile] = {}
        self._requested_missing: set[str] = set()

    @staticmethod
    def _instruments(instruments: Iterable[str]) -> list[str]:
        # Preserve caller order in status frames while avoiding duplicate index
        # rows, which otherwise make status lookup ambiguous for the matcher.
        return list(dict.fromkeys(str(value) for value in instruments))

    def _path_for(self, instrument: str) -> Path:
        # Instrument identifiers are filenames, never arbitrary paths.  This
        # also prevents an accidental traversal outside the configured root.
        if not instrument or Path(instrument).name != instrument or instrument in {".", ".."}:
            raise ValueError(f"invalid instrument identifier: {instrument!r}")
        path = self.root / f"{instrument}.feather"
        if path.is_symlink():
            raise ValueError(f"symlinked canonical file is not allowed: {path}")
        return path

    def _hash_source(self, instrument: str) -> _HashedFile | None:
        if instrument in self._digest_cache:
            return self._digest_cache[instrument]
        path = self._path_for(instrument)
        if not path.is_file():
            self._requested_missing.add(instrument)
            return None
        # Hash exact file bytes without retaining them.  Do not use mtime or a
        # path alias as provenance.
        digest = _file_sha256(path)
        hashed = _HashedFile(instrument, path, digest)
        self._digest_cache[instrument] = hashed
        self._requested_missing.discard(instrument)
        return hashed

    @staticmethod
    def _accounting_columns(path: Path) -> list[str]:
        """Inspect Arrow schema and select only accounting columns."""

        with pa.memory_map(str(path), "r") as source:
            names = set(pa_ipc.open_file(source).schema.names)
        if _REQUIRED_DATE_COLUMN not in names:
            raise ValueError(f"{path} lacks required column {_REQUIRED_DATE_COLUMN!r}")
        return [column for column in _ACCOUNTING_COLUMNS if column in names]

    def _load(self, instrument: str) -> _LoadedFile | None:
        if instrument in self._cache:
            return self._cache[instrument]
        hashed = self._hash_source(instrument)
        if hashed is None:
            return None
        columns = self._accounting_columns(hashed.path)
        frame = pd.read_feather(hashed.path, columns=columns)
        if _REQUIRED_DATE_COLUMN not in frame.columns:
            raise ValueError(f"{hashed.path} lacks required column {_REQUIRED_DATE_COLUMN!r}")
        frame = frame.copy()
        frame["__qsys_day"] = frame[_REQUIRED_DATE_COLUMN].map(_normalise_day)
        frame = frame[frame["__qsys_day"] != ""]
        # Canonical input is expected to be unique by instrument/date.  Do not
        # silently choose one row: ambiguity must fail closed at the accounting
        # boundary rather than make a non-reproducible source appear valid.
        if frame["__qsys_day"].duplicated().any():
            duplicates = sorted(frame.loc[frame["__qsys_day"].duplicated(keep=False), "__qsys_day"].unique())
            raise ValueError(
                f"{hashed.path} contains duplicate canonical rows for date(s): {duplicates}"
            )
        frame = frame.sort_values("__qsys_day", kind="mergesort").reset_index(drop=True)
        loaded = _LoadedFile(instrument, hashed.path, hashed.sha256, frame)
        self._cache[instrument] = loaded
        return loaded

    @staticmethod
    def _row_for(loaded: _LoadedFile | None, day: str) -> Mapping[str, Any] | None:
        if loaded is None:
            return None
        rows = loaded.frame[loaded.frame["__qsys_day"] == day]
        if rows.empty:
            return None
        return rows.iloc[-1]

    def snapshot(
        self,
        trade_date: str,
        instruments: Iterable[str],
        price_col: str = "close",
    ) -> tuple[dict[str, float], pd.DataFrame]:
        """Return legal execution prices and a fail-closed status frame.

        Missing rows, paused rows, and missing/non-positive execution prices
        never enter the returned price dictionary.  Every requested instrument
        has a status row, so a matcher cannot accidentally interpret omission as
        permission to trade.
        """

        day = _normalise_day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid date")
        names = self._instruments(instruments)
        prices: dict[str, float] = {}
        rows: list[dict[str, Any]] = []
        for instrument in names:
            loaded = self._load(instrument)
            row = self._row_for(loaded, day)
            has_row = row is not None
            paused_state = _paused_state(row.get("paused")) if row is not None and "paused" in row else None
            execution = _finite_positive(row.get(price_col)) if row is not None and price_col in row else None
            upper = _finite_positive(row.get("high_limit")) if row is not None and "high_limit" in row else None
            lower = _finite_positive(row.get("low_limit")) if row is not None and "low_limit" in row else None
            constraint_status_known = (
                has_row and paused_state is not None and upper is not None and lower is not None
            )
            # A missing constraint can mean either bad source coverage or a
            # legitimate no-limit session.  Until canonical data provides an
            # explicit source indicator to distinguish them, reject safely.
            suspended = (
                (not has_row)
                or execution is None
                or not constraint_status_known
                or bool(paused_state)
            )
            is_limit_up = False
            is_limit_down = False
            if not suspended and row is not None:
                assert upper is not None and lower is not None
                is_limit_up = upper is not None and execution >= upper
                is_limit_down = lower is not None and execution <= lower
                prices[instrument] = execution
            rows.append({
                "instrument": instrument,
                "has_row": has_row,
                "constraint_status_known": constraint_status_known,
                "is_suspended": suspended,
                "is_limit_up": bool(is_limit_up),
                "is_limit_down": bool(is_limit_down),
            })
        status = pd.DataFrame(rows).set_index("instrument")
        for column in (
            "has_row", "constraint_status_known", "is_suspended",
            "is_limit_up", "is_limit_down",
        ):
            status[column] = status[column].astype(bool)
        return prices, status

    def observed_close(self, trade_date: str, instruments: Iterable[str]) -> dict[str, float]:
        """Return only finite, positive closes observed on *trade_date*.

        This is intentionally not a stale-price valuation function.  A caller
        needing suspension valuation must carry forward a previous observation
        in its accounting state and mark it stale.
        """

        day = _normalise_day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid date")
        result: dict[str, float] = {}
        for instrument in self._instruments(instruments):
            row = self._row_for(self._load(instrument), day)
            if row is not None and _is_paused(row.get("paused")):
                continue
            close = _finite_positive(row.get("close")) if row is not None and "close" in row else None
            if close is not None:
                result[instrument] = close
        return result

    def latest_legal_close_asof(
        self,
        trade_date: str,
        instruments: Iterable[str],
        *,
        strict_before: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Return the most recent legal close as-of *trade_date*.

        This method is strictly for seeding valuation state.  It is not called
        by :meth:`snapshot`, and its stale close must never be used as an
        execution price.  Paused rows and rows with a missing/non-positive
        close are not legal observations; the search continues backward.

        ``strict_before=True`` enforces ``price_date < trade_date`` for
        pre-open seeding.  The default permits a same-day close for explicitly
        post-close valuation callers.

        Returns
        -------
        dict
            ``instrument -> {"price": close, "price_date": ISO date}``.
            Instruments with no legal observation on or before the requested
            date are omitted.
        """

        day = _normalise_day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid date")
        result: dict[str, dict[str, Any]] = {}
        for instrument in self._instruments(instruments):
            loaded = self._load(instrument)
            if loaded is None or "close" not in loaded.frame.columns:
                continue
            # Apply the PIT boundary before inspecting observation validity so
            # no future (or, in pre-open mode, same-day) close can seed current
            # valuation.
            if strict_before:
                eligible = loaded.frame[loaded.frame["__qsys_day"] < day]
            else:
                eligible = loaded.frame[loaded.frame["__qsys_day"] <= day]
            for _, row in eligible.iloc[::-1].iterrows():
                if _is_paused(row.get("paused")):
                    continue
                close = _finite_positive(row.get("close"))
                if close is None:
                    continue
                result[instrument] = {
                    "price": close,
                    "price_date": str(row["__qsys_day"]),
                }
                break
        return result

    def latest_legal_close_before(
        self,
        trade_date: str,
        instruments: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        """Return the last legal close strictly before *trade_date*.

        This named API is the preferred contract for pre-open valuation
        seeding.  It delegates to the audited as-of implementation with the
        strict PIT boundary enabled.
        """

        return self.latest_legal_close_asof(
            trade_date, instruments, strict_before=True
        )

    def adv_snapshot(
        self,
        trade_date: str,
        instruments: Iterable[str],
        window: int = 20,
        min_periods: int = 5,
    ) -> tuple[dict[str, float], dict[str, int]]:
        """Return prior-day ADV (CNY) and observation counts.

        The date filter is strictly ``row_day < trade_date`` before selecting
        the trailing window, so a giant amount on the order date can never
        inflate its own liquidity gate.  Within that date-based window, only
        rows whose ``paused`` value is explicitly false and whose amount is
        finite and positive are legal observations.  Insufficient
        observations return NaN ADV and their actual count rather than a
        fabricated zero ADV.
        """

        day = _normalise_day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid date")
        if not isinstance(window, int) or window <= 0:
            raise ValueError("window must be a positive integer")
        if not isinstance(min_periods, int) or min_periods < 1 or min_periods > window:
            raise ValueError("min_periods must be between 1 and window")
        means: dict[str, float] = {}
        observations: dict[str, int] = {}
        for instrument in self._instruments(instruments):
            loaded = self._load(instrument)
            if loaded is None or "amount" not in loaded.frame.columns:
                values: list[float] = []
            else:
                prior = loaded.frame[loaded.frame["__qsys_day"] < day].tail(window)
                values = []
                for _, row in prior.iterrows():
                    # A stale positive amount on a suspended row is not a
                    # tradable-liquidity observation.  Unknown suspension
                    # state is rejected fail-closed rather than inferred as
                    # active trading.
                    if _paused_state(row.get("paused")) is not False:
                        continue
                    amount = _finite_positive(row.get("amount"))
                    if amount is not None:
                        values.append(amount)
            observations[instrument] = len(values)
            means[instrument] = float(sum(values) / len(values)) if len(values) >= min_periods else float("nan")
        return means, observations

    def factor_snapshot(self, trade_date: str, instruments: Iterable[str]) -> dict[str, float]:
        """Return valid raw adjustment factors observed on the exact date."""

        day = _normalise_day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid date")
        result: dict[str, float] = {}
        for instrument in self._instruments(instruments):
            row = self._row_for(self._load(instrument), day)
            factor = _finite_positive(row.get("factor")) if row is not None and "factor" in row else None
            if factor is not None:
                result[instrument] = factor
        return result

    def freeze_sources(
        self,
        instruments: Iterable[str],
        output_root: str | Path,
        *,
        through_date: str,
    ) -> dict[str, Any]:
        """Atomically freeze selected canonical files through one as-of date."""

        names = sorted(self._instruments(instruments))
        if not names:
            raise ValueError("market-data slice requires at least one instrument")
        through = _normalise_day(through_date)
        if not through:
            raise ValueError("market-data slice through_date must be a valid date")
        source_root = self.root.resolve()
        target = Path(output_root).resolve()
        if self.root.is_symlink() or not source_root.is_dir():
            raise ValueError("canonical market-data root must be a regular directory")
        if target.is_symlink() or target.exists():
            raise FileExistsError(f"market-data slice target already exists: {target}")
        if source_root == target or source_root in target.parents:
            raise ValueError("market-data slice target cannot be inside its source root")
        missing = [name for name in names if not self._path_for(name).is_file()]
        if missing:
            raise ValueError(f"market-data slice source files are missing: {missing}")

        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
        )
        try:
            files: list[dict[str, Any]] = []
            for instrument in names:
                source_path = self._path_for(instrument)
                source_sha256 = _file_sha256(source_path)
                frame = pd.read_feather(source_path)
                source_sha256_after_read = _file_sha256(source_path)
                if source_sha256_after_read != source_sha256:
                    raise ValueError(
                        f"canonical market source changed while freezing: {instrument}"
                    )
                if _REQUIRED_DATE_COLUMN not in frame.columns:
                    raise ValueError(
                        f"{source_path} lacks required column {_REQUIRED_DATE_COLUMN!r}"
                    )
                dates = frame[_REQUIRED_DATE_COLUMN].map(_normalise_day)
                if dates.eq("").any():
                    raise ValueError(
                        f"canonical market source contains invalid dates: {instrument}"
                    )
                if dates.duplicated().any():
                    raise ValueError(
                        f"canonical market source contains duplicate dates: {instrument}"
                    )
                frozen = frame.loc[dates.le(through)].copy()
                frozen_path = staging / f"{instrument}.feather"
                frozen.to_feather(frozen_path)
                frozen_dates = dates.loc[frozen.index]
                files.append({
                    "instrument": instrument,
                    "file": frozen_path.name,
                    "source_sha256": source_sha256,
                    "frozen_sha256": _file_sha256(frozen_path),
                    "source_row_count": int(len(frame)),
                    "frozen_row_count": int(len(frozen)),
                    "frozen_date_min": frozen_dates.min() if len(frozen_dates) else None,
                    "frozen_date_max": frozen_dates.max() if len(frozen_dates) else None,
                })
            identity = {
                "schema_version": MARKET_SLICE_SCHEMA_VERSION,
                "source_root": str(source_root),
                "through_date": through,
                "producer_code_sha256": _file_sha256(Path(__file__)),
                "files": files,
            }
            manifest = {
                **identity,
                "market_slice_identity_sha256": _canonical_hash(identity),
            }
            manifest_path = staging / "market_slice_manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        final_manifest = target / "market_slice_manifest.json"
        return {
            "root": str(target),
            "manifest": str(final_manifest),
            "manifest_sha256": _file_sha256(final_manifest),
            "market_slice_identity_sha256": manifest[
                "market_slice_identity_sha256"
            ],
        }

    def _slice_artifact_identity(
        self, files: list[dict[str, str]]
    ) -> dict[str, Any] | None:
        manifest_path = self.root / "market_slice_manifest.json"
        if not manifest_path.exists():
            return None
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("market-data slice manifest is not a regular file")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != MARKET_SLICE_SCHEMA_VERSION:
            raise ValueError("unsupported market-data slice schema")
        identity = {
            key: value for key, value in manifest.items()
            if key != "market_slice_identity_sha256"
        }
        if _canonical_hash(identity) != manifest.get(
            "market_slice_identity_sha256"
        ):
            raise ValueError("market-data slice identity hash mismatch")
        declared = {
            str(item["file"]): item for item in manifest.get("files", [])
        }
        if len(declared) != len(manifest.get("files", [])):
            raise ValueError("market-data slice manifest contains duplicate files")
        for item in files:
            source = declared.get(item["file"])
            if source is None or source.get("frozen_sha256") != item["sha256"]:
                raise ValueError(
                    f"market-data slice file lineage mismatch: {item['file']}"
                )
        return {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": _file_sha256(manifest_path),
            "market_slice_identity_sha256": manifest[
                "market_slice_identity_sha256"
            ],
            "through_date": manifest["through_date"],
        }

    def source_identity(self, instruments: Iterable[str] | None = None) -> dict[str, Any]:
        """Return deterministic identity of files actually read.

        Passing *instruments* explicitly loads those files (if present), which
        is useful at an artifact boundary.  With no argument this reports the
        files already touched by lazy reads.  Missing files are not listed as
        used source files because no bytes were read from them.
        """

        if instruments is not None:
            requested = self._instruments(instruments)
            for instrument in requested:
                self._hash_source(instrument)
        requested_missing = sorted(self._requested_missing)
        files = sorted(
            (
                {
                    "instrument": loaded.instrument,
                    "file": loaded.path.name,
                    "sha256": loaded.sha256,
                }
                for loaded in self._digest_cache.values()
            ),
            key=lambda item: (item["instrument"], item["file"]),
        )
        payload = {
            "source": "canonical_daily_feather",
            "used_instruments": [item["instrument"] for item in files],
            "used_files": [item["file"] for item in files],
            "requested_missing_instruments": requested_missing,
            "files": files,
        }
        slice_artifact = self._slice_artifact_identity(files)
        if slice_artifact is not None:
            payload["market_slice"] = slice_artifact
        aggregate = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # Keep both concise and explicit names: callers may persist the whole
        # identity, while artifact schemas often prefer ``aggregate_sha256``.
        return {
            **payload,
            "sha256": aggregate,
            "aggregate_sha256": aggregate,
            "used_file_sha256": {
                item["file"]: item["sha256"] for item in files
            },
        }


# Explicit descriptive alias for callers that want to distinguish this from
# live/ops market snapshots.  Both names share the exact same implementation.
BacktestMarketDataAdapter = MarketDataAdapter


__all__ = ["BacktestMarketDataAdapter", "MarketDataAdapter"]
