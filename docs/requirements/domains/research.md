# Domain: Research Backtest

## Domain Scope
量化研究与回测链路：特征集、标签、信号研究、信号分析、信号组合、信号驱动回测、实验比较。
不包含：模型生产化训练（model_training domain）、candidate 晋级（promotion domain）。

## UC_RESEARCH_BACKTEST

### Status
stable

### Source
`docs/USE_CASES.md` UC-2（Feature List）、UC-3（Label Config）、UC-4（Signal Research）、
UC-5（Signal Analytics）、UC-6（Signal Combination）、UC-7（Signal Backtest）。

### User Goal
研究员可以定义实验（特征集、标签、模型参数），运行滚动训练/预测，评估信号质量，基于信号运行回测，并比较不同实验的结果。

### Scope
包含：
- 特征集定义与解析
- 标签定义与计算
- 滚动 OOS 训练/预测
- 信号评估（IC / RankIC / ICIR）
- 基于信号的策略回测
- 实验索引与比较

不包含：
- 模型训练生产化（见 UC_MODEL_TRAINING）
- 策略晋级生产（见 UC_CANDIDATE_PROMOTION）
- UI 层面的回测比较（见 UC_UI_ANALYSIS）

### Inputs
- 研究配置 YAML（`configs/research/*.yaml`）
- 特征配置（`configs/features/*.yaml`）
- 标签配置（`configs/labels/*.yaml`）
- 行情数据（canonical / qlib_bin）
- 启用四个 growth-confirmation income feature 时，正式 audited research 必须显式
  选择 `audited_sidecar_v1`，pin parquet path+SHA、manifest path+SHA，并声明由
  audit scope/feature-set 消费范围给出的 `required_history_start`。旧配置映射为
  `legacy_unverified_global_v0` 并在运行时告警；该模式只用于兼容，不能通过 PIT
  certification。
- 启用 shareholder feature 时，正式 audited research 还必须 pin holder/top10 path+SHA
  以及同目录 terminal-backed v2 manifest path+SHA。旧两文件配置仍可作为 legacy research
  输入，但 signal lineage 缺 manifest/source terminal identity，不能通过 certification。

### Outputs
- `data/research/signals/{signal_id}/{signal_run_id}/predictions.parquet`
- `data/research/signals/{signal_id}/{signal_run_id}/manifest.json`
- `data/research/experiments/{experiment_id}/`
- `data/research/backtests/{run_id}/{backtest_id}/`
- 信号评估 metrics

### Canonical Entrypoints
- `scripts/run_research.py` — 信号研究 + 信号组合（UC-4/6）
- `scripts/run_signal_analytics.py` — 信号只读分析（UC-5）
- `scripts/research/backtest_from_signal.py` — 信号驱动回测（UC-7）

### Supporting Tools
- `scripts/research/compute_labels.py` — 标签计算（UC-3）
- `scripts/research/rebuild_pit_universes.py` — 从冻结快照重建 hash-bound PIT universe
- `scripts/research/build_corporate_action_artifact.py` — 生成与 signal/strategy
  解耦的、完整 PIT universe corporate-action artifact（UC_RESEARCH_BACKTEST）。
  它只是 backtest 的 supporting tool；canonical backtest entrypoint 仍是
  `scripts/research/backtest_from_signal.py`。

### Legacy Entrypoints
- `scripts/research/run_backtest.py` — 旧版回测入口，待收束

### Key Artifacts
- `data/research/signals/` — SignalStore
- `data/research/labels/` — LabelStore
- `data/research/experiments/` — 实验索引
- `data/research/backtests/` — 回测产物

Growth-confirmation 的 income sidecar 由 daily canonical entrypoint 的显式 offline
bootstrap mode 产生，但由 research generator 消费。`LightGBMSingleLabelGenerator`
在构造时复核 artifact/manifest hash 和 contract，并把 artifact id、source run、terminal
receipt hash、scope/cutoff 写入 `feature_source_lineage`，同时绑定 checkpoint 与 feature
cache identity。adapter 只接收 generator 显式传入的 identity；缺失、tamper、scope
或 cutoff 不覆盖实际请求 window 时 fail closed，禁止 catch-all 转成全 NaN。

Shareholder 的正式 immutable snapshot 同样只由 `scripts/data_sync.py` 显式 offline bootstrap
mode 从一个 trusted full-history terminal run 生成。generator 校验 v2 manifest 对 holder/top10
文件的反向 hash、historical union symbol/date scope、source run 与 terminal receipt identity；
manifest hash 同时进入 checkpoint、feature cache 和 signal `feature_source_lineage`。normal daily
不会构建或猜测这个 snapshot。

