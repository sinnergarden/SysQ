# SysQ Improvement Loop Engineering

> Loop is not agent team.
> Loop is not autonomous runtime.
> Loop is failure-driven improvement of skills, harness, use cases, and memory.

## 核心结构

```
Trigger → Capture → Classify → Propose → Validate → Update memory → Stop
```

- **Reviewer proposes.** 只读审查，不修改文件。
- **Main conversation decides.** 主对话拥有决策权。
- **sysq-dev implements.** 实施修改。
- **Harness validates.** 用原失败用例验证。
- **Loop memory records only validated lessons.**

## Trigger


Loop is not only a document.
Loop requires a trigger.
SysQ uses two triggers:
1. **Failure trigger**: failed check, user correction, abnormal output.
2. **Post-task trigger**: every task ends with a loop check.

Loop 由以下事件触发：

- user correction
- failed harness check
- PR review finding
- repeated agent mistake
- daily ops safety failure

## Failure Taxonomy

| 类型 | 说明 |
|------|------|
| `skill_gap` | task skill 缺少某个步骤或约束 |
| `harness_gap` | 缺少自动化检查 |
| `harness_semantic_bug` | harness check 语义错误 |
| `usecase_gap` | 缺少对应的 use case 定义 |
| `boundary_gap` | allowed/forbidden paths 映射不准确 |
| `memory_gap` | loop memory 缺少已验证的经验 |
| `artifact_contract_gap` | artifact 字段/路径约定不一致 |
| `documentation_conflict` | 文档之间或文档与代码不一致 |

## Allowed Improvements

- 收紧一个 skill（补充约束或步骤）
- 新增或修复一个 harness check
- 更新 use case 文档
- 更新 `harness_map.yaml`（仅当边界映射错误时）
- 向 `loop_memory.md` 添加已验证的 lesson

## Forbidden Improvements

- 削弱 harness 来让当前工作通过
- reviewer 直接修改文件
- 在 subagent 中做 retrain/infer/candidate 最终决策
- 引入 agent team runtime
- 向 memory 添加未经验证的 lesson

## Stopping Rule

- 原失败用例通过 **且** 回归检查通过 → 停止。
- 当修复涉及交易、生产、broker、ledger、模型晋级或无法验证时 → 升级到人工。
