# Alpha V1 — Baseline Version (Shadow Baseline / Daily Ops Baseline)

## Strategy Identity

| Field | Value |
|-------|-------|
| ID | `qsys_alpha_v1_blend20_weekly_top20_buffer` |
| Status | **Shadow Baseline / Daily Ops Baseline** |
| Universe | CSI 300 / CSI 800 |
| Horizon | Weekly rebalance |
| Max Positions | 20 |
| Initial Cash | ¥1,000,000 (shadow) |

---

## Model Ensemble

### Dual LightGBM

| Component | Horizon | Weight | Label |
|-----------|---------|--------|-------|
| `clean_5d` | 5 trading days | 0.8 | zscore(fwd_5d_return) |
| `clean_20d` | 20 trading days | 0.2 | zscore(fwd_20d_return) |

### Scoring

```
blended_score = 0.8 × zscore(pred_5d) + 0.2 × zscore(pred_20d)
```

- Both models are retrained every week on a **rolling 2-year window**
- Training data: all stocks in the universe, features = 132 clean features
- Labels are cross-sectional zscored within each trading date
- No sector neutralization at the model level

### Features

- **Clean features** (~132): All features except Harmful groups (Fundamental, VolumeAmt, Valuation, Margin, PricePattern)
- Harmful groups removed because they showed negative or unstable IC across the 2022-2026 test period
- Robust zscore normalization: `(x - median) / median_abs_dev`, clipped to [-3, 3]

---

## Portfolio Construction Rules

### Selection

1. Score all stocks in the universe with `blended_score`
2. Hold current positions if their rank ≤ 60 (buffer hold)
3. Fill remaining slots from unheld stocks with rank ≤ 40 (buffer buy)
4. Target portfolio size: **20 stocks**

### Weighting — Rank Linear Decay with Cap

**公式：**
```
选中 N 只 → 降序排列 rank 1..N
原始权重: w_raw(rank_i) = (N - i + 1) / sum(1..N)
                    = (N - i + 1) / (N * (N + 1) / 2)
单股上限: single_stock_cap = 7%
实际权重: w_i = min(w_raw_i, single_stock_cap)
最终权重: w_i_norm = w_i / sum(w_1..w_N)
```

**举例（N=20, sum(1..20)=210, 总资产=¥1,000,000）：**

| Rank | 原始比例 | 原始金额 | Cap后 | 最终比例 | 最终金额 |
|------|---------|---------|-------|---------|---------|
| 1    | 20/210=9.52% | ¥95,238 | Capped 7% | 7.61% | **¥76,087** |
| 2    | 19/210=9.05% | ¥90,476 | Capped 7% | 7.61% | **¥76,087** |
| 3    | 18/210=8.57% | ¥85,714 | Capped 7% | 7.61% | **¥76,087** |
| 4    | 17/210=8.10% | ¥80,952 | Capped 7% | 7.61% | **¥76,087** |
| 5    | 16/210=7.62% | ¥76,190 | Capped 7% | 7.61% | **¥76,087** |
| 6    | 15/210=7.14% | ¥71,429 | Capped 7% | 7.61% | **¥76,087** |
| 7    | 14/210=6.67% | ¥66,667 | — | 7.25% | ¥72,464 |
| 8    | 13/210=6.19% | ¥61,905 | — | 6.73% | ¥67,288 |
| 9    | 12/210=5.71% | ¥57,143 | — | 6.21% | ¥62,112 |
| 10   | 11/210=5.24% | ¥52,381 | — | 5.70% | ¥56,936 |
| ...  | ... | ... | ... | ... |
| 20   | 1/210=0.48%  | ¥4,762  | — | 0.52% | **¥5,176** |

**为什么尾部看起来衰减厉害？**
1. 前 6 名被 7% 硬顶压缩 → 它们的超额权重（约 10.6%）被重新分配给尾部
2. 但 rank 20 的原始占比只有 1/210 = 0.48%，即使 renormalize 也仅到 0.52%
3. 这是线性等间距加权（相邻 rank 之间间隔相同），不是 score 比例加权
4. 如果想拉高尾部：可提高 single_stock_cap、或用 sqrt 衰减（衰减曲线更平滑）

