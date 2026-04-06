# SysQ Feature Gap Analysis

## 1. 当前已有哪些 feature

### 1.1 当前主干 feature 集
当前主干仍以 **Qlib phase123** 为基础，由 `qsys/feature/library.py` 中的 `FeatureLibrary.get_alpha158_config()` 提供。

在此基础上，项目已经扩展出两套增强版：

- `extended`
  - phase123 + 一批日频可直接复用的基本面 / 估值 / 资金流字段
- `margin_extended`
  - `extended` + 两融字段

### 1.2 当前 phase123 之外的已接入字段
当前已经显式纳入 `FeatureLibrary` 的 phase123 之外字段包括：

#### 扩展基本面 / 估值 / 资金流
- `$pe`
- `$pb`
- `$total_mv`
- `$circ_mv`
- `$net_inflow`
- `$big_inflow`
- `$roe`
- `$grossprofit_margin`
- `$debt_to_assets`
- `$current_ratio`
- `$net_income`
- `$revenue`
- `$total_assets`
- `$equity`
- `$op_cashflow`

#### 两融字段
- `$margin_balance`
- `$margin_buy_amount`
- `$margin_repay_amount`
- `$margin_total_balance`
- `$lend_volume`
- `$lend_sell_volume`
- `$lend_repay_volume`

### 1.3 当前缺少但适合补充的方向
从短周期 A 股日频调仓视角看，当前明显缺少以下几类：

- 微观结构 / K 线结构特征
- 流动性 / 容量 / 冲击特征
- 涨跌停 / 可交易性特征
- 横截面相对强弱特征
- 市场 regime / breadth 特征
- 行业上下文特征
- 更细的估值 / 财务质量变化率特征

也就是说，当前系统已经不再是纯 phase123，但仍更像“phase123 + 少量原始扩展字段”，还不是结构化的短周期 A 股特征体系。

---

## 2. 当前 feature 计算链路在哪里

## 2.1 离线训练主链路
当前训练主链路是：

1. `scripts/run_update.py`
   - 拉原始数据（raw）
2. `qsys/data/adapter.py`
   - raw -> qlib csv/bin
3. `scripts/run_train.py`
   - 根据 `feature_set` 选择 feature list
4. `qsys/model/zoo/qlib_native.py`
   - 构造 `QlibNativeModel`
   - 用 `QlibDataLoader` 从 qlib bin 取 feature / label
5. Qlib `DataHandlerLP`
   - 对 feature 做 `RobustZScoreNorm + Fillna`
   - 对 label 做 `DropnaLabel + CSZScoreNorm`
6. 模型训练完成后落到 `data/models/...`

### 2.2 feature 定义入口
当前 feature 的“定义入口”主要在：

- `qsys/feature/library.py`
  - 决定不同 feature set 用哪些字段
- `qsys/model/zoo/qlib_native.py`
  - 把 feature list 交给 Qlib loader
- `qsys/data/adapter.py`
  - 决定哪些 raw 字段真正进入 qlib bin

### 2.3 纯 Python feature 计算能力
项目已有一个 `qsys/feature/calculator.py`，能对一小部分 Qlib 表达式进行纯 Python 计算，支持：

- `Ref`
- `Mean`
- `Std`
- `Max`
- `Min`

但它现在更像“轻量 inference / research helper”，还不是新增成体系特征的主模块。

### 2.4 dataview 层
- `qsys/dataview/research.py`
- `qsys/dataview/inference.py`

这两层可以直接读取 `StockDataStore` 中的日线原始字段，并适合作为研究态 / 推理态的 feature build 支撑层。

结论：
**当前最适合新增 feature 的方式，不是继续只往 `FeatureLibrary` 里追加原始列，而是引入一层更清晰的“研究特征构建层 / registry / group 化模块”。**

---

## 3. 当前 label 定义是什么

当前 `QlibNativeModel` 默认 label 定义位于：

- `qsys/model/zoo/qlib_native.py`

默认 label：

```python
["(Ref($close, -5) / Ref($close, -1) - 1)"]
```

