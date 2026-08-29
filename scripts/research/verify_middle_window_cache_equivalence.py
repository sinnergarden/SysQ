#!/usr/bin/env python3
"""Prove annual feature-cache equivalence on selected rolling windows.

This is a diagnostic supporting tool.  It never reads from or writes to the
official window-checkpoint store except for a final read-only comparison.
Each direct/cache stage is hash-bound so an interrupted run can resume from
the last complete stage.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

import duckdb
import numpy as np
import pandas as pd

from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    expand_multi_label_generators,
)
from qsys.research.rolling_window import build_rolling_windows


DEFAULT_CONFIG = Path(
    "configs/research/60d/"
    "financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_terminal_r2.yaml"
)
DEFAULT_POSITIONS = (28, 34, 40)
ABS_TOLERANCE = 1e-12


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _write_parquet_idempotent(frame: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".parquet.tmp", dir=path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        frame.to_parquet(temp_path, index=False)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        new_hash = _sha256(temp_path)
        if path.exists():
            if _sha256(path) != new_hash:
                raise RuntimeError(
                    f"Refusing to replace non-matching diagnostic artifact: {path}"
                )
            temp_path.unlink()
        else:
            os.replace(temp_path, path)
        return new_hash
    finally:
        if temp_path.exists():
            temp_path.unlink()


class _Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        self.parts.append(text)
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    @property
    def text(self) -> str:
        return "".join(self.parts)


def _stage_valid(
    stage_path: Path,
    identity: dict[str, Any],
    *,
    allow_prior_verifier: bool = False,
) -> dict[str, Any] | None:
    if not stage_path.is_file():
        return None
    payload = json.loads(stage_path.read_text(encoding="utf-8"))
    actual_identity = payload.get("identity")
    if actual_identity != identity:
        comparable_actual = dict(actual_identity or {})
        comparable_expected = dict(identity)
        comparable_actual.pop("script_sha256", None)
        comparable_expected.pop("script_sha256", None)
        if not allow_prior_verifier or comparable_actual != comparable_expected:
            return None
    for artifact in payload.get("artifacts", {}).values():
        path = Path(artifact["path"])
        if not path.is_file() or _sha256(path) != artifact["sha256"]:
            return None
    return payload


def _run_mode(
    *,
    mode: str,
    window: Any,
    window_position: int,
    total_windows: int,
    gen_config: dict[str, Any],
    config: RollingResearchConfig,
    config_hash: str,
    script_hash: str,
    window_dir: Path,
    allow_prior_verifier: bool = False,
) -> dict[str, Any]:
    identity = {
        "schema_version": "middle_window_cache_equivalence_stage_v1",
        "mode": mode,
        "window_position": window_position,
        "window": asdict(window),
        "config_sha256": config_hash,
        "script_sha256": script_hash,
        "source_manifest_hash": config.source_manifest_hash,
    }
    stage_path = window_dir / f"{mode}.stage.json"
    complete = _stage_valid(
        stage_path, identity, allow_prior_verifier=allow_prior_verifier
    )
    if complete is not None:
        print(
            f"[{window_position}/{total_windows}][{mode}] validated checkpoint reuse",
            flush=True,
        )
        return complete

    feature_path = window_dir / f"{mode}.loaded_features.parquet"
    prediction_path = window_dir / f"{mode}.predictions.parquet"
    use_cache = mode == "cache"
    generator = _create_generator_from_config(
        gen_config,
        feature_list_id=config.feature_list_id,
        use_feature_cache=use_cache,
        write_through=False,
        feature_cache_root=config.feature_cache_root,
        source_manifest_hash=config.source_manifest_hash,
    )

    original_load = generator._load_data
    load_meta: dict[str, Any] = {}

    def load_and_capture(start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        load_started = time.monotonic()
        frame, features = original_load(start, end)
        load_seconds = time.monotonic() - load_started
        print(
            f"[{window_position}/{total_windows}][{mode}] loaded "
            f"{len(frame):,} rows x {len(features)} consumed features "
            f"in {load_seconds:.1f}s; persisting comparison frame",
            flush=True,
        )
        feature_hash = _write_parquet_idempotent(frame, feature_path)
        load_meta.update(
            {
                "requested_start": start,
                "requested_end": end,
                "row_count": int(len(frame)),
                "columns": list(frame.columns),
                "consumed_features": list(features),
                "load_seconds": load_seconds,
                "feature_frame_sha256": feature_hash,
            }
        )
        return frame, features

    generator._load_data = load_and_capture
    print(
        f"[{window_position}/{total_windows}][{mode}] starting "
        f"train={window.train_start}..{window.train_end} "
        f"predict={window.predict_start}..{window.predict_end}",
        flush=True,
    )
    started = time.monotonic()
    tee = _Tee(sys.stdout)
    with redirect_stdout(tee):
        predictions = generator.generate(
            train_start=window.train_start,
            train_end=window.train_end,
            predict_start=window.predict_start,
            predict_end=window.predict_end,
            signal_id="__cache_equivalence_diagnostic__",
            signal_run_id="__cache_equivalence_diagnostic__",
        )
    elapsed = time.monotonic() - started
    prediction_hash = _write_parquet_idempotent(predictions, prediction_path)
    match = re.search(r"Train RankIC=([-+0-9.eE]+), trees=(\d+)", tee.text)
    training = {
        "rank_ic": float(match.group(1)) if match else None,
        "tree_count": int(match.group(2)) if match else None,
        "elapsed_seconds": elapsed,
    }
    payload = {
        "identity": identity,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "load": load_meta,
        "training": training,
        "prediction_rows": int(len(predictions)),
        "prediction_trade_dates": int(predictions["trade_date"].nunique()),
        "artifacts": {
            "loaded_features": {
                "path": str(feature_path.resolve()),
                "sha256": load_meta["feature_frame_sha256"],
            },
            "predictions": {
                "path": str(prediction_path.resolve()),
                "sha256": prediction_hash,
            },
        },
    }
    _write_json_atomic(stage_path, payload)
    print(
        f"[{window_position}/{total_windows}][{mode}] complete in {elapsed:.1f}s; "
        f"RankIC={training['rank_ic']} trees={training['tree_count']} "
        f"predictions={len(predictions):,}",
        flush=True,
    )
    del predictions, generator
    gc.collect()
    return payload


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _compare_features(
    direct_path: Path,
    cache_path: Path,
    features: list[str],
    temp_dir: Path,
) -> dict[str, Any]:
    connection = duckdb.connect()
    connection.execute(f"SET temp_directory='{str(temp_dir).replace(chr(39), chr(39)*2)}'")
    connection.execute("SET preserve_insertion_order=false")
    direct_sql = str(direct_path).replace("'", "''")
    cache_sql = str(cache_path).replace("'", "''")
    connection.execute(
        f"CREATE VIEW direct_frame AS SELECT * FROM read_parquet('{direct_sql}')"
    )
    connection.execute(
        f"CREATE VIEW cache_frame AS SELECT * FROM read_parquet('{cache_sql}')"
    )
    direct_rows, direct_distinct = connection.execute(
        "SELECT count(*), count(DISTINCT (instrument, trade_date)) FROM direct_frame"
    ).fetchone()
    cache_rows, cache_distinct = connection.execute(
        "SELECT count(*), count(DISTINCT (instrument, trade_date)) FROM cache_frame"
    ).fetchone()
    direct_only = connection.execute(
        "SELECT count(*) FROM direct_frame d ANTI JOIN cache_frame c "
        "USING (instrument, trade_date)"
    ).fetchone()[0]
    cache_only = connection.execute(
        "SELECT count(*) FROM cache_frame c ANTI JOIN direct_frame d "
        "USING (instrument, trade_date)"
    ).fetchone()[0]
    if direct_rows != direct_distinct or cache_rows != cache_distinct:
        raise RuntimeError("Duplicate feature keys prevent one-to-one comparison")
    if direct_only or cache_only:
        return {
            "direct_rows": direct_rows,
            "cache_rows": cache_rows,
            "direct_duplicate_keys": direct_rows - direct_distinct,
            "cache_duplicate_keys": cache_rows - cache_distinct,
            "direct_only_keys": direct_only,
            "cache_only_keys": cache_only,
            "status": "fail",
            "feature_metrics": [],
        }

    expressions: list[str] = []
    aliases: list[tuple[str, str]] = []
    for index, feature in enumerate(features):
        column = _quoted(feature)
        d = f"d.{column}"
        c = f"c.{column}"
        d_missing = f"({d} IS NULL OR isnan({d}))"
        c_missing = f"({c} IS NULL OR isnan({c}))"
        both_finite = f"(isfinite({d}) AND isfinite({c}))"
        specs = {
            "direct_missing": f"sum(CASE WHEN {d_missing} THEN 1 ELSE 0 END)",
            "cache_missing": f"sum(CASE WHEN {c_missing} THEN 1 ELSE 0 END)",
            "missing_mask_mismatch": (
                f"sum(CASE WHEN {d_missing} <> {c_missing} THEN 1 ELSE 0 END)"
            ),
            "finite_exact_mismatch": (
                f"sum(CASE WHEN {both_finite} AND {d} <> {c} THEN 1 ELSE 0 END)"
            ),
            "finite_tolerance_mismatch": (
                f"sum(CASE WHEN {both_finite} AND abs({d} - {c}) > "
                f"{ABS_TOLERANCE:.17g} THEN 1 ELSE 0 END)"
            ),
            "nonfinite_mismatch": (
                f"sum(CASE WHEN NOT {d_missing} AND NOT {c_missing} "
                f"AND NOT ({both_finite}) AND {d} <> {c} THEN 1 ELSE 0 END)"
            ),
            "max_abs_diff": (
                f"max(CASE WHEN {both_finite} THEN abs({d} - {c}) ELSE NULL END)"
            ),
        }
        for metric, expression in specs.items():
            alias = f"m{index}_{metric}"
            expressions.append(f"{expression} AS {_quoted(alias)}")
            aliases.append((feature, metric))
    row = connection.execute(
        "SELECT " + ",".join(expressions) + " FROM direct_frame d "
        "JOIN cache_frame c USING (instrument, trade_date)"
    ).fetchone()
    connection.close()

    by_feature: dict[str, dict[str, Any]] = {feature: {} for feature in features}
    for (feature, metric), value in zip(aliases, row, strict=True):
        if metric == "max_abs_diff":
            by_feature[feature][metric] = 0.0 if value is None else float(value)
        else:
            by_feature[feature][metric] = int(value or 0)
    failing_features = [
        feature
        for feature, metrics in by_feature.items()
        if metrics["missing_mask_mismatch"]
        or metrics["finite_tolerance_mismatch"]
        or metrics["nonfinite_mismatch"]
    ]
    return {
        "direct_rows": int(direct_rows),
        "cache_rows": int(cache_rows),
        "direct_duplicate_keys": int(direct_rows - direct_distinct),
        "cache_duplicate_keys": int(cache_rows - cache_distinct),
        "direct_only_keys": int(direct_only),
        "cache_only_keys": int(cache_only),
        "absolute_tolerance": ABS_TOLERANCE,
        "feature_count": len(features),
        "failing_features": failing_features,
        "max_abs_diff": max(
            metrics["max_abs_diff"] for metrics in by_feature.values()
        ),
        "finite_exact_mismatch_cells": sum(
            metrics["finite_exact_mismatch"] for metrics in by_feature.values()
        ),
        "finite_tolerance_mismatch_cells": sum(
            metrics["finite_tolerance_mismatch"] for metrics in by_feature.values()
        ),
        "missing_mask_mismatch_cells": sum(
            metrics["missing_mask_mismatch"] for metrics in by_feature.values()
        ),
        "nonfinite_mismatch_cells": sum(
            metrics["nonfinite_mismatch"] for metrics in by_feature.values()
        ),
        "feature_metrics": [
            {"feature": feature, **metrics}
            for feature, metrics in by_feature.items()
        ],
        "status": "pass" if not failing_features else "fail",
    }


def _ordered_instruments(frame: pd.DataFrame) -> dict[str, list[str]]:
    ordered: dict[str, list[str]] = {}
    for trade_date, group in frame.groupby("trade_date", sort=True):
        group = group.sort_values(
            ["score", "instrument"], ascending=[False, True], kind="mergesort"
        )
        ordered[str(trade_date)] = group["instrument"].astype(str).tolist()
    return ordered


def _compare_predictions(left_path: Path, right_path: Path) -> dict[str, Any]:
    keys = ["trade_date", "data_date", "instrument"]
    left = pd.read_parquet(left_path).sort_values(keys).reset_index(drop=True)
    right = pd.read_parquet(right_path).sort_values(keys).reset_index(drop=True)
    keys_equal = left[keys].equals(right[keys])
    metrics: dict[str, Any] = {
        "left_rows": int(len(left)),
        "right_rows": int(len(right)),
        "keys_exact": bool(keys_equal),
    }
    if not keys_equal:
        metrics["status"] = "fail"
        return metrics
    for column in ("score_model_raw", "score"):
        diff = np.abs(
            left[column].to_numpy(dtype=float) - right[column].to_numpy(dtype=float)
        )
        metrics[f"{column}_max_abs_diff"] = float(diff.max(initial=0.0))
        metrics[f"{column}_mismatch_gt_tolerance"] = int(
            (diff > ABS_TOLERANCE).sum()
        )
    left_orders = _ordered_instruments(left)
    right_orders = _ordered_instruments(right)
    dates = sorted(set(left_orders) | set(right_orders))
    full_order_exact = sum(left_orders.get(d) == right_orders.get(d) for d in dates)
    top5_exact = sum(
        left_orders.get(d, [])[:5] == right_orders.get(d, [])[:5] for d in dates
    )
    metrics.update(
        {
            "trade_dates": len(dates),
            "full_daily_order_exact_dates": full_order_exact,
            "top5_exact_dates": top5_exact,
            "status": "pass"
            if all(
                (
                    metrics["score_model_raw_mismatch_gt_tolerance"] == 0,
                    metrics["score_mismatch_gt_tolerance"] == 0,
                    full_order_exact == len(dates),
                    top5_exact == len(dates),
                )
            )
            else "fail",
        }
    )
    return metrics


def _official_checkpoint(
    checkpoint_dir: Path, window_id: str
) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in checkpoint_dir.glob("*.manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("identity", {}).get("window", {}).get("window_id") == window_id:
            matches.append((manifest_path, manifest))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one official checkpoint for {window_id}, found {len(matches)}"
        )
    manifest_path, manifest = matches[0]
    prediction_path = checkpoint_dir / manifest["predictions_file"]
    if _sha256(prediction_path) != manifest["predictions_sha256"]:
        raise RuntimeError(f"Official checkpoint hash mismatch: {prediction_path}")
    return prediction_path, {
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "predictions_path": str(prediction_path.resolve()),
        "predictions_sha256": manifest["predictions_sha256"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--window-positions",
        type=int,
        nargs="+",
        default=list(DEFAULT_POSITIONS),
        help="One-based positions in the canonical rolling-window list",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/research/cache_equivalence"),
    )
    parser.add_argument(
        "--resume-run-dir",
        type=Path,
        help=(
            "Explicit interrupted run directory. Reuse is allowed only when "
            "all generation identity fields except verifier script hash match."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config_path = args.config.resolve()
    config_hash = _sha256(config_path)
    script_hash = _sha256(Path(__file__).resolve())
    config = RollingResearchConfig.from_file(config_path)
    lag = max(
        (entry.get("label_maturity_lag_trading_days") or 0)
        for entry in config.labels
    )
    windows = build_rolling_windows(
        config.calendar["start_date"],
        config.calendar["end_date"],
        train_window_days=config.calendar["train_window_days"],
        step_days=config.calendar["step_days"],
        label_maturity_lag_trading_days=lag,
    )
    positions = list(dict.fromkeys(args.window_positions))
    if len(positions) < 2 or any(position < 1 or position > len(windows) for position in positions):
        raise ValueError(
            f"Choose at least two one-based positions in [1, {len(windows)}]"
        )
    generators = expand_multi_label_generators(config.generators)
    if len(generators) != 1:
        raise ValueError(f"Expected one expanded generator, found {len(generators)}")
    gen_config = generators[0]
    run_identity = hashlib.sha256(
        _canonical_json(
            {
                "config_sha256": config_hash,
                "script_sha256": script_hash,
                "positions": positions,
                "source_manifest_hash": config.source_manifest_hash,
            }
        )
    ).hexdigest()[:16]
    run_dir = (
        args.resume_run_dir.resolve()
        if args.resume_run_dir
        else args.output_root.resolve()
        / f"{config.experiment_id}__p{'-'.join(map(str, positions))}__{run_identity}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = (
        Path("data/research/experiments")
        / config.experiment_id
        / "window_checkpoints"
        / gen_config["generator_id"]
    ).resolve()
    print(
        f"cache-equivalence run={run_dir.name} windows={positions}/{len(windows)}",
        flush=True,
    )
    reports: list[dict[str, Any]] = []
    for position in positions:
        window = windows[position - 1]
        window_dir = run_dir / f"p{position:02d}_{window.window_id}"
        window_dir.mkdir(parents=True, exist_ok=True)
        direct = _run_mode(
            mode="direct",
            window=window,
            window_position=position,
            total_windows=len(windows),
            gen_config=gen_config,
            config=config,
            config_hash=config_hash,
            script_hash=script_hash,
            window_dir=window_dir,
            allow_prior_verifier=bool(args.resume_run_dir),
        )
        cache = _run_mode(
            mode="cache",
            window=window,
            window_position=position,
            total_windows=len(windows),
            gen_config=gen_config,
            config=config,
            config_hash=config_hash,
            script_hash=script_hash,
            window_dir=window_dir,
            allow_prior_verifier=bool(args.resume_run_dir),
        )
        direct_features = Path(direct["artifacts"]["loaded_features"]["path"])
        cache_features = Path(cache["artifacts"]["loaded_features"]["path"])
        consumed_features = direct["load"]["consumed_features"]
        if consumed_features != cache["load"]["consumed_features"]:
            raise RuntimeError("Direct/cache consumed-feature lists differ")
        comparison_columns = [*consumed_features]
        if (
            "$close" in direct["load"]["columns"]
            and "$close" in cache["load"]["columns"]
        ):
            comparison_columns.append("$close")
        print(
            f"[{position}/{len(windows)}][compare] streaming keyed comparison of "
            f"{len(consumed_features)} consumed features + auxiliary $close",
            flush=True,
        )
        feature_comparison = _compare_features(
            direct_features,
            cache_features,
            comparison_columns,
            window_dir / "duckdb_tmp",
        )
        direct_predictions = Path(direct["artifacts"]["predictions"]["path"])
        cache_predictions = Path(cache["artifacts"]["predictions"]["path"])
        prediction_comparison = _compare_predictions(
            direct_predictions, cache_predictions
        )
        official_path, official_identity = _official_checkpoint(
            checkpoint_dir, window.window_id
        )
        official_comparison = _compare_predictions(cache_predictions, official_path)
        tree_count_exact = (
            direct["training"]["tree_count"] == cache["training"]["tree_count"]
        )
        rank_ic_exact = (
            direct["training"]["rank_ic"] == cache["training"]["rank_ic"]
        )
        status = (
            "pass"
            if feature_comparison["status"] == "pass"
            and prediction_comparison["status"] == "pass"
            and official_comparison["status"] == "pass"
            and tree_count_exact
            and rank_ic_exact
            else "fail"
        )
        report = {
            "schema_version": "middle_window_cache_equivalence_report_v1",
            "status": status,
            "window_position": position,
            "window_count": len(windows),
            "window": asdict(window),
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "source_manifest_hash": config.source_manifest_hash,
            "consumed_feature_count": len(consumed_features),
            "auxiliary_comparison_columns": comparison_columns[len(consumed_features):],
            "direct": direct,
            "cache": cache,
            "feature_comparison": feature_comparison,
            "prediction_comparison": prediction_comparison,
            "official_checkpoint": official_identity,
            "official_checkpoint_comparison": official_comparison,
            "tree_count_exact": tree_count_exact,
            "train_rank_ic_exact_at_logged_precision": rank_ic_exact,
        }
        report_path = window_dir / "comparison.report.json"
        _write_json_atomic(report_path, report)
        reports.append(
            {
                "window_position": position,
                "window_id": window.window_id,
                "status": status,
                "feature_rows": feature_comparison["direct_rows"],
                "feature_max_abs_diff": feature_comparison.get("max_abs_diff"),
                "feature_tolerance_mismatch_cells": feature_comparison.get(
                    "finite_tolerance_mismatch_cells"
                ),
                "missing_mask_mismatch_cells": feature_comparison.get(
                    "missing_mask_mismatch_cells"
                ),
                "prediction_raw_max_abs_diff": prediction_comparison.get(
                    "score_model_raw_max_abs_diff"
                ),
                "full_daily_order_exact_dates": prediction_comparison.get(
                    "full_daily_order_exact_dates"
                ),
                "top5_exact_dates": prediction_comparison.get("top5_exact_dates"),
                "tree_count": direct["training"]["tree_count"],
                "train_rank_ic": direct["training"]["rank_ic"],
                "report_path": str(report_path.resolve()),
                "report_sha256": _sha256(report_path),
            }
        )
        print(
            f"[{position}/{len(windows)}][compare] {status.upper()} "
            f"feature_max_diff={feature_comparison.get('max_abs_diff')} "
            f"feature_mismatch>{ABS_TOLERANCE:g}="
            f"{feature_comparison.get('finite_tolerance_mismatch_cells')} "
            f"raw_score_max_diff={prediction_comparison.get('score_model_raw_max_abs_diff')} "
            f"Top5={prediction_comparison.get('top5_exact_dates')}/"
            f"{prediction_comparison.get('trade_dates')}",
            flush=True,
        )
        if status != "pass":
            break

    summary = {
        "schema_version": "middle_window_cache_equivalence_summary_v1",
        "status": (
            "pass"
            if len(reports) == len(positions)
            and all(report["status"] == "pass" for report in reports)
            else "fail"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "config_path": str(config_path),
        "config_sha256": config_hash,
        "script_sha256": script_hash,
        "source_manifest_hash": config.source_manifest_hash,
        "requested_positions": positions,
        "total_window_count": len(windows),
        "absolute_tolerance": ABS_TOLERANCE,
        "windows": reports,
    }
    summary_path = run_dir / "summary.json"
    _write_json_atomic(summary_path, summary)
    print(
        f"SUMMARY {summary['status'].upper()} {summary_path} sha256={_sha256(summary_path)}",
        flush=True,
    )
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
