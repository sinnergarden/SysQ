# MiniQMT Server Change Guard

description: Guardrails for any change under `miniqmt_server/`. Use when adding endpoints, changing broker behavior, editing storage, touching config, or modifying tests/docs for the execution server.

## Triggers

Use when:
- a task changes any file under `miniqmt_server/`
- a task changes `tests/test_miniqmt_server.py`
- a task changes scripts or docs that call the MiniQMT server
- a task adds new execution-facing API fields or persistence behavior

## Core Rules

1. Keep the external contract stable.
   - Prefer additive API changes.
   - If a response schema changes, update README examples and tests in the same change.
   - Preserve `request_id` idempotency semantics.

2. Test through the real server surface whenever practical.
   - Fake broker state is allowed.
   - The preferred path is real local HTTP requests against the server, not only direct method calls.
   - Cover the full request lifecycle for new behavior: validate, submit, query, cancel, snapshot, or replay as applicable.

3. Never couple development tests to a real account.
   - Do not require a live MiniQMT process for default tests.
   - Do not place real orders, touch a real ledger, or assume Windows-side broker availability.
   - Keep mock mode deterministic and local-file backed.

4. Keep persistence and auditability explicit.
   - Any new state transition should remain inspectable via files under the configured `data_dir`.
   - If storage shape changes, update migration expectations or document the compatibility assumption.

5. Keep changes small and vertically complete.
   - Prefer one production-facing improvement with docs and tests over broad unfinished scaffolding.
   - Changes should usually include code, targeted tests, and README/API updates together.

## Required Checks

Before merging a MiniQMT server change, run the smallest relevant set that covers the touched surface.

Minimum default check:
- `/home/liuming/.local/bin/micromamba run -p /home/liuming/.openclaw/workspace/SysQ/.envs/test python -m pytest tests/test_miniqmt_server.py -q`

If the change touches docs only:
- confirm API examples still match the current server behavior

## Required README Coverage

`miniqmt_server/README.md` should stay focused on:
- service responsibilities
- directory structure
- startup and configuration
- API examples
- local mock mode
- logging and troubleshooting
- relationship with SysQ

Testing notes can exist, but should stay secondary.

## Notes

- Treat `mock` mode as the default integration contract for development.
- Treat `broker.mode=miniqmt` as a future real adapter path unless the task explicitly requires Windows-side integration work.
- If a change would affect real execution safety, stop and call it out before proceeding.
