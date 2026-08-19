# S180 四 Retrain Phase 巨大 Portfolio-Path 差异 —— 机制归因与决策

Date: 2026-08-18 · **⚠️ 2026-08-20: P5/P10/P15 证据作废（params-fallback bug）——见下**

## ⚠️ VOID / CORRECTION NOTICE（2026-08-20）

**本文档的全部 P5/P10/P15 数值基于 VOID bare-params runs**：`rr_{p5,p10,p15}__rawrank` 在 params-fallback 回归（commit `db539d6a`，`dict(gen.lgb_params or {})`）期间用 LightGBM 原生默认参数训练（2026-08-18 01:11–01:20 +0800），Spearman ≈ 0.80 vs corrected。因此：

1. **CAGR 差异叙事失效**：旧数字 "P0 57.9% vs P5 15.8% / P10 22.4% / P15 18.2%"（跨 tuned-P0 vs bare-shifted 的**跨 config 比较**）**不是** entry-timing/phase 效应。Corrected backtests（`RR_p{5,10,15}_single_correct` = `848f2b47`/`13892a75`/`7a73b7fa`，`data/research/ablation/ensemble_pf/`，配置与旧 backtest 逐字段相同、仅 signal 为 `rawrank_correct`）给出 **P5 +44.2% · P10 +55.0% · P15 +29.3%** vs P0 +57.9% —— P10 ≈ P0，P5 落后 13.7pp，**仅 P15 是明显弱尾**（+29.3%，MaxDD −49.1% 也是四相最深）。
2. **"巨大路径差异"作废**：差异的**量级**大幅缩水，不是 3–4× CAGR 摆动。"名字级排名实现分歧"作为机制仍部分成立（见 Sec 5），但它是 **P15 特定滞后 + P15 日历边缘 clamp**，不是四相普遍的彩票差异。
3. **KEEP_CURRENT 决策的证据基础作废**：该决策建立在其上的是 bare-vs-tuned 假象。需在 corrected 相位上重估（P15 弱尾是否值得投入规则）。指向 corrected 基线：`RAW_RANKING_PHASE_REPORT.md`（Sec 5 + Four-answers #3，2026-08-20 修正）。
4. 本文档 **Sec 2 同日跨 phase rank 表、Sec 6 consensus 反事实** 均基于 VOID 信号 rank —— 保留为历史机制分析，但**不可引用为当前证据**。

Signal: `fwd_ret_180d_raw__daily_zscore` (fresh S180, raw rank, Top5)
Phases: P0/P5/P10/P15 = 整条 rolling schedule 平移 +0/+5/+10/+15 交易日（独立重训）。
Backtest_ids: P0 `afdd7696`（有效）· P5 `25f9f4cb` / P10 `bf2fbcd8` / P15 `c91ea5ae`（**VOID**，bare 信号）· corrected `848f2b47`/`13892a75`/`7a73b7fa`。
CAGR: **（VOID）P0 57.9% vs P5 15.8% / P10 22.4% / P15 18.2%** → **（corrected）P0 57.9% vs P5 44.2% / P10 55.0% / P15 29.3%**。

> 决策（Sec4）：**KEEP_CURRENT —— 证据基础作废，待 corrected 重估**。原结论"路径差异来自独立重训模型的**名字级排名实现分歧**，无 PIT 可观察规则可事前识别"建立在 bare-vs-tuned 假象上；corrected 下差异大幅缩水、集中于 P15。

---

## 1. 前置（已复现，非轶事）

- Episode 统计与用户数据完全吻合：P0 218 eps / avg hold 31.8td / avg ret +7.8% / med +1.4%；
  61-120td bucket n=25, win 72%, med_ret +24.9%（用户 +24.7%），med_MFE 41.5%。
  其他 phase 261-276 eps / 25.0-26.4td / 61-120td bucket 仅 12-13。
- 首次选择 lead/lag **对称**（P0 早 32-35 / 晚 33-36）→ P0 不系统更早选到赢家。

## 2. 核心发现：同日跨 phase rank 分类性分歧

> **2026-08-20 VOID 说明**：本节 rank 数据来自 VOID bare-params 信号（`rr_{p5,p10,p15}__rawrank`）构建的 master dataset。rank 分歧的**机制**（不同 retrain 窗口→不同模型权重→同日同特征不同排名）依然成立，但**具体 rank 数字与"P0 独享怪兽"的强度不可引用** —— corrected 下 P0 仍最强但差距大幅缩小（P5 44.2% / P10 55.0% vs P0 57.9%），见顶部 VOID notice。重新计算需在 `rawrank_correct` 上重建 alignment（phase_alignment.py 待更新）。

