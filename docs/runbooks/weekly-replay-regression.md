# Weekly Replay Regression Runbook

> 在 PR 合并前验证变更不会改变交易语义。通过重放 2026-05-18 ~ 2026-05-22 一周的 daily pipeline，比较输出产物是否与 main 分支完全一致。

## Prerequisites

- 代码已 checkout 到待验证分支
- `experiments/` 目录有写入权限
- Qlib 数据已更新（`python scripts/ops/run_raw_full_update.py`）
- Python 环境可用（`conda activate py311` 或等效）
- Redis 可访问（用于 qlib cache）

## 初始化（仅首次）

```bash
# 创建影子账户（如果 ledger 是空的）
python -c "
from qsys.ledger.service import LedgerService
db = 'data/trade.db'
svc = LedgerService(db)
svc.create_account('shadow_alpha_v1', initial_cash=1_000_000.0)
svc.close()
"
```

## 重放命令

### 第1步：训练（5月16日周六）

```bash
python scripts/run_alpha_v1_daily.py --mode train --trade-date 2026-05-16
```

**检查**：
- Telegram 通知包含 RankIC、特征重要性
- `experiments/alpha_v1_models/latest/` 下有模型文件

### 第2步：每日 Preopen + Postclose（5.18 ~ 5.22）

```bash
# 周一
python scripts/run_alpha_v1_daily.py --mode preopen --trade-date 2026-05-18
python scripts/run_alpha_v1_daily.py --mode postclose --trade-date 2026-05-18

# 周二
python scripts/run_alpha_v1_daily.py --mode preopen --trade-date 2026-05-19
python scripts/run_alpha_v1_daily.py --mode postclose --trade-date 2026-05-19

# 周三
python scripts/run_alpha_v1_daily.py --mode preopen --trade-date 2026-05-20
python scripts/run_alpha_v1_daily.py --mode postclose --trade-date 2026-05-20

# 周四
python scripts/run_alpha_v1_daily.py --mode preopen --trade-date 2026-05-21
python scripts/run_alpha_v1_daily.py --mode postclose --trade-date 2026-05-21

# 周五
python scripts/run_alpha_v1_daily.py --mode preopen --trade-date 2026-05-22
python scripts/run_alpha_v1_daily.py --mode postclose --trade-date 2026-05-22
```

### 第3步：导出结果

```bash
python scripts/export_ledger_state.py --account shadow_alpha_v1
```

## 对比的内容（PR branch vs main）

### 交易信号

| 文件 | 对比内容 | 容差 |
|------|---------|------|
| `experiments/alpha_v1_shadow_predictions/predictions_{date}.csv` | 每只股票的 score | 1e-6 |
| `experiments/alpha_v1_shadow_predictions/predictions_{date}.csv` | rank 排序 | 严格一致 |

### 交易计划

| 文件 | 对比内容 | 容差 |
|------|---------|------|
| `plan/order_intents.csv` | 每笔订单的 side, requested_qty, target_value | 严格一致 |
| `plan/target_weights.csv` | 每只股票的 target_weight | 1e-6 |
| `plan/rebalance_audit.csv` | buy/sell/in_plan/hold 分类 | 严格一致 |

### 执行结果

| 文件 | 对比内容 | 容差 |
|------|---------|------|
| `execution/ledger_rows.csv` | 成交数量、价格、金额 | 1e-4 |
| `execution/positions_after.csv` | 持仓数量、市值 | 1e-4 |

### Ledger 状态

| 查询 | 对比内容 | 容差 |
|------|---------|------|
| `SELECT cash, total_asset FROM accounts` | 现金、总资产 | 0.01 |
| `SELECT daily_pnl, total_pnl FROM portfolio_snapshots` | PnL | 0.01 |
| `SELECT status FROM strategy_runs WHERE run_id LIKE '%...%'` | 运行状态 | 严格一致 |
| COMMITTING / COMMITTED marker 文件 | 存在性 | 严格一致 |

### Telegram 通知

Telegram 消息内容不在对比范围内（时间戳、run_id 等会变化）。

### 对比工具

```bash
python scripts/dev/compare_weekly_replay.py \
    --baseline /path/to/main/experiments \
    --candidate /path/to/branch/experiments \
    --trade-dates 2026-05-18,2026-05-19,2026-05-20,2026-05-21,2026-05-22
```

## Pass / Fail 判定

| 条件 | 判定 |
|------|------|
| 所有 CSVs 在容差内匹配 | ✅ Pass |
| 至少一个字段超出容差 | ❌ Fail — 语义被改变 |
| 缺少某日文件 | ❌ Fail — pipeline 异常 |
| Telegram 消息格式/内容差异 | ⚠️ 人工确认（非阻塞） |

## 注意事项

1. **存量数据清除**：每次在 main 和 PR branch 之间切换时，删除 `data/trade.db` 并重新初始化账户，确保起始状态一致。
   ```bash
   rm -f data/trade.db
   python -c "
   from qsys.ledger.service import LedgerService
   LedgerService('data/trade.db').create_account('shadow_alpha_v1', initial_cash=1_000_000.0)
   "
   ```

2. **模型版本**：两个分支必须使用同一模型（`experiments/alpha_v1_models/latest/`），避免预测差异干扰验证。

3. **数据版本**：确保两个分支使用同一数据版本（`python scripts/ops/run_raw_full_update.py` 在同一点运行）。
