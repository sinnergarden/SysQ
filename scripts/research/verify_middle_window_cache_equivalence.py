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
from typing import Any, Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

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
FEATURE_ABS_TOLERANCE = 1e-9
PREDICTION_ABS_TOLERANCE = 1e-12


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


def _generator_code_identity(generator: Any) -> dict[str, Any]:
    module_path = Path(sys.modules[generator.__class__.__module__].__file__).resolve()
    declared = generator.checkpoint_code_dependencies
    dependencies = [
        {"name": name, "sha256": _sha256(Path(path).resolve())}
        for name, path in sorted(declared.items())
    ]
    return {
        "generator_source_sha256": _sha256(module_path),
        "dependencies": dependencies,
    }


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
    generator_code_identity: dict[str, Any],
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
        "generator_code_identity": generator_code_identity,
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


def _compare_features(
    direct_path: Path,
    cache_path: Path,
    features: list[str],
    temp_dir: Path,
    *,
    row_filter: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    comparison_scope: str = "loaded_union_rows",
    absolute_tolerance: float = FEATURE_ABS_TOLERANCE,
) -> dict[str, Any]:
    del temp_dir  # Kept in the public helper signature for artifact compatibility.
    keys = ["instrument", "trade_date"]
    columns = [*keys, *features]
    direct = pd.read_parquet(direct_path, columns=columns)
    cache = pd.read_parquet(cache_path, columns=columns)
    loaded_direct_rows = len(direct)
    loaded_cache_rows = len(cache)
    if row_filter is not None:
        direct = row_filter(direct)
        cache = row_filter(cache)
    direct = direct.sort_values(
        keys, kind="mergesort"
    ).reset_index(drop=True)
    cache = cache.sort_values(
        keys, kind="mergesort"
    ).reset_index(drop=True)
    direct_rows = len(direct)
    cache_rows = len(cache)
    direct_duplicate_keys = int(direct.duplicated(keys).sum())
    cache_duplicate_keys = int(cache.duplicated(keys).sum())
    keys_equal = direct[keys].equals(cache[keys])
    direct_only = 0
    cache_only = 0
    if not keys_equal:
        direct_index = pd.MultiIndex.from_frame(direct[keys])
        cache_index = pd.MultiIndex.from_frame(cache[keys])
        direct_only = len(direct_index.difference(cache_index))
        cache_only = len(cache_index.difference(direct_index))
    if direct_duplicate_keys or cache_duplicate_keys:
        raise RuntimeError("Duplicate feature keys prevent one-to-one comparison")
    if not keys_equal:
        return {
            "direct_rows": direct_rows,
            "cache_rows": cache_rows,
            "direct_duplicate_keys": direct_duplicate_keys,
            "cache_duplicate_keys": cache_duplicate_keys,
            "direct_only_keys": direct_only,
            "cache_only_keys": cache_only,
            "comparison_scope": comparison_scope,
            "loaded_direct_rows": int(loaded_direct_rows),
            "loaded_cache_rows": int(loaded_cache_rows),
            "status": "fail",
            "feature_metrics": [],
        }
    by_feature: dict[str, dict[str, Any]] = {}
    for feature in features:
        direct_values = pd.to_numeric(direct[feature], errors="coerce").to_numpy(
            dtype=float
        )
        cache_values = pd.to_numeric(cache[feature], errors="coerce").to_numpy(
            dtype=float
        )
        direct_missing = np.isnan(direct_values)
        cache_missing = np.isnan(cache_values)
        finite_pair = np.isfinite(direct_values) & np.isfinite(cache_values)
        finite_diff = np.abs(
            direct_values[finite_pair] - cache_values[finite_pair]
        )
        by_feature[feature] = {
            "direct_missing": int(direct_missing.sum()),
            "cache_missing": int(cache_missing.sum()),
            "missing_mask_mismatch": int(
                np.logical_xor(direct_missing, cache_missing).sum()
            ),
            "finite_exact_mismatch": int(
                (direct_values[finite_pair] != cache_values[finite_pair]).sum()
            ),
            "finite_tolerance_mismatch": int(
                (finite_diff > absolute_tolerance).sum()
            ),
            "nonfinite_mismatch": int(
                (
                    ~direct_missing
                    & ~cache_missing
                    & ~finite_pair
                    & (direct_values != cache_values)
                ).sum()
            ),
            "max_abs_diff": float(finite_diff.max(initial=0.0)),
        }
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
        "direct_duplicate_keys": direct_duplicate_keys,
        "cache_duplicate_keys": cache_duplicate_keys,
        "direct_only_keys": int(direct_only),
        "cache_only_keys": int(cache_only),
        "comparison_scope": comparison_scope,
        "loaded_direct_rows": int(loaded_direct_rows),
        "loaded_cache_rows": int(loaded_cache_rows),
        "absolute_tolerance": absolute_tolerance,
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
        left_values = left[column].to_numpy(dtype=float)
        right_values = right[column].to_numpy(dtype=float)
        left_missing = np.isnan(left_values)
        right_missing = np.isnan(right_values)
        finite_pair = np.isfinite(left_values) & np.isfinite(right_values)
        finite_diff = np.abs(left_values[finite_pair] - right_values[finite_pair])
        metrics[f"{column}_missing_mask_mismatch"] = int(
            np.logical_xor(left_missing, right_missing).sum()
        )
        metrics[f"{column}_nonfinite_mismatch"] = int(
            (
                ~left_missing
                & ~right_missing
                & ~finite_pair
                & (left_values != right_values)
            ).sum()
        )
        metrics[f"{column}_max_abs_diff"] = float(
            finite_diff.max(initial=0.0)
        )
        metrics[f"{column}_mismatch_gt_tolerance"] = int(
            (finite_diff > PREDICTION_ABS_TOLERANCE).sum()
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
                    metrics["score_model_raw_missing_mask_mismatch"] == 0,
                    metrics["score_model_raw_nonfinite_mismatch"] == 0,
                    metrics["score_mismatch_gt_tolerance"] == 0,
                    metrics["score_missing_mask_mismatch"] == 0,
                    metrics["score_nonfinite_mismatch"] == 0,
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
    parser.add_argument(
        "--reference-config",
        type=Path,
        help=(
            "Optional frozen prior research config whose official checkpoints "
            "are compared read-only. Reference differences are diagnostic and "
            "do not weaken the direct/cache equivalence gate."
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
    if not positions or any(position < 1 or position > len(windows) for position in positions):
        raise ValueError(
            f"Choose at least one one-based position in [1, {len(windows)}]"
        )
    generators = expand_multi_label_generators(config.generators)
    if len(generators) != 1:
        raise ValueError(f"Expected one expanded generator, found {len(generators)}")
    gen_config = generators[0]
    identity_generator = _create_generator_from_config(
        gen_config,
        feature_list_id=config.feature_list_id,
        use_feature_cache=False,
        write_through=False,
        feature_cache_root=config.feature_cache_root,
        source_manifest_hash=config.source_manifest_hash,
    )
    generator_code_identity = _generator_code_identity(identity_generator)
    del identity_generator
    reference_config_path = (
        args.reference_config.resolve()
        if args.reference_config is not None
        else None
    )
    reference_config_hash = (
        _sha256(reference_config_path) if reference_config_path else None
    )
    run_identity = hashlib.sha256(
        _canonical_json(
            {
                "config_sha256": config_hash,
                "script_sha256": script_hash,
                "positions": positions,
                "source_manifest_hash": config.source_manifest_hash,
                "generator_code_identity": generator_code_identity,
                "reference_config_sha256": reference_config_hash,
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
    reference_checkpoint_dir: Path | None = None
    if reference_config_path is not None:
        reference_config = RollingResearchConfig.from_file(reference_config_path)
        reference_generators = expand_multi_label_generators(
            reference_config.generators
        )
        if len(reference_generators) != 1:
            raise ValueError(
                "Expected one expanded reference generator, found "
                f"{len(reference_generators)}"
            )
        reference_checkpoint_dir = (
            Path("data/research/experiments")
            / reference_config.experiment_id
            / "window_checkpoints"
            / reference_generators[0]["generator_id"]
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
            generator_code_identity=generator_code_identity,
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
            generator_code_identity=generator_code_identity,
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
        auxiliary_columns = comparison_columns[len(consumed_features):]
        print(
            f"[{position}/{len(windows)}][compare] keyed comparison of "
            f"{len(consumed_features)} consumed features"
            + (
                f" + auxiliary {', '.join(auxiliary_columns)}"
                if auxiliary_columns
                else ""
            ),
            flush=True,
        )
        loaded_feature_diagnostic = _compare_features(
            direct_features,
            cache_features,
            comparison_columns,
            window_dir / "duckdb_tmp",
            comparison_scope="loaded_union_rows_diagnostic",
        )
        comparison_generator = _create_generator_from_config(
            gen_config,
            feature_list_id=config.feature_list_id,
            use_feature_cache=False,
            write_through=False,
            feature_cache_root=config.feature_cache_root,
            source_manifest_hash=config.source_manifest_hash,
        )
        row_filter = (
            comparison_generator._apply_pit_membership
            if comparison_generator._effective_pit_filter_mode()
            else None
        )
        feature_comparison = _compare_features(
            direct_features,
            cache_features,
            comparison_columns,
            window_dir / "duckdb_tmp",
            row_filter=row_filter,
            comparison_scope=(
                "post_pit_membership_consumed_rows"
                if row_filter is not None
                else "loaded_consumed_rows"
            ),
        )
        del comparison_generator
        gc.collect()
        direct_predictions = Path(direct["artifacts"]["predictions"]["path"])
        cache_predictions = Path(cache["artifacts"]["predictions"]["path"])
        prediction_comparison = _compare_predictions(
            direct_predictions, cache_predictions
        )
        reference_identity: dict[str, Any] | None = None
        reference_comparison: dict[str, Any] | None = None
        if reference_checkpoint_dir is not None:
            reference_path, reference_identity = _official_checkpoint(
                reference_checkpoint_dir, window.window_id
            )
            reference_comparison = _compare_predictions(
                cache_predictions, reference_path
            )
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
            and tree_count_exact
            and rank_ic_exact
            else "fail"
        )
        report = {
            "schema_version": "middle_window_cache_equivalence_report_v2",
            "status": status,
            "window_position": position,
            "window_count": len(windows),
            "window": asdict(window),
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "source_manifest_hash": config.source_manifest_hash,
            "consumed_feature_count": len(consumed_features),
            "auxiliary_comparison_columns": auxiliary_columns,
            "direct": direct,
            "cache": cache,
            "loaded_feature_diagnostic": loaded_feature_diagnostic,
            "loaded_feature_diagnostic_is_gating": False,
            "feature_comparison": feature_comparison,
            "prediction_comparison": prediction_comparison,
            "reference_checkpoint": reference_identity,
            "reference_checkpoint_comparison": reference_comparison,
            "reference_difference_is_diagnostic_only": True,
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
                "loaded_feature_diagnostic_status": (
                    loaded_feature_diagnostic.get("status")
                ),
                "loaded_feature_diagnostic_missing_mask_mismatch_cells": (
                    loaded_feature_diagnostic.get("missing_mask_mismatch_cells")
                ),
                "prediction_raw_max_abs_diff": prediction_comparison.get(
                    "score_model_raw_max_abs_diff"
                ),
                "full_daily_order_exact_dates": prediction_comparison.get(
                    "full_daily_order_exact_dates"
                ),
                "top5_exact_dates": prediction_comparison.get("top5_exact_dates"),
                "reference_status": (
                    reference_comparison.get("status")
                    if reference_comparison is not None
                    else None
                ),
                "reference_raw_max_abs_diff": (
                    reference_comparison.get("score_model_raw_max_abs_diff")
                    if reference_comparison is not None
                    else None
                ),
                "reference_top5_exact_dates": (
                    reference_comparison.get("top5_exact_dates")
                    if reference_comparison is not None
                    else None
                ),
                "tree_count": direct["training"]["tree_count"],
                "train_rank_ic": direct["training"]["rank_ic"],
                "report_path": str(report_path.resolve()),
                "report_sha256": _sha256(report_path),
            }
        )
        print(
            f"[{position}/{len(windows)}][compare] {status.upper()} "
            f"scope={feature_comparison.get('comparison_scope')} "
            f"feature_max_diff={feature_comparison.get('max_abs_diff')} "
            f"feature_mismatch>{FEATURE_ABS_TOLERANCE:g}="
            f"{feature_comparison.get('finite_tolerance_mismatch_cells')} "
            f"raw_score_max_diff={prediction_comparison.get('score_model_raw_max_abs_diff')} "
            f"Top5={prediction_comparison.get('top5_exact_dates')}/"
            f"{prediction_comparison.get('trade_dates')}",
            flush=True,
        )
        if status != "pass":
            break

    summary = {
        "schema_version": "middle_window_cache_equivalence_summary_v2",
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
        "generator_code_identity": generator_code_identity,
        "source_manifest_hash": config.source_manifest_hash,
        "reference_config_path": (
            str(reference_config_path) if reference_config_path else None
        ),
        "reference_config_sha256": reference_config_hash,
        "requested_positions": positions,
        "total_window_count": len(windows),
        "feature_absolute_tolerance": FEATURE_ABS_TOLERANCE,
        "prediction_absolute_tolerance": PREDICTION_ABS_TOLERANCE,
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
