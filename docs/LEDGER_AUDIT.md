# Ledger Audit — 2026-06-14

本文件记录 QSys 当前 ledger 实现状态、daily pipeline 接入情况、以及剩余 gap。

---

## 1. SQLite Ledger 实现状态

**代码**：`qsys/ledger/service.py` — `LedgerService`，~450 行，单入口 API。

| 能力 | 状态 |
|------|------|
| `apply_fills()` 原子事务 | ✅ 已实现（单 SQLite 事务：validate→insert→cash_ledger→position_ledger→positions） |
| `start_run()` / `finish_run()` | ✅ 已实现，含 `force=True` 重开已有 run_id |
| `record_orders()` | ✅ 已实现，支持 `idempotent=True` 跳过重复 order_id |
| `create_portfolio_snapshot()` | ✅ 已实现 |
| `roll_available_positions()` | ✅ T+1 简化实现 |
| `DuplicateRunError` / `DuplicateFillError` | ✅ 已实现 |
| `InsufficientCashError` / `InsufficientPositionError` | ✅ 已实现 |

**数据库文件：`data/trade.db`**（262 KB，最后修改 2026-06-11）

| 表 | 行数 | 说明 |
|----|------|------|
| `accounts` | 2 | shadow_alpha_v1 等 |
| `strategy_runs` | 10 | 每次 postclose 一条 run_id |
| `fills` | 92 | 每笔成交 |
| `orders` | 92 | 每笔订单 |
| `cash_ledger` | 94 | 现金事件（逐行 rebuild） |
| `position_ledger` | 92 | 持仓事件 |
| `positions` | 51 | 当前持仓（upserted） |
| `portfolio_snapshots` | 10 | 每日组合快照 |

## 2. Daily Pipeline 接入

**路径**：`scripts/run_daily.py` → `DailyRunner.run_postclose()` → `strategy.execute_plan()` → `strategy.commit_execution()` → `write_execution_to_ledger()` → `LedgerService.*`

详情：
1. `shadow_execution.py` 在 `commit_execution_artifacts()` 中调用：
   - `LedgerService.start_run()` — 创建 strategy_run
   - `LedgerService.record_orders()` — 记录订单
   - `LedgerService.roll_available_positions()` — T+1 结算
   - `LedgerService.apply_fills()` — 原子写入成交+cash+position
   - `LedgerService.create_portfolio_snapshot()` — 组合快照
   - `LedgerService.finish_run()` — 标记完成
2. `qsys/ops/commit_guard.py` 提供 COMMITTING/COMMITTED marker，防止 crash 后重复提交
3. `debug_run` 模式下跳过 ledger 写入
4. preopen 不写入 ledger（ledger_commit_status = not_applicable）

## 3. 双重事实来源问题

当前有两个并行的 source-of-truth：

| 来源 | 角色 | 写入时间 |
|------|------|----------|
| `data/trade.db` | 目标 SOT — 完整 ledger | 每次非 debug postclose |
| `shadow/account.json` + `positions.csv` | 遗留 SOT | 每次非 debug postclose（与 ledger 并行写入） |

`shadow_execution.py` 在 `write_execution_to_ledger()` 之后，还写：
- `shadow/account.json` — 账户快照
- `shadow/positions.csv` — 持仓快照

这意味着 ledger 和 CSV 可能不一致。`daily_runner.py` 的 `_collect_position_instruments()` 和 MTM 同时读两个来源。

**后续**：应迁移 `shadow/` CSV 读取方到 LedgerService 只读接口，并移除对 CSV 的写入。不在本 PR 做。

## 4. 未实现

- **Correction/Reversal**：如果 `apply_fills()` 已经提交，没有 "回滚前一天" 的 reversal 工作流。需要通过 `force=True` + `DuplicateFillError(idempotent=True)` 绕过。
- **Production ledger**：仅 shadow 模式。production 模式由 `--run-mode production` hard block 阻止。
- **real_account.db 写入**：`data/meta/real_account.db` 由 broker 网关写入，daily pipeline 不写。

## 5. Manifest 字段约定

本 PR 新增的 manifest ledger 字段取值规则：

| stage | debug_run | 已 COMMITTED | 实际执行 | ledger_commit_status |
|-------|-----------|-------------|----------|---------------------|
| preopen | 任意 | - | - | not_applicable |
| postclose | true | - | - | not_applicable |
| postclose | false | true | skip | committed |
| postclose | false | false | rerun | pending |
| postclose | false | false | success | committed |
| postclose | false | false | failed | failed |
