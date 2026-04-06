# MiniQMT Server

`miniqmt_server/` 是 SysQ 仓库内的独立执行服务，用来承接 SysQ 的标准化下单请求，并对外暴露一个稳定、可脚本调用的 HTTP 接口。

当前可稳定运行的是 `mock` 模式：它能模拟账户查询、持仓查询、订单校验、订单提交、撤单、成交与快照落盘，方便在没有 Windows MiniQMT 环境时继续开发、联调和演示。`miniqmt` 模式目前只保留了真实适配器边界，尚未接线。

## 服务职责

这个服务位于 SysQ 与 Windows MiniQMT 之间，职责很明确：

- 接收 SysQ 产出的 `order_intents` 请求
- 做最小请求校验、批量预检和 `request_id` 幂等保护
- 提供账户、持仓、委托、成交、最新快照等查询接口
- 在 `mock` 模式下提供本地可运行的完整 HTTP 合约
- 为未来 Windows 真机 MiniQMT adapter 预留统一接入面
- 将订单、成交、提交回执、快照落到本地文件，便于审计和排障

当前 HTTP 接口：

- `GET /health`
- `GET /account`
- `GET /positions`
- `GET /orders`
- `GET /trades`
- `GET /snapshots/latest`
- `POST /orders/validate`
- `POST /orders/submit`
- `POST /orders/cancel`

## 目录结构

```text
miniqmt_server/
  app.py                 HTTP server 入口
  config.py              配置加载与默认值
  config.example.yaml    示例配置
  models.py              请求/响应/记录数据模型
  storage.py             json/jsonl 本地存储
  broker/
    base.py              broker 抽象接口
    mock.py              本地 mock broker 实现
    miniqmt.py           真实 MiniQMT adapter 边界
  data/                  默认落盘目录
  README.md
```

配套文件：

- `scripts/call_miniqmt_server_mock.py`：最小调用示例
- `tests/test_miniqmt_server.py`：接口与真实 HTTP smoke 覆盖
- `docs/features/miniqmt_server.md`：功能边界与设计背景

## 启动 / 运行

建议从仓库根目录启动。

1. 使用示例配置启动 mock 服务：

```bash
python -m miniqmt_server.app --config miniqmt_server/config.example.yaml
```

2. 临时覆盖监听地址或端口：

```bash
python -m miniqmt_server.app \
  --config miniqmt_server/config.example.yaml \
  --host 127.0.0.1 \
  --port 8812
```

3. 启动后做健康检查：

```bash
curl http://127.0.0.1:8811/health
```

4. 用仓库内示例脚本联调：

```bash
python scripts/call_miniqmt_server_mock.py --base-url http://127.0.0.1:8811
```

说明：

- 默认模式是 `broker.mode=mock`
- `broker.mode=miniqmt` 当前会返回 `not_implemented`，适合先保留接口形状，不适合生产使用
- `server.data_dir` 如果写相对路径，会基于当前工作目录解析；从仓库根目录启动最不容易混淆

## 配置说明

示例见 `miniqmt_server/config.example.yaml`。

### `server`

- `host`：绑定地址，默认 `127.0.0.1`
- `port`：监听端口，默认 `8811`
- `version`：服务版本号，默认来自 `miniqmt_server.__version__`
- `data_dir`：审计文件和快照目录；默认 `miniqmt_server/data`

### `broker`

- `mode`：`mock` 或 `miniqmt`

### `broker.mock`

- `account_id`：默认账户 ID
- `allow_submit`：总闸门；关闭时 `submit` 直接拒绝
- `submit_enabled`：模拟下单开关；通常与 `allow_submit` 一起使用
- `auto_fill`：提交后是否直接生成成交并更新快照
- `miniqmt_connected`：健康检查里返回的连接状态标记
- `query_ready`：健康检查里返回的查询可用标记
- `account`：账户资金初始值，例如 `available_cash`、`total_assets`
- `positions`：初始持仓列表，供查询和 mock 风控使用

常见配置组合：

- 本地联调：`mode=mock`、`allow_submit=true`、`auto_fill=false`
- 只做预检：`mode=mock`、`allow_submit=false`
- 演示成交回流：`mode=mock`、`allow_submit=true`、`auto_fill=true`

