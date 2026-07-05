# AI 编排指导

> 本文档说明 SysQ 的 AI 操作模型设计哲学。
> 实际规则以 `AGENTS.md` 和 `harness_map.yaml` 为准。

## 操作模型

```
workspace .claude.md
  └→ AGENTS.md
       → 归类 use case
       → 读取 harness_map.yaml
       → 选择主 skill（如 sysq-daily）
       → 执行 skill SOP
       → 可选：使用只读 subagent 辅助
       → 运行 harness checks
       → 主对话返回最终结果
```

## 关键原则

1. **一个主对话**。主对话拥有任务连续性、状态、代码写入权限和最终决定权。不要委托给 subagent。
2. **Task skills**。如果任务行为重要，编码为 `.claude/skills/*/SKILL.md`。不依赖 agent 人格。
3. **Hard harness checks**。如果正确性重要，编码为 `harness/checks/*.py`。不依赖模型记忆。
4. **Optional read-only subagents**。Subagent 只做有界只读任务（搜索、总结、提取）。不做最终决策。
5. **No default agent team**。不要维护多个常驻 agent。默认 main_agent 对用户服务。

## 什么情况下用 subagent

✅ 可以用：
- 仓库搜索：`grep -r "some_function" --include="*.py" qsys/`
- 文档提取：读取大量长文档
- diff 审查：查看 PR diff
- 日志总结：汇总测试失败信息

❌ 不要用：
- 做是否重训/推理的最终决定
- 修改业务代码
- 输出最终候选股票
- 生产/promotion 决策

## 什么情况下新增 skill

- 同一类任务重复出现 3 次以上
- 任务有明确的输入、输出、执行顺序
- 任务的约束无法仅用 harness check 覆盖

## 什么情况下新增 harness check

- 决策点有明确的 PASS/FAIL 判断
- 判断逻辑可以自动化
- 模型声称不够可靠
