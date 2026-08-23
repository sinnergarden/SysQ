"""Immutable operational PIT snapshots for CSI800 + CSI1000 (CSI1800).

Daily sync resolves the latest index-weight snapshot whose snapshot date is not
after the target trading date.  The resolved 1,800-row cross-section is stored
under a target-date directory and is never silently replaced.  This keeps the
operational data pull reproducible without mutating hash-bound research
artifacts such as ``csi1800_pit_v2``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


CSI1800_INDEX_COUNTS = {"000906.SH": 800, "000852.SH": 1000}
SNAPSHOT_COLUMNS = [
    "index_code",
    "instrument",
    "snapshot_date",
    "as_of_date",
    "weight",
]


def _normalise_date(value: str | pd.Timestamp) -> str:
    text = str(value).strip().replace("-", "").replace("/", "")
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid PIT snapshot date: {value!r}")
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_hash(frame: pd.DataFrame) -> str:
    payload = frame[SNAPSHOT_COLUMNS].to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_index_leg(
    raw: pd.DataFrame,
    *,
    index_code: str,
    expected_count: int,
    as_of_date: str,
) -> pd.DataFrame:
    required = {"con_code", "trade_date"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"{index_code} index_weight lacks columns: {missing}")
    if raw.empty:
        raise ValueError(f"{index_code} index_weight returned no rows")

    frame = raw.copy()
    frame["snapshot_date"] = frame["trade_date"].map(_normalise_date)
    frame = frame[frame["snapshot_date"] <= as_of_date]
    if frame.empty:
        raise ValueError(
            f"{index_code} has no snapshot on or before target {as_of_date}"
        )
    snapshot_date = str(frame["snapshot_date"].max())
    frame = frame[frame["snapshot_date"] == snapshot_date].copy()
    if frame["con_code"].isna().any():
        raise ValueError(f"{index_code} snapshot contains null instruments")
    frame["instrument"] = frame["con_code"].astype(str).str.strip().str.upper()
    if frame["instrument"].eq("").any():
        raise ValueError(f"{index_code} snapshot contains blank instruments")
    if frame["instrument"].duplicated().any():
        raise ValueError(f"{index_code} snapshot contains duplicate instruments")
    if len(frame) != expected_count:
        raise ValueError(
            f"{index_code} PIT snapshot {snapshot_date}: expected "
            f"{expected_count} constituents, got {len(frame)}"
        )
    frame["index_code"] = index_code
    frame["as_of_date"] = as_of_date
    frame["weight"] = pd.to_numeric(frame.get("weight"), errors="coerce")
    return frame[SNAPSHOT_COLUMNS].sort_values("instrument").reset_index(drop=True)


@dataclass(frozen=True)
class OperationalPitSnapshot:
    universe: str
    as_of_date: str
    instruments: tuple[str, ...]
    source_snapshot_dates: dict[str, str]
    semantic_sha256: str
    artifact_dir: Path | None
    membership_sha256: str | None
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "as_of_date": self.as_of_date,
            "constituent_count": len(self.instruments),
            "source_snapshot_dates": dict(self.source_snapshot_dates),
            "semantic_sha256": self.semantic_sha256,
            "artifact_dir": str(self.artifact_dir) if self.artifact_dir else None,
            "membership_sha256": self.membership_sha256,
            "reused": self.reused,
            "interval_semantics": "latest_published_snapshot_as_of_target",
        }


def resolve_csi1800_pit_snapshot(
    collector: Any,
    *,
    as_of_date: str,
    project_root: Path,
    apply: bool,
) -> OperationalPitSnapshot:
    """Resolve and optionally publish an immutable target-date CSI1800 snapshot."""

    target = _normalise_date(as_of_date)
    legs = []
    for index_code, expected_count in CSI1800_INDEX_COUNTS.items():
        raw = collector.get_index_weights(index_code)
        legs.append(
            _normalise_index_leg(
                raw,
                index_code=index_code,
                expected_count=expected_count,
                as_of_date=target,
            )
        )
    membership = (
        pd.concat(legs, ignore_index=True)
        .sort_values(["index_code", "instrument"])
        .reset_index(drop=True)
    )
    duplicate_members = membership["instrument"].duplicated(keep=False)
    if duplicate_members.any():
        sample = sorted(membership.loc[duplicate_members, "instrument"].unique())[:5]
        raise ValueError(f"CSI800 and CSI1000 snapshots overlap: {sample}")
    if len(membership) != 1800:
        raise ValueError(f"CSI1800 PIT snapshot expected 1800 rows, got {len(membership)}")

    semantic_sha = _semantic_hash(membership)
    source_dates = {
        str(code): str(leg["snapshot_date"].iloc[0])
        for code, leg in membership.groupby("index_code", sort=True)
    }
    instruments = tuple(sorted(membership["instrument"].tolist()))
    if not apply:
        return OperationalPitSnapshot(
            universe="csi1800",
            as_of_date=target,
            instruments=instruments,
            source_snapshot_dates=source_dates,
            semantic_sha256=semantic_sha,
            artifact_dir=None,
            membership_sha256=None,
            reused=False,
        )

    root = (
        Path(project_root)
        / "data"
        / "research"
        / "universes"
        / "csi1800_pit_daily"
    )
    artifact_dir = root / target
    membership_path = artifact_dir / "membership.parquet"
    manifest_path = artifact_dir / "manifest.json"
    if artifact_dir.exists():
        if not membership_path.is_file() or not manifest_path.is_file():
            raise ValueError(f"incomplete existing PIT snapshot artifact: {artifact_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_membership_hash = _sha256_file(membership_path)
        if actual_membership_hash != manifest.get("membership_sha256"):
            raise ValueError(f"existing PIT snapshot hash mismatch: {artifact_dir}")
        existing = pd.read_parquet(membership_path)[SNAPSHOT_COLUMNS]
        if _semantic_hash(existing) != semantic_sha:
            raise ValueError(
                f"existing PIT snapshot differs for target {target}; refusing overwrite"
            )
        return OperationalPitSnapshot(
            universe="csi1800",
            as_of_date=target,
            instruments=instruments,
            source_snapshot_dates=source_dates,
            semantic_sha256=semantic_sha,
            artifact_dir=artifact_dir,
            membership_sha256=actual_membership_hash,
            reused=True,
        )

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target}.staging-", dir=root))
    try:
        staged_membership = staging / "membership.parquet"
        membership.to_parquet(staged_membership, index=False)
        membership_hash = _sha256_file(staged_membership)
        manifest = {
            "schema_version": "operational_pit_snapshot_v1",
            "universe": "csi1800",
            "as_of_date": target,
            "source": "tushare.index_weight",
            "source_snapshot_dates": source_dates,
            "index_counts": dict(CSI1800_INDEX_COUNTS),
            "constituent_count": 1800,
            "interval_semantics": "latest_published_snapshot_as_of_target",
            "semantic_sha256": semantic_sha,
            "membership_sha256": membership_hash,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.replace(staging, artifact_dir)
        except OSError:
            if not artifact_dir.exists():
                raise
            shutil.rmtree(staging, ignore_errors=True)
            return resolve_csi1800_pit_snapshot(
                collector,
                as_of_date=target,
                project_root=project_root,
                apply=True,
            )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    return OperationalPitSnapshot(
        universe="csi1800",
        as_of_date=target,
        instruments=instruments,
        source_snapshot_dates=source_dates,
        semantic_sha256=semantic_sha,
        artifact_dir=artifact_dir,
        membership_sha256=_sha256_file(membership_path),
        reused=False,
    )


def write_current_qlib_registry(
    *,
    qlib_dir: Path,
    universe: str,
    instruments: tuple[str, ...] | list[str],
    as_of_date: str,
) -> dict[str, Any]:
    """Atomically materialize a current-universe qlib registry from ``all.txt``."""

    all_path = Path(qlib_dir) / "instruments" / "all.txt"
    if not all_path.is_file():
        raise FileNotFoundError(f"qlib all-instrument registry not found: {all_path}")
    all_frame = pd.read_csv(
        all_path,
        sep="\t",
        names=["instrument", "start_date", "end_date"],
        dtype=str,
    )
    requested = set(str(value).strip().upper() for value in instruments)
    selected = all_frame[all_frame["instrument"].isin(requested)].copy()
    found = set(selected["instrument"])
    missing = sorted(requested - found)
    if missing:
        raise ValueError(
            f"{universe} has {len(missing)} instruments absent from qlib all.txt: "
            f"{missing[:5]}"
        )
    if selected["instrument"].duplicated().any():
        raise ValueError(f"qlib all.txt has duplicate rows for {universe} members")
    selected["end_date"] = selected["end_date"].where(
        selected["end_date"] >= pd.Timestamp(as_of_date).strftime("%Y-%m-%d"),
        pd.Timestamp(as_of_date).strftime("%Y-%m-%d"),
    )
    selected = selected.sort_values("instrument")
    output = Path(qlib_dir) / "instruments" / f"{universe}.txt"
    payload = selected.to_csv(
        sep="\t", header=False, index=False, lineterminator="\n"
    ).encode("utf-8")
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(output),
        "instrument_count": len(selected),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "as_of_date": _normalise_date(as_of_date),
    }
