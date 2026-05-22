# RUNBOOK

## 1. 先记住这五条

- `data/raw/` 和 `data/qlib_bin/` 是长期数据层，不放 daily evidence。
- `daily/{date}/` 是单日证据包，盘前和盘后只写到当天目录。
- 长期账户主库固定为 `data/meta/real_account.db`。
- `data/derived/` 只沉淀稳定结构化字段，不替代原始 daily evidence。
- 研究、训练、回测输出统一放 `experiments/`，结构化 JSON 报告默认在 `experiments/reports/`。

## 2. 日常操作顺序

0. **CSI800 数据同步**（自动）：每日 21:30 由 systemd timer 触发 `sync_csi800_daily.py --apply`，拉取最新日线数据 → 转换 Qlib → 刷新工具文件 → readiness 检查 → 写 audit → Telegram 通知。同步完成后会收到 channel 通知，包含状态摘要和检查结果。
1. 先做数据健康检查（sync 的 readiness check 已覆盖）。
2. 跑盘前：生成 signal basket、plan、order intents、report、manifest。
3. 盘中执行订单，不复制账户主库到 `daily/`。
4. 跑盘后：回写 snapshot、做 reconciliation、写报告与 manifest。
5. 如需跨日分析，再执行 rollup 到 `data/derived/`。

## 3. 当前 daily 目录

盘前只保留：

- `daily/{execution_date}/pre_open/plans`
- `daily/{execution_date}/pre_open/order_intents`
- `daily/{execution_date}/pre_open/signals`
- `daily/{execution_date}/pre_open/reports`
- `daily/{execution_date}/pre_open/manifests`

盘后只保留：

- `daily/{execution_date}/post_close/reconciliation`
- `daily/{execution_date}/post_close/snapshots`
- `daily/{execution_date}/post_close/reports`
- `daily/{execution_date}/post_close/manifests`

目录细节见 `docs/DATA_LAYOUT.md`；执行步骤见各 SOP。

## 4. 入口命令

### 主线（Mainline）运营

盘前：

```bash
python3 scripts/run_daily_trading.py --date 2026-04-02
```

盘后：

```bash
python3 scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

单独检查信号质量：

```bash
python3 scripts/run_signal_quality.py --date 2026-04-03
```

rollup：

```bash
python3 scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```

### Alpha V1 每日运营

```bash
# 盘前：inference → 生成交易计划 → Telegram 通知
python3 scripts/run_alpha_v1_daily.py --trade-date 2026-05-18 --mode preopen

# 盘后：开盘价执行 → 收盘价 MTM → Telegram 通知
python3 scripts/run_alpha_v1_daily.py --trade-date 2026-05-18 --mode postclose

# 周级别训练（周末运行）
python3 scripts/run_alpha_v1_daily.py --trade-date 2026-05-23 --mode train
```

**调试选项**：

| 选项 | 作用 |
|------|------|
| `--debug-run` | 不修改 shadow 账户，输出到独立目录 |
| `--no-notify` | 跳过 Telegram 通知 |
| `--notify-only` | 仅从已有产物重建通知，不执行 |
| `--force-rerun --reason "原因"` | 覆盖已有执行记录，强制重跑 |
| `--output-dir PATH` | 调试模式下输出目录覆盖 |

## 5. 导航

- 数据链路 SOP（CSI800 日频同步全流程）：`docs/ops/DATA_PIPELINE_SOP.md`
- CSI800 日频同步脚本：`scripts/ops/sync_csi800_daily.py`（systemd: `qsys-csi800-daily-sync.{service,timer}`）
- 盘前：`docs/ops/PRE_OPEN_SOP.md`
- 盘后：`docs/ops/POST_CLOSE_SOP.md`
- 目录契约：`docs/DATA_LAYOUT.md`

## 6. Alpha V1 详解

### 产物结构

```
experiments/alpha_v1_daily/{trade_date}/
├── run_meta.json          # 运行记录（mode、reference_date、debug_run、reason）
├── plan/
│   ├── plan_meta.json     # 计划元信息（status: built/skipped、reference_date）
│   ├── target_weights.csv # 目标权重
│   ├── order_intents.csv  # 订单意图（instrument、side、diff_value、requested_qty）
│   └── rebalance_audit.csv# 调仓审计日志
├── execution/
│   ├── COMMITTED          # 标记文件，表示本次执行已完成（幂等屏障）
│   ├── account_after.json # 执行后账户状态
│   ├── positions_after.csv# 执行后持仓
│   └── execution_summary.json  # 执行摘要
├── mtm/
│   ├── mtm_snapshot.json  # MTM 快照（收盘价估值）
│   └── stale_check.json   # 数据陈旧检查结果
├── archive/               # --force-rerun 时旧产物存档
└── staging/               # 执行暂存区，commit 后才写入 execution/
```

### 幂等（Idempotency）

- **盘后执行（postclose）是幂等的**：一旦 `execution/COMMITTED` 标记存在，再次运行不会重复执行，只重新计算 MTM + 发通知
- **如需覆盖**：`--force-rerun --reason "原因"` 会存档旧 execution/ 并重新执行
- 注意：幂等 ≠ 不执行。第一次运行正常执行，第二次起跳过（执行记录已存在）

### 调试指南

| 场景 | 做法 |
|------|------|
| 只改通知文案，验证效果 | `--notify-only`（不执行，从已有产物重建通知）|
| 测试完整流程但不改 shadow | `--debug-run`（不写 account.json/positions.csv/ledger.csv）|
| 某天执行失败要重跑 | `--force-rerun --reason "修复了XXbug"` |
| 本地开发不想刷屏 | `--no-notify` |

### 执行流程（postclose 事务性）

```
staging/ → 校验（开盘价、COMMITTED） → MatchEngine 执行 → commit → shadow/
```

commit 操作是原子的：
1. 复制 staging 产物到 execution/
2. 更新 shadow/account.json 和 positions.csv
3. 追加 ledger.csv
4. 创建 COMMITTED 标记

### 数据陈旧保护

- postclose 时检查收盘价：与前一天 MTM 快照对比
- 如果 >85% 的股票收盘价与前一日完全相同 → 阻塞执行（sys.exit 1）
- 检查结果写入 `mtm/stale_check.json`

### 布署

- systemd timer 每日自动执行（盘前 21:30 数据同步后触发）
- Telegram 通知发送到指定 channel（通过 `.env` 配置）

## 7. 排障顺序

- 查单日问题：先看 `daily/{date}/...`
- 查跨日趋势：再看 `data/derived/`
- 查研究结果：看 `experiments/`
- 查账户主状态：看 `data/meta/real_account.db`
