# MiniQMT Server

`miniqmt_server/` 是一个放在 SysQ 根目录下、独立于 `qsys/` 的 Windows MiniQMT 执行服务骨架。

当前第一版目标很克制：

- 先把 SysQ 可稳定调用的 HTTP 契约立起来
- 先把本地 mock 跑通，保证没有真实 MiniQMT 环境时也能开发、演示、测试
- 明确真实 Windows MiniQMT adapter 的边界，但不假装已经接线完成

## 服务职责

这个服务位于 SysQ daily ops 和 Windows MiniQMT 之间，负责：

- 接收 SysQ 产出的 `order_intents`
- 做最小静态校验和幂等保护
- 在 mock 模式下提供账户、持仓、委托、成交、快照查询
- 在未来的 `miniqmt` 模式下承接真实 MiniQMT 登录、查询、下单、撤单和回报归档

当前首版已提供这些接口：

- `GET /health`
- `GET /account`
- `GET /positions`
- `GET /orders`
- `GET /trades`
- `POST /orders/validate`
- `POST /orders/submit`
- `POST /orders/cancel`
- `GET /snapshots/latest`

## 目录结构

```text
miniqmt_server/
  __init__.py
  app.py
  config.py
  config.example.yaml
  models.py
  storage.py
  broker/
    base.py
    mock.py
    miniqmt.py
  data/
  README.md
```

关键说明：

- `app.py`：标准库 `http.server` 实现的最小 HTTP 服务
- `broker/mock.py`：本地可运行的 mock broker，首版开发和测试默认使用它
- `broker/miniqmt.py`：真实 MiniQMT adapter shell，当前只定义边界并显式保留 `NotImplementedError`
- `storage.py`：使用 `jsonl/json` 的本地审计存储，不引入 ORM

## 启动方式

推荐从仓库根目录启动：

```bash
python -m miniqmt_server.app --config miniqmt_server/config.example.yaml
```

如果只想临时换端口：

```bash
python -m miniqmt_server.app --config miniqmt_server/config.example.yaml --port 8812
```

启动后本地访问：

```bash
curl http://127.0.0.1:8811/health
```

## 配置说明

示例配置见 `miniqmt_server/config.example.yaml`。

核心字段：

- `server.host` / `server.port`：服务监听地址
- `server.data_dir`：本地存储目录，默认写到 `miniqmt_server/data`
- `broker.mode`：`mock` 或 `miniqmt`
- `broker.mock.allow_submit`：mock 是否允许正式提交
- `broker.mock.auto_fill`：是否在 mock 提交后直接生成成交
- `broker.mock.account`：mock 账户摘要初始值
- `broker.mock.positions`：mock 初始持仓

注意：

- 当前真实可运行的是 `broker.mode=mock`
- `broker.mode=miniqmt` 只提供 adapter shell，用于后续 Windows 真机接线

## API 示例

### 1. 健康检查

```bash
curl http://127.0.0.1:8811/health
```

### 2. 校验 order intents

```bash
curl -X POST http://127.0.0.1:8811/orders/validate \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "2026-04-06-strategyA-open-001",
    "strategy_id": "strategyA",
    "trade_date": "2026-04-06",
    "account_id": "mock_account",
    "dry_run": true,
    "orders": [
      {
        "intent_id": "intent-0001",
        "symbol": "600000.SH",
        "side": "BUY",
        "quantity": 1000,
        "order_type": "LIMIT",
        "limit_price": 12.34,
        "time_in_force": "DAY",
        "reason": "rebalance_to_target",
        "target_weight": 0.05,
        "notes": "from preopen plan"
      }
    ]
  }'
```

### 3. 正式提交 mock 订单

