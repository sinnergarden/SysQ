# sysq-dev

## Purpose
SysQ 开发与诊断 skill：诊断检查、代码实现、harness/check 开发、临时请求（UC_TEMPORARY）、模型训练侧开发（UC_MODEL_TRAINING dev）。

## Inputs
- 任务描述（诊断 / 开发 / harness check 新增 / 临时请求）
- 对应 UC：UC_DIAGNOSTICS、UC_TEMPORARY_REQUESTS、UC_MODEL_TRAINING（dev 侧）
- 相关文件路径

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- 对应 domain 定义（`docs/requirements/domains/{domain}.md`）
- 相关代码文件

## Workflow
1. 归类 UC（UC_DIAGNOSTICS / UC_TEMPORARY / UC_MODEL_TRAINING dev）。
2. 显式声明 SCOPE（Task type / UC / Skill / Scope），通过 EXECUTION_GATE（路径在 allowed_paths 内、不触 forbidden_paths）。
3. 诊断类：只读检查，运行 `scripts/checks/` 与 `harness/checks/` 对应 check，输出结构化结果，不做自动修复。
4. 开发类：在 allowed_paths 内实施；遵守"禁止混 PR"，改动走分支 + PR，不直接推 main。
5. 运行对应 harness checks，失败则停止并报告。
6. LOOP_CHECK：检查是否有 gap（skill / harness / usecase / provenance），判定是否需 reviewer。
7. 按 AGENTS.md OUTPUT CONTRACT 输出。

## 规则
- 新增 harness check 必须同步 `harness_map.yaml`（禁止只加 check 不挂 map）。
- 新增功能必须补文档（禁止不补文档直接加功能）。
- 临时/一次性脚本只放 `scripts/dev/` 或 `scratch/`，不在 `scripts/` 顶层新增入口。

## Never
- 未过 GATE 就动手
- 直接推 main
- 绕过 use case / skill / harness
- 改 broker / trader / ledger / deploy / systemd（除非 UC 明确允许）

## Required checks
```bash
python harness/checks/check_usecase_registry.py
python harness/checks/check_agent_docs.py
# 按 UC 额外运行对应 harness checks（见 harness_map.yaml）
```
