# SysQ

SysQ 是面向 A 股日频量化研究与准实盘运营的个人系统，覆盖 research、backtest、candidate/shadow、daily ops、ledger、UI/monitoring 的闭环。

---

## Current State

- 处于 **Framework Stabilization + Daily Operations Hardening** 阶段。
- Research / Backtest Chain 与 Daily Ops Chain 已形成主线。
- `data/trade.db` 是目标 Account State / Execution Ledger SOT。
- `data/meta/real_account.db` 与 `shadow/` 仍是 legacy compatibility path，不能随意删除。
- systemd 当前仍走 legacy entry（`run_preopen.sh` / `run_postclose.sh`）。目标入口 `run_daily.py` / `run_daily_batch.py` 已通过 8-gate 验证（`--trade-date auto`、signal_basket 修复、reconciliation_result），待 systemd unit 替换。

---

## Read First

按顺序阅读：

1. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 系统地图：两条主链路、状态边界、过渡态
2. [AGENTS.md](AGENTS.md) — AI 操作协议、Protected Core、安全边界
3. [ROADMAP.md](ROADMAP.md) — 当前阶段目标和优先级
4. [docs/CONTRACTS.md](docs/CONTRACTS.md) — 模块边界协议和数据对象契约
5. [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md) — 代码、数据、artifact、report 放置规则
6. [docs/ops/DAILY_OPS_SOP.md](docs/ops/DAILY_OPS_SOP.md) — daily ops 运行手册
7. [docs/ops/RESEARCH_STRATEGY_SOP.md](docs/ops/RESEARCH_STRATEGY_SOP.md) — 新策略研发 SOP

---

## Core Documents

| 文件 | 用途 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统设计 |
| [AGENTS.md](AGENTS.md) | AI 操作协议 |
| [ROADMAP.md](ROADMAP.md) | 阶段优先级 |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | 模块与数据 contract |
| [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md) | 仓库布局 |
| [docs/ops/DAILY_OPS_SOP.md](docs/ops/DAILY_OPS_SOP.md) | 日常运维 |
| [docs/ops/RESEARCH_STRATEGY_SOP.md](docs/ops/RESEARCH_STRATEGY_SOP.md) | 策略研发 |

其他文档：[docs/](docs/)（features、ADR、schema、SOP）

---

## Directory Quick View

| 目录 | 用途 |
|------|------|
| `qsys/` | 核心 Python package |
| `scripts/` | 通用 CLI 入口（`run_daily.py`、`run_daily_batch.py` 等）|
| `scripts/ops/` | 数据同步、shadow daily、入口编排 |
| `scripts/checks/` | 产检工具（schema、order intents、snapshot、reconciliation 检查）|
| `scripts/research/` | 研究评估入口（rolling research、signal eval、backtest、experiment index）|
| `scripts/live/` | 实盘操作脚本（broker 下单、对账）|
| `tests/` | 测试 |
| `config/` + `configs/` | 运行与研究配置 |
| `data/` | 行情数据、model artifact、ledger DB |
| `daily/` | 每日运行产物 |
| `experiments/` | 研究实验产物 |
| `deploy/` | systemd 配置 |

详细说明见 [docs/REPO_LAYOUT.md](docs/REPO_LAYOUT.md)。

---

## Safety Notes

- **UI / monitoring 只读**——不写 ledger，不下单，不改策略。
- **Research artifact 不能直接进入 Production**。
- **不无人值守自动实盘下单**。
- **不直接编辑 ledger**。
- **不删除 `data/meta/real_account.db` 和 `shadow/`**。
- **不把旧入口扩张成新长期接口**。
