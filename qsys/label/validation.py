"""Independent validation for lineage-bound executable label suites."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import yaml


_BASE_IDENTITY_KEYS = (
    "suite_id",
    "config_sha256",
    "source_artifacts",
    "pit_universe_artifact",
    "universe_membership_sha256",
    "universe_snapshot_hash",
    "universe_raw_source_sha256",
    "universe_manifest_sha256",
    "universe_registry_sha256",
    "label_compute_code_sha256",
    "label_store_code_sha256",
    "producer_entrypoint_code_sha256",
    "git_commit_full",
)
_SEMANTIC_KEYS = (
    "horizon",
    "return_type",
    "price_basis",
    "signal_cutoff_contract",
    "entry_eligibility_contract",
    "exit_observation_contract",
    "exit_status_basis",
    "future_exit_status_used_for_filter",
    "corporate_action_adjustment_contract",
    "data_cutoff",
    "mature_row_count",
    "entry_eligible_row_count",
    "valid_observed_row_count",
    "missing_target_price_row_count",
    "missing_entry_price_row_count",
)
_REQUIRED_COLUMNS = {
    "trade_date",
    "label_date",
    "instrument",
    "label_id",
    "horizon",
    "shift",
    "return_type",
    "price_basis",
    "signal_data_cutoff",
    "return_start_date",
    "return_start_price",
    "return_end_date",
    "return_end_price",
    "maturity_date",
    "is_mature",
    "entry_eligible",
    "exit_execution_status",
    "label_value",
    "universe",
    "is_valid",
    "invalid_reason",
    "label_missing_reason",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _resolve_under(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact path escapes research root: {relative}") from exc
    return candidate


def _git_blob(project_root: Path, commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        cwd=project_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"cannot read producer blob {commit}:{relative_path}: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def _expected_label_ids(config: dict[str, Any]) -> list[str]:
    suite = config["label_suite"]
    horizons = sorted(set(int(value) for value in suite["horizons"]))
    templates = (
        str(suite["primary_label_template"]),
        str(suite["secondary_label_template"]),
    )
    return sorted(
        template.format(horizon=horizon)
        for horizon in horizons
        for template in templates
    )


def _date_map(
    calendar: list[str],
    start: str,
    cutoff: str,
    horizon: int,
) -> pd.DataFrame:
    bounded = [date for date in calendar if date <= cutoff]
    positions = {date: idx for idx, date in enumerate(bounded)}
    previous = {
        date: bounded[idx - 1] if idx > 0 else None
        for idx, date in enumerate(bounded)
    }
    dates = [date for date in bounded if start <= date <= cutoff]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "expected_signal_cutoff": [previous[date] for date in dates],
            "expected_end": [
                bounded[positions[date] + horizon]
                if positions[date] + horizon < len(bounded)
                else None
                for date in dates
            ],
        }
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_executable_label_suite(
    *,
    suite_manifest_path: str | Path,
    config_path: str | Path,
    data_root: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Validate exact bytes, lineage, and full-row executable semantics."""
    suite_manifest_path = Path(suite_manifest_path).resolve()
    config_path = Path(config_path).resolve()
    data_root = Path(data_root).resolve()
    output_path = Path(output_path).resolve()
    research_root = suite_manifest_path.parents[2]
    project_root = Path(__file__).resolve().parents[2]

    suite = json.loads(suite_manifest_path.read_text(encoding="utf-8"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    checks: dict[str, Any] = {}

    def check(name: str, condition: bool, detail: Any) -> None:
        checks[name] = {"passed": bool(condition), "detail": detail}
        if not condition:
            failures.append(name)

    expected_label_ids = _expected_label_ids(config)
    check(
        "configured_output_set",
        suite.get("output_count") == len(expected_label_ids)
        and suite.get("label_ids") == expected_label_ids,
        {"expected": expected_label_ids, "actual": suite.get("label_ids")},
    )
    check(
        "config_sha256",
        suite.get("config_sha256") == _canonical_sha256(config),
        suite.get("config_sha256"),
    )

    base_identity = {key: suite.get(key) for key in _BASE_IDENTITY_KEYS}
    expected_suite_identity = _canonical_sha256(
        {**base_identity, "label_ids": expected_label_ids}
    )
    check(
        "suite_identity",
        suite.get("label_suite_identity_sha256") == expected_suite_identity,
        suite.get("label_suite_identity_sha256"),
    )

    source_results: dict[str, Any] = {}
    for name, artifact in sorted(suite.get("source_artifacts", {}).items()):
        path = Path(str(artifact["path"]))
        if not path.is_absolute():
            path = data_root / path
        actual = _sha256_file(path) if path.is_file() else None
        source_results[name] = {
            "exists": path.is_file(),
            "expected_sha256": artifact.get("sha256"),
            "actual_sha256": actual,
        }
    check(
        "source_artifacts",
        bool(source_results)
        and all(
            item["exists"]
            and item["actual_sha256"] == item["expected_sha256"]
            for item in source_results.values()
        ),
        source_results,
    )

    pit_dir = (
        data_root
        / "research"
        / "universes"
        / str(suite["pit_universe_artifact"])
    )
    pit_manifest_path = pit_dir / "manifest.json"
    membership_path = pit_dir / "membership.parquet"
    pit_manifest = json.loads(pit_manifest_path.read_text(encoding="utf-8"))
    universe = str(config["universe"])
    registry_path = data_root / "qlib_bin" / "instruments" / f"{universe}.txt"
    lineage_results = {
        "universe_manifest_sha256": _sha256_file(pit_manifest_path),
        "universe_membership_sha256": _sha256_file(membership_path),
        "universe_registry_sha256": _sha256_file(registry_path),
        "universe_raw_source_sha256": pit_manifest.get("raw_source_hash"),
        "universe_snapshot_hash": pit_manifest.get("raw_source_hash"),
    }
    check(
        "pit_lineage",
        all(suite.get(key) == value for key, value in lineage_results.items())
        and pit_manifest.get("membership_sha256")
        == lineage_results["universe_membership_sha256"]
        and pit_manifest.get("registry_sha256")
        == lineage_results["universe_registry_sha256"],
        lineage_results,
    )

    producer_commit = str(suite["git_commit_full"])
    producer_files = {
        "label_compute_code_sha256": "qsys/label/compute.py",
        "label_store_code_sha256": "qsys/label/store.py",
        "producer_entrypoint_code_sha256": "scripts/research/compute_labels.py",
    }
    producer_hashes = {
        key: _sha256_bytes(_git_blob(project_root, producer_commit, relative))
        for key, relative in producer_files.items()
    }
    check(
        "producer_code",
        all(suite.get(key) == value for key, value in producer_hashes.items()),
        producer_hashes,
    )

    calendar_path = data_root / "qlib_bin" / "calendars" / "day.txt"
    calendar = [
        line.strip()
        for line in calendar_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    start = str(config["date_range"]["start_date"])
    cutoff = str(config["date_range"]["data_cutoff"])

    import duckdb

    connection = duckdb.connect(database=":memory:")
    connection.from_parquet(str(membership_path)).create_view(
        "pit_membership_raw"
    )
    connection.execute(
        """
        CREATE TEMP VIEW pit_membership AS
        SELECT instrument,
               strftime(strptime(effective_from, '%Y%m%d'), '%Y-%m-%d') AS effective_from,
               strftime(strptime(effective_to, '%Y%m%d'), '%Y-%m-%d') AS effective_to
        FROM pit_membership_raw
        """
    )
    output_summaries: list[dict[str, Any]] = []
    row_key_fingerprint: tuple[int, int, int] | None = None
    output_records = {
        str(item["label_id"]): item for item in suite.get("outputs", [])
    }

    aggregate_sql = """
        SELECT
            count(*)::BIGINT AS row_count,
            count(DISTINCT (trade_date, instrument))::BIGINT AS unique_keys,
            count(DISTINCT trade_date)::BIGINT AS n_dates,
            count(DISTINCT instrument)::BIGINT AS n_instruments,
            min(trade_date) AS min_date,
            max(trade_date) AS max_date,
            bit_xor(hash(trade_date, instrument))::UBIGINT AS key_xor,
            sum(hash(trade_date, instrument))::HUGEINT AS key_sum,
            count_if(label_id IS DISTINCT FROM ?)::BIGINT AS label_id_errors,
            count_if(horizon IS DISTINCT FROM ?)::BIGINT AS horizon_errors,
            count_if(shift IS DISTINCT FROM ?)::BIGINT AS shift_errors,
            count_if(return_type IS DISTINCT FROM ?)::BIGINT AS return_type_errors,
            count_if(universe IS DISTINCT FROM ?)::BIGINT AS universe_errors,
            count_if(label_date IS DISTINCT FROM trade_date)::BIGINT AS label_date_errors,
            count_if(return_start_date IS DISTINCT FROM trade_date)::BIGINT AS start_date_errors,
            count_if(signal_data_cutoff IS DISTINCT FROM expected_signal_cutoff)::BIGINT AS cutoff_errors,
            count_if(return_end_date IS DISTINCT FROM expected_end)::BIGINT AS end_date_errors,
            count_if(maturity_date IS DISTINCT FROM expected_end)::BIGINT AS maturity_date_errors,
            count_if(is_mature IS DISTINCT FROM (expected_end IS NOT NULL))::BIGINT AS maturity_errors,
            count_if(is_valid IS DISTINCT FROM (entry_eligible AND is_mature))::BIGINT AS validity_errors,
            count_if(
                label_missing_reason IS DISTINCT FROM CASE
                    WHEN expected_end IS NULL THEN 'immature'
                    WHEN return_start_price IS NULL OR NOT isfinite(return_start_price) OR return_start_price <= 0
                        THEN 'entry_price_unobserved'
                    WHEN return_end_price IS NULL OR NOT isfinite(return_end_price) OR return_end_price <= 0
                        THEN 'target_price_unobserved'
                    WHEN NOT isfinite(return_end_price / return_start_price - 1.0)
                        THEN 'nonfinite_return'
                    ELSE ''
                END
            )::BIGINT AS missing_reason_errors,
            count_if(
                CASE
                    WHEN expected_end IS NULL
                      OR return_start_price IS NULL
                      OR NOT isfinite(return_start_price)
                      OR return_start_price <= 0
                      OR return_end_price IS NULL
                      OR NOT isfinite(return_end_price)
                      OR return_end_price <= 0
                      OR NOT isfinite(return_end_price / return_start_price - 1.0)
                    THEN label_value IS NOT NULL
                    ELSE label_value IS NULL OR abs(
                        cast(label_value AS DOUBLE)
                        - (cast(return_end_price AS DOUBLE) / cast(return_start_price AS DOUBLE) - 1.0)
                    ) > 0.000002 * greatest(
                        1.0,
                        abs(cast(return_end_price AS DOUBLE) / cast(return_start_price AS DOUBLE) - 1.0)
                    )
                END
            )::BIGINT AS formula_errors,
            count_if(is_mature)::BIGINT AS mature_rows,
            count_if(entry_eligible)::BIGINT AS entry_eligible_rows,
            count_if(is_valid AND label_value IS NOT NULL)::BIGINT AS valid_observed_rows,
            count_if(label_missing_reason = 'target_price_unobserved')::BIGINT AS missing_target_rows,
            count_if(label_missing_reason = 'entry_price_unobserved')::BIGINT AS missing_entry_rows
        FROM read_parquet(?) labels
        LEFT JOIN date_map USING (trade_date)
    """

    for label_id in expected_label_ids:
        record = output_records.get(label_id, {})
        data_path = _resolve_under(research_root, str(record.get("data_path", "")))
        manifest_path = _resolve_under(
            research_root, str(record.get("manifest_path", ""))
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        parquet = pq.ParquetFile(data_path)
        columns = set(parquet.schema_arrow.names)
        horizon = int(manifest["horizon"])
        return_type = str(manifest["return_type"])
        mapping = _date_map(calendar, start, cutoff, horizon)
        connection.register("date_map", mapping)
        row = connection.execute(
            aggregate_sql,
            [
                label_id,
                horizon,
                horizon,
                return_type,
                universe,
                str(data_path),
            ],
        ).fetchone()
        names = [item[0] for item in connection.description]
        stats = dict(zip(names, row))
        fingerprint = (
            int(stats["row_count"]),
            int(stats["key_xor"]),
            int(stats["key_sum"]),
        )
        if row_key_fingerprint is None:
            row_key_fingerprint = fingerprint
        semantic_payload = {key: manifest.get(key) for key in _SEMANTIC_KEYS}
        expected_label_identity = _canonical_sha256(
            {
                **base_identity,
                "label_id": label_id,
                "semantics": semantic_payload,
            }
        )
        errors = {
            key: int(value)
            for key, value in stats.items()
            if key.endswith("_errors")
        }
        manifest_counts_match = (
            int(record["row_count"]) == stats["row_count"]
            and int(manifest["row_count"]) == stats["row_count"]
            and int(manifest["n_dates"]) == stats["n_dates"]
            and int(manifest["n_instruments"]) == stats["n_instruments"]
            and int(manifest["mature_row_count"]) == stats["mature_rows"]
            and int(manifest["entry_eligible_row_count"])
            == stats["entry_eligible_rows"]
            and int(manifest["valid_observed_row_count"])
            == stats["valid_observed_rows"]
            and int(manifest["missing_target_price_row_count"])
            == stats["missing_target_rows"]
            and int(manifest["missing_entry_price_row_count"])
            == stats["missing_entry_rows"]
        )
        passed = (
            _REQUIRED_COLUMNS <= columns
            and _sha256_file(data_path) == record.get("labels_sha256")
            and manifest.get("labels_sha256") == record.get("labels_sha256")
            and _sha256_file(manifest_path) == record.get("manifest_sha256")
            and parquet.metadata.num_rows == stats["row_count"]
            and stats["row_count"] == stats["unique_keys"]
            and stats["min_date"] == start
            and stats["max_date"] == cutoff
            and fingerprint == row_key_fingerprint
            and all(value == 0 for value in errors.values())
            and manifest_counts_match
            and manifest.get("label_suite_identity_sha256")
            == expected_suite_identity
            and manifest.get("label_identity_sha256")
            == expected_label_identity
            and record.get("label_identity_sha256")
            == expected_label_identity
            and all(
                manifest.get(key) == value
                for key, value in base_identity.items()
            )
            and manifest.get("future_exit_status_used_for_filter") is False
        )
        check(f"output:{label_id}", passed, errors)
        output_summaries.append(
            {
                "label_id": label_id,
                "passed": passed,
                "row_count": int(stats["row_count"]),
                "n_dates": int(stats["n_dates"]),
                "n_instruments": int(stats["n_instruments"]),
                "min_date": stats["min_date"],
                "max_date": stats["max_date"],
                "valid_observed_rows": int(stats["valid_observed_rows"]),
                "entry_eligible_rows": int(stats["entry_eligible_rows"]),
                "mature_rows": int(stats["mature_rows"]),
                "row_key_fingerprint": [str(value) for value in fingerprint],
                "errors": errors,
            }
        )
        connection.unregister("date_map")

    primary_path = _resolve_under(
        research_root,
        str(output_records[expected_label_ids[0]]["data_path"]),
    )
    nonmember_count = connection.execute(
        """
        SELECT count(*)::BIGINT
        FROM read_parquet(?) labels
        WHERE NOT EXISTS (
            SELECT 1
            FROM pit_membership membership
            WHERE membership.instrument = labels.instrument
              AND labels.trade_date BETWEEN membership.effective_from
                                        AND membership.effective_to
        )
        """,
        [str(primary_path)],
    ).fetchone()[0]
    check("pit_membership_anti_join", nonmember_count == 0, int(nonmember_count))
    connection.close()

    try:
        validator_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        validator_commit = "unknown"
    report_core = {
        "artifact_type": "label_suite_validation",
        "status": "passed" if not failures else "failed",
        "suite_id": suite.get("suite_id"),
        "input_suite_manifest": str(
            suite_manifest_path.relative_to(project_root)
        ),
        "input_suite_manifest_sha256": _sha256_file(suite_manifest_path),
        "label_suite_identity_sha256": expected_suite_identity,
        "validator_code_sha256": _sha256_file(Path(__file__)),
        "validator_git_commit_full": validator_commit,
        "calendar_sha256": _sha256_file(calendar_path),
        "checks": checks,
        "failures": failures,
        "outputs": output_summaries,
    }
    report = {
        **report_core,
        "validation_identity_sha256": _canonical_sha256(report_core),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _atomic_write_json(output_path, report)
    if failures:
        raise ValueError(
            "label suite validation failed: " + ", ".join(failures)
        )
    return report
