# Agent Operating Contract

## Purpose

Rules for coding agents (Claude Code, or any automated assistant) when
working on the Qsys repository. These rules prevent silent semantic
changes, hidden lookahead leaks, and unverified claims.

---

## Rule 1: Implement, Don't Redefine

An agent may implement new code or modify existing code, but must not
silently change the **semantics** of an existing function.

- A function's input/output meaning, date interpretation, and business
  logic must remain unchanged unless explicitly requested.
- If a semantic change is necessary, the agent must:
  1. Call it out in the PR body.
  2. Add a test proving the new behavior.
  3. Add a test proving the old behavior would have been wrong.

## Rule 2: Time Semantics Require Tests

If a PR touches date resolution, trading calendars, data_date, or
time windows:
- The PR body must include a **Time semantics impact** section.
- At least one test must verify the new date logic.
- The `check_no_lookahead.py` checker must pass against the affected
  artifact schemas.

## Rule 3: Core Execution Changes Are Blocked Until Explained

If a PR touches any of these files, the agent must **stop and explain**
before implementing:

- `qsys/trader/matcher.py`
- `qsys/ops/daily_runner.py`
- `qsys/backtest/engine.py`
- `qsys/ledger/*.py`
- `qsys/ops/commit_guard.py`

Explanation must include:
- Current behavior
- Desired new behavior
- Why the change is necessary
- Why it cannot be achieved without touching core execution

## Rule 4: Every PR Must Include Scope / Non-Scope / Verification

The PR body must follow the
[pr-evidence-template.md](../templates/pr-evidence-template.md).

At minimum:
- **Scope**: what this PR does
- **Non-goals**: what this PR explicitly does not do
- **Verification**: commands run and their output

## Rule 5: Evidence Over Summary

- Every claim of "tested" or "verified" must be accompanied by the
  **actual command output** (captured in the PR body or an attached file).
- Do not paraphrase test results — include pass/fail counts.
- Do not say "works correctly" — say "command X exited 0 with output Y".

## Rule 6: No Progress Without Evidence

A task is not complete until:
1. Code is written.
2. Tests pass (command output captured).
3. Linter/type checker passes (if applicable).
4. PR is created or commit SHA is provided.

Saying "I have started" or "the implementation is done" without
evidence is not acceptable.

## Rule 7: Generated Evidence Files

Evidence may include:

```
# test output
python -m pytest tests/path/test_file.py -v 2>&1 | tail -20

# checker output
python scripts/checks/check_signal_schema.py --path /tmp/sample.csv

# git diff
git diff --stat

# coverage (optional)
coverage report
```

All evidence must be attached to the PR or linked from the PR body.

## Rule 8: Ask Before Cross-Boundary Changes

If a change crosses a boundary defined in
[strategy-boundary-contract.md](strategy-boundary-contract.md)
(e.g. a strategy PR touches calendar.py), the agent must flag this
and request explicit approval before proceeding.

## Rule 9: Bad Answers

The following are **not acceptable** as PR summaries:

| Bad answer | Why |
|---|---|
| "Refactored X for clarity" | No evidence |
| "Fixed date handling" | Which date? What was wrong? |
| "Added check_no_lookahead" | No test output, no example command |
| "All tests pass" | No command output |
| "Minor change, no impact" | Your judgment, not facts |
