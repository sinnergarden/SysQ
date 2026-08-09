"""Signal combination — combine multiple SignalRuns into a derived SignalRun.

Supports:
- linear_blend: weighted sum of scores
- equal_weight: equal-weighted average of scores
- confirm_filter: scale primary score where secondary score > threshold

Each combination loads input SignalRuns from SignalStore, joins by
(trade_date, data_date, instrument), computes the combined score, and
saves a new SignalRun.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.research.paths import ResearchPaths
from qsys.signal.store import SignalStore


@dataclass
class CombineInput:
    """Reference to one source signal for a combination."""
    source_signal_id: str
    source_signal_run_id: str
    weight: float = 1.0


@dataclass
class CombineSpec:
    """Specification for a signal combination."""
    combine_id: str
    combine_type: str  # linear_blend | equal_weight | confirm_filter
    inputs: list[CombineInput]
    params: dict[str, Any] | None = None


def build_combine_spec_from_config(
    combine_cfg: dict[str, Any],
    signal_id_map: dict[str, str],
    signal_run_id_map: dict[str, str],
) -> CombineSpec:
    """Build a CombineSpec from a config dict.

    Parameters
    ----------
    combine_cfg:
        Config dict with combine_id, type, inputs.
        Each input has source_signal_id (or source_generator_id +
        source_transform_id) and weight.
    signal_id_map:
        Mapping from generator_id__transform_id to actual signal_id.
    signal_run_id_map:
        Mapping from generator_id__transform_id to actual signal_run_id.
    """
    inputs_raw = combine_cfg.get("inputs", [])
    inputs: list[CombineInput] = []
    for inp in inputs_raw:
        source = inp.get("source", "")
        if not source:
            gen = inp.get("source_generator_id", "")
            tf = inp.get("source_transform_id", "")
            source = f"{gen}__{tf}"
        signal_id = signal_id_map.get(source, source)
        signal_run_id = signal_run_id_map.get(source, source)
        inputs.append(CombineInput(
            source_signal_id=signal_id,
            source_signal_run_id=signal_run_id,
            weight=inp.get("weight", 1.0),
        ))
    return CombineSpec(
        combine_id=combine_cfg["combine_id"],
        combine_type=combine_cfg.get("type", "linear_blend"),
        inputs=inputs,
        params=combine_cfg.get("params"),
    )


_DEFAULT_JOIN_POLICY = "inner"
_SUPPORTED_JOIN_POLICIES = {"inner", "outer_zero_fill"}


def combine_signals(
    spec: CombineSpec,
    *,
    output_signal_id: str,
    output_signal_run_id: str,
    signal_store: SignalStore,
    research_paths: ResearchPaths,
    overwrite: bool = False,
    join_policy: str | None = None,
) -> pd.DataFrame:
    """Combine multiple SignalRuns into one derived SignalRun.

    Loads each input SignalRun, joins by (trade_date, data_date, instrument),
    computes combined score, saves as a new SignalRun.

    Parameters
    ----------
    spec:
        Combination specification.
    output_signal_id:
        Signal ID for the output.
    output_signal_run_id:
        Signal run ID for the output.
    signal_store:
        SignalStore instance for loading/saving.
    research_paths:
        ResearchPaths instance for path resolution.
    overwrite:
        Allow overwriting existing SignalRun.
    join_policy:
        Join policy for combining signals. Default "inner":
        only rows covered by ALL input signals are combined.
        "outer_zero_fill": outer join with fillna(0) for missing scores.

    Returns the combined DataFrame.
    """
    if join_policy is None:
        join_policy = _DEFAULT_JOIN_POLICY
    if join_policy not in _SUPPORTED_JOIN_POLICIES:
        raise ValueError(
            f"Unsupported join_policy: {join_policy!r}. "
            f"Supported: {sorted(_SUPPORTED_JOIN_POLICIES)}"
        )

    input_row_counts: list[int] = []
    input_data_hashes: list[str] = []
    frames: list[pd.DataFrame] = []
    for idx, inp in enumerate(spec.inputs):
        df = signal_store.load_signal_run(
            inp.source_signal_id, inp.source_signal_run_id,
        )
        if df.empty:
            raise ValueError(
                f"Combine: empty SignalRun for {inp.source_signal_id}/"
                f"{inp.source_signal_run_id}"
            )
        input_row_counts.append(len(df))
        input_data_hashes.append(
            signal_store.signal_data_sha256(
                inp.source_signal_id, inp.source_signal_run_id
            )
        )
        df = df.rename(columns={"score": f"score_{idx}"})
        frames.append(df)

    # Join on (trade_date, data_date, instrument)
    how = "inner" if join_policy == "inner" else "outer"
    combined = frames[0]
    for f in frames[1:]:
        combined = pd.merge(
            combined, f,
            on=["trade_date", "data_date", "instrument"],
            how=how,
            suffixes=("", "_right"),
        )

    # Clean up any _right columns from duplicate merge keys
    right_cols = [c for c in combined.columns if c.endswith("_right")]
    combined = combined.drop(columns=right_cols, errors="ignore")

    score_cols = sorted(
        [c for c in combined.columns if c.startswith("score_") and c[len("score_"):].isdigit()],
        key=lambda x: int(x.split("_")[1]),
    )
    if not score_cols:
        raise ValueError("Combine: no score columns after join")

    max_rows = max(len(f) for f in frames)
    dropped_by_join = max_rows - len(combined)

    total_weight = sum(inp.weight for inp in spec.inputs)
    n_inputs = len(spec.inputs)

    if spec.combine_type == "linear_blend":
        combined["score"] = sum(
            combined[score_cols[i]].fillna(0.0) * spec.inputs[i].weight
            for i in range(n_inputs)
        ) / total_weight
    elif spec.combine_type == "equal_weight":
        combined["score"] = sum(
            combined[score_cols[i]].fillna(0.0)
            for i in range(n_inputs)
        ) / n_inputs
    elif spec.combine_type == "confirm_filter":
        primary_col = score_cols[0]
        if len(score_cols) > 1:
            secondary_col = score_cols[1]
            combined["score"] = combined[primary_col].fillna(0.0).where(
                combined[secondary_col].fillna(0.0) > 0,
                combined[primary_col].fillna(0.0) * 0.5,
            )
        else:
            combined["score"] = combined[primary_col].fillna(0.0)
    else:
        raise ValueError(f"Unknown combine type: {spec.combine_type}")

    # Drop helper columns
    drop_cols = [c for c in combined.columns if c.startswith("score_")]
    combined = combined.drop(columns=drop_cols, errors="ignore")

    # Fill required columns
    combined["signal_id"] = output_signal_id
    combined["signal_run_id"] = output_signal_run_id

    # Build manifest
    input_refs = [
        {
            "signal_id": inp.source_signal_id,
            "signal_run_id": inp.source_signal_run_id,
            "weight": inp.weight,
            "predictions_sha256": input_data_hashes[idx],
        }
        for idx, inp in enumerate(spec.inputs)
    ]

    signal_store.save_signal_run(
        output_signal_id, output_signal_run_id, combined,
        manifest={
            "artifact_type": "combined_signal_run",
            "combine_id": spec.combine_id,
            "combine_type": spec.combine_type,
            "join_policy": join_policy,
            "inputs": input_refs,
        },
        overwrite=overwrite,
    )

    # Write combination_manifest.json
    sig_dir = research_paths.signal_dir(output_signal_id, output_signal_run_id)
    manifest = with_standard_metadata({
        "artifact_type": "signal_combination",
        "combine_id": spec.combine_id,
        "combine_type": spec.combine_type,
        "join_policy": join_policy,
        "inputs": input_refs,
        "output_signal_id": output_signal_id,
        "output_signal_run_id": output_signal_run_id,
        "input_row_counts": input_row_counts,
        "output_row_count": len(combined),
        "dropped_by_join": dropped_by_join,
        "date_range": {
            "start": str(combined["trade_date"].min()),
            "end": str(combined["trade_date"].max()),
        },
    })
    write_manifest(sig_dir / "combination_manifest.json", manifest)

    return combined


def build_cross_signal_index(
    specs: list[CombineSpec],
    output_signal_ids: list[str],
    output_signal_run_ids: list[str],
    research_paths: ResearchPaths,
    experiment_id: str,
) -> pd.DataFrame:
    """Build cross_signal_index.csv for an experiment.

    Records all combinations and their source signals.
    """
    rows = []
    for spec, sig_id, run_id in zip(specs, output_signal_ids, output_signal_run_ids):
        row = {
            "combine_id": spec.combine_id,
            "combine_type": spec.combine_type,
            "output_signal_id": sig_id,
            "output_signal_run_id": run_id,
            "input_signal_ids": ";".join(
                inp.source_signal_id for inp in spec.inputs
            ),
            "input_signal_run_ids": ";".join(
                inp.source_signal_run_id for inp in spec.inputs
            ),
            "weights": ";".join(str(inp.weight) for inp in spec.inputs),
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    exp_dir = research_paths.experiment_dir(experiment_id)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "cross_signal_index.csv", index=False)
    return df