同一交易日、同一股票，四个独立重训模型的**全宇宙 rank**（VOID 历史值，仅作机制示意）：

| 日期 | 股票 | P0 | P5 | P10 | P15 |
|---|---|---|---|---|---|
| 2025-12-02 | 603256.SH | **r1** | r67 | r126 | r78 |
| 2022-12-26 | 302132.SZ | **r1** | r25 | r21 | r34 |
| 2021-11-24 | 002265.SZ | r339 | **r10** | r18 | r259 |

- 预测 `data_date` lag=1（特征每日新鲜，周一 lag=3 周末）→ 四 phase 在同一日期用**同一份新鲜特征**，
  分歧**纯在模型权重**（不同 retrain 窗口），与信息/时点/特征无关。
- 怪兽行情期，P0 的模型把赢家排 top5（起涨点便宜入场），其他 phase 排 6-126（错过或高价追）。

## 3. Sec1 累计 PnL 归因（equal-weight log proxy，精确可加，W=0.2）

> **2026-08-20 VOID 说明**：本节归因基于 VOID bare-params backtests（P5/P10/P15 行）。"early 主驱动"机制方向可能保留，但**量级（+2.144/+2.182/+0.988）与 TOTAL gap（+1.247/+0.928/+1.051）不可引用** —— corrected 下相位 CAGR 差距大幅缩小，归因需在 `RR_p{5,10,15}_single_correct` 上重跑。

`gap(s) = ln(1+P0_tot) - ln(1+PX_tot)`，全 symbol 累计：

| 成分 | vs P5 | vs P10 | vs P15 | 解读 |
|---|---|---|---|---|
| early（入场价格） | **+2.144** | **+2.182** | **+0.988** | **主驱动**：同名赢家 P0 更便宜入场（排名实现，非时间） |
| hold（出场价格） | -0.808 | -2.475 | -1.367 | P0 在共同赢家上更早出场 |
| dropout（换手成本） | -0.620 | +1.106 | +1.672 | P10/P15 卖后再买损失大 |
| exclusive（独家赢家） | +0.531 | +0.114 | -0.242 | 小 —— 不是不同名字 |
| **TOTAL** | **+1.247** | **+0.928** | **+1.051** | = logged gap，无残差 |

**怪物集中度**：P0 正收益 top3（302132 +405%、603256 +259%、002265 +206%）= **50.3%** 的 P0 全部正收益；
top8 ≈ 75%。但 P0 **非**普遍最佳：301377 P0 -16% vs P10 +40%；001203 P0 +1% vs P5 +152%；
002281 P0 -6% vs P10 +93% —— 其他 phase 也抓到自己的怪兽。

### 同一赢家的失败模式（Sec1 C 分类）

| 赢家 | P0 | P5 | P10 | P15 | 机制 |
|---|---|---|---|---|---|
| 302132.SZ | +405% | -14% | +4% | -17% | P0 独占怪兽窗口（09-26 r1 起 4 reb 连续 top5）；P5 全程 rank 9-17 从未进；P10 r4 半山腰进→r11 掉→r1 行情后回补；P15 行情后 r1 |
| 603256.SH | +259% | +20% | -33% | +64% | 全 phase 都抓过同名，P0 抓住两大腿（+54%/+134%）；P10 21td 反复换手 + 尾部 -41% |
| 002265.SZ | +206% | -14% | -3% | -6% | **P15 最早入场（2021-06-28，比 P0 早）仍 -6%**——r259 掉出，主升前被甩；P0 11-03 重训（Q3 后、暴涨进训练窗前）r3 抓 +232% |
| 688313.SH | +83% | -47% | +25% | -7% | 模型反复 flip-flop；income 无公告，无信息触发 |

## 4. Sec2A 信息新鲜度 —— **REFUTED**

- 特征每日新鲜（lag=1），四 phase 同信息；"更晚重训=更准"不成立（P5 11-25 比 P0 11-18 新，603256 反而 r67 vs r1）。
- 聚合：fresh<=10d 的 top5 入场 fwd180 med +17.1%（mean），**差于** >90d 的 +21.2%。
- NEW_IN × fresh：fresh<=10d med +3.6% vs >90d +8.5% —— 新鲜公告的新进名单**更差**。
- 688313（最大换手怪兽）在 income store 无任何公告。
- 四 phase freshness 分布几乎相同（P0 med 64.5d / pct<=20d 15%；P5 66d/19%；P10 73d/19%；P15 71d/9%）。