```yaml
params:
  shareholder_holder_path: data/research/source_snapshots/shareholder/<artifact_id>/holder_num.parquet
  shareholder_holder_sha256: <exact_sha256>
  shareholder_top10_path: data/research/source_snapshots/shareholder/<artifact_id>/top10_holder_ratio.parquet
  shareholder_top10_sha256: <exact_sha256>
  shareholder_manifest_path: data/research/source_snapshots/shareholder/<artifact_id>/manifest.json
  shareholder_manifest_sha256: <exact_manifest_sha256>
```

```yaml
params:
  income_source_mode: audited_sidecar_v1
  income_sidecar_path: data/research/income_sidecars/<artifact_id>/income.parquet
  income_sidecar_sha256: <exact_sha256>
  income_sidecar_manifest_path: data/research/income_sidecars/<artifact_id>/manifest.json
  income_sidecar_manifest_sha256: <exact_manifest_sha256>
  income_sidecar_required_history_start: '20140313'  # example; derive from audit scope
```

四项必须来自同一次 bootstrap 输出；禁止填 `latest` 或 symlink。完整历史起点与单次
rolling request 的 `start/end` 是两项独立约束：manifest `range_start` 必须覆盖显式
`required_history_start`，而 request window 只校验本次消费 cutoff，不能替代历史起点。
真实 baseline identity 只能在 terminal sidecar 生成后写入；当前不得伪造 path/hash 或
声称 baseline 已 READY。

四个 feature 的数值公式与 feature list 不变。每列独立传播所有实际季度依赖的最大
`available_from`；同一可用日取最大报告期，较旧报告晚成熟时不回退，较新报告的合法
NaN 仍作为新事件覆盖旧季度值。audited 模式下公告日 T 的 income event 对 T feature
不可见，只能用于 T+1 及以后；forecast 和显式 legacy income 保持原有同日可见语义。

Canonical cached-signal backtest 还必须输出 manifest 绑定的
`executions.csv`（schema `backtest_executions_v2`），逐订单记录请求数量、真实
模拟成交数量/价格、费用、拒单原因及策略明确提供的交易原因。不得从日级汇总
反推逐笔成交；启用尚未接入逐笔 collector 的 legacy stop 时，manifest 必须将
该 artifact 标为不完整。

### Canonical cached-signal accounting v1 contract

研究配置若声明 `research_protocol.holdout.start_date`，且 `calendar.end_date`
达到或越过该边界，canonical `scripts/run_research.py` 必须在写任何实验制品前
fail closed。只有配置同时声明 `holdout.status: authorized_terminal_run` 与非空
`holdout.authorization_ref` 才可启动终端运行；这两个字段只能在取得明确授权后
写入，并作为 research-config identity 的一部分进入 checkpoint 与 signal lineage。
Supporting `scripts/research/preheat_feature_cache.py` 必须复用同一配置 gate，并在
读取或写入任何留出期 feature shard 前失败；不得把 cache 物化当作绕过终端授权的
旁路。
终端 benchmark 与 portfolio analytics 同样必须接收并 hash-bind 非空
`terminal_authorization_ref`，同时把 `holdout_consumed` 明确写为 `true`；未授权时
仍在读取或写入留出制品前 fail closed。

`run_from_signal_cache` 的 accounting v1 是研究回测的账本边界，不是生产
ledger，也不改变 signal、feature、model 或 strategy。输入 SignalRun 的内容、
source manifest/hash 与 strategy config 必须保持 immutable；accounting 只替换
execution/valuation 层。run manifest 必须绑定实际读取的 raw market-data bytes、
corporate-action artifact（`events.parquet` 与其 manifest/hash）、SignalRun
identity、strategy/config 参数及所有 accounting 参数。不得用 `latest`、mtime 或
未绑定路径推断 lineage。

Accounting v1 的规范如下：

- **不可变行情切片。** `scripts/research/backtest_from_signal.py` 可通过
  `--freeze-canonical-data-to` 在回测启动时按实际再平衡候选标的、保留截至
  `end_date` 的全部历史行，并原子写入不可覆盖的 canonical market slice；本次
  回测随即只读取该切片。slice manifest 必须同时绑定源文件与冻结文件 SHA-256、
  截止日、逐文件行数和 producer code hash，backtest manifest 必须继续绑定 slice
  manifest。终端区间的切片创建仍受同一显式授权门约束。

