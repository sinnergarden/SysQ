# MiniQMT Server

## 1. 背景

Qsys 当前研究、信号生成、daily ops 已逐步形成统一产物骨架，包括：

- preopen plan
- order intents
- signal basket
- signal quality
- daily ops manifest

下一步需要把“研究/调度侧输出”和“实际下单执行”打通，但执行侧受限于 MiniQMT 运行环境，必须部署在 Windows 上，且与策略研究环境（常驻在 WSL / Linux / 远程 runner）天然分离。

因此需要一个常驻在 Windows 的 `miniqmt server`，负责：

- 托管 MiniQMT 交易连接
- 对外暴露稳定、可脚本调用的 broker 接口
- 接收 SysQ 生成的订单意图
- 完成校验、风控、下单、查询、回报归档
- 为后续 production ops 提供统一执行面

该功能是 Qsys 从“研究系统”走向“可控生产执行系统”的关键桥接层。

---

## 2. 目标

本功能的目标不是做完整交易平台，而是做一个**最小但可生产使用的 MiniQMT 执行服务**，满足以下要求：

- 部署在 Windows 主机，常驻运行
- 能被 SysQ 脚本稳定调用
- 支持账户状态查询、持仓查询、订单提交、订单撤销、成交回报查询
- 支持 order intents -> broker order 的转换
- 支持基础风控与幂等控制
- 支持结构化日志、审计留痕、失败可追踪
- 支持后续接入 daily ops / workflow layer，而不推翻现有输出骨架

---

## 3. 非目标

本功能当前**不负责**以下事项：

- 不负责信号生成、选股、组合优化
- 不负责回测与研究逻辑
- 不负责跨券商统一适配，当前只面向 MiniQMT
- 不负责高频 / 低延迟交易
- 不负责自动绕过交易软件限制或做桌面自动化点击
- 不负责复杂 OMS / PMS / 风险引擎全量重建
- 不负责在 Linux 上直接运行 MiniQMT

---

## 4. 功能边界

本功能位于以下边界之间：

- 上游：SysQ workflow / strategy / daily ops
- 中间：`miniqmt server`
- 下游：MiniQMT 客户端 / 券商柜台

职责划分：

### 上游 SysQ 负责

- 生成策略信号
- 生成目标持仓或 order intents
- 决定交易日流程（preopen / intraday / postclose）
- 决定是否调用执行服务
- 保存研究侧上下文、策略上下文、调仓理由

### MiniQMT Server 负责

- 接收调用请求
- 做请求校验与幂等控制
- 将标准化 order intents 转成 broker order
- 调用 MiniQMT API
- 保存请求、响应、成交、错误、快照
- 提供查询接口与健康状态
- 提供最小风控闸门

### MiniQMT / 券商侧负责

- 实际报单、撤单、成交
- 真实资金与持仓变动
- 柜台规则、交易所规则、交易时段规则

---

## 5. 使用场景

### 场景 A：盘前计划后执行
SysQ 在盘前生成 `order_intents.json`，调用 server 做预校验、资金检查和正式下单。

### 场景 B：盘中人工确认后执行
研究侧给出订单建议，由人工审核后触发脚本调用 server 下单。

### 场景 C：盘后对账
SysQ 调用 server 拉取当日委托、成交、持仓、资金快照，生成 daily ops manifest。

### 场景 D：故障恢复
Windows 重启或 MiniQMT 重连后，server 能恢复账户状态查询能力，并继续提供只读或读写服务。

---

## 6. 功能需求

## 6.1 服务常驻

服务应作为 Windows 常驻进程运行，支持：

- 手动启动 / 停止
- 开机自启（后续可选）
- 单实例运行
- 健康检查
- 重连 MiniQMT
- 结构化日志输出

建议首版以 Python 服务实现，运行方式可为：

- 命令行常驻进程
- Windows Task Scheduler / NSSM / WinSW 包装成服务（二期）

---

## 6.2 对外接口

服务需要提供一组对 SysQ 友好的稳定接口。首版建议 HTTP API，本机优先，局域网或 Tailscale 白名单可选开放。

### 必需接口

#### `GET /health`
返回服务健康状态，包括：

- server 进程状态
- MiniQMT 连接状态
- 当前账户是否可查询
- 是否允许下单
- server version
- 当前交易日
- 最近一次成功同步时间

#### `GET /account`
返回账户资金摘要，包括：

- 总资产
- 可用资金
- 持仓市值
- 冻结资金
- 当日盈亏（若可得）
- 账户 ID

#### `GET /positions`
返回当前持仓，包括：

- symbol
- volume
- available_volume
- cost_price
- market_value
- pnl / pnl_pct（若可得）
- update_time

#### `GET /orders`
查询委托列表，支持按以下条件过滤：

- trade_date
- symbol
- status
- client_order_id
- strategy_id

#### `GET /trades`
查询成交列表，支持按以下条件过滤：

- trade_date
- symbol
- order_id
- strategy_id

#### `POST /orders/validate`
对传入 order intents 做静态校验，不实际下单。用于盘前检查与人工审核前预览。

#### `POST /orders/submit`
提交订单，返回：

- request_id
- accepted / rejected
- rejection_reason
- broker_order_ids
- normalized_orders
- submit_time

#### `POST /orders/cancel`
按 broker_order_id / client_order_id 撤单。

#### `GET /snapshots/latest`
返回最近一次账户、持仓、委托、成交快照的本地缓存。

---

## 6.3 输入数据格式

Server 的核心输入不是随意参数，而是 SysQ 输出的标准化 `order intents`。

首版约定输入格式如下：

```json
{
  "request_id": "2026-04-06-strategyA-open-001",
  "strategy_id": "strategyA",
  "trade_date": "2026-04-06",
  "account_id": "sim_or_real_account",
  "dry_run": false,
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
}