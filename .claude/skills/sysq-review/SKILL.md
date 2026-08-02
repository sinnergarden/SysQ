# sysq-review

## Purpose
SysQ 代码审查 skill：审查 PR / 改动是否满足框架规则（use case 归属、canonical entrypoint、lookahead/leakage、latest 禁令、ledger 安全、PR 分层、文档同步）。用于 UC_DAILY_INFERENCE_RUN 与 UC_DIAGNOSTICS 的 review 角色。

## Inputs
- PR / 改动范围（分支、diff、changed files）
- 相关 UC、harness checks
- 审查 checklist（框架守卫规则：use case / entrypoint / lookahead / latest / ledger / PR scope）

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- `docs/ARCHITECTURE.md`
- 改动文件及其调用链

## Workflow
1. 确认改动归属的 UC 与 canonical entrypoint。
2. 逐项审查：
   - 是否归属既有 UC（无 → scope expansion，需显式报告）
   - 是否用 canonical entrypoints，是否新增 ad-hoc 脚本
   - 是否存在 lookahead / leakage（信号 feature 截止 ≤ data_date < trade_date）
   - 是否引入 latest / mtime / symlink 解析
   - ledger / 账户状态是否安全（无竞争 SOT、无绕过 idempotency）
   - PR 是否混层（架构+业务+文档+测试分开）
   - artifact lineage / provenance 字段是否完整
   - 文档是否同步且不过度宣称
3. 运行相关 harness checks 验证结论。
4. 输出审查结论。

## 输出格式
```
Verdict: merge / do not merge
Blocking issues: ...
Non-blocking issues: ...
Required fix: ...
```

## Never
- 未读代码就下结论
- 忽略 lookahead / ledger / PR scope 检查
- 对 deprecated / archive 代码过度升严重度

## Required checks
```bash
python harness/checks/check_no_latest_model_resolution.py
python harness/checks/check_model_resolution_boundary.py
python harness/checks/check_scripts_entrypoints.py
# 合并后补充：check_promotion_pointer.py（PR #218 引入，见 F03）
```
