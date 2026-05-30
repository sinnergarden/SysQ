# DAILY_OPS_SOP

本文档是 daily ops 的运行手册，不是架构文档。
系统设计、模块职责和过渡态见 `docs/ARCHITECTURE.md`，数据接口契约见 `docs/CONTRACTS.md`。

---

## Purpose

定义 daily ops 从数据同步到盘后归档的完整流程、检查点和故障处理。

## Current Transition Note

当前 systemd 仍可能走 legacy entry（`run_preopen.sh` → `run_daily_trading.py` + `run_alpha_v1_daily.py`）。目标入口是 `run_daily.py` / `run_daily_batch.py`。本文档同时描述两套路径的行为，但不得把目标态写成当前事实。

---

## Daily Timeline

```
数据同步（开市前/盘后）
  → preopen（开市前）
  → execution（盘中）
  → postclose（盘后）
  → report / notification
```

---

## Preopen Checklist

### 1. Data Readiness

- 检查 latest raw date 和 latest qlib date 是否已到 T-1。
- 检查 missing rate 是否在可接受范围。
- 如果数据不满足 readiness（blocked），不得继续生成 plan。

### 2. Model / Manifest Check

- 确认 production manifest 已批准且版本正确。
- 检查模型新鲜度——模型是否过时，是否需要触发 refresh。

### 3. Previous Account State

- 从 ledger 读取上一交易日收盘后账户状态。
- 如 ledger 不可用，检查 legacy path 作为 fallback。

### 4. Expected Outputs

- signal（`daily/{date}/pre_open/signals/`）
- plan（`daily/{date}/pre_open/plans/`）
- order intents（`daily/{date}/pre_open/order_intents/`）
- manifest（`daily/{date}/pre_open/manifests/`）

---

## Postclose Checklist

### 1. Execution Input

- 确认 order intents 已就绪。
- 确认执行路径（shadow simulation / broker bridge）。

### 2. Fills

- 获取实际或模拟成交记录。
- 记录 fill_id、fill_qty、fill_price、fee。

### 3. MTM

- 以当日收盘价重新估值持仓。
- 计算当日收益（逐日盯市）。

### 4. Reconciliation

- 内部 ledger 与 broker snapshot 对比（如有 broker）。
- 输出 position gap 和 cash gap。

### 5. Ledger / Evidence

- 写入 ledger（`data/trade.db`）。
- 归档 daily evidence（`daily/{date}/post_close/`）。

### 6. Report

- 生成 daily ops digest（JSON + Markdown）。
- 发送 Telegram 通知。

---

## Outputs

| 路径 | 内容 |
|------|------|
| `daily/{date}/pre_open/` | 盘前信号、计划、订单意图、清单 |
| `daily/{date}/post_close/` | 盘后成交、对账、快照、报告 |
| `data/trade.db` | 账户状态 ledger（目标 SOT） |
| Telegram | 每日通知摘要 |

---

## Failure Handling

| 场景 | 处理方式 |
|------|---------|
| 数据 blocked | 阻断 preopen，发通知，不生成 plan |
| 空 plan | preopen 失败，跳过当天交易，留空 evidence |
| 模型过时 | preopen 降级运行，写入报告，发 stale model 告警 |
| 缺失价格 | postclose 跳过对应标的 MTM，记录缺失 |
| 账户状态不可用 | 尝试 legacy fallback，失败则阻断流程 |
| reconciliation gap | 记录 gap，发告警，不自动修正 |
| 通知失败 | 日志记录，不影响主流程 |
| 存量三态不一致 | 以 ledger 为准，记录差异，不自动迁移 |

---

## UI / Monitoring

Ops UI 应只读展示：

- data readiness（ready / degraded / blocked）
- latest data date
- daily plan
- order intents
- shadow / production status
- ledger snapshot
- postclose report
- reconciliation gap
- blocking reason

UI 不写 ledger，不下单，不改策略。

---

## Do Not

- 不无人值守下单。
- 不直接编辑 ledger。
- 不删除 legacy state（`data/meta/real_account.db`、`shadow/`）。
- 不绕过 artifact contract。
- 不把旧入口当作长期接口扩张新依赖。
