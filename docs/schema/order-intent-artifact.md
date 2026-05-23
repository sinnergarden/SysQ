# OrderIntentArtifact Schema

## 目的

OrderIntentArtifact 记录从信号到交易计划的转换结果——每个股票的目标权重、期望交易量和调整原因。这是 preopen pipeline 的核心输出，后续被 postclose pipeline 消费以执行实际交易。

## 格式

CSV（推荐）或 JSON。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日，格式 `YYYY-MM-DD` |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 目标账户 ID，如 `shadow_alpha_v1` |
| `instrument` | STRING | 股票代码，如 `600000.SH` |
| `side` | STRING | 买卖方向：`BUY` / `SELL` / `HOLD` |
| `target_weight` | FLOAT | 目标权重（最终归一化后） |
| `current_weight` | FLOAT | 当前权重 |
| `target_quantity` | INTEGER | 目标持仓量 |
| `current_quantity` | INTEGER | 当前持仓量 |
| `delta_quantity` | INTEGER | 需要调整的数量（正=买入，负=卖出，0=持有） |
| `reason` | STRING | 调整原因，如 `rebalance_to_target_weight` / `new_entry` / `exit` |
| `constraints` | JSON | 约束条件 JSON 字符串，如 `{"min_qty": 100, "tick_size": 0.01}` |
| `created_at` | TIMESTAMP | 产物生成时间 |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `limit_price` | FLOAT | 限价单价格（市价单可省略） |
| `order_type` | STRING | 订单类型：`market` / `limit`，默认 `market` |
| `status` | STRING | 订单状态：`pending` / `filled` / `canceled`，默认 `pending` |
| `price` | FLOAT | 参考价格（用于估值） |

## 字段说明

- **side**: `BUY` 表示需要增加持仓，`SELL` 表示需要减少持仓，`HOLD` 表示维持当前持仓不变（delta_quantity=0）。
- **target_weight**: 最终施加 cap 和 renormalize 后的目标权重。
- **delta_quantity**: 实际需要交易的股数。对于 A 股，必须是 100 的整数倍（一手）。
- **reason**: 用于区分不同类型调整：新开仓（new_entry）、再平衡（rebalance_to_target_weight）、清仓（exit）、风控调整（risk_control）。

## 示例

```csv
trade_date,strategy_id,account_id,instrument,side,target_weight,current_weight,target_quantity,current_quantity,delta_quantity,reason,constraints,created_at
2026-05-18,alpha_v1,shadow_alpha_v1,600584.SH,BUY,0.07,0.0,1400,0,1400,rebalance_to_target_weight,"{""min_qty"":100,""tick_size"":0.01}",2026-05-18T08:00:00
2026-05-18,alpha_v1,shadow_alpha_v1,300251.SZ,BUY,0.07,0.0,1500,0,1500,new_entry,"{""min_qty"":100,""tick_size"":0.01}",2026-05-18T08:00:00
```

## 验证规则

- `side` 只能为 `BUY` / `SELL` / `HOLD`。
- `target_weight` 应在 `[0.0, 1.0]` 范围内，所有权重之和应约为 1.0。
- `delta_quantity` 的符号必须与 `side` 一致：BUY → 正数，SELL → 负数，HOLD → 0。
- 对于 A 股，`target_quantity`、`current_quantity`、`delta_quantity` 的绝对值应为 100 的整数倍。
