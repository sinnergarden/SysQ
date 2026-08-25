# PIT CSI1800 daily / S180 baseline r1 closure and postmortem

## 最终结论

`CSI1800_S180_baseline_v1_r1` 是当前唯一通过完整输入 lineage、PIT
universe、raw-price corporate-action ledger、缺价估值、A 股成交约束和账面恒等式
验收的 CSI1800 S180 benchmark。

| 指标 | accounting-complete 旧 v1 | post-bootstrap r1 | 变化 |
|---|---:|---:|---:|
| 期末资产 | 38,809,001.76 | 48,257,002.62 | +9,448,000.86 |
| 总收益 | +288.09% | +382.57% | +94.48 pct |
| CAGR | 27.57% | 32.66% | +5.09 pct |
| Sharpe（日频） | 1.024 | 1.157 | +0.134 |
| MaxDD | -47.26% | -41.49% | +5.77 pct |
| 年化换手 | 14.16x | 14.30x | +0.14x |

这个差异不是纯 accounting attribution。r1 同时换成了完成 shareholder bootstrap、
带 immutable snapshot/freshness lineage 的 signal，并把 corporate-action artifact 从旧
Top5 候选覆盖改成了 signal-independent PIT CSI1800 覆盖。因此不能把 CAGR 的
`+5.09 pct` 归因于分红或任一单项修复。

旧 static-current CSI800、旧 accounting-incomplete `23.70%` CSI1800 以及旧 v1
都只保留为历史对照。后续 objective/feature/model 研究统一使用 r1。

## r1 冻结 identity

- Backtest：`data/research/backtests/CSI1800_S180_baseline_v1_r1`
- Backtest ID：`bt_2021-01-04_2026-07-31_7276da40`
- Backtest manifest SHA：`07b744698e3a664847e2e0ccbe41c9319a151ee0b313eb57c34ae20c7b75ef84`
- Git revision：`7f946565`
- PIT universe：`csi1800_pit_v2`
- PIT membership SHA：`567137db93fb9b2bbdb9220f6d0ed813fec233da87948a953a255b2e08b386df`
- PIT manifest SHA：`70065082299a682a4c3443fafcee21e57fe62f159ce18c0002895a960683c579`
- Rolling checkpoint-set SHA：`e79ea3af9ca32b343677a4c5b187538fe5e8ef845ff9277e76cd101261f3e497`
- Signal manifest SHA：`61914eb32c4abeb4fbcef3c278f1e83c656369154a5cffc837dcca385b510ff3`
- Predictions SHA：`e1af06d09d83901f9da9f7bd3c368ede0fd3350b1282624a30f8d34a2e95f205`
- Predictions：2,431,524 行，2021-01-04 至 2026-07-31，共 1,351 个交易日。

策略仍是 Top5 equal-weight entry/hold drift、20d offset 0、posterior-confirmed +
rank exit、pre-open decision、open execution、close MTM。费用、滑点、T+1、
10% strict prior ADV reject 和其他 strategy/accounting 参数与旧 v1 相同。

## Accounting 验收

主要 artifact：

| Artifact | Rows | SHA256 |
|---|---:|---|
| `daily_summary.csv` | 1,351 | `bd77525519c9867fb8bfb963f17d8c60db20ce23d99d30c48dad3d41d7d60a0e` |
| `executions.csv` | 407 | `9516ef2904a870b3cc6b998c9026d911c3e02d07507b5929299adbed7bf3c539` |
| `corporate_action_ledger.csv` | 10,811 | `ee3d7eff0cbb584afabfb3b9754f9bbe9fc212493f97a524e1daa468ece3f4e0` |
| `valuation_ledger.csv` | 6,275 | `6624665d7d1f213bd5e9f8392806925f030f5b5efab345d278656865cc0e950e` |
| `accounting_attribution.json` | 1 | `881e8145ebaa3dd3bd10ffc8c9d416113c20eb9a5ab8192424dd265b6fc1b29c` |
| `metrics.json` | 1 | `ab041bf4ef807169a2bd49196f0599b9f7a7a63b6e09c52a3f8d42ddb1a0b2ac` |

- 1,351 天均为 `success`。
- `cash + receivable + market value = total value` 最大误差 `7.45e-9`。
- 完整 accounting identity 最大误差 `2.24e-8`。
- 407 个订单，405 成交，2 个因 participation 超过 10% ADV 被拒；无非法停牌、
  涨停买入、跌停卖出或 T+1 成交。
- 23 个 stale position-days，均使用最近合法 close；没有把持仓估值为 0。
- stale market-value day sum 为 76,482,923 元·天。

### Corporate action v3

旧 v2 的全市场 raw 本来已有 `000426.SZ` 2023-08-29 每股 `0.017` 元现金分红，
但在 normalize 之前按旧 signal 的 145 个 Top5 候选过滤，导致事件被丢弃。旧 v1
没有持有该股票，所以 held-position factor guard 没触发；新 signal 持有后才暴露。
“旧回测能跑完”只证明旧持仓路径没碰到缺口，不证明 source coverage 完整。

