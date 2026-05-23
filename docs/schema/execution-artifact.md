# ExecutionArtifact Schema

## 目的

ExecutionArtifact 记录实际模拟或真实执行结果——每个订单的成交明细。这是 postclose pipeline 的核心输出，用于更新账户状态和生成报告。

## 格式

CSV（推荐）或 JSON。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日，格式 `YYYY-MM-DD` |
| `run_id` | STRING | 运行标识，如 `2026-05-18.alpha_v1.shadow` |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 账户标识 |
| `order_id` | STRING | 订单 ID，如 `ord_20260518_600584_SH_BUY_0` |
| `fill_id` | STRING | 成交 ID，如 `fil_20260518_600584_SH_BUY_0` |
| `instrument` | STRING | 股票代码 |
| `side` | STRING | 买卖方向：`BUY` / `SELL` |
| `quantity` | INTEGER | 成交数量 |
| `price` | FLOAT | 成交价格 |
| `commission` | FLOAT | 手续费，如 `0.0003 * gross_amount` |
| `stamp_tax` | FLOAT | 印花税，A 股卖出时 `0.001 * gross_amount` |
| `slippage` | FLOAT | 滑点成本，如 `0.001 * gross_amount` |
| `status` | STRING | 成交状态：`filled` / `partial` / `pending` / `canceled` |
| `reason` | STRING | 执行说明，如 `executed_at_open` / `limit_up_skipped` |
| `created_at` | TIMESTAMP | 产物生成时间 |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `gross_amount` | FLOAT | 成交总金额 = `quantity * price` |
| `net_amount` | FLOAT | 净金额 = `gross_amount - commission - stamp_tax - slippage` |
| `source` | STRING | 数据来源，如 `simulation` / `broker` |

## 字段说明

- **status**: `filled` 表示完全成交，`partial` 表示部分成交，`pending` 表示未成交，`canceled` 表示已撤销。
- **commission**: 对于模拟执行，按配置的费率计算。对于 broker 执行，从券商回传数据读取。
- **stamp_tax**: A 股仅卖出时收取，当前为成交金额的 0.1%。
- **slippage**: 模拟执行中用于估计实际成交价格与目标价格之间的差异。

## 示例

```csv
trade_date,run_id,strategy_id,account_id,order_id,fill_id,instrument,side,quantity,price,commission,stamp_tax,slippage,status,reason,created_at
2026-05-18,2026-05-18.alpha_v1.shadow,alpha_v1,shadow_alpha_v1,ord_20260518_600584_SH_BUY_0,fil_20260518_600584_SH_BUY_0,600584.SH,BUY,1400,49.50,20.79,0.0,6.93,filled,executed_at_open,2026-05-18T09:30:00
2026-05-18,2026-05-18.alpha_v1.shadow,alpha_v1,shadow_alpha_v1,ord_20260518_300251_SZ_BUY_1,fil_20260518_300251_SZ_BUY_1,300251.SZ,BUY,1500,34.20,15.39,0.0,5.13,filled,executed_at_open,2026-05-18T09:30:00
```

## 验证规则

- `side` 只能为 `BUY` 或 `SELL`。
- `quantity` 必须为正整数。
- `price` 必须大于 0。
- `commission`、`stamp_tax`、`slippage` 应 >= 0。
- A 股 `stamp_tax` 仅在 `side=SELL` 时可能 > 0。
- `order_id` 和 `fill_id` 应在运行内唯一。