含义可理解为：
- 以 **T+1** 作为可交易起点
- 预测 **未来约 5 个交易日窗口内的收益**（更准确地说，是 `Ref($close, -5)` 相对 `Ref($close, -1)`）

这说明当前模型虽然用于日频调仓，但 label 不是纯 next-day return，而更接近一个 **短周期 forward return**。

这一点与用户当前目标“持有周期 1-4 个交易日”并不完全一致，后续可能需要：

- 明确保留当前 baseline label 作为可比基线
- 再额外研究更短周期 label（例如 2d / 3d / 4d）
- 不要在本次 feature 扩展里默默改掉现有 label，否则 ablation 不可比

---

## 4. 当前 backtest 和 signal 流程是什么

## 4.1 signal 生成
当前信号生成入口：

- `qsys/strategy/generator.py`

流程：
1. 从 `model_path/meta.yaml` 读取模型元信息
2. 载入已训练模型
3. 用模型直接对 inference data 预测得分

## 4.2 live / daily plan 流程
当前盘前计划入口：

- `qsys/live/manager.py`
- `scripts/run_daily_trading.py`

大致流程：
1. 获取当天 `signal_date`
2. 从 qlib 中读取当天 feature
3. 用 `SignalGenerator` 生成 scores
4. `StrategyEngine` 做 Top-K / weight 生成
5. `PlanGenerator` 生成 target plan / execution plan
6. 导出 plan bundle / 报告

## 4.3 backtest 流程
当前历史回测入口：

- `scripts/run_backtest.py`
- `qsys/backtest.py`

流程：
1. 读取模型
2. 用模型 feature config 在给定窗口批量取 feature
3. 生成 daily score
4. 再批量取 market data：
   - `$close`
   - `$open`
   - `$factor`
   - `$paused`
   - `$high_limit`
   - `$low_limit`
5. `StrategyEngine` 生成目标权重
6. `OrderGenerator + MatchEngine + Account` 模拟成交
7. 输出 daily result / trade log / summary

当前回测已经具备：
- Top-K
- 等权 / score_weighted
- 停牌 / 涨跌停基本过滤
- 账户与成交仿真

但还缺：
- rolling backtest 标准化入口
- feature coverage / readiness 与回测联动
- 按 feature group 的 ablation 研究脚本

---

## 5. 当前数据表里已经有哪些字段可直接复用

根据当前 raw/adapter/config 与近期修复，项目中可直接复用的字段大致可分为几类。

### 5.1 基础 OHLCV / 交易状态
- `open`
- `high`
- `low`
- `close`
- `vol` / `volume`
- `amount`
- `adj_factor`
- `high_limit`
- `low_limit`
- `paused`
- `turnover_rate`
- `factor`
- `vwap`

### 5.2 估值 / 规模
- `pe`
- `pb`
- `total_mv`
- `circ_mv`

### 5.3 资金流
- `net_inflow`
- `big_inflow`

### 5.4 两融
当前链路上已朝 `margin_detail` 统一，目标字段包括：
- `margin_balance`
- `margin_buy_amount`
- `margin_repay_amount`
- `margin_total_balance`
- `lend_volume`
- `lend_sell_volume`
- `lend_repay_volume`

### 5.5 PIT 财报 / 基本面主字段
- `net_income`
- `revenue`
- `total_assets`
- `equity`
- `op_cashflow`

### 5.6 财报比率 / 派生字段
- `roe`
- `grossprofit_margin`
- `debt_to_assets`
- `current_ratio`

### 5.7 潜在可继续复用但当前覆盖不稳的字段
从近期 raw 校验与 collector 逻辑看，还可能存在但覆盖不稳定或尚未完全纳入研究主线的字段包括：
- `oper_cost`
- `total_cur_assets`
- `total_cur_liab`
- `q_dt_profit`
- `q_gr_yoy`
- `roe_ttm`
- 以及其他 `fina_indicator` / income / balance / cashflow 衍生列

这些字段对后续 Group H（财务质量与变化率）有价值，但需要先做披露后可见性和覆盖率核验。