v3 改为按每条 `ex_date` 对 `csi1800_pit_v2` 做成员过滤，与 signal/Top5 解耦：

- 全市场 raw：26,300 行；PIT-filtered raw：10,554 行；rejection：0。
- 归一化经济事件：10,784；历史股票：2,281。
- cash dividend 10,022、bonus shares 692、stock dividend 70。
- Events SHA：`287a10d9ea2f3031e8a77a3353e0c3ab44c2034d7abacd544f76abd547cfaccc`
- Manifest SHA：`4560109ff16b7c55a384d73d217ac8bb321dd65a77fcfae57056e8a3d3e1c916`
- Source bundle SHA：`87cec6b701de3e4b0c7ff6e4fc170cc826906769433e2c8932c44ee35edfe117`
- Source bundle 同时保存原始全市场 parquet 和派生 PIT parquet，并排除
  `candidate_filter`、`candidate_raw`、signal identity。
- r1 实际持仓应用 27 个事件，现金分红 1,670,874.79 元，股份调整 63,720 股。
- `000426.SZ` 实际入账为 `356,100 × 0.017 = 6,053.70` 元。

## 为什么旧 signal / backtest 能跑，这次却被 gate

准确时间线：

1. 8 月 22 日 `06bd9ac4` 只是提交 CSI1800 rolling 配置，不是训练完成证据。
2. 旧 cached-signal backtest 在 8 月 23 日 00:58 生成，manifest 明确为
   `rolling_train=false`、`model_mode=cached_signal`，不会重新进入训练 gate。
3. 旧 rolling signal 实际在 8 月 23 日 15:40 生成，git revision 为 `2ad394b0`。
4. 当时 `FinancialRCTrainer` 已有 freshness 检查；不能笼统说“旧 trainer 没 gate”。
5. 但是产生 68 窗 research signal 的 `lightgbm_single_label` rolling generator
   没有 immutable shareholder snapshot/freshness contract，缺值直接 `fillna(0.0)`。
6. 8 月 25 日 `7a94c491` / `891a9e1d` 才把 snapshot/hash/freshness 接入 rolling
   generator；当前重跑因此第一次在同一路径上真正执行该 contract。

第一次新 gate 失败于 window 1：`holder_num_stale_days` coverage 为
`94.240633%`，低于通用 95%。对全部 143 个受影响历史 PIT 股票复查后，113 个
endpoint 空、30 个返回 row 但 `holder_num` 为空，0 个可修复。最终保留全局 95%，
只基于证据为 holder/top10 两类 source 设置 94% / 99% feature-specific floor；没有
改 feature 值、label、signal、model 或 strategy。

所以旧版“能跑”的真实原因是执行路径和契约不一致，加上 cached backtest 绕过训练；
不是旧数据已经满足今天的 gate。

## 8 月 25 日 daily 输入与 Top10

- 2026-08-24 core sync audit：`data/audit/sync_csi1800_20260824.json`，状态 READY。
- PIT member 1,800；行情 1,799。唯一缺失 `002155.SZ` 已由 Tushare `suspend_d`
  确认 8 月 20/21/24 停牌，不是 ingestion gap。
- 8 月 24 日有 30 家 CSI1800 公司披露报表，canonical 对应 30 条均更新至
  `20260630`。
- shareholder state 已增量推进至 2026-08-24；后续 run 的历史 deficient 为 0。
- `roe_ttm` / `roe_waa` 当前仍未 populated，不能把 8 月 24 日描述为所有基本面字段
  100% 完整；这是已知 source/feature gap。
- 当日 infer 的决策日为 2026-08-24，feature/signal date 为 2026-08-21，执行日为
  2026-08-25；没有使用尚未在决策时可见的 8 月 24 日收盘后数据。

S180 raw Top10（无 z-score）：

| Rank | 股票 | Raw 180d prediction |
|---:|---|---:|
| 1 | 600601.SH 方正科技 | 0.4848390927 |
| 2 | 688213.SH 思特威-W | 0.4663523915 |
| 3 | 002463.SZ 沪电股份 | 0.4546226322 |
| 4 | 300308.SZ 中际旭创 | 0.4420093816 |
| 5 | 300476.SZ 胜宏科技 | 0.4281397931 |
| 6 | 002837.SZ 英维克 | 0.4172974140 |
| 7 | 300458.SZ 全志科技 | 0.4125126579 |
| 8 | 002683.SZ 广东宏大 | 0.4113128656 |
| 9 | 600111.SH 北方稀土 | 0.4108409068 |
| 10 | 300496.SZ 中科创达 | 0.4026695892 |

Top10 artifact SHA：`d9f34af119e0726605a9a5861c5a667ddf98be08d62a1ffa9a726cec44eee890`。
基本面审计严格按“模型信号可靠性审计”角色执行：5 个 `supported/monitor`，5 个
`mixed/reduce_confidence`，没有 `conflicted` 或 `strongly_challenge`。审计用于降低
极端错误，不替代模型排序。

## 这次流程哪里做错了，已经怎样修

### 1. CSI800 daily 与 CSI1800 research 脱节

