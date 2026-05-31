# REPO_LAYOUT

本文档说明 SysQ 中代码、配置、数据、artifact、report 和 legacy compatibility path 的放置规则。
系统设计见 `docs/ARCHITECTURE.md`，模块边界见 `docs/CONTRACTS.md`。

---

## 1. Principles

- 代码、配置、运行产物、研究产物、账户状态分离。
- Research 产物不直接进入 Production。
- Daily evidence 只追加归档，不作为随意临时文件清理。
- Legacy compatibility path 可以读，但不得扩张新依赖。
- UI / monitoring 优先读 read model / artifact，不直接写状态。
- 新路径必须先确认是否已有现成目录，不随意新增平级目录。

---

## 2. Code Layout

| 路径 | 用途 | 备注 |
|------|------|------|
| `qsys/` | 核心 Python package | 业务逻辑下沉到这里 |
| `qsys/ops/` | daily runner / ops 编排内部模块 | Protected Core |
| `qsys/backtest/` | backtest engine 与 strategy runner | 共享执行语义收束目标 |
| `qsys/ledger/` | ledger service | 目标 SOT 写入边界 |
| `qsys/trader/` | order generation / matcher / account | Protected Core |
| `qsys/strategy/` | 策略 adapter（alpha_v1 等） | 策略层 |
| `qsys/broker/` | broker / MiniQMT client | 高风险边界 |
| `qsys/research/` | rolling runner / evaluation / experiment | research chain |
| `qsys/feature/` | feature groups、feature logic | research input |
| `qsys/model/` | model zoo、training logic | model layer |
| `qsys/signal/` | signal store、expression runner | signal 层 |
| `qsys/evaluation/` | strict evaluator | 评估工具 |
| `qsys/execution/` | execution backend | simulated execution |
| `qsys/live/` | live ops（account、scheduler、reconciliation） | 旧 legacy 路径引用模块 |
| `qsys/reports/` | daily / backtest / strict eval / unified schema 报告 | report 生成 |
| `qsys/analysis/` | 分析工具 | 辅助分析 |
| `qsys/data/` | data adapter、health check | 数据层 |
| `qsys/common/` | 通用工具（config、deprecation 等） | 基础设施 |
| `qsys/config/` | 配置加载 | 基础设施 |
| `qsys/core/` | archive / contracts / run_id | 核心基础组件 |
| `qsys/risk/` | risk constraints | 风险控制 |
| `qsys/workflow/` | workflow 编排 | 任务编排 |
| `qsys/dataview/` | 数据视图 | 数据可视化 |
| `qsys/experiment/` | 实验管理 | experiment 管理 |
| `qsys/research_ui/` | Research UI 模块 | current（完整 UI 未完全实现） |
| `scripts/` | CLI / shell entrypoints | 只做编排，不放复杂业务核心 |
| `scripts/ops/` | data sync、shadow daily、ops 入口 | Protected Core 运维 |
| `scripts/ops/audit_state_paths.py` | data/trade.db + real_account.db + shadow/ 只读审计 | 0 write，0 migration |
| `scripts/research/` | rolling research、signal eval、backtest from signal、experiment index | research 入口 |
| `scripts/live/` | broker order、reconciliation、alpha_v1 live plan | live ops 入口 |
| `scripts/checks/` | data leakage / schema / order intents / portfolio snapshot / reconciliation result / daily read model / experiment index 检查 | 产检工具（run_daily.py 产物验证）|
| `tests/` | unit / regression tests | 改动必须补测试 |

---

## 3. Config Layout

| 路径 | 用途 | 备注 |
|------|------|------|
| `config/` | 基础运行配置（`settings.yaml`、`settings.example.yaml`） | 本地敏感配置不入库 |
| `configs/` | 研究/策略/Candidate 配置 | 结构化策略配置 |
| `configs/research/` | rolling research pipeline 配置 | `.yaml` 文件 |
| `configs/signal_expressions/` | signal expression 组合配置 | `.yaml` 文件 |
| `configs/strategies/` | 策略级运行参数 | 如 `alpha_v1.yaml` |
| `configs/alpha_v1/` | alpha_v1 专用配置 | 回测、训练参数 |
| `deploy/systemd/` | systemd service / timer | 生产级定时任务配置 |
| `docs/schema/` | artifact 字段级 schema | 如 `signal-artifact.md` |
| `docs/adr/` | 架构决策记录 | 长期决策 |
| `docs/features/` | 功能规格 / 历史设计 | 非 current truth |
| `docs/ops/` | 运营 SOP | 操作手册 |

---

## 4. Data Layout

| 路径 | 类型 | 角色 |
|------|------|------|
| `data/raw/` | raw market data | 原始行情数据 |
| `data/qlib_bin/` | qlib serving data | 研究/训练/回测输入 |
| `data/audit/` | audit / readiness reports | 数据检查结果（如 `sync_csi800_*.json`） |
| `data/research/` | research analytics 中间数据 | 信号、label、evaluation 缓存 |
| `data/models/` | model artifact + manifest | 模型产物与 `production_manifest.yaml` |
| `data/trade.db` | SQLite ledger | **目标** Account State / Execution Ledger SOT |
| `data/meta/real_account.db` | SQLite legacy account store | active legacy compatibility |
| `data/meta/meta.db` | SQLite meta store | 元数据缓存 |
| `data/meta/shadow_test.db` | SQLite shadow test store | shadow 测试用 |
| `data/feature/` | feature 计算中间数据 | feature engineering |
| `data/clean/` | 清洗后数据 | clean data |
| `data/experiments/` | 实验中间数据 | experiment runs（research pipeline 配置中的 root 路径） |