---

## 6. 当前最适合新增 feature 的模块和文件

## 6.1 最适合新增的代码位置
### A. feature registry / feature group 定义
建议新增或扩展：
- `qsys/feature/registry.py`（建议新增）
- 或扩展 `qsys/feature/library.py`

用途：
- 管理 feature group
- 管理开关
- 区分 raw feature / normalized feature / context feature
- 为 ablation 提供统一入口

### B. feature build 逻辑
建议新增：
- `qsys/feature/groups/`（建议新增目录）
  - `microstructure.py`
  - `liquidity.py`
  - `tradability.py`
  - `cross_sectional.py`
  - `regime.py`
  - `industry_context.py`
  - `fundamental_context.py`

原因：
- 当前若继续把逻辑塞进 `library.py`，会把“字段清单”和“特征计算逻辑”混在一起
- group 化后更利于分 phase 开发与 ablation

### C. 标准化 / winsorize / rank 处理
建议新增：
- `qsys/feature/transforms.py`

用于统一：
- winsorize
- cross-sectional zscore
- rank normalize
- 可选行业中性 / 市值中性

### D. 研究态脚本
建议新增：
- `scripts/run_feature_build.py`
- `scripts/run_feature_experiment.py`
- `scripts/run_feature_ablation.py`

分别负责：
- 特征刷新/构建
- 最小实验
- 分组消融

## 6.2 需要复用而不是重写的模块
- 数据读取：`qsys/data/adapter.py` / `qsys/data/storage.py`
- 研究数据视图：`qsys/dataview/research.py`
- 模型训练：`qsys/model/zoo/qlib_native.py`
- 回测：`qsys/backtest.py`
- 盘前信号/计划：`qsys/strategy/generator.py`、`qsys/live/manager.py`

结论：
**最小侵入式改法** 是：
- 新增一层研究特征模块与 registry
- 通过 `feature_set` / config 开关接入现有训练与回测
- 不直接重写 DataManager / BacktestEngine 主干

---

## 7. 当前最关键的 feature gap

从本项目当前状态看，最值得优先补的不是更多财报列，而是：

### 优先级最高
1. **微观结构 / K线结构**
2. **流动性 / 容量 / 冲击**
3. **涨跌停 / 可交易性**
4. **横截面相对强弱**

原因：
- 与 1-4 日持有周期最贴近
- 数据源稳定，基本日频可得
- 不容易产生 PIT 对齐争议
- 能快速做最小可用研究闭环

### 第二优先级
5. **市场 regime / breadth**
6. **行业上下文**

原因：
- 适合作为 context / filter
- 不一定直接提高单日横截面排序，但能提高策略解释性和稳健性

### 第三优先级
7. **估值与财务质量快变量**

原因：
- 很有价值，但必须先把披露后可见性、覆盖率和标准化口径处理干净
- 更适合在 Phase 3 引入，而不是一开始混进主信号

---

## 8. Phase 1 实施建议（供后续开发）

推荐先做：

- Group A: 微观结构 / K线结构
- Group B: 流动性 / 冲击
- Group C: 可交易性 / 涨跌停
- Group D: 横截面相对强弱

并且采用以下工程原则：

- 每组独立模块
- 每组同时输出：
  - raw 版
  - winsorized + cross-sectional standardized 版
  - 必要时 rank 版
- 先在研究配置下启用，不碰 production manifest
- 先在较小 universe + 短窗口做最小实验，再扩大

---

## 9. 小结

当前 SysQ 已经具备：
- phase123 基线
- 一批扩展原始字段
- 可训练、可回测、可盘前推理的完整主链路

但尚未具备：
- 结构化的 feature registry
- 面向短周期 A 股实盘的成体系微观结构 / 可交易性 / regime 特征
- 分 group 的研究配置与 ablation 流程

因此，本轮 feature 扩展最合适的落点是：

1. **先补研究态特征分组模块**
2. **接入最小研究配置与脚本**
3. **按 phase 做可比实验，而不是一次性大重构**
