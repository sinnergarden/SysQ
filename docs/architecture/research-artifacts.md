# Research Artifact Architecture (ADR-8)

## Context

Research experiments produce intermediate artifacts — labels, signals,
predictions — that need to be stored, versioned, and loaded back for
analysis.  Without a uniform storage convention, each experiment script
invents its own ad-hoc file layout, making cross-run comparison and
automated pipelines difficult.

This document defines the standard layout and store API for research
artifacts in Qsys Framework Stable 2.0.

## Directory Layout

```
<project_root>/
  research/
    artifacts/
      <run_name>/
        manifest.json          # RunManifest (lineage metadata)
        labels/
          <label_id>.parquet   # LabelStore output
          <label_id>.spec.json # LabelSpec sidecar (optional)
        signals/
          <signal_id>.parquet  # SignalStore output
          <signal_id>.spec.json # SignalSpec sidecar (optional)
        predictions/
          <prediction_id>.parquet
```

### `manifest.json`

Top-level lineage file written at run creation time.  Contains:

```json
{
  "run_id": "20260528_a1b2c3d4e5f6g7h8",
  "run_name": "alpha_v1_20260501",
  "created_at": "2026-05-28T10:00:00",
  "model_id": "alpha_v1_20260525",
  "feature_set_id": "csi300_daily_v3",
  "label_id": "forward_return_5d",
  "description": "...",
  "tags": {},
  "params": {}
}
```

All fields except ``run_id``, ``run_name``, ``created_at`` are optional.
The lineage triple ``(model_id, feature_set_id, label_id)`` is the
minimum provenance needed to reproduce a run.

### Parquet schema conventions

**Labels** (``labels/<label_id>.parquet``):

| Column | Type | Description |
|---|---|---|
| ``date`` | str (YYYY-MM-DD) | Trading date |
| ``instrument`` | str | Instrument code |
| ``value`` | float64 | Label value |
| ``weight`` | float64 | Sample weight (default 1.0) |

**Signals** (``signals/<signal_id>.parquet``):

| Column | Type | Description |
|---|---|---|
| ``date`` | str (YYYY-MM-DD) | Trading date |
| ``instrument`` | str | Instrument code |
| ``value`` | float64 | Signal value |

## Key modules

| Module | Responsibility |
|---|---|
| ``qsys.research.paths`` | ``resolve_run_dir()``, ``resolve_artifact_path()``, ``make_run_id()`` |
| ``qsys.research.manifest`` | ``RunManifest`` dataclass, ``write_run_manifest()``, ``load_run_manifest()`` |
| ``qsys.label.schema`` | ``LabelSpec``, ``LabelRecord`` |
| ``qsys.label.store`` | ``LabelStore.save()`` / ``load()`` / ``list()`` |
| ``qsys.signal.schema`` | ``SignalSpec``, ``SignalRecord`` |
| ``qsys.signal.store`` | ``SignalStore.save()`` / ``load()`` / ``list()`` |

## Design decisions

1. **Pure dataclasses, no pydantic.**  Follows existing qsys convention.
2. **Parquet as primary storage.**  Columnar, fast, compression-friendly,
   pandas-native.  JSON sidecars for human-readable specs.
3. **No model registry coupling.**  The manifest stores *strings* for
   ``model_id`` / ``feature_set_id`` / ``label_id`` — it references
   external registry objects by name without importing them.
4. **Store.read() returns DataFrames, not Record lists.**  Consumers
   typically need to join/aggregate across dates; DataFrames are more
   convenient for this than row-level objects.
5. **Global `set_research_root()` override.**  Allows tests and
   non-standard deployments to redirect artifact output.
