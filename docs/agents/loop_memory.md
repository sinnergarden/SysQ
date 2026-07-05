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


### LM-002: Runtime inference tasks need explicit provenance + post-task loop trigger

- Status: accepted
- Trigger: A manual latest-feature inference task (top200 candidates on 2026-07-03) was executed without UC classification, sysq-daily skill, or provenance checks. The framework gap was only discovered after the user asked for reflection.
- Failure type: skill_gap + harness_gap + memory_gap
- Root cause: Existing AI workflow mainly covered code-change PRs, not runtime inference tasks before daily shadow. No check existed to verify inference artifact provenance. Improvement Loop existed as documentation but had no post-task trigger.
- Fix: Add UC_DAILY_INFERENCE_RUN. Strengthen sysq-daily with Manual / Ad-hoc Inference Run section. Add check_inference_artifact.py for provenance. Add Post-task Loop Check to AGENTS.md and sysq-daily skill so every task ends with a loop check.
- Validation:
  - Future manual inference task must report UC, selected skill, signal_date, strategy_id, model provenance, artifact path, and check result.
  - `python harness/checks/check_inference_artifact.py --artifact harness/checks/test_fixtures/inference_artifact_valid.json` → PASS
  - `python harness/checks/check_inference_artifact.py --artifact harness/checks/test_fixtures/inference_artifact_invalid.json` → FAIL
  - Future SysQ tasks must end with `Loop check: no new framework gap found` or a structured `Loop Finding`.
- Applies to:
  - AGENTS.md (§4a + §8 Post-task Loop Check)
  - .claude/skills/sysq-daily/SKILL.md
  - docs/requirements/harness_map.yaml (UC_DAILY_INFERENCE_RUN)
  - docs/requirements/domains/daily_ops.md
  - harness/checks/check_inference_artifact.py
  - docs/agents/SYSQ_LOOP_ENGINEERING.md (post-task trigger)

## Rejected Lessons

None yet.
