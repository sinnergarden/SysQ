"""Canonical PIT shareholder sidecar sync, health, and impact audit."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


HOLDER_FILENAME = "holder_num.parquet"
TOP10_FILENAME = "top10_holder_ratio.parquet"
STATE_FILENAME = "shareholder_sync_state.json"
AUDITED_SNAPSHOT_SCHEMA = "audited_shareholder_pit_sidecars_v2"
AUDITED_SNAPSHOT_CONTRACT = "shareholder_exact_event_complete_top10_v2"
AUDITED_SNAPSHOT_MANIFEST = "manifest.json"
_TERMINAL_GATES = frozenset({
    "fetch", "raw_payloads", "canonical_commit", "qlib_readback", "readiness",
    "contiguous_range",
})
SHAREHOLDER_FEATURES = {
    "holder_num_chg_qoq",
    "holder_num_chg_2q",
    "avg_shares_per_holder_chg_qoq",
    "top10_holder_ratio_chg_qoq",
    "holder_concentration_score",
    "holder_squeeze_score",
    "holder_price_confirm_score",
    "holder_num_stale_days",
    "top10_holder_stale_days",
    "top10_holder_ratio",
}


class ShareholderProjectionError(RuntimeError):
    """Raw shareholder events cannot be projected without guessing."""


def _normalise_holder_identity(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"[‐‑‒–—―﹘﹣－]", "-", text)
    text = re.sub(r"\s+", "", text)
    return text.casefold() or None


def _latest_report_period_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Select latest report period before filtering its value rows."""

    if frame.empty:
        return frame
    ordered = frame.sort_values(
        ["inst", "ann_date", "end_date"], kind="mergesort", na_position="first"
    )
    latest = ordered.groupby(
        ["inst", "ann_date"], dropna=False, sort=False
    )["end_date"].transform("max")
    return ordered.loc[ordered["end_date"].eq(latest)].copy()


def _resolve_data_root(
    *, project_root: Path | None = None, data_root: Path | None = None
) -> tuple[Path, Path]:
    """Return the canonical data root and a stable provenance path base."""

    if data_root is not None and project_root is not None:
        raise ValueError("pass data_root or project_root, not both")
    if data_root is not None:
        resolved = Path(data_root)
        return resolved, resolved.parent
    if project_root is not None:
        root = Path(project_root)
        return root / "data", root
    raise ValueError("data_root or project_root is required")


def _normalise_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    digits = "".join(re.findall(r"\d", text))
    if len(digits) >= 8:
        digits = digits[:8]
        parsed = pd.to_datetime(digits, format="%Y%m%d", errors="coerce")
    else:
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).strftime("%Y-%m-%d")


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n"
    ).encode("utf-8")


