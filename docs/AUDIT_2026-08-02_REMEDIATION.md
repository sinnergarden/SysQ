# Qsys 审计整改计划（2026-08-02）

> 来源：2026-08-02 双轮只读审计
> - 轮 1（合规/健康）：6 维度并行 + 14 对抗性验证，产出 1 BLOCKER + 10 HIGH + 4 MEDIUM + 1 LOW
> - 轮 2（Quant 方法论/框架合理性）：7 评估器 + 12 事实核查，39 条评估 + 2 新 BLOCKER
>
> 本文件是整改计划的**唯一事实源**。执行原则：按 Wave 顺序，每个改动走对应 UC + skill + harness check + PR（遵守 AGENTS.md「禁止直接推 main」）。每次完成一项，勾掉「状态追踪」对应项。

---

## 决策记录（用户已确认 2026-08-02）

| 决策 | 结论 | 影响 |
|------|------|------|
| **D1 · F01 修法** | **方案 A**：研究生成器特征锚定 `data_date`（前一交易日收盘），`trade_date`=买入日，`open[trade_date]` 成交 —— 与生产 alpha_v1 同语义 | 根除研究链路 1 交易日 lookahead；需重跑关键实验 |
| **D2 · F17 处置** | **方案 C**：financial_rc 保持现状产候选，并行补正式 OOS 回测 | 不暂停在产线；修复期间补证据 |
| **D3 · F18 权重** | **0.5/0.5**（60d/180d 平权），收敛为唯一权重 | 删除 predict_financial_rc 与 gen_candidate_top200 权重分歧 |

---

## Wave 0 — BLOCKER 正确性（先修）

| ID | 问题（审计引用） | 修复方案 | 验证 | 归属 UC |
|----|------|---------|------|---------|
| F01 | 研究链路 1 交易日 lookahead（A1, BLOCKER） | **方案 A**（见决策 D1）：统一研究/生产信号日期语义，研究生成器特征锚定 `data_date`；修复后重跑关键实验复核 IC/回测 | `check_no_lookahead` 升级为校验 feature 矩阵真实信息截止（非自报 `data_date`）；重跑 60d/180d 对比 | UC_RESEARCH_BACKTEST |
| F02 | 校准器硬 0/1 输出（B2, BLOCKER） | `calibrate.py:172-176` sigmoid 分支 `_calibrator.predict()` → `predict_proba()`；已产出坏输出（4 次 sigmoid 运行）标注作废 | 单测：sigmoid 输出连续概率；outputs 加质量断言（logloss/唯一值数） | UC_DAILY_INFERENCE_RUN |
| F03 | Promotion pointer 物化缺失（B1, BLOCKER） | 落地 `data/research/promotions/shadow.yaml` + `artifacts/registry/models/`；候选 hash 显式写入 pointer | `check_inference_artifact` 校验 pointer 存在且指向具体 hash | UC_CANDIDATE_PROMOTION |

## Wave 1 — 账户状态与资金安全

| ID | 问题 | 修复方案 | 验证 | 归属 UC |
|----|------|---------|------|---------|
| F04 | committed run force-rerun 静默让 ledger 失效（A5, HIGH） | 实现 ledger reversal + re-apply，或对 `completed` run 的 force-rerun 阻断；`ctx.ledger_run_id` 贯通 `commit_execution` | ledger 幂等性 harness check；rerun 测试 | UC_DAILY_OPS |
| F05 | TradeLedger 与 LedgerService 争写同一 trade.db（A6, HIGH） | TradeLedger/ExecutionService 移出 `data/trade.db` 或统一 schema；`CREATE TABLE` 前 schema 冲突断言 | 冲突断言 harness check；`pytest tests/ledger` + `tests/ops` | UC_DAILY_OPS |
| F06 | scripts/live/ 实盘链路游离 UC 框架（A2, HIGH） | 归入 UC（UC_LIVE_OPS 或并入 UC_DAILY_OPS）+ 订单审批 gate；或移 `scripts/deprecated/` | `check_scripts_entrypoints` 覆盖子目录；PR scope check | UC_TEMPORARY |

## Wave 2 — 测试与机械门禁