旧 daily 只维护 CSI800，切到 CSI1800 时有 699 个历史 deficient 股票，因此第一次
必须做一次历史 catch-up。修复后 daily 先查本地 watermark/deficient symbols：正常日
只拉目标交易日，只有真实 deficient 才做 bounded history repair；后续两次审计均为 0。

### 2. “当前日期”与“最后完整交易日”混淆

已增加 18:30 cutoff 和 exact completed-session 解析。cutoff 前不能把当天未完成行情
当输入，也不能静默 fallback；timer 固定 19:00。

### 3. 财务 endpoint 使用方式不一致

`disclosure_date` 只用于发现候选，四张普通财务表仍必须携带 `ts_code`。无候选时
API 调用数为 0；响应缺关键字段或跨日时 fail closed，并对候选调用限流。

### 4. 横截面价格被误当时间序列

原 validation 对同一天不同股票直接 `pct_change()`，制造 1,521 个“>25% 异常”。
已改成按 `ts_code` 分组后计算；同股跨日异常仍会被检查。

### 5. Rolling contracts 不一致

formal trainer 与 research rolling generator 曾有不同 freshness/lineage 规则。现在
rolling artifact 固定 shareholder snapshot/hash、coverage/staleness contract、checkpoint
set 和 cache identity；旧 artifact 不再因为“文件存在”就被误复用。

### 6. 跨 PID namespace 的重复 supervisor

68 窗最后一次恢复时，当前 shell 的 `ps` 看不到另一 exec namespace 的活进程。我把
“看不到 PID”误判成“进程已死”，短暂启动第二份 w1340；内核日志确认两份 6–8 GiB
Python 叠加触发全局 OOM，杀掉一份。已增加 `<state>.lock` 的 non-blocking
`fcntl.flock`，锁覆盖 state 读取、child、checkpoint 和 terminal validation 全生命周期；
第二实例在碰 state/checkpoint 前即 fail closed。每窗独立子进程仍保留，正常释放内存。

### 7. 完整 CA artifact 造成 30 分钟性能退化

`CorporateActionStore.for_date()` 原来每天重新扫描并解析全表。事件从 929 增到
10,784 后，完整回测耗时约 30 分钟。现在初始化时一次按 `effective_date` 建索引；
等价复算约 2 分 19 秒。两次 run 的 daily、executions、CA ledger、valuation 和
attribution 五个 artifact 字节 SHA 完全一致，metrics 除 `created_at` 外一致。

## 后续 daily 应该只有一个 READY contract

理想状态机：

```text
resolve exact completed target
  -> load exact PIT CSI1800 snapshot
  -> inspect local watermarks / target rows
  -> target-only repair OR bounded history catch-up
  -> build market + financial + shareholder readiness artifact
  -> schema/date/PIT/coverage validation
  -> READY
  -> inference consumes this exact READY identity
```

正常日不应重新校验多年历史 hash，也不应重训 68 窗。hash 用于 immutable artifact
发布和 cache identity；daily 只比较上一次已接受 watermark/hash 与本次增量。历史 68
窗是 research baseline，live 路径只在既定 retrain cadence 训练最新模型，其他天复用已
部署 model bundle。

## 仍然存在的问题

按优先级：

1. **P0 — corporate action 尚未接入 daily READY。** v3 builder 已 signal-independent，
   但 daily/shadow portfolio 还需要按 ex-date 增量拉取、watermark、原子发布和 readiness
   绑定；否则 live accounting 仍可能在事件日才发现 source gap。
2. **P0 — `roe_ttm` / `roe_waa` source gap。** 当前主模型是否实际消费这两列需由
   feature-list contract 显式声明；若 required 必须阻断，若 optional 必须在 READY 中
   明确标记，不能笼统宣称“基本面完整”。
3. **P1 — CA 的 index-exit carry edge。** v3 按 `member_on_ex_date` 过滤；若股票在上次
   调仓后离开指数、但组合尚未退出且发生事件，held factor guard 会 fail closed，不会
   静默错账，但 source artifact 应进一步覆盖“历史 PIT union + 可能持有宽限期”。
4. **P1 — 长任务 heartbeat。** rolling supervisor 有 durable state；backtest 仍缺固定
   交易日 heartbeat，长 run 时 operator 无法区分正常 CPU 工作与 hang。
5. **P1 — rolling 特征加载太慢。** 68 窗每窗重新加载约三年 Qlib 数据，虽已用独立
   子进程避免内存累积，但晚期单窗仍约 8–9 分钟。应预物化 immutable feature matrix
   并按窗口切片；这属于 research 加速，不应进入 daily。
6. **P1 — partial fill 可观测性。** 现金/费用导致的缩量成交仍标为 `filled`，账本正确，
   但 execution ledger 缺独立 `partial_fill` reason。
7. **P2 — split/consolidation source coverage。** accounting kernel/test 支持拆并股；当前
   Tushare dividend source 在本样本主要覆盖现金、送股、转增，仍需独立验证真实拆并股
   的 source mapping。

这些问题不会使 r1 当前历史结果失效；P0 项会影响 daily/shadow portfolio 的长期稳定性，
应在开始新的 objective/feature 研究前先纳入 production readiness backlog。

