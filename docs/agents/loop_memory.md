# SysQ Loop Memory

> This file stores validated lessons only.
> It is not a scratchpad.
> It is not a conversation transcript.
> Each accepted lesson must be tied to a concrete failure and validation.

---

## Accepted Lessons

### LM-001: Future-return label maturity direction

- Status: accepted
- Trigger: PR #214 review found that `check_label_maturity.py` initially calculated latest mature label date as `trade_date + horizon`.
- Failure type: harness_semantic_bug
- Root cause: For future-return labels, label date D matures only after D + H trading days. At trade_date T, the latest mature label date is T - H trading days.
- Fix: Calculate `latest_mature_label_date = trade_date - horizon trading days`.
- Validation:
  - `python harness/checks/check_label_maturity.py --trade-date 2026-07-05 --horizon 5 --train-end 2026-06-29` → PASS
  - `python harness/checks/check_label_maturity.py --trade-date 2026-07-05 --horizon 5 --train-end 2026-06-30` → FAIL
- Applies to:
  - `harness/checks/check_label_maturity.py`
  - `.claude/skills/sysq-daily/SKILL.md`
- Do not generalize to:
  - A-share trading calendar implementation. Weekday calendar remains a TODO until replaced by qlib/project trading calendar.

## Rejected Lessons

None yet.
