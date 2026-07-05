# AGENTS — SysQ AI 操作总入口

> 所有 SysQ 相关工作必须从这里开始。
> 系统架构见 `docs/ARCHITECTURE.md`，use case registry 见 `docs/requirements/01_usecase_index.md`。

---

## 1. Mission

SysQ 是一个**个人可维护的 A 股日频量化系统**。AI 负责：需求拆解、方案实施、测试修复、文档同步、边界检查。

执行模式：**skill-first + harness-first** — 行为编码为 skill，正确性编码为 harness check。主对话拥有最终权。

---

## 2. 执行 FSM

```
INIT → UC → SKILL → SCOPE → GATE → EXECUTE → HARNESS → LOOP → DONE
```

不得跳步。

---

## 3. UC（归类）

归类到 `01_usecase_index.md` 中的一个 UC。读取 `harness_map.yaml` 和对应 `domains/*.md`。

- 无 UC → 停止，走 `UC_TEMPORARY_REQUESTS`
- 同类请求 ≥2 次 → 补文档

---

## 4. SKILL（选技能）

| Skill | 用途 | 对应 UC |
|-------|------|---------|
| `sysq-daily` | 日频运营 + 手工推理 | UC_DAILY_OPS, UC_DAILY_INFERENCE_RUN, UC_MODEL_TRAINING |
| `sysq-dev` | 开发 | UC_DIAGNOSTICS, UC_TEMPORARY |
| `sysq-stock-research` | 基本面研究 | UC_STOCK_FUNDAMENTAL_RESEARCH |

必须显式选择（`.claude/skills/*/SKILL.md`）。无 skill → 停止。

---

## 5. SCOPE（声明范围）

开始执行前输出：

```
Task type: <analysis / inference / code_change / docs>
UC:
Skill:
Scope: <read-only / write-code / PR required>
```

PR 不确定 → 默认 PR。

---

## 6. EXECUTION_GATE（阻断条件）

| 条件 | 不满足 → |
|------|---------|
| UC 已识别 | 走 UC_TEMPORARY |
| skill 已选 | 停止 |
| 路径在 allowed_paths 内 | 停止 |
| forbidden_paths 未触碰 | 停止 |
| PR requirement 明确 | 默认 PR |

全部通过才能执行。

---

## 7. EXECUTE（执行）

在 `allowed_paths` 内实施变更。

推理类任务（infer / pred / candidate / shadow 前检查）：
1. 归类 `UC_DAILY_INFERENCE_RUN`，读 `sysq-daily/SKILL.md` 的 Manual Inference 章节
2. 确认 model_hash、train_start/end、signal_date 等 provenance
3. 运行 `check_daily_inference_ready.py` + `check_inference_artifact.py`
4. 缺 provenance → 标记 exploratory

---

## 8. HARNESS（检查）

按 UC 运行对应 checks（定义在 `harness_map.yaml`）。check 失败 → 停止+报告。

---

## 9. LOOP_CHECK（复盘）

每个任务结束时强制输出：

```
Loop Result:
- Status: <clean / found>
- Type: <gap type or none>
- Root cause:
- Fix suggestion:
- Reviewer needed: <yes / no>
```

检查是否遵守了 UC/skill/harness、有无 correction/retry/缺 provenance/临时 workaround、是否暴露了 gap（skill / harness / usecase / memory / provenance 等）。

---

## 10. REVIEWER_TRIGGER（审查触发）

触发条件：跳过规则 / 出现 latest/mtime/symlink / 缺 provenance / 用户指异常 / 重复错误 / 改 skill/harness/memory。

输出：`Reviewer needed: yes` + 审查任务描述。

---

## 11. 不可越界规则

- **禁止推 main**（必须 PR，零例外）
- 禁止 `latest` / mtime / symlink
- 禁止改 broker / trader / ledger / deploy / systemd
- 禁止从 `archive/` import
- 禁止混 PR（架构+业务+文档+测试分开）

---

## 12. OUTPUT CONTRACT

按序输出：

```
Summary
Detected UC:
Skill:
Scope:
Actions:
Changed files:
Harness checks:
Loop Result:
Reviewer needed:
Open questions:
```
