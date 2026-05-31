# Ops Migration Audit — Framework Semantics

Audit of existing ops entrypoints, scripts, systemd templates, and documentation
for alpha_v1-specific hardcoding, with migration status toward framework-semantics
dispatch via `run_daily.py` / `run_daily_batch.py`.

---

## Scripts Audit

| Old / Current | Stage | New Framework Command | Status |
|---|---|---|---|
| `scripts/run_alpha_v1_daily.py --mode preopen` | alpha_v1 only | `python scripts/run_daily.py --strategy alpha_v1 --mode preopen` | legacy compatibility |
| `scripts/run_alpha_v1_daily.py --mode postclose` | alpha_v1 only | `python scripts/run_daily.py --strategy alpha_v1 --mode postclose` | legacy compatibility |
| `scripts/run_alpha_v1_daily.py --mode train` | alpha_v1 only | `python scripts/run_daily.py --strategy alpha_v1 --mode train` | legacy compatibility |
| `scripts/run_alpha_v1_weekly_train.py` | alpha_v1 only | `python scripts/run_daily.py --strategy alpha_v1 --mode train` | legacy compatibility |
| `scripts/run_daily.py --strategy <id> --mode <mode>` | generic | unchanged | **primary single-strategy** |
| `scripts/run_daily_batch.py --stage <stage> --mode <mode>` | stage-aware | unchanged | **primary batch** (this PR) |
| `scripts/run_preopen.sh` | alpha_v1 + legacy | `run_daily_batch.py --stage candidate --mode preopen` | legacy — update systemd template |
| `scripts/run_postclose.sh` | alpha_v1 + legacy | `run_daily_batch.py --stage candidate --mode postclose` | legacy — update systemd template |
| `scripts/run_alpha_v1_shadow_observation.py` | alpha_v1 only | use `run_daily.py` cycles | legacy compat |
| `scripts/run_daily_trading.py` | legacy daily trading | — | legacy (pre-framework) |
| `scripts/run_post_close.py` | legacy postclose | — | legacy (pre-framework) |
| `scripts/run_alpha_v1_backtest.py` | alpha_v1 only | `scripts/check_dr_bt_equivalence.py` | legacy compat |

---

## Docs Audit

| Doc / File | alpha_v1-specific references | Migration Status |
|---|---|---|
| `docs/alpha_v1_baseline.md` | Multiple `run_alpha_v1_daily.py` refs | ⏳ update to run_daily.py |
| `docs/RUNBOOK.md` | `run_alpha_v1_daily.py` in manual cmds | ⏳ update to run_daily.py |
| `docs/runbooks/weekly-replay-regression.md` | `run_alpha_v1_daily.py` iterations | ⏳ update to run_daily_batch.py or DR |
| `docs/features/new-strategy-development-guide.md` | `run_alpha_v1_weekly_train.py` refs | ✅ already says "Do not copy scripts/run_alpha_v1_daily.py" |
| `docs/features/strategy-lifecycle.md` | — | ✅ stage-aware (this PR) |
| `docs/runbooks/semantic-checks.md` | — | ✅ updated (this PR) |
| `archive/docs/runbooks/daily-ops.md` | — | ✅ created (this PR) |
| `docs/adr/005-protected-core-boundary.md` | lists `run_alpha_v1_daily.py` | ⏳ update to run_daily.py |
| `deploy/README.md` | `run_preopen.sh` / `run_postclose.sh` | ⏳ point to new templates |

---

## Systemd Template Audit

| Template File | Current Command | Migration Status |
|---|---|---|
| `deploy/systemd/qsys-preopen.service` | `scripts/run_preopen.sh` (wraps `run_alpha_v1_daily.py`) | ⏳ example template created |
| `deploy/systemd/qsys-post-close.service` | `scripts/run_postclose.sh` (wraps `run_alpha_v1_daily.py`) | ⏳ example template created |
| `deploy/systemd/qsys-alpha-v1-weekly-train.service` | `run_alpha_v1_weekly_train.py` | ⏳ example template created |
| `deploy/systemd/qsys-csi800-daily-sync.service` | `scripts/ops/sync_csi800_daily.py` | unchanged (data pipeline, not strategy) |

---

## Migration Principle

| Old Way | New Way |
|---|---|
| Alpha V1 hardcoded in scripts | Strategy-agnostic `run_daily.py --strategy <id>` |
| Per-strategy shell wrappers | Stage-aware `run_daily_batch.py --stage <stage>` |
| Per-strategy systemd units | Stage-based systemd template (candidate / production) |
| Script-specific ops runbook | Framework `docs/ops/DAILY_OPS_SOP.md` runbook |

---

## Framework Dispatch Status

Stage-aware framework dispatch via ``run_daily.py`` / ``run_daily_batch.py`` is
**effectively complete** (PR #96, PR #97).  Legacy scripts remain as deprecated
compatibility entrypoints with visible warnings.  New ops must use the framework
entrypoints.

---

- ✅ **Done** — migrated / implemented
- ⏳ **Pending** — acknowledged, update planned
- **legacy compatibility** — kept for backward compat, not for new ops
- **primary** — recommended path for all new ops
