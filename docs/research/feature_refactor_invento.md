# Feature Refactor Inventory

## 1. 当前系统里实际存在两套 feature 机制

当前仓库的 feature 体系不是一套统一系统，而是两条并行链路：

1. `qsys/feature/library.py`
   - 管理 Qlib 原生字段集合。
   - 负责 `alpha158 / extended / margin_extended / phase1 / phase12 / phase123` 这些训练入口里的字段列表。
   - 本质上是在做“原始字段选择”，不是在做研究派生特征构建。

2. `qsys/feature/builder.py` + `qsys/feature/groups/*.py`
   - 管理研究态派生特征。
   - 这些特征主要从日线 raw panel 上做滚动、相对强弱、上下文和 regime 派生。
   - 目前主要服务 readiness audit、ablation、样本导出这类研究脚本。

这两条链路都叫 “feature”，但语义层级不同：

- `FeatureLibrary` 管的是 Qlib 可直接读取的原生输入列集合。
- `builder/groups` 管的是研究派生层。

这是当前最核心的结构问题：原生输入层和派生研究层边界存在，但没有被正式命名和制度化。

## 2. 当前代码与文档分别在表达什么

### 2.1 代码

- `qsys/feature/library.py`
  - 负责维护 Qlib native raw field set。
  - `extended` 和 `margin_extended` 本质是“Alpha158 + 扩展原始字段”。
  - `phase1 / phase12 / phase123` 当前仍只是 raw field set 别名，并没有真实表达研究派生层的结构。

- `qsys/feature/groups/microstructure.py`
  - 描述单日 K 线与日内价格结构。
  - 更接近“单日状态原子特征”。

- `qsys/feature/groups/liquidity.py`
  - 混合了成交额对数这种近原子特征，和 `volume_shock_* / amount_zscore_20` 这类窗口压缩特征。
  - 业务上统一属于流动性/容量，但建模角色并不一致。

- `qsys/feature/groups/tradability.py`
  - 描述涨跌停、停牌、可交易性。
  - 既像 alpha 特征，也像执行约束或过滤信号。

- `qsys/feature/groups/relative_strength.py`
  - 混合了收益率原始滚动值、横截面排名值、相对指数/行业超额收益。
  - 更偏 tabular 压缩层。

- `qsys/feature/groups/regime.py`
  - 描述市场广度、风格偏好、指数波动与市场趋势。
  - 明显是 regime/context，不适合与基础价格特征混放。

- `qsys/feature/groups/industry_context.py`
  - 描述行业收益背景与行业内相对位置。
  - 更像 context 层，而不是主原子输入。

- `qsys/feature/groups/fundamental_context.py`
  - 描述估值、财务质量与变化率。
  - 主要是慢变量/context。

- `qsys/feature/registry.py`
  - 当前只是一个轻量的 group -> feature name 清单。
  - 缺少 business meaning、modeling role、temporal type、sequence suitability 等关键信息。

- `qsys/feature/config.py`
  - 当前只是一组 legacy flag。
  - 可运行，但不足以表达“feature preset / feature layer / model-specific selection”。

### 2.2 脚本

- `scripts/run_train.py`
  - 训练仍完全依赖 `FeatureLibrary` 返回的 Qlib 原生字段列表。
  - 没有直接使用研究派生 builder。

- `scripts/run_feature_build.py`
  - 直接按顺序拼 group，属于“builder 试跑脚本”。

- `scripts/run_feature_readiness_audit.py`
  - 以 research builder 生成特征，再做覆盖率和可用性审计。

- `scripts/run_feature_experiment.py`
  - 用 builder 导出样本做研究实验。

- `scripts/run_feature_ablation.py`
  - 通过开关 flag 做 feature ablation。

这些脚本说明：项目已经在事实层面承认“派生研究特征层”的存在，但尚未把它升级成正式的一层系统。

### 2.3 文档

- `docs/features/feature_system.md`
  - 有长期目标和边界意识，但没有把“原生输入层”和“派生研究层”的差异讲透。

- `docs/features/phase1_feature_groups.md`
  - 记录了早期 Phase 1 分组，但仍偏阶段性说明。

- `docs/research/feature_gap_analysis.md`
  - 判断很到位，已经指出需要 registry / group 化模块。
  - 但尚未落成新的长期结构。

- `docs/research/feature_groups_phase2.md`
- `docs/research/feature_groups_phase3.md`
  - 提供了阶段性 feature 方向，但没有统一纳入一个稳定目录和命名规范。

## 3. 当前分类和命名哪里不合理

### 3.1 `phase1 / phase12 / phase123` 命名同时承担了三层含义

它们同时被拿来表示：

- 研究阶段
- feature set
- 训练输入版本

但在实现上又没有真正对应到不同构建层或不同 manifest，导致：

- 名字像 feature system 版本
- 实际却只是 raw field set alias

这会误导读者，也会误导未来的 ablation 和模型迁移。

### 3.2 `groups/` 里的分组是业务分组，但不是完整系统分层

当前 `groups/` 的分组按业务语义大体成立，但没有显式区分：

- 原子单日状态
- 滚动压缩后的聚合特征
- context / regime / slow variable

于是会出现同一个文件里既有 sequence-friendly 单日状态，又有明显只适合树模型的时间压缩结果。

