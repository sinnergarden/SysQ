# FEATURE: daily_signal_monitoring

## Goal

- 为 daily ops 增加“推荐篮子质量监控”能力，回答模型信号在最近 1/2/3/5/10 个交易日是否有效。
- 将“信号好坏评估”与“账户组合执行结果”解耦，先稳定衡量模型推荐本身的质量，再为后续接入 MiniQMT 的真实执行链路预留接口。
- 让运营每天不仅知道“今天推荐了什么”，还知道“昨天、前天、大前天的推荐篮子后来表现如何”。

## Use Cases

- 用例 1：日常运营用户在盘后查看 `signal_date=T-1` 生成的推荐篮子在 `T` 日的收益、超额收益和命中情况，用来判断最新信号是否有效。
- 用例 2：运营用户查看 `signal_date=T-2`、`T-3` 对应推荐篮子在最近 2 日、3 日的累计表现，用来观察信号衰减和持有周期适配度。
- 用例 3：研究或运营用户对比最近 5 个 vintage 的平均收益、胜率和相对基准超额，用来判断模型近期是否退化。
- 用例 4：后续接入 MiniQMT 后，用户继续使用同一套“推荐篮子质量报表”衡量模型信号，不受真实账户是否全量换仓、是否只做 rebalance 的影响。
- 用例 5：若某日推荐篮子因数据缺失、价格缺失或 universe 不完整而无法评估，系统必须明确标记原因，不能把缺失结果当成 0 收益。

## API Change

- 是否新增 API：是。
- 是否修改现有 API：是，新增 daily ops report section 与 CLI 输出字段。
- 变更点列表：
  - 新增推荐篮子监控模块，负责记录 `signal vintage` 元信息与后续收益快照。
  - 新增盘后或独立任务入口，用于刷新最近 N 个 vintage 的收益观测结果。
  - 扩展 `qsys.reports.daily` 的 post-close 报告结构，增加 `signal_quality` section。
  - 扩展盘前/盘后结构化 JSON artifact，记录 vintage 评估结果文件路径。
  - 向后兼容策略：不移除现有 daily ops CLI；新增参数和报表字段时保持旧字段可读。

## UI

- 无图形 UI。
- CLI 和 JSON 报告需要新增面向运营的摘要字段：
  - `yesterday_basket_today_return`
  - `t_minus_2_basket_2d_return`
  - `t_minus_3_basket_3d_return`
  - `recent_vintage_win_rate`
  - `recent_vintage_excess_return`
- 若评估失败，输出应明确写出失败原因，如 `missing_price`, `missing_plan`, `insufficient_holding_window`。

## Constraints

- 技术约束：
  - 推荐篮子评估必须使用可追溯的 `signal_date`、`execution_date`、参考价格和 benchmark 口径。
  - 推荐篮子评估不能依赖真实账户持仓结果，必须能够在无交易执行的情况下独立运行。
  - 需要优先复用现有 `plan_<signal_date>_<account>.csv`、数据健康检查和 qlib 价格读取逻辑。
  - 报表必须支持最近多个 vintage 的滚动更新，不能只覆盖最新一天。
- 业务约束：
  - 本功能衡量的是“推荐篮子质量”，不是“真实账户收益”。
  - 评估对象是当天推荐篮子，不是账户组合；即便真实执行是全量换仓或 rebalance，也不改变 vintage 定义。
  - 至少支持等权收益、推荐权重收益、相对基准超额三种口径。
- 禁止改动：
  - 不变更 `qsys.live.account.RealAccount.sync_broker_state`。
  - 不变更 `qsys.live.account.RealAccount.get_state`。
  - 不变更 `qsys.live.account.RealAccount.get_latest_date`。
  - 不变更 `qsys.live.manager.LiveManager.run_daily_plan` 对外接口。
  - 不在本需求里引入真实下单逻辑；MiniQMT 接入留到后续需求。

## Done Criteria

- 系统可以为最近至少 3 个 signal vintage 输出 1d/2d/3d 收益观测结果。
- 盘后 JSON 报告包含 `signal_quality` section，且可回答“昨天、前天、大前天推荐篮子的表现”。
- 推荐篮子表现与 shadow/real 账户表现明确分层展示，不混淆。
- 当 vintage 无法评估时，系统输出明确状态和原因，不默默丢失。
- 相关测试通过，并补充必要的 runbook 或 feature 文档说明。

## Test Plan

- 单元测试：
  - vintage 元信息解析测试。
  - 1d/2d/3d 收益计算测试。
  - benchmark 超额收益计算测试。
  - 缺失价格、缺失计划、空计划的状态分类测试。
- 集成测试：
  - 使用已有 plan artifact 和 qlib 数据，验证 post-close 生成 `signal_quality` section。
  - 验证 recent vintage summary 不依赖真实账户执行结果。
- 回归测试：
  - `python -m compileall qsys scripts tests`
  - `python -m unittest tests/test_live_trading.py`
  - 新增 `tests/test_daily_signal_monitoring.py`

## Rollback Plan

- 回滚策略：按 commit 回滚，恢复到仅输出 pre-open/post-close 基础报告的状态。
- 回滚触发条件：
  - vintage 收益口径被发现存在日期语义错误。
  - 报告结果与实际价格路径明显不一致。
  - 新逻辑影响原有盘前计划生成或盘后对账主流程。

## Notes

- 推荐新增核心对象：`signal vintage`，最少包含 `signal_date`、`execution_date`、`basket_constituents`、`weights`、`price_basis`、`model_version`、`feature_set`。
- 建议优先实现推荐篮子质量看板，再决定是否追加 top1/top5/top10 命中拆解、rank decay 和因子归因。
- 后续接入 MiniQMT 时，本功能应继续保持“信号评估层”独立，不耦合真实执行细节。
