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
