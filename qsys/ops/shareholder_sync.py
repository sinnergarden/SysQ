"""Canonical PIT shareholder sidecar sync, health, and impact audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


HOLDER_FILENAME = "holder_num.parquet"
TOP10_FILENAME = "top10_holder_ratio.parquet"
STATE_FILENAME = "shareholder_sync_state.json"
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


def normalise_holder_rows(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return one canonical holder-count row per instrument/announcement."""

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
    out = out.dropna(subset=["inst", "ann_date", "holder_num"])
    out = out[(out["inst"] != "") & (out["holder_num"] > 0)]
    out = out.sort_values(
        ["inst", "ann_date", "end_date"], kind="mergesort", na_position="first"
    )
    out = out.drop_duplicates(["inst", "ann_date"], keep="last")
    return out[columns].reset_index(drop=True)


def normalise_top10_rows(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Aggregate raw Tushare top-ten holders into one PIT ratio per announcement."""

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
        out = out.dropna(subset=["inst", "ann_date", "top10_ratio"])
        out = out[(out["inst"] != "") & out["top10_ratio"].between(0, 100)]
        out = out.sort_values(
            ["inst", "ann_date", "end_date"],
            kind="mergesort",
            na_position="first",
        ).drop_duplicates(["inst", "ann_date"], keep="last")
        return out[columns].reset_index(drop=True)

    required = {"inst", "ann_date", "end_date", "hold_ratio"}
    if not required.issubset(out.columns):
        return pd.DataFrame(columns=columns)
    out["inst"] = out["inst"].astype(str).str.strip().str.upper()
    out["ann_date"] = out["ann_date"].map(_normalise_date)
    out["end_date"] = out["end_date"].map(_normalise_date)
    out["hold_ratio"] = pd.to_numeric(out["hold_ratio"], errors="coerce")
    out = out.dropna(subset=["inst", "ann_date", "end_date", "hold_ratio"])
    if "holder_name" in out.columns:
        out["holder_name"] = out["holder_name"].astype(str)
        out = out.drop_duplicates(
            ["inst", "ann_date", "end_date", "holder_name"], keep="last"
        )
    grouped = (
        out.groupby(["inst", "ann_date", "end_date"], as_index=False)["hold_ratio"]
        .sum(min_count=1)
        .rename(columns={"hold_ratio": "top10_ratio"})
    )
    grouped = grouped[grouped["top10_ratio"].between(0, 100.0001)].copy()
    grouped["top10_ratio"] = grouped["top10_ratio"].clip(upper=100.0)
    grouped = grouped.sort_values(
        ["inst", "ann_date", "end_date"], kind="mergesort"
    ).drop_duplicates(["inst", "ann_date"], keep="last")
    return grouped[columns].reset_index(drop=True)


def merge_shareholder_rows(
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame | None,
    *,
    kind: str,
) -> pd.DataFrame:
    normaliser = normalise_holder_rows if kind == "holder_num" else normalise_top10_rows
    return normaliser(pd.concat([normaliser(existing), normaliser(incoming)], ignore_index=True))


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
    periods = _quarter_ends(start_date, end_date)
    for period in periods:
        quarter = pd.Period(period, freq="Q-DEC")
        quarter_start = quarter.start_time.strftime("%Y-%m-%d")
        quarter_end = quarter.end_time.strftime("%Y-%m-%d")
        page = (
            _audited_paged_call(
                collector,
                endpoint="top10_holders",
                dataset="shareholder_top10",
                fields=("ann_date", "hold_ratio"),
                response_fields="ts_code,ann_date,end_date,holder_name,hold_ratio",
                limit=6000,
                requested_scope=evidence_scope(quarter_start, quarter_end),
                request_variant=f"report_period:{period}",
                run_id=str(run_id),
                audit_store=audit_store,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                period=period,
            )
            if audited
            else _paged_call(collector.pro.top10_holders, limit=6000, period=period)
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