| ID | 问题 | 修复方案 | 验证 | 归属 UC |
|----|------|---------|------|---------|
| F07 | pytest 收集即中止（A9, HIGH） | `test_60d_configs_smoke.py` 改真正 pytest 函数（去模块级 `sys.exit`）；补 CI（`.github/workflows/ci.yml`） | 全量 `pytest tests/ -q` 恢复绿 | UC_DIAGNOSTICS |
| F08 | backtest NameError（A10, HIGH） | `run_from_signal_cache` 绑定 `maxdd_signal_id/run_id/threshold/percentile`（缺省参数或从 config 取） | `pytest tests/backtest` 转绿；更新 KNOWN_FAILURES | UC_RESEARCH_BACKTEST |
| F09 | harness checks 无机械门禁（A11, HIGH） | 3 个决策 check（label maturity / inference ready / inference artifact）接入 `run_daily_batch.py` preopen 前后；CI 跑 standalone checks | 定时任务日志含 check 结果；失败阻断 stage | UC_DAILY_OPS |
| F10 | 180d `_last` config 0 窗口（A8, HIGH） | 修 config（单窗口正确表达 last-window）或删除；`build_rolling_windows` 0 窗口时报错 | harness guard（0 窗口 → fail） | UC_RESEARCH_BACKTEST |

## Wave 3 — 框架治理与代码卫生

| ID | 问题 | 修复方案 | 验证 | 归属 UC |
|----|------|---------|------|---------|
| F11 | 缺失 skill（A3, HIGH） | 补齐 `sysq-dev`/`sysq-review`/`sysq-stock-research` SKILL.md 或移除悬空引用；`check_usecase_registry` 校验 skill 存在性 | harness check 扩展 | UC_DIAGNOSTICS |
| F12 | run_backtest.py 遗留悬空引用（A13, MEDIUM） | run_train `--run_backtest` 指向 `backtest_from_signal.py`；修 `run_mainline_rolling_eval.py` import；清 SOP 引用；legacy 移 deprecated | 3 个测试收集恢复；`--run_backtest` 可跑 | UC_RESEARCH_BACKTEST |
| F13 | 混 PR 治理（A12, MEDIUM） | 后续框架/业务/文档/测试分 PR；loop_memory 记录教训 | PR scope check 纳入 review | UC_TEMPORARY |
| F14 | mtime 解析信号 run（A16, LOW） | `signal_analytics.py` 改 manifest 指针解析，或 `check_no_latest` 扩展扫 `qsys/research/` | 扩展 harness 扫描 | UC_RESEARCH_BACKTEST |
| F15 | stop-loss artifact 无模型标识（A14, MEDIUM） | stop-loss LGBM 落盘 `data/research/models/stop_loss_binary_5d/{hash}/`，artifact 写 model_hash/train_start/feature_snapshot | `check_inference_artifact` 通过 | UC_DAILY_INFERENCE_RUN |

## Wave 4 — 研究方法论重构（轮 2 低分项）

