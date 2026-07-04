# Scripts Directory Layout

> Canonical entrypoints are defined in `docs/requirements/harness_map.yaml`.
> Only these scripts belong at the `scripts/` top level.

---

## Top-Level (Canonical Entrypoints)

| Script | Use Case | Purpose |
|--------|----------|---------|
| `data_sync.py` | UC_DAILY_OPS | Data sync & validation |
| `run_daily.py` | UC_DAILY_OPS | Daily ops: preopen / postclose / train |
| `run_research.py` | UC_RESEARCH_BACKTEST | Signal research pipeline |
| `run_signal_analytics.py` | UC_RESEARCH_BACKTEST | Signal IC / RankIC analysis |
| `promote_candidate.py` | UC_CANDIDATE_PROMOTION | Candidate create & promote |
| `run_research_ui_api.py` | UC_UI_ANALYSIS | Research UI API service |

## Compatibility Wrappers (Top-Level)

These live at top-level for backward compatibility but are **not** canonical entrypoints:

| Script | Note |
|--------|------|
| `run_daily_batch.py` | Batch wrapper around `run_daily.py`. Used by systemd timer. |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `research/` | Research pipeline sub-entrypoints and support tools |
| `ops/` | Operational tools, maintenance, and compatibility wrappers |
| `dev/` | Temporary experiments, debug scripts, examples |
| `checks/` | Runtime data/artifact checks (runtime) + framework stability checks |
| `deprecated/` | Historical entrypoints, scheduled for removal |
| `live/` | Broker / live ops scripts (Phase 3+) |
| `data/` | Data sync support scripts |

## Adding a New Script

1. **Default location**: put it in one of the subdirectories above.
2. **Top-level only**: if you believe it should become a canonical entrypoint,
   first add it to the use case registry and `harness_map.yaml`.
3. **Temporary scripts**: go in `scripts/dev/`. Move or remove when done.
4. **Old scripts**: move to `scripts/deprecated/` rather than deleting immediately.
