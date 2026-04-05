# FEATURE: miniqmt_bridge_and_production_ops

## Goal

- 明确 Qsys 的长期定位：既是量化研究系统，也是线上生产可交易系统。
- 明确生产运行形态：daily 生产流程运行在 Win 上的 WSL 环境中，尽量减少 agent 介入，优先使用固定脚本、固定输入输出与严格 review。
- 为 MiniQMT 接入定义桥接方向：QMT 仅运行在 Windows 原生环境，Qsys 需要一层稳定的 broker bridge 与订单回流机制。

## System Positioning

Qsys 后续不是单纯研究仓库，也不是只做日报/推荐输出的系统，而是一个同时具备两种能力的系统：

- **研究性**：支持特征实验、模型训练、回测验证、严格评估、候选晋级
- **生产可交易性**：支持固定 daily 流程、生产模型管理、真实账户同步、执行桥接、盘后对账

这两种能力必须并存，但边界要清晰：
- 研究链路允许更高迭代速度
- 生产链路优先稳定、可审计、可回滚

## Production Operating Model

### Runtime

- daily 生产流程默认运行在 **Windows 主机上的 WSL** 中
- 生产脚本应尽量固定，不依赖 agent 的临场判断
- 任何会影响 daily 生产行为的改动，都应走更严格的 review 与回归

### Production Principles

- production 流程优先使用固定入口脚本或固定 adapter，不依赖临时命令拼接
- production 输入输出必须结构化，避免只看 stdout
- production 改动默认要求：文档、测试、回滚路径同时存在
- agent 可参与研究、设计、代码生成与 review，但生产流程本身应尽量去 agent 化

## MiniQMT Constraint

- MiniQMT / QMT 只能运行在 Windows 原生环境
- 因此 Qsys 的生产主流程与下单执行不在同一运行时
- 必须设计一层 bridge，让 WSL 侧生成的执行意图能安全地传递给 Windows 侧 QMT，并拿回订单/成交/账户状态

## Proposed Bridge Layers

### Layer 1: Order Intent

WSL 侧 Qsys 先只生成标准化的订单意图，而不是直接调用 QMT：

- source: `preopen-plan` / production daily script
- output: structured `order_intents` artifact
- fields should include:
  - symbol
  - side
  - target_shares / delta_shares
  - reference_price
  - price_policy
  - signal_date
  - execution_date
  - model_version
  - risk_tags

### Layer 2: MiniQMT Broker Adapter

Windows 侧提供一层 broker adapter，负责：
- 读取 QMT 账户状态
- 读取持仓、可卖数量、资金、委托、成交
- 接收订单意图并转换为具体委托
- 回写 order status / fills / rejects

### Layer 3: Execution Reconciliation

Qsys 侧继续消费 Windows 回流结果，形成：
- order status summary
- fill summary
- end-of-day account snapshot
- real vs shadow / target vs executed reconciliation

## Daily Flow Target State

### Pre-open (WSL)

1. 数据 readiness 检查
2. production model 解析
3. 生成 target portfolio
4. 生成 executable portfolio
5. 产出 `order_intents`
6. 交由 bridge 发往 Windows/QMT

### Intraday (Windows/QMT bridge)

1. 接收订单意图
2. 做 broker 约束转换
3. 下发委托
4. 记录委托、成交、撤单、拒单
5. 回写执行结果

### Post-close (WSL)

1. 拉取/读取 Windows 侧执行结果
2. 更新 real account snapshot
3. 完成 reconciliation
4. 输出 daily ops report
5. 为次日流程留存标准状态

## Current Gaps

当前 repo 相对这个目标，主要还缺：

- Windows 原生的 MiniQMT 真实桥接实现仍未落地
- 没有稳定的 WSL -> Windows -> WSL 回流通道约定
- production daily 仍偏脚本可运行，但还不是“低 agent 依赖的固定生产脚本”

当前已落地的最小骨架：

- `order_intents` artifact contract 已落地
- `qsys/broker/miniqmt.py` 已落地 dry-run bridge contract
- 已定义订单生命周期枚举：`pending / partial_fill / filled / canceled / rejected`
- 当前 `MiniQMTAdapter` 仅负责：加载 intent、校验 lot/price/side、转换 dry-run broker order、保留未来 Windows bridge 的接口位

## Development Priority

### P1
- 固化 `preopen-plan` adapter
- 补 target vs executable contract
- 补 `order_intents` artifact contract

### P2
- 设计并实现 `qsys/broker/miniqmt.py` 的抽象接口
- 先支持读取账户、持仓、委托、成交，不急着自动下单

### P3
- 实现 order intent -> broker order 的转换层
- 明确 lot / limit / T+1 / 可卖数量 / 现金冻结约束

### P4
- 实现执行回流与订单生命周期跟踪
- 将 real account sync 从手工 CSV 逐步推进到 bridge 回流

## Done Criteria

- 文档中明确记录 Qsys 的双定位与生产运行形态
- MiniQMT 约束与 bridge 分层清晰可追踪
- 后续 PR 可以直接围绕 `preopen-plan`、`order_intents`、`miniqmt broker adapter` 展开

## Notes

- 当前阶段仍不直接承诺“自动实盘下单上线”。
- 先把生产输入输出与桥接 contract 设计对，再决定自动化程度。
- 生产链路的首要目标不是炫技，而是稳定、可审计、可回滚。