**注意**：
- `data/trade.db` 是目标 SOT，但旧入口仍可能写 `data/meta/real_account.db`。
- 不得随意删除或迁移 DB。迁移前必须完成 consumer 切换、数据迁移和回归验证。

---

## 5. Daily Artifact Layout

| 路径 | 用途 | 状态 |
|------|------|------|
| `daily/{date}/pre_open/signals/` | 盘前 signal 文件 | current |
| `daily/{date}/pre_open/plans/` | 盘前 plan 文件 | current |
| `daily/{date}/pre_open/order_intents/` | 盘前订单意图 | current |
| `daily/{date}/pre_open/manifests/` | 盘前 manifest | current |
| `daily/{date}/pre_open/reports/` | 盘前报告 | current |
| `daily/{date}/post_close/` | 盘后产物（fills、MTM、reconciliation_result.json、snapshot、daily_ops_digest） | current |
| `daily/{date}/snapshot_index.json` | 每日快照索引 | current |

**原则**：
- Daily evidence 不当临时文件随意删除。
- UI / monitoring 优先消费 `daily/{date}/` 下的 read model。

---

## 6. Research Artifact Layout

| 路径 | 用途 | 备注 |
|------|------|------|
| `experiments/` | experiment outputs、signal cache、eval reports、backtest summary | 研究主产物 |
| `research/` | 研究分析结果（factors、decisions） | 辅助研究 |
| `reports/` | 可读报告（如 `csi800_1w_audit_*`） | 审计/报告 |
| `runs/` | 运行记录（训练、推理、回测） | 运行时产物 |
| `mlruns/` | MLflow 实验跟踪 | MLflow 元数据 |
| `tmp/` | 运行时临时文件 | 不保留，不归档 |
| `scratch/` | 临时实验目录 | **target convention**——当前不存在，需要时手动创建，不自动创建 |

**原则**：
- 进入 Candidate 前，research artifact 必须可追溯 `run_id`。
- `scratch/` 不能作为 promotion evidence。
- Research Analytics / DuckDB index 可以从 `experiments/` 构建。
- 不把 research artifact 直接接入 production。

---

## 7. Legacy Compatibility Paths

| 路径 | 当前角色 | 处理原则 |
|------|---------|---------|
| `shadow/` | legacy shadow JSON/CSV（`account.json`、`positions.csv`、`ledger.csv`） | 当前仍可能被旧入口读写，不扩张新依赖 |
| `shadow_alpha_v2/` | shadow alpha_v2 产物 | 同上 |
| `data/meta/real_account.db` | legacy account store | 迁移前保留 |
| `scripts/run_preopen.sh` | legacy preopen shell wrapper（DEPRECATED） | systemd 当前仍调用，不扩张 |
| `scripts/run_postclose.sh` | legacy postclose shell wrapper（DEPRECATED） | systemd 当前仍调用，不扩张 |
| `scripts/run_daily_trading.py` | legacy daily trading entry | 被 shell wrapper 调用 |
| `scripts/run_post_close.py` | legacy post-close entry | 被 shell wrapper 调用 |
| `scripts/run_alpha_v1_daily.py` | legacy alpha_v1 daily entry（DEPRECATED） | 被 shell wrapper 调用 |

**规则**：
- 可以读。
- 不能随意删。
- 不能在新功能中增加新依赖。
- 迁移需单独 PR。
- 只读审计工具 `scripts/ops/audit_state_paths.py` 可检查此路径状态（0 write，0 migration）。

---

## 8. UI / Monitoring Read Paths

UI / monitoring 应优先读以下路径：

- `daily/{date}/pre_open/reports/`、`daily/{date}/post_close/` — daily ops read model
- `data/audit/` — data readiness 报告
- `experiments/` — research analytics index（通过 DuckDB 查询）
- `data/trade.db` — ledger 只读查询（不直接写）
- `data/models/production_manifest.yaml` — 当前 approved manifest

**禁止**：

- UI 写 ledger。
- UI 下单。
- UI 修改策略。
- UI 直接依赖 legacy `shadow/` 文件作为长期接口。

---

## 9. Where Should New Things Go?

| 新东西 | 应放位置 |
|--------|---------|
| 新 strategy adapter | `qsys/strategy/<name>/` |
| 新 feature group | `qsys/feature/` 或现有 feature registry |
| 新 model type | `qsys/model/` |
| 新 research config | `configs/research/` |
| 新 experiment result | `experiments/` |
| 新 daily run result | `daily/{date}/` |
| 新 ops report | `daily/{date}/reports/` 或 `reports/` |
| 新 artifact schema | `docs/schema/` |
| 新 architecture decision | `docs/adr/` |
| 新 long-term interface rule | `docs/CONTRACTS.md` |
| 新 operating procedure | `docs/ops/` |
| 临时验证脚本 | `scripts/` 下临时脚本，合并前清理 |

---

## 10. Do Not

- 不把业务核心写进 `scripts/`（scripts 只做编排）。
- 不把 daily evidence 放进临时目录。
- 不把 production 状态写进 research artifact。
- 不把 UI 做成写状态入口。
- 不把 legacy path 扩张为新标准。
- 不无说明移动或删除历史产物。
