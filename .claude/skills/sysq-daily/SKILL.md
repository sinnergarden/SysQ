# sysq-daily

## Purpose
Daily operational workflow for SysQ: data readiness, label maturity, retrain eligibility, inference readiness, candidate output.

## Inputs
- trade_date
- strategy_id
- horizons
- optional force_retrain=false

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- relevant UC blocks: UC_DAILY_OPS, UC_DAILY_INFERENCE_RUN, UC_MODEL_TRAINING, UC_CANDIDATE_PROMOTION
- model registry / pointer docs or code
- promotion pointer (`data/research/promotions/shadow.yaml`) + candidate lineage
- latest data readiness artifact if available

## Workflow
1. Resolve trade_date.
2. Check data readiness.
3. Check label maturity per horizon.
4. Decide retrain eligibility.
5. Verify model pointer.
6. Decide inference eligibility.
7. Produce candidate list only from standard artifacts.
8. For promotion: resolve the shadow promotion pointer (UC_CANDIDATE_PROMOTION), verify candidate lineage, and record provenance.
9. Report checks and risks.

## Manual / Ad-hoc Inference Run

适用于 infer 最新一天、trigger 最新 pred、用最新 feature 预测、生成最新 signal/candidate、shadow 前观察模型输出。

### 执行前必须确认
1. selected UC = `UC_DAILY_INFERENCE_RUN`
2. signal_date
3. execution_date
4. strategy_id
5. feature snapshot date/path
6. model pointer — 解析到具体 model_path，不只是"latest"
7. model_id / model_hash
8. train_start / train_end
9. calibration artifact / calibration window（如适用）
10. output artifact path

### 规则
- 禁止用 free-form 文本直接给候选股票。
- 禁止只说"latest model"，必须解析到具体 model_id / model_path / model_hash。
- 禁止输出无法追溯的 candidate（缺少 provenance 字段）。
- 禁止写 broker / trader / ledger / production。
- 如果缺 provenance 字段，结果只能标记为 exploratory，不能标记为 candidate artifact。

### 输出格式

```
UC: UC_DAILY_INFERENCE_RUN
Selected skill: sysq-daily
signal_date: <date>
execution_date: <date>
strategy_id: <id>
feature_snapshot: <path>
model_pointer: <path>
model_id: <id>
model_path_or_hash: <path or hash>
train_start: <date>
train_end: <date>
calibration: <method or none>
prediction_artifact: <path>
candidate_artifact: <path>
checks_run: <list>
known_gaps: <list>
```

## End-of-task Loop Check
For daily ops, inference, signal, candidate, retrain, or shadow-related tasks:
Before final answer, check whether:
- UC was selected
- this skill was read
- harness checks were run or intentionally skipped
- output artifact has provenance
- any temporary workaround was used
- user had to correct the workflow

If any answer indicates a gap, output Loop Finding and propose the smallest fix.


## Never
- invent stocks
- train with immature labels
- use latest model for historical inference unless explicitly allowed
- modify model code during daily decision
- confuse research artifacts with daily/shadow/prod artifacts

## Required checks
```bash
python harness/checks/check_label_maturity.py --trade-date <date> --horizon <h> --train-end <date>
python harness/checks/check_daily_inference_ready.py --trade-date <date> --strategy-id <strategy>
python harness/checks/check_inference_artifact.py --artifact <path>
```
