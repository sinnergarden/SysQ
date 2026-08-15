# Domain: UI Analysis

## Domain Scope
只读 UI 可视化层：策略看板、回测收益图、单股视角 review、信号 IC 可视化、账户查询。
不包含：通过 UI 下单、修改策略参数、修改 ledger 状态。

## UC_UI_ANALYSIS

### Status
draft

### Source
**新增 use case**，不在 `docs/USE_CASES.md` 现有 UC 编号中。设计留 TODO。

### User Goal
通过只读 UI 查看策略状态、回测结果、信号指标、账户摘要。首批交付两个视图：回测收益图 + 单股视角 review。

### Scope
包含：
- 策略看板（最新信号、持仓、PnL）
- 回测结果查看与对比（含净值曲线、基准对比）
- canonical 回测产物查看（orders 明细、月度/周度收益热力图、成本分析）— 从不可变回测 artifact 只读派生，不触碰 qsys/backtest/ 写入路径
- 单股视角 review — 回测中单只股票的买入/卖出/持仓/信号/特征
- 信号 IC/RankIC 可视化
- 账户/ledger 只读查询
- 数据 readiness 查看

不包含：
- 通过 UI 下单
- 修改策略参数或 ledger 状态
- 生产级 dashboard（Phase 3+）

### Inputs
- daily read model（`qsys/ops_api/`）
- research index / experiment manifest
- ledger query API

### Outputs
- UI 页面（只读，不产生持久化 artifact）

### Canonical Entrypoints
- `scripts/run_research_ui_api.py`

### Key Artifacts
- 无持久化 artifact（UI 是读取层）

### Required Checks
- TBD: UI read-only check（禁止 UI 代码写 ledger/broker）

### Owner Agent
ui_agent

### Allowed Paths
- `scripts/run_research_ui_api.py`
- `qsys/research_ui/`
- `qsys/ops_api/`
- `docs/requirements/`
- `tests/`

### Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`
- `deploy/`

### Open Questions
- （已定）首批交付两个视图：回测收益图 + 单股视角 review。

## UC_UI_SINGLE_STOCK_REVIEW

### Status
merged

### Notes
此 UC 已融合到 UC_UI_ANALYSIS 首批交付中的"单股视角 review"。不设独立 canonical entrypoint。
历史参考见原 `uc_single_stock_review.md`。
