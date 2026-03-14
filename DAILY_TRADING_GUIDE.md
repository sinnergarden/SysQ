# SysQ 每日交易指南

本指南介绍了如何使用 `SysQ` 框架运行每日交易工作流。该工作流支持 **影子交易**（模拟）、**实盘交易**（券商同步）和 **自动模型维护**（滚动重训练）。

## 前置条件

1.  **Qlib 数据**：确保 Qlib 数据已初始化。
2.  **模型**：您至少需要一个初始模型。系统可以自动对其进行重训练。
3.  **环境**：确保 `sysq` 环境已激活。

## 工作流概览

脚本 `scripts/run_daily_trading.py` 执行以下步骤：
1.  **更新数据**：获取最新的市场数据（通过 `QlibAdapter`）。
2.  **模型检查**：
    - 检查最新模型是否过期（默认 > 7 天）。
    - 如果过期，使用最新数据自动 **重训练** 一个新模型（滚动窗口）。
3.  **影子模拟**：
    - 在今日市场上模拟昨日计划的执行。
    - 更新影子账户状态。
4.  **实盘账户同步**（可选）：
    - 同步外部券商状态（如果提供了 CSV）。
5.  **生成计划**：
    - 预测明日得分。
    - 生成可执行的 **买入/卖出指令**。
    - 将计划保存到 `data/plan_{date}_{account_name}.csv`。

## 使用方法

### 1. 基本用法（每日例行）

在每日收盘后（例如 17:00）运行此命令。

```bash
python scripts/run_daily_trading.py
```

- **自动重训练**：如果模型超过 7 天未更新，它将自动重训练。
- **输出**：在控制台打印交易摘要（例如 "BUY 100 shares of AAPL"）。

### 2. 配置选项

- **指定日期**：`--date 2026-02-01`
- **重训练频率**：`--retrain_days 14`（每 2 周重训练一次，而不是 1 周）。
- **模型路径**：`--model_path data/models/my_special_model`（覆盖自动检测）。
- **影子现金**：`--shadow_cash 500000`（设置初始模拟现金）。
- **策略持仓数**：`--top_k 30`（持仓股票数量，默认 30）。
- **最小交易额**：`--min_trade 5000`（最小交易金额人民币）。

```bash
python scripts/run_daily_trading.py --retrain_days 5 --shadow_cash 1000000
```

### 3. 小资金测试（例如 2万人民币）

对于小账户，减少 `top_k` 和 `min_trade` 以确保能生成交易。

```bash
python scripts/run_daily_trading.py --shadow_cash 20000 --top_k 2 --min_trade 2000
```

### 4. 实盘模式

要同步您的真实券商账户，请提供包含当前持仓的 CSV 文件。

```bash
python scripts/run_daily_trading.py --real_sync path/to/broker_export.csv
```

### 5. 跳过数据更新

如果您已经手动更新了数据：

```bash
python scripts/run_daily_trading.py --skip_update
```

## 输出示例

**控制台输出：**
```text
=== Trading Plan for shadow ===
SELL 200 shares of SH600000 @ 10.50
BUY 100 shares of SZ000001 @ 12.30
Total Trades: 2
```

**文件：**
- `data/plan_2026-03-01_shadow.csv`：详细订单列表。
- `data/models/qlib_lgbm_20260301_170000/`：新模型产物（如果进行了重训练）。

## 自动化 (Crontab)

要在每个工作日 18:00 自动运行：

```bash
0 18 * * 1-5 cd /path/to/SysQ && /path/to/python scripts/run_daily_trading.py >> logs/daily.log 2>&1
```

## 故障排除

- **重训练失败**：检查日志中的训练错误。确保有足够的内存。
- **未生成计划**：检查 Qlib 数据是否已更新到目标日期。
- **未找到模型**：确保 `data/models` 或 `data/experiments` 中至少有一个模型。
- **幂等性**：在同一天多次运行脚本是安全的。它会复用已计算的状态。
