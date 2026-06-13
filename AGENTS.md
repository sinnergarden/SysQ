# AGENTS — AI 操作协议

系统设计见 `docs/ARCHITECTURE.md`，当前优先级见 `ROADMAP.md`。

---

## 1. 当前工作模式

Framework Stabilization + 投研研发迭代。人主导、AI 辅助执行。

- 人负责方向判断、验收标准、高风险裁决。
- AI 负责读代码、提出方案、实施小步改动、跑测试、同步文档。
- 单轮尽量只解决一个主题，避免跨层大改。

---

## 2. 核心原则

- **Research 不能直接进入 Production** — 必须经过 Candidate/Shadow。
- **三条链路**：Research（回放）/ Daily Ops（推进）/ Candidate→Shadow→Production（晋级）。
- **Ledger SOT**：`data/trade.db` 是目标账户状态 SOT。`data/meta/real_account.db` 和 `shadow/` 是 legacy，不能擅自删除。
- **Data readiness check** 是训练、回测、daily ops 的前置条件。

---

## 3. 文档事实优先级

实际代码调用链 → `docs/ARCHITECTURE.md` → `ROADMAP.md` → `docs/adr/` → `archive/docs/features/`。

---

## 4. 必读文档路由

| 任务 | 优先阅读 |
|------|---------|
| 系统架构、模块边界 | `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` |
| 数据对象、artifact 字段 | `docs/CONTRACTS.md`, `docs/schema/`, `docs/REPO_LAYOUT.md` |
| 新增策略/feature/label/signal | `docs/ops/RESEARCH_STRATEGY_SOP.md`, `docs/CONTRACTS.md`, `docs/GENERATOR_DEV_GUIDE.md` |
| 新信号生成器 | `docs/GENERATOR_DEV_GUIDE.md` |
| 运行 daily ops、修改入口 | `docs/ops/DAILY_OPS_SOP.md`, `docs/ARCHITECTURE.md` |
| 修改 ledger/portfolio/execution | `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md` |
| 新增/移动文件或产物 | `docs/REPO_LAYOUT.md` |
| 修改路线图 | `ROADMAP.md`, `docs/ARCHITECTURE.md` |
| 清理 legacy/shadow/real_account | `docs/ARCHITECTURE.md`, `docs/REPO_LAYOUT.md` |

---

## 5. 许可

AI 可直接处理：
- 文档修订、bug fix、测试补充、只读审查
- 不改变语义的重构、小范围研究实验
- 在独立分支做低风险改动并运行测试

必须说明：改了什么、为什么改、如何验证、是否影响 Research / Daily Ops / Production。

---

## 6. PR Workflow

**代码变更必须走 PR。禁止直接推送到 main 分支。零例外。**

```
git checkout -b pr-<topic>
git add -A && git commit -m "..."
git push origin <branch>
gh pr create --title "..." --body "..."
# 用户 approve 后
gh pr merge <number> --squash --delete-branch
```

---

## 7. 必须先讨论

以下不能自动改，必须先说明影响并等待确认：
- DB schema、ledger/account/position/cash/order/fill 语义变化
- `data/trade.db`、`data/meta/real_account.db`、`shadow/` 的迁移或删除
- systemd、production entry、broker bridge、MiniQMT
- 交易规则变更（手续费、T+1、撮合规则）
- 公共接口删除或重命名
- Protected Core 核心语义变更
- 不可逆清理
- 真实下单/撤单/持仓修正

---

## 8. Protected Core

以下路径默认只允许只读分析、测试补充和文档说明：
`qsys/ledger/`、`qsys/backtest/`、`qsys/trader/`、`qsys/ops/daily_runner.py`、`qsys/broker/`、`qsys/strategy/alpha_v1/`、`scripts/ops/`、systemd 配置、artifact contract schema。

修改必须给出：修改原因、影响范围、回滚方式、最小验证命令、是否影响 production。

---

## 9. 零造轮子原则

**禁止在已有代码路径的情况下，手写一份等价的业务逻辑。**

## 9a. 新功能与文档规则

- 影响架构边界、引入新长期接口、改 schema/artifact、影响 Candidate/Shadow/Production、改 ledger/daily entry/broker → 先补设计文档。
- 普通实验、bugfix、小脚本可以直接实现并在 PR 里说明。
- `docs/adr/` 放长期架构决策；`docs/schema/` 放 artifact 字段约束。

---

## 10. 测试

提交前默认执行：

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```

按改动范围可用 `python -m unittest tests/<module>/`。

---

## 11. 日期与数据语义

- `signal_date`：信号来源日期（最近已收盘交易日）。
- `execution_date`：实际计划执行日期。
- 盘前推荐基于 T-1 收盘。数据 readiness 不满足时显式失败。
- 训练、回测、推理必须避免未来数据泄露。

### 11a. Lookahead 铁律（违反 = 无效结果）

**任何推理任务使用的模型训练截止日（train_end）必须不晚于推理目标日期（trade_date）。**

- 正常 daily ops：用 latest 模型做当天 prediction，OK。
- **回溯 backfill**：必须显式指定目标日期当时的模型版本，禁止用 latest。
- 禁止用 target_date 之后才产生的数据来预测 target_date 的信号。

---

## 12. 输出方式

结论 → 关键依据 → 风险/阻塞点 → 下一步动作。引用代码给明确路径。不把推测写成事实。

---

## 13. 禁止事项

**PR/分支**：禁止直接推送或合并到 main。零例外。

**数据/状态**：禁止删除 `shadow/`；删除或迁移 `real_account.db`；绕过 LedgerService 直接写生产状态；直接编辑 `data/trade.db`（只能通过 `run_daily.py` / `LedgerService` 写入）；切换 production systemd 入口。

**交易/生产**：禁止绕过 artifact contract 接入 Candidate/Production；让 Research artifact 直接进入 Production；自动提交真实券商订单；修改生产密钥。

**代码/依赖**：禁止从 `archive/` import 或调用脚本。
