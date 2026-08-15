# Strategy Behavior Diagnostics — Design (2026-08-15)

> UC: `UC_UI_ANALYSIS` (draft) · Skill: `sysq-dev` · Entrypoint: `scripts/run_research_ui_api.py`
> 只读派生层，不触碰 `qsys/backtest/` 写入路径，不修改 ledger/broker/trader。

## 1. Purpose

给回测结果新增「策略行为诊断」区域，回答三个问题：

1. **持仓片段（Episode）** — 一笔持仓从建仓到清仓到底经历了什么？持有多久、入场/离场信号强弱、MFE/MAE 有多大、事后收益如何、输/赢集中在哪个退出规则。
2. **换仓决策（Swap）** — 每次卖 A 买 B 的置换，A 被卖后涨了还是跌了？信号排序在 20/60 日后有没有翻转？
3. **退出规则消融（Rule Ablation）** — 如果某类退出规则不存在，曲线会怎样？规则到底赚没赚钱。

三条线全部**从不可变回测 artifact（executions.csv + predictions.parquet + 日线价格）只读派生**，不重跑引擎。

## 2. Delivery Breakdown（三个 PR，逐个交付）

| PR | Section | 核心产物 |
|----|---------|---------|
| PR #1 | Position Episode Analytics | `behavior.py::derive_episodes` + `/behavior/episodes` + Episode 面板 |
| PR #2 | Swap Decision Analytics | `behavior.py::derive_swaps` + `/behavior/swaps` + Swap 面板 |
| PR #3 | Exit Rule Counterfactual/Ablation | `behavior.py::replay_without_rule` + `/behavior/ablation` + Ablation 面板 |

Winner/Loser Lifecycle 与 Alpha/Beta Attribution 延后（暂不实现，设计不阻塞）。

## 3. Common Conventions（三个 PR 共用）

- **holding_days**：持仓区间内的交易日数（`entry_date`→`exit_date` 按交易日索引差 + 1）。
- **score_delta_5d / 20d**：`score(exit_date) − score(exit_date − 5 / 20 个交易日)`。数据不足则 `null`。
- **MFE / MAE**：持仓区间内逐日 `high/low` 相对当日持仓成本（avg_cost）的最大上行/下行偏离。`MFE = max((high/avg_cost − 1))`，`MAE = min((low/avg_cost − 1))`（负值）。
- **open episode**：数据末尾仍持仓 → `exit_reason = "open"`，`realized_return = null`，用 `last_close` 折算 `unrealized_return`。
- **post_exit_return_20d / 60d**：`close(exit + N 交易日)/close(exit) − 1`，数据不足则 `null`。
- **swap score 分组**：按 exit 时 score 排名分位数分桶（tercile）。
- 所有金额/价格为**前复权价**（与 executions 一致，`use_adjusted_price=true`）。

## 4. PR #1 — Position Episode Analytics

### 4.1 概念

一次 **episode** = 同一 instrument 从持仓为 0 建仓开始，到持仓归 0 清仓为止的连续持有段。

- 建仓：第一笔 buy 使 qty `0 → >0`。
- 加仓（qty 仍 >0 的 buy）合并进当前 episode。
- 减仓不清仓（qty 仍 >0 的 sell）留在当前 episode。
- 清仓：最后一笔 sell 使 qty `→ 0`，episode 结束。
- 清仓后再买入 = **新 episode**（卖一买一不合并）。

### 4.2 Episode 字段

| 字段 | 来源 |
|------|------|
| `symbol` | executions `instrument` |
| `entry_date` / `exit_date` | 首 buy / 末 sell 的 `trade_date` |
| `holding_days` | 交易日索引差 + 1 |
| `entry_score` / `exit_score` | predictions.parquet 中该股在 entry/exit 日的 score |
| `score_delta_5d` / `score_delta_20d` | exit_score − 5/20 交易日前 score（数据不足 null） |
| `realized_return` | cash-weighted round-trip：`Σsell_proceeds/Σbuy_cost − 1`（含费） |
| `unrealized_return` | open 时 `(qty×last_close − Σbuy_cost)/Σbuy_cost` |
| `MFE` / `MAE` | 区间内逐日偏离 max/min |
| `max_drawdown_from_peak` | 区间内 close 相对区间峰值回撤的最大值 |
| `exit_reason` | 清仓 sell 的 `trade_reason`；open 记为 `"open"` |
| `post_exit_return_20d` / `60d` | 退出后 20/60 交易日 forward return（不足 null） |

### 4.3 聚合输出（后端返回）

```
episodes: [...]              # 全量 episode 明细
summary:
  total_episodes, closed_episodes, open_episodes
  win_rate (closed), avg_return, median_return, avg_holding_days
  by_exit_reason: [ {exit_reason, count, win_rate, avg_return, median_return,
                      avg_mfe, avg_mae} ]
```

### 4.4 UI（Episode 面板）

放在 view-backtest 现有面板之后，新增 **Strategy Behavior Diagnostics → Episode Analytics** 区块：

1. **Return 分布直方图**（closed episodes，Plotly histogram）
2. **Holding days 分布直方图**
3. **MFE/MAE vs realized_return 散点**（x=MFE, y=MAE, color=return）
4. **By Exit Reason 聚合表**（count / win_rate / avg / median return / avg MFE / avg MAE）
5. **明细表**：点击行 → `jumpToCase(symbol)` 跳到 Case Workspace（复用现有 drill-down）

