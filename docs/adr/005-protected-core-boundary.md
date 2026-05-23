# ADR 005: Protected Core Boundary

**状态**：已采纳 (Accepted)
**日期**：2026-05-23

## 背景 (Context)

SysQ 已从研究脚本集合演化为具有准生产 daily ops 核心的系统。SQLite ledger 已成为影子/仿真账户状态的事实标准。随着系统成熟，需要明确定义哪些模块是"保护核心"——不可随意修改，以保证生产稳定性。

在此之前，所有模块理论上都可被任意 agent 或开发者修改，缺乏明确的边界控制和变更审查要求。这导致以下风险：

1. **策略研究与生产核心耦合**：研究过程中的临时修改可能意外影响回测或 daily ops 行为。
2. **缺乏变更审计**：核心模块的变更未强制包含回滚计划和影响评估。
3. **测试覆盖不完整**：核心模块变更有时未伴随充分的回归测试。

## 决策 (Decision)

我们决定定义 **Protected Core Boundary**——一组受保护的模块和规则。任何对这些模块的修改都必须满足明确的 PR 要求。

### Protected Core 列表

以下路径及其递归子模块属于 Protected Core：

| 路径 | 说明 |
|------|------|
| `qsys/data/` | 数据接入、数据健康检查、数据版本管理 |
| `qsys/ledger/` | SQLite 账本（账户、订单、成交、现金、持仓、快照） |
| `qsys/backtest/` | 回测引擎、组合构建、交易成本模型 |
| `qsys/trader/account.py` | 账户抽象层 |
| `qsys/trader/matcher.py` | 成交匹配引擎 |
| `qsys/ops/run_archive/` | 运行归档与产物管理 |
| `scripts/run_alpha_v1_daily.py` | Alpha V1 daily ops 主入口 |
| 生产执行配置 | 运行清单、daily DAG 配置 |
| broker bridge | 券商桥接接口（`qsys/broker/`） |
| 生产报告 | `scripts/ops/check_shadow_status.py` 等运营监控脚本 |

### Protected Core 修改规则

Agent/开发者不得修改 Protected Core，除非任务明确属于以下类型之一：

| 允许类型 | 说明 |
|----------|------|
| Core bugfix | 修复 Protected Core 中的缺陷，不改变语义预期 |
| Core feature | 在 Protected Core 中新增功能的 PR，需明确说明业务需求 |
| Schema migration | 数据库 schema 变更，需提供迁移脚本和回滚方案 |
| Performance optimization | 性能优化不改变语义（如查询优化、缓存引入） |
| Observability improvement | 增加日志、指标、监控，不改变业务逻辑 |
| Test-only change | 在 Protected Core 对应测试文件中的新增或修复 |

### PR 必须包含的信息

如果 Protected Core 被修改，PR 必须显式包含以下内容：

1. **Core Change Reason** — 为什么必须修改 Protected Core，而非在 research 层解决。
2. **Semantic Impact** — 变更是否改变了回测结果、daily 交易结果、账本状态或账户行为。
3. **Regression Tests** — 变更后的回归测试结果（至少包含涉及模块的全量测试）。
4. **Rollback Plan** — 在生产环境中回滚此变更的方案。

### 禁止事项

以下行为在 Protected Core 中严格禁止：

- ❌ 修改 fill/matcher 逻辑以改善特定策略的 PnL
- ❌ 放松 T+1 约束以通过测试
- ❌ 绕过 ledger 进行临时研究（如直接读写 shadow CSV 代替 ledger）
- ❌ 为单一策略修改交易成本/滑点假设
- ❌ 将 research 脚本直接接入 production daily DAG
- ❌ 允许 agent 自动提交真实券商订单

## 影响 (Consequences)

### 正面影响

- **生产稳定性**：核心模块的变更受到审查，降低意外破坏的风险。
- **分层清晰**：research 和 production 之间的边界明确，
- **审计可追溯**：所有核心变更都附带原因、影响评估和回滚计划。

### 代价

- **额外 PR 开销**：即使简单变更也需要填写 Core Change Reason 等元信息。
- **灵活性降低**：需要修改核心模块的研究实验需要额外的审查步骤。

### 缓解措施

- Research 层（`research/`、`qsys/signal/`、`qsys/feature/`、`qsys/model/`）不受此规则限制。
- 实验性策略可通过 Candidate 生命周期在 Shadow 账户中安全验证，无需直接修改核心。

## 后续

- [ ] 实施 `scripts/dev/check_protected_core_changes.py` 脚本，自动检测 PR 中是否包含 Protected Core 变更。
- [ ] 在 CI 中集成保护核心检查（可选 stage，非阻塞）。
- [ ] 定期审查 Protected Core 列表，确保与实际系统架构同步。