### 3.3 `registry.py` 名叫 registry，但还不是 registry

当前 registry 只有 feature list，没有：

- 中文业务含义
- 建模角色
- 时态类型
- 归一化建议
- upstream dependency
- 是否适合 sequence / tabular

因此它还不能承担“审计、ablation、迁移、后续多模型共用”的权威表职责。

## 4. 当前 feature 的业务语义与建模角色判断

### 4.1 更接近原子特征

这些特征表达的是“某股票某日的直接状态”，更适合未来 sequence/Transformer 做通道输入：

- `close_to_open_gap_1d`
- `open_to_close_ret`
- `close_pos_in_range`
- `open_pos_in_range`
- `upper_shadow_ratio`
- `lower_shadow_ratio`
- `intraday_reversal_strength`
- `amount_log`
- `distance_to_limit_up`
- `distance_to_limit_down`
- `tradability_score`
- Qlib native raw fields，如 `$open/$high/$low/$close/$volume/$amount/$turnover_rate`

说明：
- 其中 `amount_log`、`tradability_score` 不是完全原始 raw，但仍保留单日状态语义，sequence 兼容性较好。

### 4.2 更接近聚合/压缩特征

这些特征更偏“把一段历史压缩成单日一个数”，更适合 tabular 模型：

- `amount_zscore_20`
- `volume_shock_3`
- `volume_shock_5`
- `turnover_acceleration`
- `illiquidity`
- `ret_3d`
- `ret_5d`
- `vol_mean_3d / 5d`
- `amount_mean_3d / 5d`
- 各类 `_rank`
- `limit_up_count_5d`
- `revenue_yoy / profit_yoy`

### 4.3 更接近 context / regime / slow variable

- `industry_ret_*`
- `industry_breadth`
- `stock_minus_industry_ret*`
- `market_breadth`
- `limit_up_breadth`
- `index_volatility_*`
- `small_vs_large_strength`
- `growth_vs_value_proxy`
- `market_trend_strength`
- `log_mktcap`
- `float_mktcap`
- `pe_ttm`
- `pb_raw`
- `gross_margin`
- `debt_to_asset`
- `operating_cf_to_profit`

这些特征不应该继续和基础价格状态写在同一个语义文件里。

### 4.4 更接近事件/行为类特征

- `is_limit_up`
- `is_limit_down`
- `opened_from_limit_up`
- 两融原始字段：`$margin_* / $lend_*`

这类特征在短周期 A 股里经常兼具行为和事件含义，需要在 registry 里显式标注。

## 5. 当前重复、重叠与边界含糊的问题

- `FeatureLibrary` 的 `phase1/phase12/phase123` 与 builder 阶段分组存在语义重叠，但并不对应同一层对象。
- `relative_strength.py` 同时输出底层滚动收益、横截面 rank 和相对指数/行业超额，层次过多。
- `liquidity.py` 同时包含近原子状态与滚动压缩结果。
- `regime.py` 使用 `circ_mv` 和 `pb` 做风格代理，这没有问题，但它本质上是环境标签，不应再被误看成“普通主信号 feature”。
- `index_context.py` 和 `industry_context.py` 本质上是 context attachment/helper，但当前与 feature builder 绑定较紧。

## 6. 当前组织方式如何阻碍未来 sequence / Transformer

### 6.1 原子日状态与时间压缩特征没有显式分开

sequence 模型更适合吃“时间轴上逐日展开的原子状态”。

当前很多研究特征已经被压缩成：

- rolling mean
- rolling zscore
- rank
- spread

这对树模型很好，但对 sequence 来说会带来两个问题：

- 丢掉原始路径信息
- 把“模型自己该学的时序压缩”提前手工做掉

### 6.2 原生 Qlib 字段层与研究派生层没有正式分层

未来 sequence builder 很可能需要：

- 直接取 panel-friendly raw/native fields
- 再选少量 context/regime 作为额外通道

如果继续把两层都叫 “feature set”，未来会很难清楚回答：

- 这个模型到底吃的是 raw sequence，还是压缩后的 tabular feature？

### 6.3 context / regime / slow variable 没有成为正式层

未来 sequence 模型里，market regime、industry context、fundamental context 往往不是主序列通道，而是：

- side input
- condition token
- gating/context branch

当前系统没有显式把这些对象区分出来，不利于后续建模迁移。

## 7. 当前文档缺什么

最缺的是一套面向“长期演进”的统一说明：

- feature 系统到底分几层
- 各层分别服务什么模型
- 目录为什么这样组织
- 新增一个 feature 应该落在哪一层
- 哪些 feature 更适合 sequence
- 哪些 feature 更适合 tabular
- 哪些 feature 只是 context/regime
- 哪些旧命名只是历史兼容，不代表长期推荐命名

## 8. 结论

当前 feature 体系的主要问题，不是“特征太少”，而是“层次不清”：

- 原生输入层与派生研究层混名
- 业务分组存在，但建模分层未显式化
- registry 还不够权威
- phase 命名背负过多语义

因此这次重构的正确方向不是继续加 feature，而是先把以下几件事做实：

1. 正式区分 native raw field set 与 research derived feature layer
2. 给派生特征建立稳定分类
3. 引入可审计 registry
4. 让 builder/selection 与 feature definition 分离
5. 为 sequence 模型保留 panel-friendly 原子输入的主路径
