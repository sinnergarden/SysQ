# Qsys Research UI / Debug Cockpit

## 目标

为 Qsys 提供一个面向研究、调试、验收、复盘的前端工作台，把原本分散在脚本、日志、csv、notebook、回测结果中的信息统一起来，支持围绕具体 case 快速定位问题。

它不是通用行情终端，也不是 TradingView 复刻。  
它的核心目标只有一个：

> 让研究者能够快速看清某只票、某一天、某次回测、某次调仓，到底发生了什么。

---

## 为什么要做

当前 Qsys 已经逐步形成完整链路：

- raw / fq 行情管理
- feature 生产与 registry
- signal 计算
- strategy / rebalance
- backtest
- 订单 / 持仓记录
- 研究与执行并存

但现在调试和研究仍然有几个明显问题：

1. 信息分散  
   同一个 case 往往要来回看行情、feature、signal、回测、日志、订单、持仓。

2. case 级问题定位慢  
   很多问题本质发生在某只票某一天，但当前主要还是靠 aggregate 图和 notebook 推断。

3. feature 健康检查不统一  
   缺少一个地方系统检查：
   - feature 是否齐全
   - coverage 是否正常
   - NaN / inf 是否异常
   - 分布是否漂移
   - 某个 feature 在某票某天到底是多少

4. 回测结果和决策过程割裂  
   现在能看收益曲线，但很难快速解释：
   - 为什么某天调仓这样做
   - 为什么买了 A 没买 B
   - 为什么某段时间突然失效

因此需要一个专门面向 Qsys 的 Research UI / Debug Cockpit。

---

## 产品定位

Qsys Research UI 是一个 **研究与调试控制台**，不是行情软件。

优先服务以下任务：

- 单票 case 研究
- feature 健康检查
- 回测结果复盘
- 调仓决策解释
- 研究态与执行态对照

---

## 范围

### 本期做

- 单票时间轴工作台
- feature 健康检查台
- 回测总览页
- 调仓解释页
- 统一只读 API
- 统一 schema / manifest / result contract

### 本期不做

- TradingView 级画线与脚本系统
- 社交 / 分享 / 评论
- 多用户权限系统
- 实时 tick 级行情终端
- broker 终端
- 在线编辑策略 / feature
- 复杂写操作

---

## 页面设计

## 1. Dashboard / Run Overview

系统入口页，用于展示最近运行结果和快速跳转。

### 需要展示

- 最近 backtest runs
- 最近 feature runs
- 最近 signal runs
- 最近异常 / 失败 runs
- 搜索股票代码 / 日期 / run_id
- 快速跳转入口

### 目标

让用户快速进入：
- Case Workspace
- Feature Health
- Backtest Explorer

---

## 2. Case Workspace

最核心页面。围绕单只股票 + 时间区间展示完整上下文。

### 输入

- instrument_id
- start_date
- end_date
- price_mode: raw / fq
- optional run_id:
  - feature_run_id
  - signal_run_id
  - backtest_run_id

### 页面内容

#### 主图
- K线
- 买卖点
- 持仓区间
- 调仓点
- 订单点
- 关键事件点

#### 副图
- volume
- turnover
- 其他可切换辅助图

#### signal / score 面板
- main signal
- score
- rank
- optional 子信号

#### feature 面板
- feature 搜索
- 多 feature 曲线
- 单点值查看
- 常用 feature 收藏

#### snapshot 明细
点击某个 trade_date 后展示：
- 当日 bar
- 当日 feature snapshot
- 当日 signal snapshot
- 当日 position / order
- annotation / event

### 关键要求

- 所有图表按 trade_date 联动
- 支持 raw / fq 切换
- 支持从 backtest / replay 页面跳转进入
- 不允许只显示价格，必须能联动 signal / feature / order / position

---

## 3. Feature Health

用于检查某次 feature run 的健康状况。

### 输入

- feature_run_id
- optional date range
- optional universe

### 页面内容

#### feature 列表
每个 feature 展示：
- feature_id
- group
- owner / pipeline
- coverage ratio
- NaN ratio
- inf ratio
- min / max
- mean / std
- status

支持搜索 / 排序 / 过滤。

#### feature detail
针对单个 feature 展示：
- coverage 随时间变化
- mean / std 随时间变化
- 横截面分布
- 分位数轨迹
- 异常值数量
- registry 信息
- source fields
- normalization 信息

#### instrument-date snapshot
针对某个 instrument + date 展示：
- 全量 feature 值
- 缺失 feature
- 异常 feature
- 相关基础字段
- 对应 signal

### 最低要求

必须能检查：
- feature 缺失
- NaN / inf
- 极值异常
- 分布漂移
- coverage 突降

---

## 4. Backtest Explorer

用于展示回测总览并支持下钻。

### 输入

- backtest_run_id

### 页面内容

#### summary cards
- total return
- annualized return
- max drawdown
- sharpe
- turnover
- win rate
- holding count stats

#### performance curves
- equity curve
- benchmark curve
- excess return
- drawdown

#### diagnostics
- daily turnover
- daily holding count
- daily buy/sell count
- IC / RankIC
- group return
- industry contribution
- cap bucket performance

#### drill-down table
按日期展示：
- daily pnl
- turnover
- top contributors
- worst contributors
- order count

### 关键要求

