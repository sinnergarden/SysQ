#!/usr/bin/env python3
"""Build a signal-independent corporate-action artifact from a raw bundle.

The input bundle is the source of truth.  This entrypoint filters only by the
requested ex-date interval, validates every selected raw row with the existing
Tushare dividend normalizer, and retains the complete selected raw coverage in
a deterministic source bundle.  It deliberately has no candidate or signal
inputs: corporate-action availability must not depend on a strategy run.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.backtest.accounting import (  # noqa: E402
    _canonical_source_hash,
    normalize_tushare_dividend,
    write_corporate_action_artifact,
)


RAW_MEMBER = "raw_all_market.parquet"
SOURCE_SCHEMA_VERSION = "corporate_action_source_bundle_v1"


class BuildError(RuntimeError):
    """A fail-closed source-bundle or corporate-action build error."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_bytes(payload: Any) -> bytes:
    return (_canonical_json(payload) + b"\n")


def _day(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if not pd.isna(parsed):
            return pd.Timestamp(parsed).strftime("%Y-%m-%d")
    return ""


def _requested_day(value: str) -> str:
    normalized = _day(value)
    if not normalized:
        raise ValueError(f"invalid date: {value!r}")
    return normalized


def _read_input_bundle(
    path: Path,
) -> tuple[str, bytes, pd.DataFrame, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"source bundle must be an existing non-symlink file: {path}")
    bundle_bytes = path.read_bytes()
    input_sha = _sha256_bytes(bundle_bytes)
    try:
        with ZipFile(io.BytesIO(bundle_bytes)) as bundle:
            members = [name for name in bundle.namelist() if name == RAW_MEMBER]
            if len(members) != 1:
                raise BuildError(
                    f"source bundle must contain exactly one {RAW_MEMBER}: {path}"
                )
            raw_bytes = bundle.read(RAW_MEMBER)
            query_coverage: dict[str, Any] = {}
            if "query_coverage.json" in bundle.namelist():
                try:
                    loaded = json.loads(bundle.read("query_coverage.json"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BuildError(
                        f"invalid query_coverage.json in source bundle: {path}"
                    ) from exc
                if not isinstance(loaded, dict):
                    raise BuildError("query_coverage.json must contain an object")
                # Runtime timestamps do not describe source coverage and would
                # make an otherwise identical derived bundle non-deterministic.
                query_coverage = {
                    key: value for key, value in loaded.items()
                    if key != "created_at"
                }
    except BadZipFile as exc:
        raise BuildError(f"invalid source bundle ZIP: {path}") from exc
    try:
        raw = pd.read_parquet(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise BuildError(f"cannot read {RAW_MEMBER} from source bundle: {path}") from exc
    if "ex_date" not in raw.columns:
        raise BuildError(f"{RAW_MEMBER} lacks required ex_date column")
    return input_sha, raw_bytes, raw, query_coverage


def _deterministic_zip(entries: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name in sorted(entries):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.external_attr = 0
            archive.writestr(info, entries[name])
    return output.getvalue()


def _pit_context(
    pit_universe_artifact: str | Path | None,
) -> tuple[Any, dict[str, Any] | None, str]:
    if pit_universe_artifact is None:
        return None, None, ""
    from qsys.research.pit_universe import PitUniverseStore

    try:
        store = PitUniverseStore(pit_universe_artifact, verify_hash=True)
        manifest_path = store.artifact_dir / "manifest.json"
        manifest_sha = _sha256_bytes(manifest_path.read_bytes())
        return store, store.provenance.to_dict(), manifest_sha
    except Exception as exc:
        raise BuildError(f"cannot verify PIT universe identity: {pit_universe_artifact}") from exc


def build_corporate_action_artifact(
    source_bundle: str | Path,
    research_root: str | Path,
    *,
    artifact_name: str,
    start_date: str,
    end_date: str,
    allow_rejections: bool = False,
    pit_universe_artifact: str | Path | None = None,
) -> Path:
    """Build one immutable artifact from the exact requested ex-date range.

    Every selected raw row is preflighted independently.  A normalizer error
    is a structural rejection; by default it prevents any output.  With the
    explicit quarantine flag, rejected rows remain in ``raw_all_market`` and
    are recorded by hash and reason in the copied source bundle.
    """
    source_bundle = Path(source_bundle)
    research_root = Path(research_root)
    start = _requested_day(start_date)
    end = _requested_day(end_date)
    if start > end:
        raise ValueError("start_date must be <= end_date")

    target = research_root / "corporate_actions" / str(artifact_name)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"corporate action artifact already exists: {target}")

    input_sha, input_raw_bytes, raw, query_coverage = _read_input_bundle(source_bundle)
    raw_ex_dates = raw["ex_date"].map(_day)
    date_mask = (raw_ex_dates >= start) & (raw_ex_dates <= end)
    date_selected_count = int(date_mask.sum())
    selected = raw.loc[date_mask].copy()
    selected = selected.reset_index(drop=True)
    pit_store, pit_identity, pit_manifest_sha = _pit_context(pit_universe_artifact)
    if pit_store is not None:
        if "ts_code" not in selected.columns:
            raise BuildError("PIT filtering requires raw ts_code column")
        pit_input_count = len(selected)
        pit_mask = []
        for row in selected.to_dict(orient="records"):
            instrument = str(row.get("ts_code") or "").strip()
            ex_date = _day(row.get("ex_date"))
            pit_mask.append(
                bool(instrument and ex_date and pit_store.is_member(instrument, ex_date))
            )
        selected = selected.loc[pit_mask].reset_index(drop=True)
        pit_filter = {
            "enabled": True,
            "rule": "member_on_ex_date",
            "input_row_count": int(pit_input_count),
            "output_row_count": int(len(selected)),
            "drop_row_count": int(pit_input_count - len(selected)),
            "universe_id": pit_identity["universe_id"],
            "universe_manifest_sha256": pit_manifest_sha,
            "membership_sha256": pit_identity["membership_sha256"],
            "signal_independent": True,
        }
    else:
        pit_filter = {
            "enabled": False,
            "rule": "none",
            "input_row_count": int(len(selected)),
            "output_row_count": int(len(selected)),
            "drop_row_count": 0,
            "signal_independent": True,
        }

    accepted_rows: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    non_actionable_count = 0
    for row in selected.to_dict(orient="records"):
        raw_hash = _canonical_source_hash(row)
        try:
            preflight = normalize_tushare_dividend(pd.DataFrame([row]))
        except Exception as exc:  # fail closed for any malformed source row
            rejections.append({
                "raw_row_hash": raw_hash,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        accepted_rows.append(row)
        if preflight.empty:
            non_actionable_count += 1

    rejections.sort(key=lambda item: (item["raw_row_hash"], item["reason"]))
    if rejections and not allow_rejections:
        raise BuildError(
            f"{len(rejections)} raw corporate-action rows rejected; "
            "rerun with --allow-rejections only after auditing quarantine"
        )

    accepted = pd.DataFrame(accepted_rows, columns=selected.columns)
    events = normalize_tushare_dividend(accepted)
    if not events.empty:
        effective_dates = events["effective_date"].map(_day)
        if ((effective_dates < start) | (effective_dates > end)).any():
            raise BuildError("normalized event escaped requested ex-date range")

    filtered_raw_bytes = io.BytesIO()
    selected.to_parquet(filtered_raw_bytes, index=False)
    filtered_raw = filtered_raw_bytes.getvalue()
    raw_coverage = {
        "requested_start_date": start,
        "requested_end_date": end,
        "input_raw_row_count": int(len(raw)),
        "date_filtered_raw_row_count": date_selected_count,
        "filtered_raw_row_count": int(len(selected)),
        "accepted_raw_row_count": int(len(accepted_rows)),
        "rejected_raw_row_count": int(len(rejections)),
        "non_actionable_raw_row_count": int(non_actionable_count),
        "filtered_ex_date_min": min(selected["ex_date"].map(_day), default=""),
        "filtered_ex_date_max": max(selected["ex_date"].map(_day), default=""),
        "input_raw_parquet_sha256": _sha256_bytes(input_raw_bytes),
        "pit_filtered_raw_parquet_sha256": _sha256_bytes(filtered_raw),
    }
    rejection_bytes = _json_bytes(rejections)
    coverage_bytes = _json_bytes(raw_coverage)
    build_manifest = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "input_bundle_sha256": input_sha,
        "raw_all_market_sha256": _sha256_bytes(input_raw_bytes),
        "pit_filtered_raw_sha256": _sha256_bytes(filtered_raw),
        "raw_coverage": raw_coverage,
        "rejections": rejections,
        "rejections_sha256": _sha256_bytes(rejection_bytes),
        "pit_universe_identity": pit_identity,
        "pit_filter": pit_filter,
        "rejection_policy": "allow_rejections" if allow_rejections else "fail_closed",
    }
    manifest_bytes = _json_bytes(build_manifest)
    entries = {
        "build_manifest.json": manifest_bytes,
        # Preserve the all-market source bytes exactly; the derived PIT subset
        # is a separate member so both provenance and filtering are auditable.
        "raw_all_market.parquet": input_raw_bytes,
        "pit_filtered_raw.parquet": filtered_raw,
        "query_coverage.json": _json_bytes(query_coverage),
        "raw_coverage.json": coverage_bytes,
        "rejections.json": rejection_bytes,
        "pit_filter.json": _json_bytes(pit_filter),
    }
    source_zip_bytes = _deterministic_zip(entries)

    with tempfile.TemporaryDirectory(prefix="corporate_action_source_") as temp_dir:
        source_path = Path(temp_dir) / "source_bundle.zip"
        source_path.write_bytes(source_zip_bytes)
        source_digest = _sha256_bytes(source_zip_bytes)
        return write_corporate_action_artifact(
            events,
            research_root,
            artifact_name=str(artifact_name),
            source="tushare_dividend_all_market",
            source_raw_artifact_sha256=source_digest,
            source_raw_path=str(source_path),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--allow-rejections", action="store_true")
    parser.add_argument("--pit-universe-artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        target = build_corporate_action_artifact(
            args.source_bundle,
            args.research_root,
            artifact_name=args.artifact_name,
            start_date=args.start_date,
            end_date=args.end_date,
            allow_rejections=args.allow_rejections,
            pit_universe_artifact=args.pit_universe_artifact,
        )
    except (BuildError, FileExistsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
