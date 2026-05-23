# Phase 1.5 — Daily Runner Boundary Refactor

## Motivation

`scripts/run_alpha_v1_daily.py` grew to ~1400 lines handling three operation modes
(preopen, postclose, train) with inline inference, planning, execution, MTM,
notification, and artifact management.  This doc defines the boundary layers and
the incremental migration plan to move toward a configurable multi-strategy
runtime.

## Boundary Layers

```
┌─────────────────────────────────────────────────┐
│  Scripts / CLI                                  │
│  scripts/run_alpha_v1_daily.py                  │
│  scripts/run_preopen.sh / run_postclose.sh      │
├─────────────────────────────────────────────────┤
│  Daily Runner         (phase 1.5 → future)      │
│  qsys/ops/daily_runner.py                       │
│    "reusable runtime skeleton"                  │
├─────────────────────────────────────────────────┤
│  Strategy                                      │
│  qsys/strategy/base.py          Protocol       │
│  qsys/strategy/alpha_v1/adapter.py  Adapter    │
│  qsys/strategy/alpha_v1/spec.py  Config        │
├─────────────────────────────────────────────────┤
│  Protected Core / Runtime                       │
│  qsys/ops/shadow_rebalance.py                  │
│  qsys/ops/mtm.py                                │
│  qsys/ops/daily_artifacts.py                   │
│  qsys/ops/commit_guard.py                      │
│  qsys/ops/replay.py                             │
│  qsys/ops/telegram.py                           │
│  qsys/ops/run_context.py                        │
├─────────────────────────────────────────────────┤
│  Common Utilities   (business-neutral)          │
│  qsys/common/io.py                              │
│  qsys/common/time.py                            │
│  qsys/common/git.py                             │
├─────────────────────────────────────────────────┤
│  Model / Feature / Signal / Data               │
│  qsys/signal/alpha_v1/                          │
│  qsys/feature/                                  │
│  qsys/data/                                     │
│  qlib / LightGBM models                         │
└─────────────────────────────────────────────────┘
```

### 1. Scripts / CLI Layer — thin entry point

- Parse CLI arguments, build `DailyRunContext`, delegate to runner or inline.
- Shell scripts (`run_preopen.sh`, `run_postclose.sh`) sequence stages and
  handle exit codes — no business logic.

### 2. Daily Runner — skeleton runtime

- `DailyRunner` class with `run_preopen(ctx)`, `run_postclose(ctx)`,
  `run_train(ctx)`.
- Owns the stage orchestration sequence but delegates strategy-specific work
  through the `StrategyCandidate` protocol.
- Currently a skeleton — alpha_v1 daily script calls it only for the shared
  scaffolding (`save_run_meta`, `archive_execution`).
- Future: strategy resolution via config, not hard-coded singletons.

### 3. Strategy Layer — strategy-specific logic

- `StrategyCandidate` Protocol defines the interface each strategy must expose:
  identity (`strategy_id`, `account_id`), config (`universe`, `feature_set`,
  `model_version`, `signal_version`, `rebalance_policy`), and optional lifecycle
  hooks (`on_preopen`, `on_postclose`, `on_train`).
- `AlphaV1StrategyAdapter` wraps the existing `ALPHA_V1_CANDIDATE` singleton
  into the Protocol, serving as a bridge until strategies become first-class
  plugin objects.
- `spec.py` is the single source of truth for alpha_v1 parameters — no
  duplicate constants.

### 4. Protected Core / Runtime — stable, reused across strategies

- **no-change zone**: `shadow_rebalance.py`, `mtm.py`, `telegram.py`,
  `commit_guard.py`, `replay.py` — internal implementation details that should
  not be modified during the boundary refactor.
- **new** `run_context.py`: `DailyRunContext` dataclass and `resolve_run_root`
  helper, extracted from the daily script's argument plumbing.
- **new** `daily_artifacts.py`: artifact I/O moved out of the daily script.

### 5. Common Utilities — business-neutral

- `qsys/common/` has zero imports from `qsys.*` — safe for any module to
  depend on.
- Current contents: `io.py` (JSON read/write/archive), `time.py` (ISO
  timestamps), `git.py` (commit hash lookup).

## File Map

| Concern | File | Status |
|---------|------|--------|
| Config singleton | `qsys/strategy/alpha_v1/spec.py` | existing |
| Strategy base | `qsys/strategy/base.py` | extended |
| Strategy adapter | `qsys/strategy/alpha_v1/adapter.py` | **new** |
| Run context | `qsys/ops/run_context.py` | **new** |
| Daily runner | `qsys/ops/daily_runner.py` | **new** |
| Common I/O | `qsys/common/io.py` | **new** |
| Common time | `qsys/common/time.py` | **new** |
| Common git | `qsys/common/git.py` | **new** |
| Architecture doc | `docs/features/phase1.5-daily-runner-boundary.md` | **this file** |
| Artifact helpers | `qsys/ops/daily_artifacts.py` | extracted |
| Commit guard | `qsys/ops/commit_guard.py` | extracted |
| MTM helpers | `qsys/ops/mtm.py` | extracted |
| Replay comparison | `qsys/ops/replay.py` | extracted |

## Migration Plan

### Phase A (this PR) — Boundary extraction
- Extract pure-utility helpers from `run_alpha_v1_daily.py` → `qsys/common/`
- Extract artifact I/O → `qsys/ops/daily_artifacts.py`
- Extract commit guard → `qsys/ops/commit_guard.py`
- Extract MTM helpers → `qsys/ops/mtm.py`
- Extract replay comparison → `qsys/ops/replay.py`
- Add `DailyRunContext`, `StrategyCandidate` Protocol, `AlphaV1StrategyAdapter`,
  `DailyRunner` skeleton
- **No change to trading semantics** — all extracted code is move/copy with
  identical behavior.

### Phase B — Daily runner integration
- Wire `DailyRunner` as the orchestration backbone for preopen/postclose/train.
- `run_alpha_v1_daily.py` delegates stage sequencing to `DailyRunner` methods.

### Phase C — Multi-strategy support
- `DailyRunner` resolves strategy from `--strategy-id` CLI arg.
- Strategy discovery via config or registry, not hard-coded singletons.

### Phase D — Deprecation
- `run_alpha_v1_daily.py` becomes a thin CLI wrapper.  New strategies write
  their own small CLI scripts that reuse `DailyRunner`.

## Constraints

1. **Behavior-preserving**: no changes to ledger, matcher, backtest, T+1
   settlement, cost model, or portfolio construction logic.
2. **No new strategy**: this PR adds interfaces and adapters, not a new
   `alpha_v2` or multi-strategy runtime.
3. **No plugin registry**: strategy resolution remains explicit (import +
   instantiate).
4. **Existing CLI unchanged**: all `run_alpha_v1_daily.py` flags continue to
   work.
5. **One-week replay**: all changes in this PR must produce identical outputs
   for 2026-05-18 through 2026-05-22 when compared against `main`.