- 点击某个日期 -> 跳到 Decision Replay
- 点击某个标的 -> 跳到 Case Workspace
- 不允许只有总览，必须能 drill-down

---

## 5. Decision Replay

用于解释某个调仓日发生了什么。

### 输入

- backtest_run_id
- trade_date

### 页面内容

#### context
- strategy name
- run_id
- trade_date
- 参数摘要
- universe size
- target holding count

#### previous positions
- 昨日持仓
- 持仓权重
- 持有天数
- 未实现盈亏

#### candidate pool
- instrument_id
- raw score
- adjusted score
- rank
- signal status
- risk filter status
- turnover constraint status
- final decision

#### order table
- BUY / SELL
- target weight
- delta weight
- filled weight
- reason
- status

#### explanation panel
对单个标的展示：
- 买入原因
- 卖出原因
- 未买入原因
- 被剔除原因

### 关键要求

这个页面必须能回答：
- 为什么买了 A
- 为什么没买 B
- 为什么卖了 C

---

## 数据对象要求

前端不能直接依赖临时 csv 或 notebook 产物，必须建立在稳定 schema 上。

至少需要以下对象：

- Instrument
- BarSeries
- FeatureSeries
- SignalSeries
- PositionSnapshot
- OrderEvent
- FeatureRegistryEntry
- BacktestRunSummary
- DecisionReplay
- CaseBundle

其中最关键的是 `CaseBundle`。

### CaseBundle 最低要求

应至少包含：

- instrument metadata
- bar series
- selected features
- signal series
- position events
- order events
- event markers
- run metadata

Case 页面尽量围绕 CaseBundle 拉数据，而不是前端自己拼接一堆分散接口。

---

## API 要求

后端优先做只读 API。

### 基础查询
- `GET /api/instruments`
- `GET /api/instruments/{instrument_id}`
- `GET /api/search?q=...`

### 行情
- `GET /api/bars?instrument_id=...&start=...&end=...&price_mode=raw|fq`
- `GET /api/events?instrument_id=...&start=...&end=...`

### feature
- `GET /api/feature-runs`
- `GET /api/feature-registry`
- `GET /api/features?...`
- `GET /api/feature-health?run_id=...`
- `GET /api/feature-health/{feature_id}?run_id=...`
- `GET /api/feature-snapshot?run_id=...&instrument_id=...&trade_date=...`

### signal
- `GET /api/signal-runs`
- `GET /api/signals?...`

### backtest
- `GET /api/backtest-runs`
- `GET /api/backtest-runs/{run_id}/summary`
- `GET /api/backtest-runs/{run_id}/metrics`
- `GET /api/backtest-runs/{run_id}/daily`
- `GET /api/backtest-runs/{run_id}/positions?trade_date=...`
- `GET /api/backtest-runs/{run_id}/orders?trade_date=...`

### replay / case
- `GET /api/decision-replay?run_id=...&trade_date=...`
- `GET /api/cases/{case_id}`
- `POST /api/cases/build`

---

## 落地原则

### 1. 先 schema，后 UI
先明确：
- feature registry schema
- backtest result schema
- decision replay schema
- case bundle schema
- manifest schema

### 2. 先只读，后写入
第一版只做结果可视化，不反向控制 Qsys 生产流程。

### 3. aggregate 必须能 drill-down
所有总览页都必须能跳到具体日期、具体标的、具体订单。

### 4. Debug 优先
不要为了漂亮堆很多图，优先保证：
- case 可定位
- feature 可检查
- 决策可解释

---

## 第一阶段 MVP

第一阶段只做三页：

### 1) Case Workspace
必须有：
- K线
- volume
- signal
- 买卖点
- 当日 snapshot

### 2) Feature Health
必须有：
- feature 列表
- coverage / NaN / inf
- feature detail
- instrument-date snapshot

### 3) Backtest Explorer
必须有：
- equity curve
- drawdown
- turnover
- IC / RankIC
- 跳转 replay / case

Decision Replay 可以作为第一阶段后半段或第二阶段补齐。

---

## 验收标准

满足以下条件视为一期可用：

1. 能通过股票代码 + 时间区间打开 Case Workspace，并看到 K线、signal、买卖点、snapshot
2. 能查看 feature run 列表和单个 feature 的健康信息
3. 能查看某个 instrument + trade_date 的 feature snapshot
4. 能查看 backtest 的收益曲线、回撤、turnover、IC / RankIC
5. 能从 backtest 某日跳到 replay
6. 能从 backtest / replay 某只票跳到 case
7. 页面支持 raw / fq 切换
8. 前端基于稳定 API / schema，不直接读取临时 notebook 输出

---

## 风险

### 风险 1：直接做页面，不先做 schema
这会导致前端和临时文件格式强耦合，后续维护成本很高。

### 风险 2：范围失控
如果一开始想做成完整量化终端，会迅速失焦。

### 风险 3：run manifest 不统一
没有 manifest，前端很难可靠关联 feature / signal / backtest / order / position。

---

## 最终判断

这个功能值得做，而且应该尽早做。  
但它必须被定义成：

> Qsys 的研究与调试控制台

而不是：

> 一个很酷的行情软件

最正确的推进顺序是：

1. schema
2. read APIs
3. 三页 MVP
4. decision replay
5. 研究态 / 执行态对照