# ADR-004: Elevate Factor Objects, Variants, Bundles, and Experiments Over Raw Feature Sets

## 状态

已采纳

## 背景

Qsys 已经从“少量特征 + 单次训练/回测”的阶段，进入“需要长期维护主线因子、变体、bundle 与实验结论”的阶段。

现状中，系统已具备：

- raw -> adapter -> derived feature 的构造链路
- 命名 feature set / subset 驱动训练的最小闭环
- signal eval、backtest、strict eval 的研究雏形

但研究对象还不够稳定，主要表现为：

- `FeatureLibrary` 同时承担 feature set、variant 展开和研究辅助职责
- subset 仍主要是训练配置，不是真正的研究对象
- factor variant 缺少完整 lineage
- experiment 结果与输入对象绑定不够紧

这会导致长期问题：

- 很难清楚回答一个结论到底属于 factor、variant、bundle 还是一次性 run
- 很难稳定比较 `254` 与 `254 norm` 这类主线变体
- 很难把研究结果沉淀成可治理资产

## 决策

Qsys 的研究系统后续默认采用以下对象优先级：

1. `FactorDefinition`
2. `FactorVariant`
3. `FactorBundle`
4. `ExperimentRun`
5. `Decision / Promotion Record`

训练、signal eval、portfolio backtest 都应逐步转为消费这些对象，而不是继续以粗粒度 `feature_set` 作为主要入口。

具体原则：

- base factor 与 transform variant 分层管理
- bundle 作为研究对象管理，而不是仅作为字段列表
- experiment 作为标准对象管理，必须保留输入快照与结论
- signal evaluation 与 portfolio backtest 继续分层
- `top_k` 继续视为 portfolio/backtest 参数，不进入训练层语义

## 影响

正向影响：

- 因子与变体谱系更清楚，主线结论更易追溯
- 研究结果更容易横向比较与沉淀
- 训练、回测、strict eval 可以围绕统一 snapshot 对接
- 未来 UI 更容易展示 bundle、variant 和 decision 状态

代价：

- 需要引入 manifest/schema 与快照治理
- 初期会同时存在旧 `feature_set` 与新 `bundle_id` 两套入口
- 需要补一批 contract tests 保障对象层不漂移

## 后续

- 先以文档和 manifest 形式引入对象层，不强行中断现有训练主线
- 优先迁移当前主线 bundle，以及 `173`、`254`、`254 norm` 等核心对象
- 后续在 `research_framework_v2` 中补足 factor / variant / bundle / decision 的正式定义
