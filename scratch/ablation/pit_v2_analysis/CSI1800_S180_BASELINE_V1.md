# CSI1800 S180 accounting baseline v1

## 结论

`CSI1800_S180_baseline_v1` 通过 execution/accounting 层验收，可作为后续 Top-tail、feature 和 model 研究的统一基准。冻结的 PIT universe、signal、model lineage 与策略参数均未改变。

最终结果：

| 指标 | 旧 PIT CSI1800 S180（accounting 不完整） | `CSI1800_S180_baseline_v1` | 变化 |
|---|---:|---:|---:|
| 期末净值 | 32,690,639.70 | 38,809,001.76 | +6,118,362.06 |
| 总收益 | +226.91% | +288.09% | +61.18 pct |
| CAGR | 23.70% | 27.57% | +3.87 pct |
| Sharpe（日频，sqrt(252)） | 0.726 | 1.024 | +0.298 |
| MaxDD | -49.65% | -47.26% | +2.39 pct |
| 年化换手（gross notional / mean equity / calendar years） | 15.69x | 14.16x | -1.53x |

这不是 signal alpha 的提升，也不能把 6,118,362 元差额简单归给分红。新的结果来自完整账本、合法估值和成交可行性共同改变后续资金路径；各项会互相复利，不能相加分摊。旧结果仅保留为污染对照。

## 冻结 lineage

- Signal manifest SHA256：`3ad84c42fa0ba186bfcdebf35d22d6a9440978f76118d6c3963b94552c315dcb`
- Predictions SHA256：`e76c25a95c1985914f5dd74bbdc40dbbd35250a787e44a52333ff2f0a8230eb4`
- PIT membership SHA256：`567137db93fb9b2bbdb9220f6d0ed813fec233da87948a953a255b2e08b386df`
- PIT manifest SHA256：`70065082299a682a4c3443fafcee21e57fe62f159ce18c0002895a960683c579`
- 策略仍为 Top5、equal-weight entry/hold drift、20d offset 0、posterior policy 原参数、open 成交、close MTM。
- 佣金、印花税、最低佣金与滑点参数均与旧 run 相同。

逐字段比较旧、新 manifest：signal source、PIT identity、strategy template、allocation、rebalance、posterior policy、execution timing、费用参数全部一致。

## Accounting 验收

### 缺价与停牌估值

- 23 个 stale position-days，涉及 `002312.SZ` 1 天、`000425.SZ` 12 天、`000630.SZ` 10 天。
- 以最近合法 close 估值的 stale market value 累计为 54,685,742 元·天。
- stale 期间不产生伪 PnL；复牌后恢复当日合法 close。
- 如果按旧错误逻辑把这些持仓临时记为 0，Sharpe 会从 1.024 降至 0.837，并制造最高约 -25.4% / +32.9% 的单日假跌/假涨；正确曲线最大绝对单日变化约 10.0%。
- 这些区间不在全局最大回撤点，也不含期末，因此零估值反事实的 CAGR 和全局 MaxDD 恰好未变；这不代表错误无害。

### 公司行动

- immutable source：929 个归一化经济事件；900 个发生时无持仓，29 个对持仓实际应用并完成结算。
- 持仓事件：27 个现金分红、2 个送股/红股事件。
- 宣告毛现金权益与实际入账现金均为 1,381,771.53 元。
- 股份增加 57,605 股；share event 前后 total cost basis 最大差为 0。
- 使用 raw price + event ledger；未使用前复权价格代替账本。
- 当前 baseline 没有实际命中的 split/consolidation，但 kernel 和测试覆盖该语义。
- 分红个人/机构税未建模，现金口径为 Tushare `cash_div_tax` 的 declared gross entitlement；这是保留限制。

Tushare 同一实施方案可能出现多条更新记录。归一化层按股票、除权日、报告期、现金/送转参数、登记/派付/上市日组成的经济 key 去重，确定性保留最新公告记录；原始 rows 全部保存在 hash-bound ZIP。初次 run 因 `688676.SH` 2025-05-23 重复派息被审计作废，保留在 `CSI1800_S180_baseline_v1_rejected_duplicate_ca`，未用于结论。

### A 股成交约束与流动性

