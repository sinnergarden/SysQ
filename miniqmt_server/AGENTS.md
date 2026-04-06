# MiniQMT Server Local Guardrails

This directory follows the repo skill `qsys_plugins/core/skills/miniqmt-server-change-guard/SKILL.md`.

If you edit anything under `miniqmt_server/`, also follow these local rules:

- Default to `mock` mode for development and testing; do not depend on a live MiniQMT process.
- Prefer end-to-end checks through the real local HTTP server surface, even when broker data is fake.
- Keep `request_id` idempotency stable for submit flows.
- Keep file-backed audit artifacts under the configured `data_dir` inspectable.
- When API behavior changes, update `miniqmt_server/README.md` and `tests/test_miniqmt_server.py` in the same change.
- Before handing off, run:
  `/home/liuming/.local/bin/micromamba run -p /home/liuming/.openclaw/workspace/SysQ/.envs/test python -m pytest tests/test_miniqmt_server.py -q`

The intent is simple: fake broker state is fine, but the server contract should be exercised through the real interface and documented as if it were a production boundary.
