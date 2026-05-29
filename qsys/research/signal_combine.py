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


def combine_signals(
    spec: CombineSpec,
    *,
    output_signal_id: str,
    output_signal_run_id: str,
    signal_store: SignalStore,
    research_paths: ResearchPaths,
    overwrite: bool = False,
) -> pd.DataFrame:
    """Combine multiple SignalRuns into one derived SignalRun.

    Loads each input SignalRun, joins by (trade_date, data_date, instrument),
    computes combined score, saves as a new SignalRun.

    Returns the combined DataFrame.
    """
    frames: list[pd.DataFrame] = []
    for inp in spec.inputs:
        df = signal_store.load_signal_run(
            inp.source_signal_id, inp.source_signal_run_id,
        )
        if df.empty:
            raise ValueError(
                f"Combine: empty SignalRun for {inp.source_signal_id}/"
                f"{inp.source_signal_run_id}"
            )
        df = df.rename(columns={"score": f"score_{inp.weight}"})
        df["_weight"] = inp.weight
        frames.append(df)

    # Full outer join on (trade_date, data_date, instrument)
    combined = frames[0]
    for f in frames[1:]:
        combined = pd.merge(
            combined, f,
            on=["trade_date", "data_date", "instrument"],
            how="outer",
            suffixes=("", "_right"),
        )

    # Gather weighted scores
    weight_cols = [c for c in combined.columns if c.startswith("score_")]
    if not weight_cols:
        raise ValueError("Combine: no score columns after join")

    total_weight = sum(spec.inputs[i].weight for i in range(len(spec.inputs)))

    if spec.combine_type == "linear_blend":
        combined["score"] = sum(
            combined[f"score_{inp.weight}"].fillna(0.0) * inp.weight
            for inp in spec.inputs
        ) / total_weight
    elif spec.combine_type == "equal_weight":
        n = len(spec.inputs)
        combined["score"] = sum(
            combined[f"score_{inp.weight}"].fillna(0.0)
            for inp in spec.inputs
        ) / n
    elif spec.combine_type == "confirm_filter":
        # score = primary_score where secondary_score > 0 else primary * 0.5
        primary_inp = spec.inputs[0]
        secondary_inp = spec.inputs[1] if len(spec.inputs) > 1 else None
        primary_col = f"score_{primary_inp.weight}"
        if secondary_inp:
            secondary_col = f"score_{secondary_inp.weight}"
            combined["score"] = combined[primary_col].fillna(0.0).where(
                combined[secondary_col].fillna(0.0) > 0,
                combined[primary_col].fillna(0.0) * 0.5,
            )
        else:
            combined["score"] = combined[primary_col].fillna(0.0)
    else:
        raise ValueError(f"Unknown combine type: {spec.combine_type}")

    # Drop helper columns
    drop_cols = [c for c in combined.columns if c.startswith("score_") or c == "_weight"]
    combined = combined.drop(columns=drop_cols, errors="ignore")

    # Fill required columns
    combined["signal_id"] = output_signal_id
    combined["signal_run_id"] = output_signal_run_id

    # Save SignalRun
    input_refs = [
        {
            "signal_id": inp.source_signal_id,
            "signal_run_id": inp.source_signal_run_id,
            "weight": inp.weight,
        }
        for inp in spec.inputs
    ]

    signal_store.save_signal_run(
        output_signal_id, output_signal_run_id, combined,
        manifest={
            "artifact_type": "combined_signal_run",
            "combine_id": spec.combine_id,
            "combine_type": spec.combine_type,
            "inputs": input_refs,
        },
        overwrite=overwrite,
    )

    # Write cross_signal_manifest.json in the signal output dir
    sig_dir = research_paths.signal_dir(output_signal_id, output_signal_run_id)
    manifest = with_standard_metadata({
        "artifact_type": "signal_combination",
        "combine_id": spec.combine_id,
        "combine_type": spec.combine_type,
        "inputs": input_refs,
        "output_signal_id": output_signal_id,
        "output_signal_run_id": output_signal_run_id,
        "row_count": len(combined),
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
