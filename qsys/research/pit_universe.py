"""Point-in-time index membership store and corrected v2 artifact builder.

The default ``csi800_pit_v2`` artifact is a set of membership spans reconstructed from
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
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ARTIFACT_DIR = Path("data") / "research" / "universes" / "csi800_pit_v2"
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


def build_membership_spans(
    raw_snapshots: pd.DataFrame,
    *,
    source: str,
    source_date: str,
    source_version: str,
) -> pd.DataFrame:
    """Normalize index snapshots into inclusive PIT membership spans.

    Snapshot dates form an independent observation axis for each index.  A
    constituent observed in adjacent snapshots remains active between them.
    When it is first absent, the span closes on the calendar day immediately
    before that next snapshot; a later observation starts a new span.
    """
    required = {"index_code", "con_code", "trade_date"}
    missing = sorted(required - set(raw_snapshots.columns))
    if missing:
        raise ValueError(f"raw snapshots missing columns: {missing}")
    if raw_snapshots.empty:
        raise ValueError("raw snapshots are empty")

    def normalize_identifier(value: Any, column: str) -> str:
        if pd.isna(value):
            raise ValueError(f"raw snapshots contain null {column}")
        text = str(value).strip().upper()
        if not text:
            raise ValueError(f"raw snapshots contain blank {column}")
        return text

    def normalize_snapshot_date(value: Any) -> str:
        if pd.isna(value):
            raise ValueError("raw snapshots contain null trade_date")
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y%m%d")
        text = str(value).strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = pd.to_datetime(text, format=fmt, errors="raise")
            except (TypeError, ValueError):
                continue
            return pd.Timestamp(parsed).strftime("%Y%m%d")
        raise ValueError(f"cannot normalize snapshot date: {value!r}")

    normalized = pd.DataFrame(
        {
            "index_code": [
                normalize_identifier(value, "index_code")
                for value in raw_snapshots["index_code"]
            ],
            "instrument": [
                normalize_identifier(value, "con_code")
                for value in raw_snapshots["con_code"]
            ],
            "trade_date": [
                normalize_snapshot_date(value)
                for value in raw_snapshots["trade_date"]
            ],
        }
    )
    duplicate_key = ["index_code", "instrument", "trade_date"]
    duplicates = normalized.duplicated(duplicate_key, keep=False)
    if duplicates.any():
        sample = normalized.loc[duplicates, duplicate_key].iloc[0].to_dict()
        raise ValueError(
            "duplicate index constituent snapshot after normalization: "
            f"{sample}"
        )

    rows: list[dict[str, str]] = []
    for index_code, leg in normalized.groupby("index_code", sort=True):
        members_by_date = {
            date: set(group["instrument"])
            for date, group in leg.groupby("trade_date", sort=True)
        }
        dates = sorted(members_by_date)
        active_start: dict[str, str] = {}

        for date in dates:
            current = members_by_date[date]
            close_date = (
                pd.to_datetime(date, format="%Y%m%d") - pd.Timedelta(days=1)
            ).strftime("%Y%m%d")
            for instrument in sorted(set(active_start) - current):
                rows.append(
                    {
                        "index_code": index_code,
                        "instrument": instrument,
                        "effective_from": active_start.pop(instrument),
                        "effective_to": close_date,
                        "source": source,
                        "source_date": source_date,
                        "source_version": source_version,
                    }
                )
            for instrument in sorted(current - set(active_start)):
                active_start[instrument] = date

        final_date = dates[-1]
        for instrument in sorted(active_start):
            rows.append(
                {
                    "index_code": index_code,
                    "instrument": instrument,
                    "effective_from": active_start[instrument],
                    "effective_to": final_date,
                    "source": source,
                    "source_date": source_date,
                    "source_version": source_version,
                }
            )

    return (
        pd.DataFrame(rows, columns=SPAN_COLUMNS)
        .sort_values(["instrument", "effective_from", "index_code"])
        .reset_index(drop=True)
    )


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
            if not expected:
                raise ValueError(
                    "PIT universe manifest lacks membership_sha256; "
                    "cannot verify artifact integrity"
                )
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.stem}.{uuid.uuid4().hex}.tmp{path.suffix}"
    )
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git_provenance(project_root: Path) -> dict[str, Any]:
    scoped_paths = [
        "qsys/research/pit_universe.py",
        "qsys/research/generators/lightgbm_single_label.py",
        "qsys/research/matrix_job.py",
        "qsys/label/compute.py",
        "qsys/label/store.py",
        "scripts/data_sync.py",
        "scripts/research/compute_labels.py",
        "scripts/research/backtest_from_signal.py",
        "scripts/run_research.py",
        "configs/labels",
        "configs/research",
    ]

    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    full = run("rev-parse", "HEAD")
    short = run("rev-parse", "--short", "HEAD")
    overall_status = run("status", "--porcelain")
    scoped_status = run("status", "--porcelain", "--", *scoped_paths)
    return {
        "git_commit_full": full,
        "git_commit_short": short,
        "git_worktree_dirty": bool(overall_status),
        "git_scoped_dirty": bool(scoped_status),
        "git_scoped_paths": scoped_paths,
    }


def _normalized_snapshot_keys(raw: pd.DataFrame) -> pd.DataFrame:
    normalized = raw[["index_code", "con_code", "trade_date"]].copy()
    normalized["index_code"] = (
        normalized["index_code"].astype(str).str.strip().str.upper()
    )
    normalized["instrument"] = (
        normalized.pop("con_code").astype(str).str.strip().str.upper()
    )
    normalized["trade_date"] = normalized["trade_date"].map(_normalize_date)
    return normalized


def _assert_snapshot_equality(raw: pd.DataFrame, spans: pd.DataFrame) -> None:
    normalized = _normalized_snapshot_keys(raw)
    for (index_code, trade_date), snapshot in normalized.groupby(
        ["index_code", "trade_date"], sort=True
    ):
        leg = spans[spans["index_code"] == index_code]
        actual = set(
            leg.loc[
                (leg["effective_from"] <= trade_date)
                & (leg["effective_to"] >= trade_date),
                "instrument",
            ]
        )
        expected = set(snapshot["instrument"])
        if actual != expected:
            raise ValueError(
                f"snapshot mismatch for {index_code} on {trade_date}: "
                f"missing={sorted(expected - actual)[:5]}, "
                f"extra={sorted(actual - expected)[:5]}"
            )


def _assert_daily_membership_count(
    spans: pd.DataFrame,
    calendar_path: Path,
    *,
    start_date: str,
    end_date: str,
    expected_size: int,
) -> int:
    if not calendar_path.is_file():
        raise FileNotFoundError(f"Qlib calendar not found: {calendar_path}")
    start = _normalize_date(start_date)
    end = _normalize_date(end_date)
    dates = [
        _normalize_date(line)
        for line in calendar_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and start <= _normalize_date(line) <= end
    ]
    if not dates:
        raise ValueError(f"Qlib calendar has no dates in {start_date}..{end_date}")
    starts = spans["effective_from"].astype(int).to_numpy()
    ends = spans["effective_to"].astype(int).to_numpy()
    instruments = spans["instrument"].astype(str).to_numpy()
    for date in dates:
        value = int(date)
        count = len(set(instruments[(starts <= value) & (ends >= value)]))
        if count != expected_size:
            raise ValueError(
                f"membership count on {date}: expected {expected_size}, got {count}"
            )
    return len(dates)


def _build_staged_pit_artifact(
    project_root: Path,
    *,
    source_id: str,
    target_id: str,
    registry_name: str,
    expected_size: int,
    registry_start: str,
    registry_end: str,
    git_metadata: dict[str, Any],
) -> dict[str, Any]:
    universes_root = project_root / "data" / "research" / "universes"
    source_dir = universes_root / source_id
    raw_source = source_dir / "raw" / "index_weight_snapshots.parquet"
    old_manifest_path = source_dir / "manifest.json"
    if not raw_source.is_file() or not old_manifest_path.is_file():
        raise FileNotFoundError(f"incomplete source artifact: {source_dir}")
    try:
        old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid source manifest: {old_manifest_path}") from exc
    if not old_manifest.get("source_date"):
        raise ValueError(f"source manifest lacks source_date: {old_manifest_path}")

    source = str(old_manifest.get("source") or "tushare_index_weight")
    source_date = str(old_manifest["source_date"])
    source_version = str(
        old_manifest.get("source_version") or "index_weight_monthly"
    )
    raw_source_hash = _sha256_file(raw_source)
    expected_raw_source_hash = str(old_manifest.get("raw_source_hash") or "")
    if not expected_raw_source_hash:
        raise ValueError(f"source manifest lacks raw_source_hash: {old_manifest_path}")
    if raw_source_hash != expected_raw_source_hash:
        raise ValueError(
            f"source raw snapshot hash mismatch for {source_id}: "
            f"expected {expected_raw_source_hash}, got {raw_source_hash}"
        )
    raw = pd.read_parquet(raw_source)
    spans = build_membership_spans(
        raw,
        source=source,
        source_date=source_date,
        source_version=source_version,
    )
    _assert_snapshot_equality(raw, spans)
    n_validated_dates = _assert_daily_membership_count(
        spans,
        project_root / "data" / "qlib_bin" / "calendars" / "day.txt",
        start_date=registry_start,
        end_date=registry_end,
        expected_size=expected_size,
    )

    staging = Path(
        tempfile.mkdtemp(prefix=f".{target_id}.staging-", dir=universes_root)
    )
    raw_target = staging / "raw" / "index_weight_snapshots.parquet"
    membership_target = staging / "membership.parquet"
    manifest_target = staging / "manifest.json"
    staged_registry = staging / f"{registry_name}.txt"
    try:
        _atomic_write_bytes(raw_target, raw_source.read_bytes())
        snapshot_counts = (
            _normalized_snapshot_keys(raw)
            .groupby(["index_code", "trade_date"])["instrument"]
            .nunique()
            .rename("n_constituents")
            .reset_index()
            .rename(columns={"trade_date": "snapshot_date"})
        )
        _atomic_write_bytes(
            staging / "raw" / "snapshot_dates.csv",
            snapshot_counts.to_csv(index=False).encode("utf-8"),
        )
        _atomic_write_parquet(membership_target, spans)
        membership_hash = _sha256_file(membership_target)
        raw_hash = _sha256_file(raw_target)

        preliminary_manifest = {
            "universe_id": target_id,
            "membership_sha256": membership_hash,
            "raw_source_hash": raw_hash,
            "source": source,
            "source_date": source_date,
            "n_snapshots": int(snapshot_counts["snapshot_date"].nunique()),
            "snapshot_date_range": [
                str(snapshot_counts["snapshot_date"].min()),
                str(snapshot_counts["snapshot_date"].max()),
            ],
            "n_unique_instruments": int(spans["instrument"].nunique()),
            "n_membership_spans": int(len(spans)),
            "description": "PIT membership with snapshot-as-of carry-forward intervals",
        }
        _atomic_write_bytes(
            manifest_target,
            (json.dumps(preliminary_manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        store = PitUniverseStore(staging)
        registry = store.to_registry_frame(registry_start, registry_end)
        registry_bytes = registry.to_csv(
            sep="\t", header=False, index=False, lineterminator="\n"
        ).encode("utf-8")
        _atomic_write_bytes(staged_registry, registry_bytes)
        registry_hash = _sha256_file(staged_registry)

        per_index = {}
        for index_code, counts in snapshot_counts.groupby("index_code"):
            per_index[str(index_code)] = {
                "min": int(counts["n_constituents"].min()),
                "max": int(counts["n_constituents"].max()),
                "n_snapshots": int(counts["snapshot_date"].nunique()),
            }
        manifest = {
            **preliminary_manifest,
            "schema_version": "pit_universe_manifest_v2",
            "normalization_version": "v2",
            "interval_semantics": "snapshot_asof_carry_forward",
            "source_endpoint": str(
                old_manifest.get("source_endpoint") or "pro.index_weight"
            ),
            "source_version": source_version,
            "source_artifact_id": source_id,
            "source_manifest_sha256": _sha256_file(old_manifest_path),
            "registry_sha256": registry_hash,
            "builder_code_sha256": _sha256_file(Path(__file__)),
            **git_metadata,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "per_index_snapshot_counts": per_index,
            "registry_window": {"start": registry_start, "end": registry_end},
            "n_registry_rows": int(len(registry)),
            "n_registry_instruments": int(registry["instrument"].nunique()),
            "expected_daily_membership": expected_size,
            "n_validated_trading_dates": n_validated_dates,
            "snapshot_validation": "exact_per_index_membership_match",
        }
        _atomic_write_bytes(
            manifest_target,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        PitUniverseStore(staging)
        if _sha256_file(raw_target) != raw_hash:
            raise ValueError(f"staged raw hash changed for {target_id}")
        if _sha256_file(staged_registry) != registry_hash:
            raise ValueError(f"staged registry hash changed for {target_id}")
        return {
            "target_id": target_id,
            "staging": staging,
            "registry_name": registry_name,
            "registry_bytes": registry_bytes,
            "manifest": manifest,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def rebuild_pit_universes_v2(
    project_root: Path | str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build and transactionally publish corrected CSI800/CSI1800 PIT v2."""
    root = Path(project_root).resolve()
    universes_root = root / "data" / "research" / "universes"
    registry_root = root / "data" / "qlib_bin" / "instruments"
    specs = [
        ("csi800_pit_v1", "csi800_pit_v2", "csi800_pit_union", 800),
        ("csi1800_pit_v1", "csi1800_pit_v2", "csi1800_pit_union", 1800),
    ]
    targets = [universes_root / target_id for _, target_id, _, _ in specs]
    existing = [str(path) for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"PIT v2 targets already exist: {existing}")

    git_metadata = _git_provenance(root)
    if git_metadata["git_scoped_dirty"]:
        raise ValueError(
            "PIT build code/config scope is dirty; commit the scoped changes first"
        )
    staged: list[dict[str, Any]] = []
    rollbacks: dict[Path, Path] = {}
    old_registries: dict[Path, bytes | None] = {}
    published: list[Path] = []
    try:
        for source_id, target_id, registry_name, expected_size in specs:
            staged.append(
                _build_staged_pit_artifact(
                    root,
                    source_id=source_id,
                    target_id=target_id,
                    registry_name=registry_name,
                    expected_size=expected_size,
                    registry_start="2018-01-01",
                    registry_end="2026-07-31",
                    git_metadata=git_metadata,
                )
            )
        for item in staged:
            target = universes_root / item["target_id"]
            if target.exists():
                rollback = universes_root / f".{target.name}.rollback-{uuid.uuid4().hex}"
                os.replace(target, rollback)
                rollbacks[target] = rollback
            os.replace(item["staging"], target)
            published.append(target)
        for item in staged:
            registry_target = registry_root / f"{item['registry_name']}.txt"
            old_registries[registry_target] = (
                registry_target.read_bytes() if registry_target.exists() else None
            )
            _atomic_write_bytes(registry_target, item["registry_bytes"])
        for item in staged:
            target = universes_root / item["target_id"]
            store = PitUniverseStore(target)
            registry_target = registry_root / f"{item['registry_name']}.txt"
            if _sha256_file(registry_target) != item["manifest"]["registry_sha256"]:
                raise ValueError(f"published registry hash mismatch: {registry_target}")
            if store.provenance.membership_sha256 != item["manifest"]["membership_sha256"]:
                raise ValueError(f"published membership mismatch: {target}")
        for rollback in rollbacks.values():
            shutil.rmtree(rollback)
        return {
            item["target_id"]: {
                "artifact_dir": str(universes_root / item["target_id"]),
                "registry_path": str(
                    registry_root / f"{item['registry_name']}.txt"
                ),
                "membership_sha256": item["manifest"]["membership_sha256"],
                "registry_sha256": item["manifest"]["registry_sha256"],
                "raw_source_hash": item["manifest"]["raw_source_hash"],
                "git_commit_full": item["manifest"]["git_commit_full"],
                "n_validated_trading_dates": item["manifest"][
                    "n_validated_trading_dates"
                ],
            }
            for item in staged
        }
    except Exception:
        for registry_path, old_bytes in old_registries.items():
            if old_bytes is None:
                registry_path.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(registry_path, old_bytes)
        for target in published:
            if target.exists():
                shutil.rmtree(target)
            rollback = rollbacks.get(target)
            if rollback and rollback.exists():
                os.replace(rollback, target)
        raise
    finally:
        for item in staged:
            staging = item["staging"]
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        for target, rollback in rollbacks.items():
            if rollback.exists() and target.exists():
                shutil.rmtree(rollback, ignore_errors=True)