```bash
curl -X POST http://127.0.0.1:8811/orders/submit \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "2026-04-06-strategyA-open-002",
    "strategy_id": "strategyA",
    "trade_date": "2026-04-06",
    "account_id": "mock_account",
    "dry_run": false,
    "orders": [
      {
        "intent_id": "intent-0002",
        "symbol": "600000.SH",
        "side": "BUY",
        "quantity": 1000,
        "order_type": "LIMIT",
        "limit_price": 12.34,
        "time_in_force": "DAY",
        "reason": "rebalance_to_target",
        "target_weight": 0.05,
        "notes": "submit to mock broker"
      }
    ]
  }'
```

### 4. 撤单

```bash
curl -X POST http://127.0.0.1:8811/orders/cancel \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "cancel-001",
    "account_id": "mock_account",
    "broker_order_ids": ["mock-order-replace-me"],
    "reason": "manual review"
  }'
```

### 5. 查询快照

```bash
curl http://127.0.0.1:8811/snapshots/latest
```

## 最小 Python 调用示例

也可以直接运行示例脚本：

```bash
python scripts/call_miniqmt_server_mock.py --base-url http://127.0.0.1:8811
```

该脚本会向 mock server 发送一个最小 `order_intents` payload，并打印 validate / submit 的返回值。

## 本地 mock 的行为

当前 mock broker 能力：

- 返回固定但可配置的健康状态
- 返回账户资金摘要和持仓
- 接受 validate / submit / cancel
- 在 validate / submit 前做最小资金和可卖数量检查
- 将订单、成交、快照写入 `miniqmt_server/data/`
- `dry_run=true` 时只返回模拟结果，不生成真实 mock broker order
- 对重复 `request_id` 做最小幂等保护，避免重复提交

当前 mock 风控规则：

- BUY 会按 `limit_price` 或已有持仓 `market_price/cost_price` 估算占用现金
- SELL 不能超过当前 `available_volume`
- 同一批请求中的 BUY 会按顺序累积占用 `available_cash`
- 出于保守性，同一批请求中的 SELL 不会反向释放现金给后续 BUY 使用

本地存储文件：

- `miniqmt_server/data/orders.jsonl`
- `miniqmt_server/data/trades.jsonl`
- `miniqmt_server/data/snapshots.jsonl`
- `miniqmt_server/data/latest_snapshot.json`

## 日志与排障

服务日志默认输出到 stdout，适合先用命令行或 Windows 服务包装器观察。

常见问题：

- `duplicate_request`：同一个 `request_id` 已提交过，mock 会拒绝重复正式下单
- `invalid_lot_size`：当前 mock 默认按 A 股 100 股整数倍校验
- `submit_disabled`：检查 `broker.mock.allow_submit` 和 `broker.mock.submit_enabled`
- `not_implemented`：你把配置切到了 `broker.mode=miniqmt`，但真实 Windows adapter 还没接线

## 与 SysQ 的调用关系

推荐的闭环是：

1. SysQ 盘前生成 `order_intents.json`
2. SysQ 脚本先调用 `POST /orders/validate`
3. 人工或脚本确认后调用 `POST /orders/submit`
4. 盘中/盘后通过 `GET /orders`、`GET /trades`、`GET /positions`、`GET /account`
5. 最后通过 `GET /snapshots/latest` 拉回账户/持仓/委托/成交快照，继续生成 daily ops 产物

这个目录故意不直接耦合进 `qsys/`：

- SysQ 负责研究、策略、daily ops 编排和 `order_intents` 产出
- `miniqmt_server/` 负责 Windows 执行服务和 broker 交互
- 两边通过稳定的 HTTP + JSON 契约衔接

## 真实 MiniQMT adapter 现状

`miniqmt_server/broker/miniqmt.py` 目前还是 adapter shell，明确保留这些未来接线点：

- MiniQMT 登录状态和健康检查
- 真实账户资金查询
- 真实持仓查询
- 委托查询 / 成交查询
- 正式下单 / 撤单
- 本地快照回写

也就是说：

- 当前 mock 是真实可跑、可测试、可演示的
- 当前真实 MiniQMT adapter 仍需在 Windows 环境继续接线