## 5. Sec2B transient dropout —— 非主驱动

- Dropouts：P0 84 / P5 96 / P10 115 / P15 102；drop_rank<=10 仅 12-20（14-24%）。
- while-out 中位收益**为负**（P0 -3.0% / P5 -3.5% / P10 -2.7% / P15 +0.4%）→ 典型 dropout 是真恶化，卖对。
- 怪兽换手代价集中在深 rank 掉出：688313 drop@r526（P0）、301377 drop@r13（P5）、301526 drop@r8/r188。
  → 深度掉出不是"轻微掉出"，persistence 规则无法也不该救。

## 6. Sec3 反事实 —— consensus（schedule-average）阴性

> **2026-08-20 VOID 说明**：本节数字基于 VOID bare-params 信号（跨 config），仅历史保留。corrected 下相位差距大幅缩小（P5 44.2% / P10 55.0% / P15 29.3% vs P0 57.9%），"consensus 稀释 P0 巨大右尾"的强度需在 corrected 上重估 —— 未重算，不可引用。

四 phase 分数同日平均 → 再 top5，@180 edgeA（同 1227 个共同日期，VOID 历史值）：

| 信号 | med edgeA | pos | 跨年 std |
|---|---|---|---|
| consensus | +11.94pp | 73% | 13.4pp |
| P0 | **+16.06pp** | 73% | 18.2pp |
| P5 | +7.28pp | 66% | 10.4pp |
| P10 | +10.51pp | 69% | 8.3pp |
| P15 | +11.34pp | 71% | 14.6pp |

Consensus 稀释 P0（16.06→11.94），稳定性不比 P5/P10 好。唯一 PIT 可执行的模型分歧解法无效——
分歧最大的恰是怪物名字，平均会稀释掉 P0 独享的右尾。与 E1「不触发」一致。

## 7. 结论

> **2026-08-20 修正**：原结论建立在 VOID bare-vs-tuned 假象上，**作废**。Corrected 视角见下。

**原结论（VOID）**：P0 的优势来自同名 recurring 赢家的排名实现彩票 —— P0 的独立模型恰好把
三个最大赢家（302132/603256/002265 = P0 正收益的 50%）在起涨点排进 top5，其他 phase 同日排 6-126。
信息新鲜度、入场时间、边界 dropout、schedule-average 全部被数据排除。单一生产模型无法事前看到
"另一个模型的排名"，故路径依赖不可事前识别，不投入生产规则。**KEEP_CURRENT。**

**Corrected 结论（2026-08-20）**：**相位差异远小于原报告** —— corrected backtests 给出 P5 +44.2% /
P10 +55.0% / P15 +29.3% vs P0 +57.9%（`RR_p{5,10,15}_single_correct`，`848f2b47`/`13892a75`/`7a73b7fa`）。
P10 ≈ P0，P5 落后 13.7pp，仅 P15 明显偏弱（+29.3%，MaxDD −49.1% 也是最深）。"名字级排名实现分歧"
作为机制仍部分成立（corrected 下 P0 仍是四相最强），但**不是** 3–4× CAGR 摆动；P15 的弱尾与其
日历-edge clamp（67 vs 68 有效窗口）相关。KEEP_CURRENT 决策需在 corrected 相位上重估：**P15 弱尾
是否值得投入规则（而非"路径差异不可事前识别"）**。

## Artifacts

- Scripts: `scratch/ablation/phase_alignment.py`（master dataset）、`phase_attribution.py`（Sec1 归因）
- Data: `/tmp/phase_analysis/`（*_top5.parquet / *_episodes.parquet / *_symbol.parquet / alignment_table.parquet / attribution_table.csv / consensus_fwd180.parquet / *_dropout_reentry.parquet）
- Probe: `/tmp/{rank_traj,samedate_rank,sec2b2,sec2a,newin_fresh,consensus_test,final_attribution,classify}.py`
- **Corrected baseline（2026-08-20）**：`RAW_RANKING_PHASE_REPORT.md`（correction notice + Sec 5 corrected portfolio table）· corrected backtests `RR_p{5,10,15}_single_correct` = `848f2b47`/`13892a75`/`7a73b7fa`（`data/research/ablation/ensemble_pf/`）· corrected signals `rr_{p5,p10,p15}__rawrank_correct`。
- **VOID（不可引用）**：P5/P10/P15 backtests `25f9f4cb`/`bf2fbcd8`/`c91ea5ae` 及全部基于其的数值。
