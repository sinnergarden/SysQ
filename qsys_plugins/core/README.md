# qsys-core

Internal draft workflow assets for Qsys.

Purpose:
- keep the Python engine as the source of truth
- add reusable workflow assets for agents and future automation
- stabilize high-frequency research and operations tasks

This layer is intentionally thin:
- `commands/` are entrypoints and orchestration hints
- `skills/` are structured SOPs
- `connectors.json` describes stable data access boundaries

It does not replace existing scripts or module code.

## Included Skills

- `trading-calendar-guard`
- `feature-readiness-audit`
- `train-split-discipline`
- `shadow-execution-planner`
- `miniqmt-server-change-guard`

## Auto Trigger Notes

- `miniqmt-server-change-guard` is the repo-level rule set for execution-server changes.
- `miniqmt_server/AGENTS.md` mirrors the same guardrails locally so coding agents entering that directory pick them up automatically.
- If a change touches `miniqmt_server/` or `tests/test_miniqmt_server.py`, follow the guard skill and run the targeted MiniQMT server test suite before handoff.
