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
11. universe_snapshot_semantics / universe_hash
12. feature_list_hash / feature_snapshot_hash
13. feature_availability / margin as_of_date

### 规则
- 禁止用 free-form 文本直接给候选股票。
- 禁止只说"latest model"，必须解析到具体 model_id / model_path / model_hash。
- 禁止输出无法追溯的 candidate（缺少 provenance 字段）。
- current constituents snapshot 只允许最近已完成交易日；PIT provider 未实现前，历史推理必须 fail closed。
- positional model input 必须严格匹配 pinned center/scale 的 ordered index，禁止只比较集合或数量。
- 日期解析与 CandidateRun `created_at` 必须共享进入 inference 时捕获的单一 run anchor。
- artifact checker 必须从权威日历独立复核 next-open 和 label maturity，不能只信 artifact 声明。
- 任一模型使用特征当日为常数或超过逐特征缺失阈值时，必须 fail closed。
- financial_rc 的 T 日夜间运行使用 T 日普通特征和精确 T-1 开市日两融输入；
  训练、推理、模型 meta 和 CandidateRun 必须共享同一 availability contract。
- 每日 CSI800 apply 同步必须回补至 T-1 margin as-of 并记录 audit；不得因为
  T 日 margin_detail 尚未发布而等待次日盘前，也不得把 T 日空值当成功。
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
python harness/checks/check_shareholder_data_freshness.py --as-of-date <date>
```

For `financial_rc`, daily sync must catch up both shareholder sidecars before
readiness/inference. Availability is `ann_date <= data_date`; never substitute
report `end_date`. A failed global source-freshness check blocks the whole run,
while a row beyond the configured stale limit is ineligible. After a historical
repair, invalidate/rebuild derived caches, models, and CandidateRuns listed by
the impact audit.
For a current-snapshot universe, also verify that every member has the configured
feature lookback before its index inclusion date. Membership windows select the
live cross-section; they do not bound feature history. A canonical CandidateRun
must enumerate all excluded instruments and their missing features.
