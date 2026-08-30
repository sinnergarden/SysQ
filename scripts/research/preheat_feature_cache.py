#!/usr/bin/env python3
"""Materialize canonical full-calendar-year feature-cache shards.

This command only loads feature frames through the canonical generator loader;
it never enters rolling training or signal generation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    expand_multi_label_generators,
)
from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator
from qsys.research.rolling_window import build_rolling_windows


def _annual_ranges(start: str, end: str) -> list[tuple[str, str]]:
    return [
        (f"{year:04d}-01-01", f"{year:04d}-12-31")
        for year in range(int(start[:4]), int(end[:4]) + 1)
    ]


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--feature-cache-root", default="data/feature_cache", type=Path)
    args = parser.parse_args()

    config = RollingResearchConfig.from_file(args.config)
    if not config.generators:
        raise ValueError("preheat requires a matrix config with generators")
    generators = expand_multi_label_generators(config.generators)
    start = str(config.calendar["start_date"])
    end = str(config.calendar["end_date"])
    lag = max(
        (label.get("label_maturity_lag_trading_days") or 0)
        for label in config.labels
    ) if config.labels else 0
    windows = build_rolling_windows(
        start,
        end,
        train_window_days=config.calendar.get("train_window_days", 252),
        step_days=config.calendar.get("step_days", 5),
        label_maturity_lag_trading_days=lag,
    )
    if not windows:
        raise ValueError("preheat found no rolling windows")
    train_start = min(window.train_start for window in windows)
    records: dict[tuple[str, str, str], dict] = {}

    for generator_config in generators:
        generator = _create_generator_from_config(
            generator_config,
            feature_list_id=config.feature_list_id,
            use_feature_cache=True,
            write_through=True,
            feature_cache_root=str(args.feature_cache_root),
            source_manifest_hash=config.source_manifest_hash,
        )
        if not isinstance(generator, LightGBMSingleLabelGenerator):
            raise ValueError(
                "preheat currently supports only single_label_lightgbm generators"
            )
        generator.cache_write_scope = "annual_shard"
        load_end = max(
            (
                window.train_end
                if generator.prediction_universe
                else window.predict_end
            )
            for window in windows
        )
        ranges = _annual_ranges(train_start, load_end)
        generator.write_through = False
        for shard_number, (shard_start, shard_end) in enumerate(ranges, start=1):
            source_end = min(shard_end, load_end)
            print(
                f"[{shard_number}/{len(ranges)}] preheat "
                f"{generator_config['generator_id']} {shard_start}..{source_end} "
                f"(cache identity through {shard_end})",
                flush=True,
            )
            frame, consumed_features = generator._load_data(shard_start, source_end)
            materialized_features = generator.materialized_features
            path = generator._annual_shard_path(
                shard_start, shard_end, materialized_features
            )
            meta_path = generator._annual_shard_meta_path(
                shard_start, shard_end, materialized_features
            )
            cached = generator._load_annual_shard_cache(
                shard_start,
                source_end,
                materialized_features,
                consumed_features=consumed_features,
            )
            if cached is None:
                path = generator._write_cache_frame(
                    frame,
                    shard_start,
                    shard_end,
                    materialized_features,
                    consumed_features=consumed_features,
                    source_coverage_start=shard_start,
                    source_coverage_end=source_end,
                )
                cached = generator._load_annual_shard_cache(
                    shard_start,
                    source_end,
                    materialized_features,
                    consumed_features=consumed_features,
                )
                if cached is None:
                    raise ValueError(
                        "annual shard failed read-after-write validation: "
                        f"{path}"
                    )
            elif not path.is_file() or not meta_path.is_file():
                raise ValueError(
                    "annual shard validator returned data without durable files: "
                    f"{path}"
                )
            identity = generator._cache_identity(
                shard_start,
                shard_end,
                materialized_features,
                consumed_features=consumed_features,
            )
            key = (shard_start, shard_end, str(path))
            records[key] = {
                "generator_id": generator_config["generator_id"],
                "start": shard_start,
                "end": shard_end,
                "source_coverage_end": source_end,
                "path": str(path),
                "rows": len(cached),
                "data_sha256": _sha256_file(path),
                "source_manifest_hash": config.source_manifest_hash,
                "identity": identity,
            }
            print(
                f"[{shard_number}/{len(ranges)}] ready rows={len(cached)} "
                f"path={path}",
                flush=True,
            )

    manifest_path = args.feature_cache_root / "annual_shards" / (
        f"{config.experiment_id}.manifest.json"
    )
    _write_json_atomic(
        manifest_path,
        {
            "schema_version": 1,
            "experiment_id": config.experiment_id,
            "prediction_start": start,
            "prediction_end": end,
            "cache_coverage_start": min(record["start"] for record in records.values()),
            "cache_coverage_end": max(record["end"] for record in records.values()),
            "source_manifest_hash": config.source_manifest_hash,
            "preheat_code_sha256": _sha256_file(Path(__file__)),
            "generator_code_sha256": _sha256_file(
                Path(sys.modules[LightGBMSingleLabelGenerator.__module__].__file__)
            ),
            "shards": list(records.values()),
        },
    )
    print(f"wrote {len(records)} annual shards: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