**代码入口：** `qsys/backtest/portfolio.py::build_rank_weight_portfolio()`

### Execution

| Parameter | Value |
|-----------|-------|
| Commission | 0.03% |
| Stamp Duty | 0.1% |
| Slippage | 0.1% |
| Min Commission | ¥5 |
| Execution Price | Open price of rebalance day |
| Limit-up/down | Orders skipped (not cancelled; re-evaluated next bar) |
| Suspension | Orders skipped |

---

## Backtest Performance (2024-01 -- 2026-05, 545 trading days)

### CSI 300

| Metric | Value |
|--------|-------|
| Total Return | +152.04% |
| Annual Return | +53.33% |
| Sharpe | 1.771 |
| Max Drawdown | -16.12% |
| Calmar | 3.309 |
| Annual Turnover | 35.8x |
| Total Fees | ¥1,138,128 |
| Weeks | 118 (win rate 44.1%) |
| Best Week | +14.71% |
| Worst Week | -11.22% |

### CSI 800

| Metric | Value |
|--------|-------|
| Total Return | +257.32% |
| Annual Return | +80.19% |
| Sharpe | 2.207 |
| Max Drawdown | -20.84% |
| Calmar | 3.848 |
| Annual Turnover | 58.9x |
| Total Fees | ¥2,342,017 |
| Weeks | 118 (win rate 54.2%) |
| Best Week | +16.34% |
| Worst Week | -8.44% |

### Signal Quality (CSI 800, test period)

| Metric | Value |
|--------|-------|
| Mean IC | 0.039 |
| Mean RankIC | 0.054 |
| ICIR | 0.305 |
| RankICIR | 0.404 |
| Group 1 NAV (top quintile) | 2.439 |
| Group 5 NAV (bottom quintile) | 1.496 |

*CSI 800 is the primary production universe. CSI 300 is maintained for cross-validation.*

---

## Monitoring Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| 60d Rolling RankIC | < 0.01 | < 0.00 |
| 20d Excess Return | < -5% | — |
| 60d Excess Return | — | < -8% |
| Max Drawdown | < -15% | < -20% |
| Feature Missing Rate | > 5% | — |
| Failed Trade Rate | > 10% | — |

---

## Deployment

### Schedule

| Task | Time | Trigger | Status |
|------|------|---------|--------|
| CSI 800 Data Sync | Mon-Fri 19:00 | `qsys-csi800-daily-sync.timer` | ✅ Active |
| Weekly Train | Mon 07:00 | `qsys-candidate-train.timer` | ✅ Active (dispatches via `run_daily_batch.py --stage candidate --mode train`) |
| Preopen | Mon-Fri 08:00 | `qsys-candidate-preopen.timer` | ✅ Active (dispatches via `run_daily_batch.py --stage candidate --mode preopen --trade-date auto`) |
| Postclose | Mon-Fri 21:00 | `qsys-candidate-postclose.timer` | ✅ Active (dispatches via `run_daily_batch.py --stage candidate --mode postclose --trade-date auto`) |

### Status Dashboard

| Check | Value |
|-------|-------|
| Research UI | `http://localhost:8000` — 2 backtest runs available |
| Last CSI800 Sync | 2026-05-21 (success) |
| Next Sync | Today 21:30 |
| Next Preopen | Tomorrow 08:00 |
| Active Universe | CSI 300 (shadow trading, ¥1M) — models trained on CSI 800 |
| Models | `clean_5d` + `clean_20d` dual LightGBM, retrained weekly |
| 0-cost Curve | Available in UI diagnostics (zero_cost_total_assets) |
| Benchmark | CSI 300 avg price (equal-weighted universe) |

### Shadow Trading Flow