## API 示例

### 健康检查

```bash
curl http://127.0.0.1:8811/health
```

### 校验订单意图

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

返回重点：

- `status`：`accepted` / `partial` / `rejected`
- `accepted` / `rejected`：逐笔结果
- `normalized_orders`：服务端标准化后的订单内容
- `errors`：请求级错误

### 正式提交订单

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
        "quantity": 100,
        "order_type": "LIMIT",
        "limit_price": 12.34,
        "time_in_force": "DAY",
        "reason": "rebalance_to_target"
      }
    ]
  }'
```

提交语义：

- 首次成功提交返回 `idempotency_status=new`
- 相同 payload 重试返回原始结果，标记 `idempotency_status=replayed`
- 不同 payload 复用同一个 `request_id` 返回 `idempotency_status=conflict`

### 撤单

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

### 查询订单、成交和快照

```bash
curl 'http://127.0.0.1:8811/orders?trade_date=2026-04-06&strategy_id=strategyA'
curl 'http://127.0.0.1:8811/trades?symbol=600000.SH'
curl http://127.0.0.1:8811/snapshots/latest
```

## 本地 mock 模式

`mock` 模式是当前推荐的默认工作方式，适合在 Linux/WSL、CI 或没有券商环境的机器上做接口开发。

已实现能力：

- 返回可配置的账户资金和持仓
- 支持 validate / submit / cancel / query 全套 HTTP 流程
- 对 BUY 做可用现金检查，对 SELL 做 `available_volume` 检查
- 批量 BUY 会按顺序累计占用 `available_cash`
- 支持 `dry_run=true`，只校验不落正式订单
- 支持 `request_id` 持久化幂等，服务重启后仍能回放
- 支持提交、成交、快照落盘，便于回看 mock 执行轨迹

默认落盘文件：

- `miniqmt_server/data/orders.jsonl`
- `miniqmt_server/data/trades.jsonl`
- `miniqmt_server/data/submissions.jsonl`
- `miniqmt_server/data/snapshots.jsonl`
- `miniqmt_server/data/latest_snapshot.json`

适用边界：

- 适合接口联调、演示、脚本开发、幂等语义验证
- 不代表真实券商成交规则、交易时段限制和柜台回报细节

## 日志与排障

服务日志默认打到 stdout，适合直接前台运行，或再交给 NSSM / WinSW / Task Scheduler 之类的包装器托管。

常见排障点：

- `request_id_conflict`：同一个 `request_id` 被用于不同提交内容，需换新的 `request_id`
- `invalid_lot_size`：A 股 mock 校验默认要求 100 股整数倍
- `insufficient_available_cash`：买单合计超出 `available_cash`
- `insufficient_available_volume`：卖出数量超过当前 `available_volume`
- `submit_disabled`：检查 `broker.mock.allow_submit` 和 `broker.mock.submit_enabled`
- `not_implemented`：当前配置切到了 `broker.mode=miniqmt`

如果要核对问题现场，先看两类信息：

- 终端日志：请求路径、状态码、异常堆栈
- `data_dir` 落盘文件：订单、成交、提交回执、最新快照

## 与 SysQ 的关系 / 调用流

推荐的调用路径如下：

1. SysQ 在研究层和 daily 流程中生成 `order_intents` 产物
2. 编排脚本把该产物转成 `POST /orders/validate` 请求，先做预检
3. 人工确认或策略允许后，再调用 `POST /orders/submit`
4. `miniqmt_server` 记录提交结果，并维护订单、成交、快照本地视图
5. SysQ 继续通过 `GET /account`、`GET /positions`、`GET /orders`、`GET /trades`、`GET /snapshots/latest` 拉回执行结果，用于盘中跟踪、盘后对账和 daily ops 产物

仓库内最小参考：

- 服务端：`miniqmt_server/app.py`
- 调用脚本：`scripts/call_miniqmt_server_mock.py`
- 功能说明：`docs/features/miniqmt_server.md`

## 测试

测试这里只保留最小入口说明：

```bash
python -m unittest tests/test_miniqmt_server.py
```

该用例已覆盖 in-process 接口测试，以及基于真实本地 HTTP 端口的 smoke 测试。