| ID | 问题（评分） | 修复方案 | 验证 | 归属 UC |
|----|------|---------|------|---------|
| F16 | label 成熟度仅声明式（A4, HIGH / label 3/5） | lag 从 label artifact 的 horizon 推导（解析 `fwd_ret_{h}d`），强制 `max(lag) >= max(label horizon)`，缺省即 fail；6 个 config 补字段 | `check_label_maturity` 接入 pipeline（F09） | UC_MODEL_TRAINING |
| F17 | financial_rc 无正式 OOS 记录（证据 1/5） | **方案 C**：不暂停，并行对在产 2 个 hash 跑正式策略级 OOS 回测，`backtest_verified: true` | 回测 + strict_eval 报告入库 | UC_CANDIDATE_PROMOTION |
| F18 | blend 权重不一致 / 双轨推理（blend 2/5, 框架 2/5） | **权重统一 0.5/0.5**（D3）；收敛为单一规范入口；删除 predict_financial_rc 与 gen_candidate_top200 之一 | artifact source 字段记录所用权重 | UC_DAILY_INFERENCE_RUN |
| F19 | 指标无风险调整/无基准（指标 2/5） | metrics.json 增补 Sharpe/MDD/Calmar + CSI800 基准相对超额；promotion 证据不再只有绝对收益 | `check_dr_bt_equivalence` + 报告字段 | UC_RESEARCH_BACKTEST |
| F20 | 无多重检验守卫（2/5） | 94 config 横扫补 deflated-Sharpe / PBO / ICIR 阈值；终端 holdout 集 | 新增 harness/脚本 check | UC_RESEARCH_BACKTEST |
| F21 | 特征列表 96 因子高相关 + 复合因子循环（2/5） | 去相关聚类、移除确定性复合分（消除 SHAP 循环）；对在产 feature list 正式消融 | feature ablation 报告 | UC_RESEARCH_BACKTEST |
| F22 | 校准方法样本内评估（校准 1/5） | 严格 train/calib/test 三分；calibration 只报 test 集指标；加 quality gate | F02 单测扩展 + 输出质量断言 | UC_DAILY_INFERENCE_RUN |
| F23 | 风险未融入选股（1/5） | maxdd_5d_prob 从标注升级为选择/减仓输入；修 fail-open 缺省 | 组合风险 harness/报告 | UC_CANDIDATE_PROMOTION |
| F24 | 无组合级风险层（1/5） | 补 max 仓位/行业/集中度/相关性约束；可选组合回撤预算 | 风险 check | UC_CANDIDATE_PROMOTION |
| F25 | gen_candidate_top200 缺 PIT 守卫（A7, HIGH） | 加载模型前断言 `train_end + horizon <= trade_date`（trading days）；`--trade-date` 改必填 | 复用 `check_label_maturity` | UC_DAILY_INFERENCE_RUN |
| F26 | maxdd 模型不落盘（maxdd 2/5） | MODEL_ROOT 生效：每次训练写 `{hash}/` 并记录 provenance | 与 F15 一并实现 | UC_DAILY_INFERENCE_RUN |

---

## 里程碑

| 里程碑 | 内容 | 完成判定 |
|--------|------|---------|
| M1 正确性止血 | F01 → F02 → F03 | 三项 BLOCKER 修复 + 单测 + 重跑对比 |
| M2 资金安全 | F04 → F05 → F06 | ledger/账户状态单一可信源，无未归 UC 的资金路径 |
| M3 门禁落地 | F07 → F08 → F09 → F10 | pytest 全绿、CI 在、决策 check 接入 daily、无 0 窗口 config |
| M4 治理收口 | F11 → F12 → F13 → F14 → F15 | skill 一致、无悬空引用、无 mtime 解析、artifact 有 provenance |
| M5 证据重建 | F16 → F17 → F18 → F22 → F25 | 在产候选有正式 OOS 记录 + 权重唯一 + 校准可信 |
| M6 方法升级 | F19 → F20 → F21 → F23 → F24 → F26 | 指标/风险/特征体系达到可辩护水平 |

---

## 状态追踪

### Wave 0
- [ ] F01 研究链路 lookahead（方案 A）
- [ ] F02 校准器硬 0/1
- [ ] F03 promotion pointer 物化

### Wave 1
- [ ] F04 force-rerun ledger 失效
- [ ] F05 TradeLedger/LedgerService schema 冲突
- [ ] F06 scripts/live 归 UC 或 deprecated

### Wave 2
- [ ] F07 pytest 收集中止 + CI
- [ ] F08 backtest NameError
- [ ] F09 harness checks 接入门禁
- [ ] F10 180d `_last` 0 窗口

### Wave 3
- [ ] F11 缺失 skill
- [ ] F12 run_backtest 悬空引用
- [ ] F13 混 PR 治理
- [ ] F14 mtime 解析
- [ ] F15 stop-loss artifact provenance

### Wave 4
- [ ] F16 label 成熟度强制化
- [ ] F17 financial_rc 正式 OOS 回测（方案 C）
- [ ] F18 权重统一 0.5/0.5
- [ ] F19 指标风险调整 + 基准
- [ ] F20 多重检验守卫
- [ ] F21 特征列表去相关
- [ ] F22 校准样本外评估
- [ ] F23 风险融入选股
- [ ] F24 组合级风险层
- [ ] F25 gen_candidate_top200 PIT 守卫
- [ ] F26 maxdd 模型落盘