1. **21:30** — CSI 800 data sync + readiness audit → Telegram notification
2. **08:00** — Alpha V1 preopen (`run_alpha_v1_daily.py --mode preopen`):
   - Load dual LightGBM models (from `experiments/alpha_v1_models/latest/`)
   - Fetch CSI300 feature data
   - Score & blend (0.8×zscore(p5) + 0.2×zscore(p20))
   - Run shadow rebalance (`rank_weight_buffer` with `top_n=20`, weekly freq)
   - Save predictions → `experiments/alpha_v1_shadow_predictions/`
   - Send Telegram: top-5 picks + buy/sell plan with amounts and 手数
3. **09:30** — Market open (A-share)
4. **15:00** — Market close
5. **15:30** — Post-close report (`run_alpha_v1_daily.py --mode postclose`):
   - Read reconciliation data (or fallback to shadow execution summary)
   - P&L snapshot + turnover

---

## Current Lifecycle Classification

根据 ADR-006 Strategy Lifecycle 和 ADR-005 Protected Core Boundary，alpha_v1 的当前分类如下：

### 阶段：Shadow Baseline / Daily Ops Baseline

| 维度 | 状态 |
|------|------|
| **Phase** | Shadow（影子/仿真阶段） |
| **Type** | Baseline |
| **Status** | Active — 每日稳定运行 |
| **Start Date** | 2026-05 (Phase 1 daily ops 过渡完成) |
| **Not (Yet)** | Production（生产阶段） |
| **Not** | Research sandbox（自由研究沙盒） |

### 验证职责

alpha_v1 作为 Shadow Baseline 用于验证以下系统组件：

- ✅ Daily pipeline（preopen → postclose）
- ✅ SQLite ledger integration（账户、订单、成交、现金流、持仓、快照）
- ✅ Run archive（运行产物管理）
- ✅ MTM（Mark-to-Market 估值）
- ✅ Order Intent generation（交易意图生成）
- ✅ Reporting（盘前/盘后报告）
- ✅ Stale data protection（陈旧数据保护）
- ✅ Telegram 通知
- ✅ 两阶段提交崩溃安全（COMMITTING / COMMITTED）

### 策略细节（冻结清单）

以下设置是 alpha_v1 Shadow Baseline 的冻结参数，修改需要经过 Protected Core 变更流程：

| 参数 | 当前值 | 保护级别 |
|------|--------|---------|
| Universe | CSI 300 | Baseline（shadow trading） |
| Training Universe | CSI 800 | Baseline |
| Horizon | Weekly | Baseline |
| Model | Dual LightGBM (5d + 20d) | Baseline |
| Blend Weight | 0.8 / 0.2 | Baseline |
| Portfolio | rank_weight_buffer | Baseline |
| Top N | 20 | Baseline |
| Buffer Hold | 60 | Baseline |
| Buffer Buy | 40 | Baseline |
| Single Stock Cap | 7% | Baseline |
| Commission | 0.03% | Baseline |
| Stamp Duty | 0.1% | Baseline |
| Slippage | 0.1% | Baseline |
| Min Commission | ¥5 | Baseline |
| Features | Clean features (~132) | Baseline |

### 未来演进

- **alpha_v1.1 / alpha_v2** 的研究必须先在 `research/` 和 `candidate` 层进行，通过 ADR-006 定义的策略生命周期循序渐进，不能直接修改 daily pipeline 入口。
- **alpha_v1 作为 Shadow Baseline** 会继续运行，直到有候选策略通过 Shadow 验证并正式接替其地位。
- 将 alpha_v1 提升到 Production 需要满足 ADR-006 中定义的 Production promotion 条件（broker read-only reconciliation、kill switch、manual confirmation 等）。

---

## Risk Notes

- **Single stock 7% cap** prevents concentrated blowup but may cause ~0.5% tracking error vs strict rank-weight
- **Buffer rules** reduce turnover in non-trending markets; in strong trend regimes the full 20 may lag by ~1 day
- **No short selling** — the strategy is long-only; the zero-cost equity curve is for diagnostics only
- **The 20d model** contribution is regime-dependent; it adds ~0.5% to annual return in normal markets but absorbs volatility
