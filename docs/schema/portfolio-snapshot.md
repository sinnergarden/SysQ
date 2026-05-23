# PortfolioSnapshot Schema

## 目的

PortfolioSnapshot 记录特定时间点的组合快照，用于日内/日度 MTM 估值和 PnL 归因。这是 daily ops 中"盘后复盘"的核心数据，用于评估策略执行效果。

## 格式

JSON（推荐结构化输出）或 CSV。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日，格式 `YYYY-MM-DD` |
| `account_id` | STRING | 账户标识 |
| `strategy_id` | STRING | 策略标识 |
| `cash` | FLOAT | 现金余额（从 cash_ledger SUM 计算） |
| `market_value` | FLOAT | 持仓总市值 |
| `total_asset` | FLOAT | 总资产 = cash + market_value |
| `daily_pnl` | FLOAT | 当日盈亏 |
| `daily_return` | FLOAT | 当日收益率（百分比，如 `0.65` 表示 +0.65%） |
| `position_count` | INTEGER | 持仓股票数 |
| `turnover` | FLOAT | 当日换手率 |
| `created_at` | TIMESTAMP | 快照生成时间 |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `initial_capital` | FLOAT | 初始资金，用于计算累计收益 |
| `cumulative_pnl` | FLOAT | 累计盈亏 |
| `cumulative_pnl_pct` | FLOAT | 累计收益率（百分比） |
| `details` | ARRAY[JSON] | 持仓明细列表 |
| `daily_pnl_pct` | FLOAT | 当日收益率（当 `daily_return` 已存在时可省略） |

## details 字段结构（可选）

每项包含：
- `instrument` (STRING): 股票代码
- `name` (STRING): 股票名称
- `quantity` (INTEGER): 持仓数量
- `cost_price` (FLOAT): 成本均价
- `last_price` (FLOAT): 最新价/收盘价
- `unrealized_pnl` (FLOAT): 浮动盈亏

## 示例

```json
{
  "trade_date": "2026-05-19",
  "account_id": "shadow_alpha_v1",
  "strategy_id": "alpha_v1",
  "cash": 85626.34,
  "market_value": 920894.00,
  "total_asset": 1006520.34,
  "daily_pnl": 9970.00,
  "daily_return": 0.65,
  "position_count": 20,
  "turnover": 0.0,
  "initial_capital": 1000000.0,
  "cumulative_pnl": 6520.34,
  "cumulative_pnl_pct": 0.65,
  "details": [
    {"instrument": "600584.SH", "name": "长电科技", "quantity": 1400, "cost_price": 49.50, "last_price": 51.20, "unrealized_pnl": 2380.0}
  ],
  "created_at": "2026-05-19T15:30:00"
}
```

## 验证规则

- `total_asset` 应等于 `cash + market_value`（允许浮点误差）。
- `daily_pnl` = `total_asset(today) - total_asset(last_trading_day)`。
- `position_count` 应等于 `details` 的长度（如果提供了 details）。
- `turnover` >= 0。