def _canonical_frame_hash(frame: pd.DataFrame, columns: list[str]) -> str:
    records = frame[columns].sort_values(columns, kind="mergesort").to_dict("records")
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalise_holder_rows(
    frame: pd.DataFrame | None, *, strict_raw: bool = False,
) -> pd.DataFrame:
    """Return one holder-count fact per instrument/announcement.

    The latest report period is selected before null/value filtering.  An
    unavailable latest-period value therefore cannot lend its announcement
    date to an older report period.
    """

    columns = ["inst", "ann_date", "end_date", "holder_num"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame.copy().rename(columns={"ts_code": "inst"})
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    out["inst"] = out["inst"].astype(str).str.strip().str.upper()
    out["ann_date"] = out["ann_date"].map(_normalise_date)
    out["end_date"] = out["end_date"].map(_normalise_date)
    out["holder_num"] = pd.to_numeric(out["holder_num"], errors="coerce")
    out = out.dropna(subset=["inst", "ann_date", "end_date"])
    out = out[out["inst"] != ""]
    exact_rows: list[dict[str, Any]] = []
    for key, group in out.groupby(
        ["inst", "ann_date", "end_date"], dropna=False, sort=True,
    ):
        positive = group.loc[group["holder_num"].gt(0), "holder_num"]
        values = positive.dropna().unique().tolist()
        if len(values) > 1:
            raise ShareholderProjectionError(
                f"conflicting holder_num values for exact event {key}: {values[:5]}"
            )
        invalid_non_null = group["holder_num"].notna() & ~group["holder_num"].gt(0)
        if strict_raw and invalid_non_null.any():
            raise ShareholderProjectionError(
                f"non-positive holder_num for exact event {key}"
            )
        exact_rows.append({
            "inst": key[0], "ann_date": key[1], "end_date": key[2],
            "holder_num": values[0] if values else pd.NA,
        })
    latest = _latest_report_period_rows(pd.DataFrame(exact_rows, columns=columns))
    latest = latest.dropna(subset=["holder_num"])
    return latest[columns].reset_index(drop=True)


def normalise_top10_rows(
    frame: pd.DataFrame | None, *, require_complete_raw: bool = False,
) -> pd.DataFrame:
    """Aggregate raw top-ten holders without treating partial rows as Top10."""

    columns = ["inst", "ann_date", "end_date", "top10_ratio"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame.copy().rename(columns={"ts_code": "inst"})
    if "top10_ratio" in out.columns:
        for column in columns:
            if column not in out.columns:
                out[column] = pd.NA
        out["inst"] = out["inst"].astype(str).str.strip().str.upper()
        out["ann_date"] = out["ann_date"].map(_normalise_date)
        out["end_date"] = out["end_date"].map(_normalise_date)
        out["top10_ratio"] = pd.to_numeric(out["top10_ratio"], errors="coerce")
        out = out.dropna(subset=["inst", "ann_date", "end_date"])
        out = _latest_report_period_rows(out.loc[out["inst"] != ""])
        out = out.dropna(subset=["top10_ratio"])
        out = out[out["top10_ratio"].between(0, 100)]
        return out[columns].drop_duplicates(
            ["inst", "ann_date"], keep="last"
        ).reset_index(drop=True)

    required = {"inst", "ann_date", "end_date", "hold_ratio"}
    if not required.issubset(out.columns):
        return pd.DataFrame(columns=columns)
    out["inst"] = out["inst"].astype(str).str.strip().str.upper()
    out["ann_date"] = out["ann_date"].map(_normalise_date)
    out["end_date"] = out["end_date"].map(_normalise_date)
    out["hold_ratio"] = pd.to_numeric(out["hold_ratio"], errors="coerce")
    out = out.dropna(subset=["inst", "ann_date", "end_date"])
    out = _latest_report_period_rows(out.loc[out["inst"] != ""])
    if "holder_name" not in out.columns:
        if require_complete_raw:
            raise ShareholderProjectionError("raw top10 event lacks holder_name")
        return pd.DataFrame(columns=columns)
    out["_holder_identity"] = out["holder_name"].map(_normalise_holder_identity)
    invalid = (
        out["_holder_identity"].isna()
        | out["hold_ratio"].isna()
        | ~out["hold_ratio"].between(0, 100)
    )
    exact_rows: list[dict[str, Any]] = []
    event_key = ["inst", "ann_date", "end_date"]
    stats = {
        "exact_event_count": 0,
        "accepted_exact_event_count": 0,
        "accepted_cutoff_tie_event_count": 0,
        "excluded_invalid_event_count": 0,
        "excluded_conflicting_ratio_event_count": 0,
        "excluded_incomplete_event_count": 0,
        "excluded_ambiguous_overfull_event_count": 0,
    }
    for key, event in out.groupby(event_key, dropna=False, sort=True):
        stats["exact_event_count"] += 1
        event_invalid = invalid.loc[event.index]
        if require_complete_raw and event_invalid.any():
            stats["excluded_invalid_event_count"] += 1
            continue
        event = event.loc[~event_invalid]
        ratios: list[float] = []
        conflicting = False
        for identity, holder_rows in event.groupby("_holder_identity", sort=True):
            values = holder_rows["hold_ratio"].dropna().unique().tolist()
            if len(values) != 1:
                if require_complete_raw:
                    conflicting = True
                    break
                raise ShareholderProjectionError(
                    f"conflicting ratio for holder {identity!r} in exact event {key}"
                )
            ratios.append(float(values[0]))
        if conflicting:
            stats["excluded_conflicting_ratio_event_count"] += 1
            continue
        if require_complete_raw:
            ratios.sort(reverse=True)
            if len(ratios) < 10:
                stats["excluded_incomplete_event_count"] += 1
                continue
            if len(ratios) > 10:
                cutoff = ratios[9]
                if any(abs(value - cutoff) > 1e-12 for value in ratios[10:]):
                    stats["excluded_ambiguous_overfull_event_count"] += 1
                    continue
                ratios = ratios[:10]
                stats["accepted_cutoff_tie_event_count"] += 1
        total = float(sum(ratios)) if ratios else float("nan")
        if not 0 <= total <= 100.0001:
            if require_complete_raw:
                stats["excluded_invalid_event_count"] += 1
                continue
            continue
        exact_rows.append({
            "inst": key[0], "ann_date": key[1], "end_date": key[2],
            "top10_ratio": min(total, 100.0),
        })
        stats["accepted_exact_event_count"] += 1
    result = pd.DataFrame(exact_rows, columns=columns).reset_index(drop=True)
    result.attrs["projection_stats"] = stats
    return result


def merge_shareholder_rows(
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame | None,
    *,
    kind: str,
) -> pd.DataFrame:
    normaliser = normalise_holder_rows if kind == "holder_num" else normalise_top10_rows
    current = normaliser(existing)
    if incoming is None or incoming.empty:
        return current
    raw = incoming.copy()
    if "inst" not in raw.columns and "ts_code" in raw.columns:
        raw = raw.rename(columns={"ts_code": "inst"})
    if not {"inst", "ann_date"}.issubset(raw.columns):
        return current
    replacement_keys = pd.DataFrame({
        "inst": raw["inst"].astype(str).str.strip().str.upper(),
        "ann_date": raw["ann_date"].map(_normalise_date),
    }).dropna().drop_duplicates()
    if not replacement_keys.empty and not current.empty:
        current = current.merge(
            replacement_keys.assign(_replace=True),
            on=["inst", "ann_date"], how="left",
        )
        current = current.loc[current["_replace"].isna()].drop(columns="_replace")
    projected = (
        normalise_holder_rows(raw, strict_raw=True)
        if kind == "holder_num"
        else normalise_top10_rows(raw, require_complete_raw=True)
    )
    return normaliser(pd.concat([current, projected], ignore_index=True))


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".parquet.tmp")
    os.close(descriptor)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _normal_compact_date(value: Any, *, field: str) -> str:
    normalized = _normalise_date(value)
    if normalized is None:
        raise RuntimeError(f"invalid {field}: {value!r}")
    return normalized.replace("-", "")


def _scope_covers_range(scopes: list[tuple[str, str]], start: str, end: str) -> bool:
    if not scopes:
        return False
    intervals = sorted(scopes)
    cursor = pd.Timestamp(start)
    target = pd.Timestamp(end)
    for left, right in intervals:
        left_ts = pd.Timestamp(left)
        right_ts = pd.Timestamp(right)
        if right_ts < cursor:
            continue
        if left_ts > cursor:
            return False
        cursor = max(cursor, right_ts + pd.Timedelta(days=1))
        if cursor > target:
            return True
    return cursor > target


def _load_audited_shareholder_payload(
    fetch: dict[str, Any], *, data_root: Path, endpoint: str,
    expected_symbols: set[str], range_start: str, range_end: str,
    immediate_source_receipt: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    status = str(fetch.get("status") or "")
    if status == "empty":
        if fetch.get("payload_path") is not None or fetch.get("payload_sha256") is not None:
            raise RuntimeError("empty shareholder receipt unexpectedly carries a payload")
        return pd.DataFrame(), {
            "source_rows": 0,
            "projected_rows": 0,
            "excluded_outside_union_rows": 0,
        }
    if status != "success" or fetch.get("payload_kind") != "raw_supplier":
        raise RuntimeError("shareholder snapshot accepts only success/empty raw receipts")
    relative = Path(str(fetch.get("payload_path") or ""))
    expected_sha = str(fetch.get("payload_sha256") or "").lower()
    if relative.is_absolute() or ".." in relative.parts or not _is_sha256(expected_sha):
        raise RuntimeError("shareholder payload identity is invalid")
    evidence_root = Path("raw") / "evidence" / "tushare" / endpoint
    physical_run_id = relative.parent.name
    layout_valid = (
        relative.parent.parent == evidence_root
        and relative.suffix == ".parquet"
    )
    if physical_run_id == str(fetch.get("run_id") or ""):
        layout_valid = layout_valid and relative.stem == str(fetch.get("receipt_id") or "")
    else:
        source = immediate_source_receipt or {}
        layout_valid = layout_valid and (
            source.get("source") == "tushare"
            and source.get("endpoint") == endpoint
            and source.get("status") == "success"
            and source.get("requested_scope") == fetch.get("requested_scope")
            and source.get("payload_kind") == "raw_supplier"
            and source.get("payload_path") == relative.as_posix()
            and source.get("payload_sha256") == expected_sha
        )
    if not layout_valid:
        raise RuntimeError("shareholder payload is outside canonical evidence layout")
    payload = (data_root / relative).resolve()
    if data_root != payload and data_root not in payload.parents:
        raise RuntimeError("shareholder payload escapes data root")
    if not payload.is_file() or _file_sha256(payload) != expected_sha:
        raise RuntimeError("shareholder payload sha256 mismatch")
    try:
        frame = pd.read_parquet(payload)
    except Exception as exc:
        raise RuntimeError(f"cannot read shareholder raw payload: {payload}") from exc
    required = (
        {"ts_code", "ann_date", "end_date", "holder_num"}
        if endpoint == "stk_holdernumber"
        else {"ts_code", "ann_date", "end_date", "hold_ratio"}
    )
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(
            f"{endpoint} raw payload missing required columns: {sorted(missing)}"
        )
    if frame.empty:
        raise RuntimeError("success shareholder receipt has an empty payload")
    raw_symbols = frame["ts_code"].astype(str)
    normalized_symbols = raw_symbols.str.strip().str.upper()
    canonical_equity = raw_symbols.eq(normalized_symbols) & (
        normalized_symbols.str.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)")
    )
    supplier_non_equity = raw_symbols.eq(normalized_symbols) & (
        normalized_symbols.str.fullmatch(r"C\d{5}")
    )
    if normalized_symbols.empty or not (canonical_equity | supplier_non_equity).all():
        raise RuntimeError(f"{endpoint} payload has invalid ts_code values")
    dates = frame["ann_date"].map(_normalise_date)
    if dates.isna().any() or not dates.between(
        pd.Timestamp(range_start).strftime("%Y-%m-%d"),
        pd.Timestamp(range_end).strftime("%Y-%m-%d"),
    ).all():
        raise RuntimeError(f"{endpoint} payload escaped announcement-date scope")
    scope = fetch.get("requested_scope") or {}
    scope_start = _normalise_date(scope.get("date_start"))
    scope_end = _normalise_date(scope.get("date_end"))
    if scope_start is None or scope_end is None or not dates.between(
        scope_start, scope_end,
    ).all():
        raise RuntimeError(f"{endpoint} payload escaped its receipt scope")
    projected = frame.loc[
        canonical_equity & normalized_symbols.isin(expected_symbols)
    ].copy()
    return projected, {
        "source_rows": int(len(frame)),
        "projected_rows": int(len(projected)),
        "excluded_outside_union_rows": int(len(frame) - len(projected)),
        "excluded_non_equity_identifier_rows": int(supplier_non_equity.sum()),
    }


def materialize_audited_shareholder_snapshot(
    *, terminal_receipt_path: str | Path, source_run_id: str, scope_key: str,
    range_start: str, range_end: str, output_root: str | Path,
) -> dict[str, Any]:
    """Offline-build an immutable shareholder snapshot from one trusted run.

    The terminal receipt and its audit.db backlink are the authority.  Legacy
    source-state/bootstrap summaries are intentionally not accepted as source
    evidence, and this function never invokes a supplier API.
    """

    start = _normal_compact_date(range_start, field="range_start")
    end = _normal_compact_date(range_end, field="range_end")
    if start > end:
        raise ValueError("shareholder snapshot range_start is after range_end")
    run_id = str(source_run_id or "").strip()
    if not run_id or not all(char.isalnum() or char in "_.-" for char in run_id):
        raise ValueError("invalid shareholder source_run_id")
    receipt_path = Path(terminal_receipt_path).expanduser()
    if receipt_path.is_symlink():
        raise RuntimeError("shareholder terminal receipt must not be a symlink")
    try:
        receipt_path = receipt_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("shareholder terminal receipt is missing") from exc
    if (
        not receipt_path.is_file()
        or receipt_path.name != "receipt.json"
        or receipt_path.parent.name != run_id
        or receipt_path.parents[1].name != "source_runs"
        or receipt_path.parents[2].name != "audit"
    ):
        raise RuntimeError("shareholder terminal receipt is outside canonical audit layout")
    data_root = receipt_path.parents[3]
    audit_db = data_root / "audit" / "audit.db"
    terminal_sha = str(_file_sha256(receipt_path) or "")
    try:
        terminal = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("shareholder terminal receipt is invalid JSON") from exc
    gates = terminal.get("terminal_gates") if isinstance(terminal, dict) else None
    if (
        terminal.get("run_id") != run_id
        or terminal.get("trust_state") not in {"trusted", "trusted_unchanged"}
        or not isinstance(gates, dict)
        or any(gates.get(gate) is not True for gate in _TERMINAL_GATES)
    ):
        raise RuntimeError("shareholder terminal receipt is not trusted")
    fetches = terminal.get("fetch_receipts")
    links = terminal.get("field_receipt_links")
    journal = terminal.get("audit_journal")
    if (
        not isinstance(fetches, list)
        or not isinstance(links, list)
        or not isinstance(journal, list)
    ):
        raise RuntimeError("shareholder terminal receipt lacks fetch/field evidence")
    reuse_events: dict[str, dict[str, Any]] = {}
    for event in journal:
        if not isinstance(event, dict) or event.get("event_type") != "fetch_shard_reused":
            continue
        payload = event.get("payload")
        receipt_id = str(payload.get("receipt_id") or "") if isinstance(payload, dict) else ""
        if not receipt_id or receipt_id in reuse_events:
            raise RuntimeError("shareholder terminal reuse journal is invalid")
        reuse_events[receipt_id] = payload

    try:
        connection = sqlite3.connect(f"{audit_db.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError("cannot open shareholder audit database read-only") from exc
    try:
        watermark_rows = connection.execute(
            """SELECT field_name,scope_key,range_start,range_end,trusted_through
               FROM trusted_watermarks
               WHERE source=? AND run_id=? AND terminal_receipt_sha256=?""",
            ("tushare", run_id, terminal_sha),
        ).fetchall()
        by_field = {str(row["field_name"]): row for row in watermark_rows}
        for field in ("ann_date", "holder_num", "hold_ratio"):
            row = by_field.get(field)
            if (
                row is None or row["scope_key"] != scope_key
                or _normal_compact_date(row["range_start"], field="watermark range_start") > start
                or _normal_compact_date(row["range_end"], field="watermark range_end") < end
                or _normal_compact_date(row["trusted_through"], field="watermark trusted_through") < end
            ):
                raise RuntimeError(
                    f"shareholder terminal watermark does not cover scope: {field}"
                )

        links_by_receipt: dict[str, set[tuple[str, str]]] = {}
        for link in links:
            if not isinstance(link, dict) or link.get("run_id") != run_id:
                continue
            links_by_receipt.setdefault(str(link.get("receipt_id") or ""), set()).add(
                (str(link.get("dataset") or ""), str(link.get("field_name") or ""))
            )
        endpoint_specs = {
            "stk_holdernumber": (
                "shareholder_holdernumber", {"ann_date", "holder_num"},
            ),
            "top10_holders": ("shareholder_top10", {"ann_date", "hold_ratio"}),
        }
        selected = [
            fetch for fetch in fetches
            if isinstance(fetch, dict) and fetch.get("endpoint") in endpoint_specs
        ]
        if not selected or {str(row.get("endpoint")) for row in selected} != set(endpoint_specs):
            raise RuntimeError("shareholder terminal lacks both historical endpoints")

        expected_symbols: list[str] | None = None
        holder_frames: list[pd.DataFrame] = []
        top10_frames: list[pd.DataFrame] = []
        intervals: dict[str, list[tuple[str, str]]] = {
            "stk_holdernumber": [], "top10_holders": [],
        }
        projection_stats = {
            endpoint: {
                "source_rows": 0,
                "projected_rows": 0,
                "excluded_outside_union_rows": 0,
                "excluded_non_equity_identifier_rows": 0,
            }
            for endpoint in endpoint_specs
        }
        receipt_material: list[dict[str, Any]] = []
        for fetch in selected:
            endpoint = str(fetch.get("endpoint"))
            receipt_id = str(fetch.get("receipt_id") or "")
            scope = fetch.get("requested_scope")
            if (
                fetch.get("run_id") != run_id or fetch.get("source") != "tushare"
                or fetch.get("status") not in {"success", "empty"}
                or not receipt_id or not isinstance(scope, dict)
            ):
                raise RuntimeError("shareholder terminal fetch identity is invalid")
            symbols = sorted({
                str(symbol).strip().upper()
                for symbol in scope.get("symbols") or [] if str(symbol).strip()
            })
            from qsys.data.source_audit import stable_scope_hash

            if (
                not symbols or scope.get("symbol_count") != len(symbols)
                or scope.get("symbols_sha256") != stable_scope_hash(symbols)
                or str(scope.get("universe") or scope_key) != scope_key
            ):
                raise RuntimeError("shareholder requested_scope union identity is invalid")
            if expected_symbols is None:
                expected_symbols = symbols
            elif symbols != expected_symbols:
                raise RuntimeError("shareholder receipts disagree on historical union")
            left = _normal_compact_date(scope.get("date_start"), field="scope.date_start")
            right = _normal_compact_date(scope.get("date_end"), field="scope.date_end")
            if left < start or right > end or left > right:
                raise RuntimeError("shareholder requested_scope escaped snapshot range")
            variant = str(scope.get("request_variant") or "")
            if endpoint == "top10_holders" and (
                left != right
                or not variant.startswith(f"announcement_date:{left}:offset=")
            ):
                raise RuntimeError(
                    "top10 shareholder receipt is not exact announcement-date evidence"
                )
            if endpoint == "stk_holdernumber" and not variant.startswith(
                "calendar_year:"
            ):
                raise RuntimeError(
                    "holdernumber receipt is not bounded announcement-date evidence"
                )
            intervals[endpoint].append((left, right))
            dataset, required_fields = endpoint_specs[endpoint]
            linked_fields = {
                field for linked_dataset, field in links_by_receipt.get(receipt_id, set())
                if linked_dataset == dataset
            }
            if not required_fields.issubset(linked_fields):
                raise RuntimeError(f"shareholder receipt field links incomplete: {receipt_id}")
            db_row = connection.execute(
                "SELECT run_id,source,endpoint,status,requested_scope_json,payload_kind,"
                "payload_path,payload_sha256 FROM fetch_receipts WHERE run_id=? AND receipt_id=?",
                (run_id, receipt_id),
            ).fetchone()
            if db_row is None:
                raise RuntimeError(f"shareholder receipt missing from audit.db: {receipt_id}")
            if (
                db_row["run_id"] != run_id or db_row["source"] != "tushare"
                or db_row["endpoint"] != endpoint or db_row["status"] != fetch.get("status")
                or json.loads(db_row["requested_scope_json"]) != scope
                or db_row["payload_kind"] != fetch.get("payload_kind")
                or db_row["payload_path"] != fetch.get("payload_path")
                or db_row["payload_sha256"] != fetch.get("payload_sha256")
            ):
                raise RuntimeError(f"shareholder terminal/audit receipt mismatch: {receipt_id}")
            immediate_source_receipt = None
            relative_payload = Path(str(fetch.get("payload_path") or ""))
            if (
                fetch.get("status") == "success"
                and relative_payload.parent.name != run_id
            ):
                reuse = reuse_events.get(receipt_id)
                if (
                    not isinstance(reuse, dict)
                    or reuse.get("source") != "tushare"
                    or reuse.get("endpoint") != endpoint
                ):
                    raise RuntimeError("shareholder reused payload lacks source lineage")
                source_row = connection.execute(
                    "SELECT source,endpoint,status,requested_scope_json,payload_kind,"
                    "payload_path,payload_sha256 FROM fetch_receipts "
                    "WHERE run_id=? AND receipt_id=?",
                    (
                        str(reuse.get("resume_from_run_id") or ""),
                        str(reuse.get("source_receipt_id") or ""),
                    ),
                ).fetchone()
                if source_row is None:
                    raise RuntimeError("shareholder reused payload source receipt is missing")
                immediate_source_receipt = dict(source_row)
                try:
                    immediate_source_receipt["requested_scope"] = json.loads(
                        immediate_source_receipt.pop("requested_scope_json")
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        "shareholder reused payload source scope is invalid"
                    ) from exc
            frame, frame_stats = _load_audited_shareholder_payload(
                fetch, data_root=data_root, endpoint=endpoint,
                expected_symbols=set(symbols), range_start=start, range_end=end,
                immediate_source_receipt=immediate_source_receipt,
            )
            for name, value in frame_stats.items():
                projection_stats[endpoint][name] += value
            if not frame.empty:
                (holder_frames if endpoint == "stk_holdernumber" else top10_frames).append(frame)
            receipt_material.append({
                "receipt_id": receipt_id, "endpoint": endpoint,
                "status": fetch["status"], "payload_sha256": fetch.get("payload_sha256"),
                "date_start": left, "date_end": right,
            })
    except sqlite3.Error as exc:
        raise RuntimeError("cannot verify shareholder receipts in audit.db") from exc
    finally:
        connection.close()

    if expected_symbols is None:
        raise RuntimeError("shareholder historical union is empty")
    for endpoint in endpoint_specs:
        if not _scope_covers_range(intervals[endpoint], start, end):
            raise RuntimeError(f"shareholder receipt coverage has a gap: {endpoint}")
    holder = normalise_holder_rows(
        pd.concat(holder_frames, ignore_index=True) if holder_frames else None,
        strict_raw=True,
    )
    top10 = normalise_top10_rows(
        pd.concat(top10_frames, ignore_index=True) if top10_frames else None,
        require_complete_raw=True,
    )
    projection_stats["top10_holders"].update(
        top10.attrs.get("projection_stats", {})
    )
    if holder.empty or top10.empty:
        raise RuntimeError(
            "audited shareholder snapshot requires non-empty holder and top10 artifacts"
        )
    expected_set = set(expected_symbols)
    for name, frame in (("holder_num", holder), ("top10_holder_ratio", top10)):
        if not set(frame["inst"]).issubset(expected_set):
            raise RuntimeError(f"{name} artifact escaped historical union")
        dates = frame["ann_date"]
        if not frame.empty and not dates.between(
            pd.Timestamp(start).strftime("%Y-%m-%d"),
            pd.Timestamp(end).strftime("%Y-%m-%d"),
        ).all():
            raise RuntimeError(f"{name} artifact escaped snapshot date scope")

    receipt_material = sorted(
        receipt_material,
        key=lambda row: (row["endpoint"], row["date_start"], row["receipt_id"]),
    )
    identity = {
        "schema": AUDITED_SNAPSHOT_SCHEMA,
        "contract": AUDITED_SNAPSHOT_CONTRACT,
        "source": "tushare",
        "source_run_id": run_id,
        "terminal_receipt_sha256": terminal_sha,
        "scope_key": scope_key,
        "range_start": start,
        "range_end": end,
        "symbol_count": len(expected_symbols),
        "symbols_sha256": stable_scope_hash(expected_symbols),
        "receipt_count": len(receipt_material),
        "receipts_sha256": hashlib.sha256(_json_bytes({"receipts": receipt_material})).hexdigest(),
    }
    artifact_id = hashlib.sha256(_json_bytes(identity)).hexdigest()
    root = Path(output_root).expanduser().absolute()
    if root.is_symlink():
        raise RuntimeError("shareholder snapshot output root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    target = root / artifact_id
    lock_path = root / ".shareholder_snapshot.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        staging = Path(tempfile.mkdtemp(prefix=".shareholder-snapshot-", dir=root))
        try:
            holder_path = staging / HOLDER_FILENAME
            top10_path = staging / TOP10_FILENAME
            _atomic_write_parquet(holder, holder_path)
            _atomic_write_parquet(top10, top10_path)
            artifacts = {
                "holder_num": {
                    "path": HOLDER_FILENAME, "sha256": _file_sha256(holder_path),
                    "rows": len(holder),
                },
                "top10_holder_ratio": {
                    "path": TOP10_FILENAME, "sha256": _file_sha256(top10_path),
                    "rows": len(top10),
                },
            }
            manifest = {
                "schema_version": 2,
                "artifact_type": AUDITED_SNAPSHOT_SCHEMA,
                "artifact_id": artifact_id,
                "identity": identity,
                "artifacts": artifacts,
                "scope": {
                    "scope_key": scope_key, "range_start": start, "range_end": end,
                    "symbol_count": len(expected_symbols),
                    "symbols_sha256": stable_scope_hash(expected_symbols),
                    "symbols": expected_symbols,
                },
                "contracts": {
                    "transform": AUDITED_SNAPSHOT_CONTRACT,
                    "availability_rule": "announcement_date_asof",
                    "holder_key": ["inst", "ann_date"],
                    "top10_key": ["inst", "ann_date"],
                    "top10_exact_event_policy": (
                        "accept_exactly_ten_or_cutoff_ties_exclude_ambiguous_v1"
                    ),
                },
                "projection": {
                    "by_endpoint": projection_stats,
                    "source_rows": sum(
                        row["source_rows"] for row in projection_stats.values()
                    ),
                    "projected_rows": sum(
                        row["projected_rows"] for row in projection_stats.values()
                    ),
                    "excluded_outside_union_rows": sum(
                        row["excluded_outside_union_rows"]
                        for row in projection_stats.values()
                    ),
                    "excluded_non_equity_identifier_rows": sum(
                        row["excluded_non_equity_identifier_rows"]
                        for row in projection_stats.values()
                    ),
                },
                "source_evidence": {
                    "run_id": run_id,
                    "terminal_receipt_path": receipt_path.relative_to(data_root).as_posix(),
                    "terminal_receipt_sha256": terminal_sha,
                    "receipt_count": len(receipt_material),
                    "receipts_sha256": identity["receipts_sha256"],
                },
            }
            manifest_path = staging / AUDITED_SNAPSHOT_MANIFEST
            manifest_path.write_bytes(_json_bytes(manifest))
            for path in (holder_path, top10_path, manifest_path):
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            staging_fd = os.open(
                staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)
            reused = False
            if target.exists():
                if target.is_symlink() or not target.is_dir():
                    raise RuntimeError("shareholder snapshot identity target is invalid")
                for filename in (HOLDER_FILENAME, TOP10_FILENAME, AUDITED_SNAPSHOT_MANIFEST):
                    if not (target / filename).is_file() or (
                        target / filename
                    ).read_bytes() != (staging / filename).read_bytes():
                        raise RuntimeError(
                            "existing shareholder snapshot identity is not byte-identical"
                        )
                reused = True
            else:
                os.rename(staging, target)
                staging = None
                root_fd = os.open(
                    root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            return {
                "status": "reused" if reused else "published",
                "artifact_id": artifact_id,
                "manifest_path": str(target / AUDITED_SNAPSHOT_MANIFEST),
                "manifest_sha256": _file_sha256(target / AUDITED_SNAPSHOT_MANIFEST),
                "holder_path": str(target / HOLDER_FILENAME),
                "holder_sha256": artifacts["holder_num"]["sha256"],
                "top10_path": str(target / TOP10_FILENAME),
                "top10_sha256": artifacts["top10_holder_ratio"]["sha256"],
                "source_run_id": run_id,
                "terminal_receipt_sha256": terminal_sha,
                "symbol_count": len(expected_symbols),
            }
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)


def _atomic_write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _source_health(
    frame: pd.DataFrame,
    *,
    value_column: str,
    symbols: set[str],
    as_of_date: str,
    min_coverage: float,
    max_median_stale_days: int,
    max_row_stale_days: int,
) -> tuple[dict[str, Any], set[str]]:
    as_of = pd.Timestamp(as_of_date)
    eligible = frame[
        frame["inst"].isin(symbols)
        & (pd.to_datetime(frame["ann_date"], errors="coerce") <= as_of)
    ].copy()
    latest = eligible.sort_values(
        ["inst", "ann_date"], kind="mergesort"
    ).groupby("inst", sort=False).tail(1)
    latest["stale_days"] = (
        as_of - pd.to_datetime(latest["ann_date"], errors="coerce")
    ).dt.days
    covered = set(latest["inst"].astype(str))
    missing = symbols - covered
    stale = set(
        latest.loc[latest["stale_days"] > max_row_stale_days, "inst"].astype(str)
    )
    stale_days = latest["stale_days"].dropna()
    coverage = len(covered) / len(symbols) if symbols else 0.0
    median = float(stale_days.median()) if not stale_days.empty else None
    p95 = float(stale_days.quantile(0.95)) if not stale_days.empty else None
    violations: list[str] = []
    if coverage < min_coverage:
        violations.append(
            f"coverage={coverage:.2%} below required={min_coverage:.2%}"
        )
    if median is None or median > max_median_stale_days:
        violations.append(
            f"median_stale_days={median} exceeds {max_median_stale_days}"
        )
    return (
        {
            "row_count": len(frame),
            "covered_symbols": len(covered),
            "coverage": round(coverage, 6),
            "latest_ann_date": frame["ann_date"].max() if not frame.empty else None,
            "median_stale_days": median,
            "p95_stale_days": p95,
            "max_stale_days": (
                float(stale_days.max()) if not stale_days.empty else None
            ),
            "missing_symbol_count": len(missing),
            "stale_symbol_count": len(stale),
            "missing_symbols_sample": sorted(missing)[:20],
            "stale_symbols_sample": sorted(stale)[:20],
            "value_column": value_column,
            "min_coverage": min_coverage,
            "max_median_stale_days": max_median_stale_days,
            "max_row_stale_days": max_row_stale_days,
            "violations": violations,
        },
        missing | stale,
    )


def inspect_shareholder_sidecar_health(
    *,
    symbols: Iterable[str],
    as_of_date: str,
    contract: dict[str, Any],
    project_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Inspect current PIT source coverage without trusting feature non-nullness."""

    resolved_data_root, provenance_root = _resolve_data_root(
        project_root=project_root, data_root=data_root
    )
    canonical = resolved_data_root / "canonical"
    holder_path = canonical / HOLDER_FILENAME
    top10_path = canonical / TOP10_FILENAME
    holder = normalise_holder_rows(
        pd.read_parquet(holder_path) if holder_path.is_file() else None
    )
    top10 = normalise_top10_rows(
        pd.read_parquet(top10_path) if top10_path.is_file() else None
    )
    symbol_set = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    source_specs = {
        "holder_num": (
            holder,
            "holder_num",
            holder_path,
            contract["features"]["holder_num_stale_days"],
        ),
        "top10_holder_ratio": (
            top10,
            "top10_ratio",
            top10_path,
            contract["features"]["top10_holder_stale_days"],
        ),
    }
    sources: dict[str, Any] = {}
    stale_by_source: dict[str, list[str]] = {}
    violations: list[str] = []
    snapshot_material: dict[str, Any] = {}
    for name, (frame, value_column, path, limits) in source_specs.items():
        health, stale = _source_health(
            frame,
            value_column=value_column,
            symbols=symbol_set,
            as_of_date=as_of_date,
            min_coverage=contract["min_coverage"],
            max_median_stale_days=limits["max_median_days"],
            max_row_stale_days=limits["max_row_days"],
        )
        health["path"] = str(path.relative_to(provenance_root))
        health["file_sha256"] = _file_sha256(path)
        subset = frame[
            frame["inst"].isin(symbol_set)
            & (frame["ann_date"] <= _normalise_date(as_of_date))
        ]
        canonical_columns = ["inst", "ann_date", "end_date", value_column]
        health["asof_snapshot_hash"] = _canonical_frame_hash(
            subset, canonical_columns
        )
        sources[name] = health
        stale_by_source[name] = sorted(stale)
        violations.extend(f"{name}: {message}" for message in health["violations"])
        snapshot_material[name] = {
            "asof_snapshot_hash": health["asof_snapshot_hash"],
            "file_sha256": health["file_sha256"],
        }
    snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "status": "pass" if not violations else "fail",
        "source": contract["source"],
        "availability_rule": "announcement_date_asof",
        "as_of_date": _normalise_date(as_of_date),
        "universe_symbol_count": len(symbol_set),
        "snapshot_hash": snapshot_hash,
        "sources": sources,
        "stale_symbols": stale_by_source,
        "violations": violations,
    }


def audit_shareholder_impact(
    *,
    project_root: Path,
    symbols: Iterable[str],
    open_dates: Iterable[str],
    as_of_date: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Inventory persisted artifacts that overlap a systemic source outage."""

    root = Path(project_root)
    canonical = root / "data" / "canonical"
    symbol_set = {str(value).strip().upper() for value in symbols if str(value).strip()}
    holder = normalise_holder_rows(
        pd.read_parquet(canonical / HOLDER_FILENAME)
        if (canonical / HOLDER_FILENAME).is_file()
        else None
    )
    top10 = normalise_top10_rows(
        pd.read_parquet(canonical / TOP10_FILENAME)
        if (canonical / TOP10_FILENAME).is_file()
        else None
    )
    source_specs = (
        (
            "holder_num",
            holder,
            "holder_num",
            contract["features"]["holder_num_stale_days"],
        ),
        (
            "top10_holder_ratio",
            top10,
            "top10_ratio",
            contract["features"]["top10_holder_stale_days"],
        ),
    )
    sessions = sorted(
        value
        for value in {_normalise_date(item) for item in open_dates}
        if value and value <= _normalise_date(as_of_date)
    )
    first_failure_by_source: dict[str, str | None] = {}
    for name, frame, value_column, limits in source_specs:
        # Keep the start of the final contiguous median-staleness failure block.
        # Early-history coverage gaps for today's universe are not evidence of
        # the recent updater outage and must not move the impact boundary back.
        first_failure: str | None = None
        for session in sessions:
            health, _ = _source_health(
                frame,
                value_column=value_column,
                symbols=symbol_set,
                as_of_date=session,
                min_coverage=contract["min_coverage"],
                max_median_stale_days=limits["max_median_days"],
                max_row_stale_days=limits["max_row_days"],
            )
            median = health["median_stale_days"]
            if median is None or median > limits["max_median_days"]:
                first_failure = first_failure or session
            else:
                first_failure = None
        first_failure_by_source[name] = first_failure
    failures = [value for value in first_failure_by_source.values() if value]
    affected_since = min(failures) if failures else None

    affected: dict[str, list[dict[str, Any]]] = {
        "candidate_runs": [],
        "model_artifacts": [],
        "model_contract_migrations": [],
        "research_manifests": [],
        "feature_caches": [],
    }
    if affected_since:
        for path in sorted((root / "outputs").rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            signal_date = _normalise_date(
                payload.get("signal_date") or payload.get("data_date")
            ) if isinstance(payload, dict) else None
            if signal_date and signal_date >= affected_since:
                strategy = str(payload.get("strategy_id") or "")
                feature_list = str(payload.get("feature_list_id") or "")
                if strategy == "financial_rc" or "financial" in feature_list:
                    affected["candidate_runs"].append(
                        {
                            "path": str(path.relative_to(root)),
                            "signal_date": signal_date,
                            "strategy_id": strategy,
                            "reason": "shareholder PIT source freshness failed",
                        }
                    )
        for path in sorted((root / "data" / "research" / "models").rglob("meta.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            features = set(payload.get("ordered_features") or [])
            uses_shareholder = bool(features.intersection(SHAREHOLDER_FEATURES)) or (
                payload.get("feature_list_id") == "v3a_plus_liquidity_financial_rc"
            )
            train_start = _normalise_date(payload.get("train_start"))
            train_end = _normalise_date(payload.get("train_end"))
            if uses_shareholder and train_end and train_end >= affected_since:
                affected["model_artifacts"].append(
                    {
                        "path": str(path.parent.relative_to(root)),
                        "tag": payload.get("tag"),
                        "train_start": train_start,
                        "train_end": train_end,
                        "reason": "training window overlaps shareholder outage",
                    }
                )
            if uses_shareholder and not payload.get("shareholder_freshness_contract"):
                affected["model_contract_migrations"].append(
                    {
                        "path": str(path.parent.relative_to(root)),
                        "tag": payload.get("tag"),
                        "train_start": train_start,
                        "train_end": train_end,
                        "reason": "model predates enforced shareholder freshness lineage",
                    }
                )
        manifest_roots = (root / "data" / "research", root / "artifacts")
        feature_tokens = tuple(sorted(SHAREHOLDER_FEATURES))
        for manifest_root in manifest_roots:
            if not manifest_root.exists():
                continue
            for path in sorted(manifest_root.rglob("*.json")):
                if path.name == "meta.json" or (
                    root / "data" / "research" / "models"
                ) in path.parents:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                if not any(token in text for token in feature_tokens):
                    continue
                dates = [
                    match
                    for match in re.findall(r"20\d{2}-\d{2}-\d{2}", text)
                    if _normalise_date(match)
                ]
                if not dates or max(dates) >= affected_since:
                    affected["research_manifests"].append(
                        {
                            "path": str(path.relative_to(root)),
                            "date_range": [min(dates), max(dates)] if dates else None,
                            "reason": "references shareholder-derived features",
                        }
                    )
        cache_root = root / "data" / "feature_cache" / "features"
        for feature in sorted(SHAREHOLDER_FEATURES):
            path = cache_root / feature
            if path.exists():
                affected["feature_caches"].append(
                    {
                        "path": str(path.relative_to(root)),
                        "affected_since": affected_since,
                        "reason": "derived cache must be rebuilt from repaired PIT sidecars",
                    }
                )

    return {
        "schema_version": 1,
        "status": "affected" if affected_since else "pass",
        "as_of_date": _normalise_date(as_of_date),
        "availability_rule": "announcement_date_asof",
        "first_failure_by_source": first_failure_by_source,
        "affected_since": affected_since,
        "affected": affected,
        "counts": {name: len(items) for name, items in affected.items()},
        "not_affected": [
            "canonical daily OHLCV/price data",
            "native Qlib price/volume fields",
            "label values computed only from prices",
        ],
        "required_actions": (
            [
                "backfill both shareholder sidecars by announcement date",
                "rebuild shareholder-derived feature caches",
                "retrain every listed model artifact",
                "retrain active 60d/180d models requiring freshness-contract migration",
                "regenerate every listed candidate run",
                "re-run listed research/backtest manifests before reuse",
            ]
            if affected_since
            else []
        ),
    }


def _paged_call(api: Any, *, limit: int, **kwargs: Any) -> pd.DataFrame:
    pages: list[pd.DataFrame] = []
    offset = 0
    previous_fingerprint: str | None = None
    for _ in range(100):
        page = api(limit=limit, offset=offset, **kwargs)
        if page is None or page.empty:
            break
        fingerprint = _canonical_frame_hash(
            page.assign(_row_number=range(len(page))),
            sorted(page.columns.tolist()) + ["_row_number"],
        )
        if fingerprint == previous_fingerprint:
            raise RuntimeError(
                f"shareholder API pagination repeated offset={offset}; aborting"
            )
        previous_fingerprint = fingerprint
        pages.append(page)
        if len(page) < limit:
            break
        offset += limit
    else:
        raise RuntimeError("shareholder API pagination exceeded 100 pages")
    return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()


def _audited_paged_call(
    collector: Any,
    *,
    endpoint: str,
    dataset: str,
    fields: tuple[str, ...],
    response_fields: str,
    limit: int,
    requested_scope: dict[str, Any],
    request_variant: str,
    run_id: str,
    audit_store: Any,
    resume_proof: dict[str, Any] | None,
    scope_key: str,
    universe: str,
    **kwargs: Any,
) -> pd.DataFrame:
    """Fetch one paged event query as exact durable supplier shards."""

    pages: list[pd.DataFrame] = []
    offset = 0
    previous_fingerprint: str | None = None
    for _ in range(100):
        page, receipt_id = collector._fetch_daily_endpoint_with_receipt(
            endpoint,
            run_id=run_id,
            audit_store=audit_store,
            requested_scope=requested_scope,
            resume_proof=resume_proof,
            scope_key=scope_key,
            universe=universe,
            request_variant=f"{request_variant}:offset={offset}",
            identity_columns=("ts_code", "ann_date"),
            evidence_fields=(),
            limit=limit,
            offset=offset,
            fields=response_fields,
            **kwargs,
        )
        if receipt_id is None:
            raise RuntimeError(f"{endpoint} did not emit a source receipt")
        audit_store.record_field_receipt_links(
            run_id=run_id,
            receipt_id=receipt_id,
            dataset=dataset,
            fields=fields,
        )
        if page is None or page.empty:
            break
        fingerprint = _canonical_frame_hash(
            page.assign(_row_number=range(len(page))),
            sorted(page.columns.tolist()) + ["_row_number"],
        )
        if fingerprint == previous_fingerprint:
            raise RuntimeError(
                f"shareholder API pagination repeated offset={offset}; aborting"
            )
        previous_fingerprint = fingerprint
        pages.append(page)
        if len(page) < limit:
            break
        offset += limit
    else:
        raise RuntimeError("shareholder API pagination exceeded 100 pages")
    return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()


def _quarter_ends(start_date: str, end_date: str) -> list[str]:
    start = pd.Timestamp(start_date) - pd.Timedelta(days=180)
    end = pd.Timestamp(end_date)
    periods = pd.period_range(start=start, end=end, freq="Q-DEC")
    return [
        period.end_time.strftime("%Y%m%d")
        for period in periods
        if period.end_time.normalize() <= end
    ]


def _calendar_year_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Return deterministic, inclusive, non-overlapping calendar-year chunks."""

    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    chunks: list[tuple[str, str]] = []
    current = start
    while current <= end:
        chunk_end = date(current.year, 12, 31)
        if chunk_end > end:
            chunk_end = end
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks


def fetch_shareholder_backfill(
    collector: Any, *, start_date: str, end_date: str,
    run_id: str | None = None, audit_store: Any | None = None,
    resume_proof: dict[str, Any] | None = None,
    scope_key: str = "ad_hoc", universe: str = "ad_hoc",
    evidence_symbols: Iterable[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fetch missed holder data with bounded, paged Tushare calls."""

    holder_pages: list[pd.DataFrame] = []
    holder_chunks: list[dict[str, Any]] = []
    audited = run_id is not None or audit_store is not None or resume_proof is not None
    if audited and (run_id is None or audit_store is None):
        raise ValueError("audited shareholder backfill requires run_id and audit_store")
    evidence_codes = sorted({
        str(value).strip().upper() for value in evidence_symbols if str(value).strip()
    })
    if audited and not evidence_codes:
        raise ValueError("audited shareholder backfill requires evidence_symbols")

    def evidence_scope(left: str, right: str) -> dict[str, Any]:
        from qsys.data.source_audit import stable_scope_hash

        return {
            "date_start": left.replace("-", ""),
            "date_end": right.replace("-", ""),
            "symbol_count": len(evidence_codes),
            "symbols": evidence_codes,
            "symbols_sha256": stable_scope_hash(evidence_codes),
        }

    for chunk_start, chunk_end in _calendar_year_chunks(start_date, end_date):
        request_kwargs = {
            "start_date": pd.Timestamp(chunk_start).strftime("%Y%m%d"),
            "end_date": pd.Timestamp(chunk_end).strftime("%Y%m%d"),
        }
        page = (
            _audited_paged_call(
                collector,
                endpoint="stk_holdernumber",
                dataset="shareholder_holdernumber",
                fields=("ann_date", "holder_num"),
                response_fields="ts_code,ann_date,end_date,holder_num",
                limit=3000,
                requested_scope=evidence_scope(chunk_start, chunk_end),
                request_variant=f"calendar_year:{chunk_start}:{chunk_end}",
                run_id=str(run_id),
                audit_store=audit_store,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                **request_kwargs,
            )
            if audited
            else _paged_call(collector.pro.stk_holdernumber, limit=3000, **request_kwargs)
        )
        holder_pages.append(page)
        holder_chunks.append(
            {
                "start_date": chunk_start,
                "end_date": chunk_end,
                "rows": len(page),
            }
        )
    holder_raw = (
        pd.concat(holder_pages, ignore_index=True) if holder_pages else pd.DataFrame()
    )
    top10_pages: list[pd.DataFrame] = []
    periods: list[str] = []
    top10_chunks: list[dict[str, Any]] = []
    if audited:
        # ``period`` selects a report period, while PIT availability is keyed by
        # ``ann_date``.  Exact announcement-date shards keep requested scope,
        # response metadata and downstream terminal proof on the same axis.
        for chunk_start, chunk_end in _calendar_year_chunks(start_date, end_date):
            chunk_rows = 0
            announcement_dates = pd.date_range(chunk_start, chunk_end, freq="D")
            for announcement in announcement_dates:
                requested_date = announcement.strftime("%Y-%m-%d")
                api_date = announcement.strftime("%Y%m%d")
                page = _audited_paged_call(
                    collector,
                    endpoint="top10_holders",
                    dataset="shareholder_top10",
                    fields=("ann_date", "hold_ratio"),
                    response_fields="ts_code,ann_date,end_date,holder_name,hold_ratio",
                    limit=6000,
                    requested_scope=evidence_scope(requested_date, requested_date),
                    request_variant=f"announcement_date:{api_date}",
                    run_id=str(run_id),
                    audit_store=audit_store,
                    resume_proof=resume_proof,
                    scope_key=scope_key,
                    universe=universe,
                    ann_date=api_date,
                )
                if page is None or page.empty:
                    continue
                if "ann_date" not in page.columns:
                    raise RuntimeError("top10_holders response missing ann_date")
                response_dates = {
                    _normalise_date(value) for value in page["ann_date"].tolist()
                }
                if response_dates != {requested_date}:
                    raise RuntimeError(
                        "top10_holders response escaped requested announcement date: "
                        f"requested={requested_date}, returned={sorted(response_dates, key=str)}"
                    )
                top10_pages.append(page)
                chunk_rows += len(page)
            top10_chunks.append({
                "start_date": chunk_start,
                "end_date": chunk_end,
                "request_count": len(announcement_dates),
                "rows": chunk_rows,
            })
    else:
        periods = _quarter_ends(start_date, end_date)
        for period in periods:
            page = _paged_call(
                collector.pro.top10_holders, limit=6000, period=period
            )
            if page is not None and not page.empty:
                top10_pages.append(page)
    top10_raw = (
        pd.concat(top10_pages, ignore_index=True) if top10_pages else pd.DataFrame()
    )
    if not top10_raw.empty:
        announced = top10_raw["ann_date"].map(_normalise_date)
        top10_raw = top10_raw[announced.between(start_date, end_date)].copy()
    return holder_raw, top10_raw, {
        "mode": "backfill",
        "start_date": start_date,
        "end_date": end_date,
        "quarter_periods": periods,
        "holder_chunks": holder_chunks,
        "top10_announcement_chunks": top10_chunks,
        "holder_source_rows": len(holder_raw),
        "top10_source_rows": len(top10_raw),
    }


def fetch_shareholder_incremental(
    collector: Any, *, start_date: str, end_date: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fetch every missed announcement date; empty dates are still audited."""

    holder_pages: list[pd.DataFrame] = []
    top10_pages: list[pd.DataFrame] = []
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    checked_dates: list[str] = []
    while current <= end:
        api_date = current.strftime("%Y%m%d")
        checked_dates.append(current.isoformat())
        holder = _paged_call(
            collector.pro.stk_holdernumber, limit=3000, ann_date=api_date
        )
        top10 = _paged_call(
            collector.pro.top10_holders, limit=6000, ann_date=api_date
        )
        if not holder.empty:
            holder_pages.append(holder)
        if not top10.empty:
            top10_pages.append(top10)
        current += timedelta(days=1)
    holder_raw = (
        pd.concat(holder_pages, ignore_index=True) if holder_pages else pd.DataFrame()
    )
    top10_raw = (
        pd.concat(top10_pages, ignore_index=True) if top10_pages else pd.DataFrame()
    )
    return holder_raw, top10_raw, {
        "mode": "incremental",
        "start_date": start_date,
        "end_date": end_date,
        "checked_dates": checked_dates,
        "holder_source_rows": len(holder_raw),
        "top10_source_rows": len(top10_raw),
    }


def run_shareholder_history_repair(
    *,
    symbols: Iterable[str],
    end_date: str,
    contract: dict[str, Any],
    apply: bool,
    output_dir: Path,
    project_root: Path | None = None,
    data_root: Path | None = None,
    collector: Any | None = None,
    start_date: str | None = None,
    required_history_start_date: str | None = None,
    run_id: str | None = None,
    audit_store: Any | None = None,
    resume_proof: dict[str, Any] | None = None,
    scope_key: str = "ad_hoc",
    evidence_universe: str = "ad_hoc",
    evidence_symbols: Iterable[str] = (),
) -> dict[str, Any]:
    """Repair canonical shareholder PIT sidecars and emit an immutable audit.

    ``required_history_start_date`` is the durable bootstrap contract.  The
    optional ``start_date`` remains a one-shot operator override and does not
    replace that contract in the persisted state.
    """

    resolved_data_root, _ = _resolve_data_root(
        project_root=project_root, data_root=data_root
    )
    canonical = resolved_data_root / "canonical"
    holder_path = canonical / HOLDER_FILENAME
    top10_path = canonical / TOP10_FILENAME
    state_path = canonical / STATE_FILENAME
    holder_before = normalise_holder_rows(
        pd.read_parquet(holder_path) if holder_path.is_file() else None
    )
    top10_before = normalise_top10_rows(
        pd.read_parquet(top10_path) if top10_path.is_file() else None
    )
    before = inspect_shareholder_sidecar_health(
        data_root=resolved_data_root,
        symbols=symbols,
        as_of_date=end_date,
        contract=contract,
    )
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state_before = dict(state)
    checked_through = _normalise_date(state.get("checked_through"))
    resolved_end = _normalise_date(end_date)
    explicit_start = _normalise_date(start_date) if start_date is not None else None
    required_start = (
        _normalise_date(required_history_start_date)
        if required_history_start_date is not None
        else explicit_start
    )
    if required_start is None and resolved_end is not None:
        required_start = (
            pd.Timestamp(resolved_end) - pd.Timedelta(days=550)
        ).strftime("%Y-%m-%d")
    state_history_start = _normalise_date(state.get("history_start_date"))
    bootstrap_required = (
        required_start is None
        or state_history_start is None
        or state_history_start > required_start
        or checked_through is None
    )
    if explicit_start is not None:
        resolved_start = explicit_start
        mode = "backfill"
    elif bootstrap_required:
        resolved_start = required_start
        mode = "backfill"
    elif checked_through:
        resolved_start = (
            date.fromisoformat(checked_through) + timedelta(days=1)
        ).isoformat()
        mode = "incremental"
    else:
        resolved_start = None
        mode = "backfill"
    if resolved_start is None or resolved_end is None or required_start is None:
        raise ValueError("shareholder repair requires valid start/end dates")
    summary: dict[str, Any] = {
        "status": (
            "healthy"
            if before["status"] == "pass" and not bootstrap_required
            else "planned"
        ),
        "apply": apply,
        "start_date": resolved_start,
        "end_date": resolved_end,
        "required_history_start_date": required_start,
        "bootstrap_required": bootstrap_required,
        "state_before": state_before,
        "state_after": state_before,
        "before": before,
        "fetch": {"mode": mode, "status": "skipped"},
        "rows_before": {"holder_num": len(holder_before), "top10": len(top10_before)},
    }
    if not apply:
        output_dir.mkdir(parents=True, exist_ok=True)
        audit_path = output_dir / "shareholder_repair_summary.json"
        _atomic_write_json(summary, audit_path)
        return {**summary, "summary_path": str(audit_path)}

    backup_dir = output_dir / "before"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source_path in (holder_path, top10_path, state_path):
        if source_path.is_file():
            shutil.copy2(source_path, backup_dir / source_path.name)
    summary["backup_dir"] = str(backup_dir)
    pending_state: dict[str, Any] | None = None
    if resolved_start > resolved_end:
        summary["status"] = (
            "success"
            if before["status"] == "pass" and not bootstrap_required
            else "failed"
        )
        summary["fetch"] = {"mode": mode, "status": "already_checked"}
    elif apply:
        if collector is None:
            from qsys.data.collector import TushareCollector

            collector = TushareCollector()
        try:
            if mode == "incremental":
                holder_raw, top10_raw, fetch = fetch_shareholder_incremental(
                    collector, start_date=resolved_start, end_date=resolved_end
                )
            else:
                evidence_kwargs = (
                    {
                        "run_id": run_id,
                        "audit_store": audit_store,
                        "resume_proof": resume_proof,
                        "scope_key": scope_key,
                        "universe": evidence_universe,
                        "evidence_symbols": evidence_symbols,
                    }
                    if run_id is not None or audit_store is not None or resume_proof is not None
                    else {}
                )
                holder_raw, top10_raw, fetch = fetch_shareholder_backfill(
                    collector, start_date=resolved_start, end_date=resolved_end,
                    **evidence_kwargs,
                )
            fetch["status"] = "success"
            summary["fetch"] = fetch
            holder_after = merge_shareholder_rows(
                holder_before, holder_raw, kind="holder_num"
            )
            top10_after = merge_shareholder_rows(
                top10_before, top10_raw, kind="top10"
            )
            _atomic_write_parquet(holder_after, holder_path)
            _atomic_write_parquet(top10_after, top10_path)
            pending_state = {
                "schema_version": 2,
                "checked_through": resolved_end,
                "history_start_date": min(
                    value
                    for value in (state_history_start, resolved_start)
                    if value is not None
                ),
                "last_successful_start": resolved_start,
                "last_successful_end": resolved_end,
                "last_successful_mode": mode,
                "holder_num_sha256": _file_sha256(holder_path),
                "top10_holder_ratio_sha256": _file_sha256(top10_path),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source": "tushare.stk_holdernumber+tushare.top10_holders",
                "availability_rule": "announcement_date_asof",
            }
        except Exception as exc:
            summary["status"] = "failed"
            summary["error"] = str(exc)

    after = inspect_shareholder_sidecar_health(
        data_root=resolved_data_root,
        symbols=symbols,
        as_of_date=resolved_end,
        contract=contract,
    )
    summary["after"] = after
    summary["rows_after"] = {
        "holder_num": len(
            normalise_holder_rows(pd.read_parquet(holder_path))
        ) if holder_path.is_file() else 0,
        "top10": len(
            normalise_top10_rows(pd.read_parquet(top10_path))
        ) if top10_path.is_file() else 0,
    }
    if summary.get("status") != "failed" and after["status"] != "pass":
        summary["status"] = "failed"
        summary["error"] = "shareholder sidecar freshness gate failed after repair"
    elif pending_state is not None and summary.get("status") != "failed":
        try:
            _atomic_write_json(pending_state, state_path)
            summary["state_after"] = pending_state
            summary["status"] = "success"
        except Exception as exc:
            summary["status"] = "failed"
            summary["error"] = str(exc)
    elif summary.get("status") not in {"failed", "healthy"}:
        summary["status"] = "success" if after["status"] == "pass" else "failed"
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "shareholder_repair_summary.json"
    _atomic_write_json(summary, audit_path)
    return {**summary, "summary_path": str(audit_path)}