- **Raw price + event ledger。** 执行与收盘估值只使用 canonical raw price；
  corporate actions 来自不可变的、hash-bound event artifact。artifact 必须绑定
  `events.parquet`、manifest 与 normalized source-row hashes；提供 raw source 时，
  writer 会把原始文件复制进 artifact，并以 `source_raw_path` +
  `source_raw_artifact_sha256` 绑定和复核实际 bytes。CSI1800 baseline 使用的
  corporate-action artifact 必须保留这组 raw-source binding。不得用前复权/后复权
  价格替代真实交易账本，也不得从 factor 变化臆造未审计事件。factor completeness
  guard 忽略 `abs(current / previous - 1) <= 5e-4` 的 canonical factor 四位小数舍入
  噪声；只有超过该 `FACTOR_ROUNDING_REL_TOLERANCE` 的变化才要求 immutable event
  artifact 覆盖（event/pending 判断使用同一阈值）。该 guard 永远不会从 factor 变化推导
  或创建公司行动事件。
- **缺价/停牌估值。** 持仓若当日没有合法 close，只能沿用最近一个合法交易日
  close，并在 valuation ledger 标记 `stale_price=true`、`price_date` 与
  `stale_days`；这样不会把持仓市值写成 0，也不会凭空制造 PnL。复牌后使用新观测
  的合法 close。carry-forward 只允许 valuation，绝不允许作为 execution price；
  从未取得过合法 close 的持仓必须 fail closed。若缺价日同时是公司行动 ex-date，
  event ledger 会对 carry-forward reference 做 valuation-only 的除息/除权换算，
  但保留原 `price_date` 和 stale 状态；该 reference 仍不得用于成交。
- **公司行动。** 事件在 ex-date 按 immutable ledger 幂等处理。Tushare cash
  entitlement 使用实施方案声明的税前毛额 `cash_div_tax`，用于维持 raw-price
  除息连续性；先形成 receivable，pay-date 才转入 cash，pay-date 前不可支用。
  按持有期计算的个人红利税明确 **not modeled**，不得以净额 `cash_div` 静默替代。
  同一 Tushare source row 的 `stk_bo_rate` 与 `stk_co_rate` 合并为一个经济事件，
  share multiplier 为 `1 + stk_bo_rate + stk_co_rate`，比例相加而不顺序复利。
  stock dividend / bonus shares 在 ex-date 调整 total shares，新增份额在
  `div_listdate` 前不可卖出，上市后才成为 sellable shares；split/consolidation
  在 ex-date 调整 shares，total basis 保持不变而 per-share average cost 随之调整。
  Tushare 同一实施方案的重复 source rows 按 instrument、ex/end date、现金 entitlement、
  `stk_div`/share rates、record/pay/list date 的 NaN-normalized economic key 去重；保留最新可用
  `ann_date`，再以 `imp_ann_date` 和 source-row hash 稳定决胜。经济金额不同的同日事件
  不合并；raw source bundle 仍保留全部原始 rows，只有 normalized event 保持单份。
  `stk_bo_rate` 与 `stk_co_rate` 均有值时，二者之和优先且必须与 `stk_div` 一致（允许
  浮点误差）；若 components 缺失/为零而 `stk_div` 大于零，则以 `stk_div` 生成一个
  `stock_dividend` event，不再从 total 与 components 双计；显著冲突必须 fail closed。
  cash/stock/bonus 的 settlement date 必须存在且不早于 effective date；split/
  consolidation 的 settlement date 为空或等于 effective date。每个事件都要留下
  event id、shares/cash/receivable/basis 前后值与处理状态。
- **A 股成交约束。** 买入份额遵守 T+1，当日新买入不可卖；停牌股票不可成交；
  涨停不可买，跌停不可卖。约束只做成交可行性判断，不实现撮合引擎。
- **流动性门槛。** `order_value / ADV` 的 ADV 必须来自严格早于 execution date
  的观测（`strict_prior_ADV`），窗口与 `max_participation_rate` 配置化。超过门槛
  按 `warning` 记录告警或按 `reject` 拒单；启用完整 accounting 时，缺失/无效 ADV
  也必须拒绝，不能把它当作无限流动性。`adv_window` 必须为正数，且
  `1 <= adv_min_periods <= adv_window`。

完整 accounting gate 必须通过 CLI `--require-complete-accounting`（runner API 为
`require_complete_accounting=True`）显式启用。gate 要求 canonical raw-data root、
corporate-action artifact，以及固定的 baseline 流动性策略
`max_participation_rate=0.10` + `liquidity_gate_mode=reject`。配置/输入 artifact 缺失，
events/manifest/raw-source 的已声明 hash 不匹配，或事件类型、announcement/effective/
settlement 日期、数值、唯一性校验失败时，整次 run 必须中止。单笔订单遇到
missing/invalid execution price、停牌、涨跌停、T+1 不可卖数量、缺失/无效 ADV 或
参与率超限时，必须留下原因明确的 rejection，而不是虚构成交。持仓从未取得过
合法估值价时同样中止。不得静默回退到 legacy zero-mark 或
`corporate_action_policy=not_modeled`。generic accounting store 允许不携带 raw-source
文件的 normalized artifact；仅 CLI gate 本身不能替代对 baseline manifest 中
`source_raw_path` / `source_raw_artifact_sha256` 非空且匹配的验收。