- 408 个订单：405 成交，3 拒绝。
- 405 个成交状态中有 24 个买单因可用现金与费用约束而缩量，合计少于 requested quantity 1,687,000 股；实际 `filled_qty`、现金和持仓账本均正确，但 schema 仍标作 `filled`，没有独立 `partial_fill` reason。它不改变本次结果，但属于执行 ledger 的可观测性限制；本任务按约束不扩展为完整撮合引擎。
- 2 个订单因 participation 13.33% / 13.20% 超过 10% ADV 门槛拒绝。
- 1 个卖单因跌停拒绝。
- 成交订单最大 participation 为 9.094%；没有成交超过 10%。
- 独立重算所有订单的严格 T-1 ADV，与 ledger 记录完全一致。
- 独立逐笔检查 canonical open/paused/high_limit/low_limit：0 个非法成交。
- 独立按交易和公司行动 ledger 重放 shares/sellable shares：1,351 天持仓完全一致，T+1 违规为 0。
- 本样本没有触发涨停买入或停牌成交请求；对应 fail-closed 行为由单元和集成测试覆盖。

## 账面与 artifact 完整性

- 1,351 个交易日。
- `cash + dividend receivable + market value = total value` 最大绝对误差：`7.45e-9`。
- 完整 realized/unrealized/corporate-action identity 最大绝对误差：`2.24e-8`。
- Daily、executions、corporate-action ledger、valuation ledger、metrics、attribution 的实际 SHA256 和 manifest 全部匹配；row count 全部匹配。
- Top-level 与 nested accounting manifest 的 6 个 artifact 条目均具备 `path/schema_version/sha256/row_count/complete=true`；baseline manifest SHA256 为 `2ef107cb61f6591d48623f7c35357ec7582c680a232f6d6e4e3d69e27a5b2704`。
- Corporate-action manifest SHA256：`fcfc6df84c027c6712faed9040aadc9349688348f3b388015fef772c956364c8`
- Corporate-action events SHA256：`40c70e1292af0ab29c642d455832896a730992990921043d3b2b8bdf76a5bbbf`
- Corporate-action raw bundle SHA256：`ae946394af3f07da0edae0486bdf1db4826759805d856a0a769d4541d559cbd6`
- Canonical market source aggregate SHA256：`f845a230aae276703f9e86aa57fa5a0f1d6329126a2f67efa7ff6a317f22ee68`

主要 baseline artifact hashes：

| Artifact | Rows | SHA256 |
|---|---:|---|
| `daily_summary.csv` | 1,351 | `9a7da69a2ff886111b0b2a9eab9e20ee436032581c50f31275852a2c12af7dc3` |
| `executions.csv` | 408 | `7fd6db2bd7ac3b82d8a8639cd401e7bb4ac04f871d2b6317e3a12638021a4e37` |
| `corporate_action_ledger.csv` | 958 | `c94f9a16221a319717f8186b610e1155883c06dbabda07128978088080ee14ee` |
| `valuation_ledger.csv` | 6,355 | `d6c70d10082ce90d4bd1c433e843509aee8c8d49aed9358e6e5af3181e9ad8c3` |
| `metrics.json` | 1 logical row | `8f3a2617654ab773c4b6bf04376408900d0544944194405616fe1342224733d4` |

## 实现与门禁

- Backtest 专用 account 与生产 ledger 语义隔离。
- pre-open 订单目标资金只使用前一合法 close；当日 open 只把目标金额换算为可成交股数，避免同日信息进入决策估值。
- 完整模式缺少 raw corporate-action provenance、canonical source、10% reject gate 等任何一项都会在输出前失败。
- canonical factor 四位小数的全市场同步重舍入噪声允许 0.05% 相对容差；超过容差的连续持仓 factor jump 必须由 immutable event 解释。离场后清除 guard state，不跨空仓误比较，且绝不从 factor 反推事件。
- `tests/backtest`：189 passed；Qsys 的 model-resolution、entrypoint、use-case registry、agent-doc harness 全部通过。

## 使用边界

后续研究统一以 `data/research/backtests/CSI1800_S180_baseline_v1` 为 benchmark。不要再用旧 23.7% accounting-incomplete run 作为模型优劣判断，也不要使用已作废的 duplicate-CA run。

下一步按既定顺序：先做 Weighted top-tail regression，评价 NDCG@5、Top5 excess、winner capture 和本 baseline 下的组合指标；只有前者通过门槛才进入 LambdaRank。不能只按单一 CAGR 选择。
