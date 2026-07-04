# UC_DAILY_OPS: Daily Operations

## Status
stable

## Source
`docs/USE_CASES.md` UC-1（Data Sync）、UC-8（Shadow Trading）、UC-9（Production Trading，远期）。
USE_CASES.md 是权威来源，本文档是超集补充。

## User Goal
每日自动执行数据同步 → 信号推理 → 交易计划 → 盘后对账的全链路。operator 可查看每日状态和产物，异常时有阻断和通知。

## Scope
包含：
- 数据同步（csi800 daily）
- 盘前推理（signal → prediction → plan）
- 盘后执行（simulated fills → ledger → MTM）
- 通知（Telegram）
- manifest 产物写入

不包含：
- 实盘 broker 下单（见 UC_CANDIDATE_PROMOTION 远期）
- 模型训练（见 UC_MODEL_TRAINING）
- 研究回测（见 UC_RESEARCH_BACKTEST）

## Inputs
- 行情数据（canonical daily / qlib_bin）
- 策略配置（`configs/strategies/*.yaml`）
- 模型 pointer（`artifacts/registry/models/{strategy_id}/shadow.json` 或 legacy pointer）
- promotion pointer（`data/research/promotions/shadow.yaml`）

## Outputs
- `data/canonical/daily/` — 规范行情数据
- `daily/{trade_date}/pre_open/signals/signal_basket_*.csv`
- `daily/{trade_date}/post_close/reconciliation_result.json`
- `runs/{trade_date}/{run_id}/` 各阶段产物
- Ledger 写入（`data/trade.db`）
- Telegram 通知

## Canonical Entrypoints

| Entrypoint | 职责 | 对应 UC | Inputs | Outputs / Artifacts |
|-----------|------|---------|--------|---------------------|
| `scripts/data_sync.py` | 数据同步与校验 | UC-1 | 数据源配置 | `data/canonical/daily/`, `data/audit/` |
| `scripts/run_daily.py --mode preopen` | 盘前推理 + 交易计划 | UC-8/9 | 策略配置、模型 pointer | 信号篮子、order intents、manifest |
| `scripts/run_daily.py --mode postclose` | 盘后执行 + MTM | UC-8/9 | preopen 产物、收盘行情 | ledger 写入、MTM、reconciliation |

`scripts/run_daily_batch.py` 是批量 wrapper，非独立 canonical entrypoint。
对齐 `docs/USE_CASES.md` §7。

## Key Artifacts
- `data/trade.db` — ledger SOT
- `daily/{date}/pre_open/signals/` — signal basket
- `daily/{date}/post_close/` — reconciliation, MTM
- `runs/{date}/{run_id}/` — 完整运行产物

## Required Checks
- `harness/checks/check_model_resolution_boundary.py`
- `harness/checks/check_no_latest_model_resolution.py`
- TBD: daily artifact schema check
- TBD: preopen/postclose stage integrity check

## Owner Agent
operator_agent

## Allowed Paths
- `scripts/data_sync.py`
- `scripts/run_daily.py`
- `qsys/ops/`
- `configs/strategies/`
- `harness/checks/`
- `tests/`

## Forbidden Paths
- `qsys/ledger/`（只读分析，不直接改 schema）
- `qsys/backtest/`（不混入研究层变化）
- `qsys/trader/`
- `qsys/broker/`

## Open Questions
- 无