### 4.5 API

```
GET /api/backtest-runs/{run_id}/behavior/episodes
→ { api_version, meta:{resource:"behavior_episodes"}, run_id,
    data:{ episodes:[...], summary:{...} } }
```

未知 run_id → 404（同 /positions、/orders 约定）。

## 5. PR #2 — Swap Decision Analytics

### 5.1 概念

**swap** = 卖出一笔持仓（清仓的 sell）与紧接着买入的新仓（同日首个买入同一 run 的 buy）配对。

- 配对规则（方案 1，已确认）：同一 `trade_date`，按 sequence 排序，`sell[i] ↔ buy[i]` 一一配对。
- 若当日 sell 数 ≠ buy 数：多余部分丢弃并统计 `unpaired_sells` / `unpaired_buys`。

### 5.2 Swap 字段

| 字段 | 说明 |
|------|------|
| `exit_symbol` / `entry_symbol` | 卖出的 A / 买入的 B |
| `swap_date` | trade_date |
| `exit_reason` | 卖出 A 的 trade_reason |
| `swap_edge_20d` / `60d` | `ret(B, +N) − ret(A, +N)`（N 交易日后），B 跑赢 A 则为正 |
| `exit_score` / `entry_score` | A 卖出时 score / B 买入时 score |
| `score_rank_gap` | 当日 A/B 的 score 排名差（选 B 时相对 A 有多靠前） |
| `exit_realized_return` | A 该笔清仓的 realized_return（复用 episode 派生） |

### 5.3 聚合输出

```
swaps: [...]
summary:
  total_swaps, unpaired_sells, unpaired_buys
  avg_swap_edge_20d / 60d
  by_exit_reason: [{exit_reason, count, avg_edge_20d, avg_edge_60d, win_rate_20d}]
```

### 5.4 UI（Swap 面板）

1. **Swap edge 分布直方图**（20d 与 60d 两色叠加）
2. **按 exit_reason 分组表格**（count / avg edge 20d / 60d / win rate）
3. **明细表**：A→B、swap_date、edge、score 信息，点击行跳 Case。

## 6. PR #3 — Exit Rule Counterfactual / Ablation

### 6.1 概念（方案 A — 解析式 counterfactual，已确认）

不重跑引擎。从 executions + prices 做**规则移除重放**：

1. 对每个 `exit_reason == R` 的清仓 sell：假设该 sell 不发生。
2. 该 episode 继续持有：用后续日线 close 折算其继续持有的市值变化；若期间又被其他规则/新事件触发，沿用实际 executions 中该 episode 的真实后续路径（若存在）或延展到数据末尾 / 直到被 sell。
3. 该 sell 配对的 buy 也取消（资金不换仓），相应 episode 现金流相应调整。
4. 汇总所有受影响的 episode，重放一遍组合现金曲线，与 baseline（真实曲线）对比。

### 6.2 校验

- 重放引擎（所有规则都不移除的「全保留」重放）必须先能复现 baseline：与 `daily_summary.csv` 的 equity 曲线逐日对账，容差 < 0.1%。
- 只有通过 baseline 校验，规则移除结果才可信。

### 6.3 输出

```
per_rule: [
  { rule, episodes_affected, baseline_contribution, counterfactual_contribution,
    delta, contribution_per_episode, rule_removed_equity_tail }
]
validation: { baseline_matches: bool, max_abs_dev: float, tolerance: "0.1%" }
```

### 6.4 UI（Ablation 面板）

1. **Baseline vs 各规则移除后的 equity 曲线**叠加图（校验失败则红色告警 + 不展示移除结果）
2. **规则贡献表**：每条规则的贡献、delta、per-episode 贡献
3. 单规则 drill-down：受影响 episode 列表。

## 7. Engineering Notes

- **新模块** `qsys/research_ui/behavior.py`：纯函数派生引擎，与 API/UI 解耦，全部可单测。禁止 import `qsys/backtest/*`（只读自有数据源）。
- **数据源**：
  - executions：`ResearchCockpitRepository._read_canonical_executions`
  - 价格：raw daily store + factor 前复权（复用 `_load_bars_from_raw_store` 逻辑）
  - 信号 score：`data/research/signals/{signal_id}/{signal_run_id}/predictions.parquet`（manifest 中读取 signal_id/signal_run_id）
  - 交易日历：executions/daily_summary 中出现的 trade_date 并集（或基准日历）。
- **错误处理**：未知 run_id → 404；executions 为空 → 空 episodes + `summary.total_episodes=0`（200）；signal 缺失 → entry/exit_score 为 null（不失败）。
- **测试**：单元测试覆盖 episode 生命周期（建仓/加仓/减仓/清仓/重新建仓）、MFE/MAE、score 对齐、open episode、post_exit_return 数据不足、unknown run 404；API 契约测试 mock 装配层。
- **Docs synced**：本设计文档 + `docs/requirements/domains/ui_analysis.md`（UC_UI_ANALYSIS Scope 增补行为诊断行）。

## 8. 明确不做（YAGNI）

- 不重跑 backtest 引擎。
- 不做 Winner/Loser Lifecycle、Alpha/Beta Attribution（延后）。
- 不修改 `qsys/backtest/`、ledger、trader、broker、deploy。
- 不做实时/增量刷新，每次请求实时计算（数据量小，executions+predictions 全量 < 10MB）。