Accounting v1 的同一 run 必须输出并在 manifest 中 hash-bind：

- `executions.csv`，schema `backtest_executions_v2`，包含请求/成交数量与价格、
  fee/tax、status/rejection reason、order value、prior ADV、participation rate 与
  liquidity status；
- `corporate_action_ledger.csv`，实际应用的事件及 shares/cash/receivable/basis
  attribution；
- `valuation_ledger.csv`，quantity、sellable quantity、raw last price、price date、
  market value、`stale_price` 与 `stale_days`；
- `accounting_attribution.json`，缺价 carry-forward 与 corporate-action 对现金、
  shares 的审计汇总，包括 stale position-days / stale market value、source/applied/
  no-position/settlement event counts、cash entitlement、pay cash 与 share adjustment；
  以及同一 run 的 `daily_summary.csv` 与 `metrics.json`。realized/unrealized PnL 与
  accounting identity error 记录在 daily summary，不由 attribution 文件虚构拆分。

manifest 的 accounting block 至少声明 schema、valuation/execution/corporate-action
policy、T+1、strict-prior-ADV 参数、各 artifact path/hash/row count，以及输入
corporate-action manifest。`backtest_executions_v1` reader 保留兼容性以读取历史
artifact，但旧 v1 产物不能被宣称为 complete accounting、不能冒充
`CSI1800_S180_baseline_v1`，也不应被回写成生产 ledger。

本 contract 明确不做：adjusted-price accounting、full order matching engine、
market-impact model、order slicing、VWAP/TWAP execution 或 order-book simulation。
这些不属于 accounting v1 的验收范围。

### Financial RC 60d/180d Cache-to-Backtest Runbook

60d 与 180d 必须分别运行研究配置，使训练标签分别使用 61 与 181 个交易日的
成熟期。不得为了组合方便把两个标签塞进同一个滚动配置；pipeline 会采用声明
标签中的最大 maturity lag，从而把 60d 训练窗口也推迟到 181 日。

```bash
# 1. 分别产生滚动 OOS 信号并写入 SignalStore。
python scripts/run_research.py \
  --config configs/research/60d/_60d_v3a_growth_financial.yaml
python scripts/run_research.py \
  --config configs/research/60d/_180d_v3a_growth_financial.yaml

# 2. 从两个明确的 SignalRun 物化 0.5/0.5 组合 cache，再回测组合产物。
python scripts/research/backtest_from_signal.py \
  --signal-id fwd_ret_60d_raw__daily_zscore \
  --signal-run-id <60d_signal_run_id> \
  --signal-id-2 fwd_ret_180d_raw__daily_zscore \
  --signal-run-id-2 <180d_signal_run_id> \
  --blend-weight 0.5 \
  --materialize-blend \
  --blend-output-signal-id financial_rc_60d180d_equal \
  --blend-output-signal-run-id <reviewed_blend_run_id> \
  --start-date <execution_start> \
  --end-date <execution_end> \
  --top-n 200
```

物化组合采用 `(trade_date, data_date, instrument)` inner join，组合 manifest 必须
保留两个 source signal/run id 与权重。当前 csi800 历史研究仍使用 current
constituents snapshot，存在幸存者偏差；在 PIT universe provider 接通前，这类
回测只能用于流程烟测和探索，不能宣称无偏 OOS 或用于晋级。

### Required Checks
- TBD: research artifact schema check
- TBD: label maturity gate check
- TBD: backtest lineage check

### Owner Agent
research_agent

### Allowed Paths
- `qsys/research/`
- `qsys/signal/`
- `qsys/label/`
- `qsys/feature/`
- `qsys/evaluation/`
- `qsys/backtest/`
- `qsys/analysis/`
- `qsys/data/adapter.py`
- `qsys/data/income_sidecar.py`
- `configs/research/`
- `configs/features/`
- `configs/labels/`
- `scripts/research/`
- `tests/`

### Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/ops/daily_runner.py`
- `deploy/`

### Open Questions
- （已定）IC 计算统一路线：rolling 过程中模型和信号层都存档，通过 SignalStore 做信号组合，然后统一算 IC/metrics 以及运行回测。
