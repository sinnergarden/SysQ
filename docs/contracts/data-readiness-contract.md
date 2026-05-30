# Data Readiness Contract

## Purpose

今天或某个 target_date 的数据是否 ready？是否允许进入 train / backtest / preopen？如果不 ready，是否必须阻断？

## Producers

- data sync pipeline（`scripts/ops/sync_csi800_daily.py` + `qsys/` 内模块）
- qlib sync / readiness check
- future monitoring job

当前 sync pipeline 走 systemd timer，暂不涉及内部模块路径细节。

## Consumers

- DailyRunner preopen
- model train / refresh
- backtest / rolling research
- Ops UI（只读）
- monitoring / alert
- daily report

## Storage / Transport

第一版无单独 readiness store。readiness 信息可通过以下方式暴露：

- JSON report（每日 data sync 产物）
- daily evidence artifact（`daily/{date}/`）
- `data/audit/`（sync audit 记录）
- future UI read model（只读聚合）

## Grain

```
per target_date
per data domain (raw / qlib / calendar / instrument)
per universe
```

## Required Fields

| 字段 | 含义 |
|------|------|
| `target_date` | 判断 readiness 的目标日期 |
| `calendar_date` | 当前日历日期 |
| `latest_raw_date` | 原始数据最新可用日期 |
| `latest_qlib_date` | qlib_bin 数据最新可用日期 |
| `universe_id` | 标的池标识（如 csi800） |
| `is_trading_day` | target_date 是否为交易日 |
| `raw_ready` | 原始数据是否已到 target_date |
| `qlib_ready` | qlib_bin 是否已构建到 target_date |
| `calendar_ready` | 交易日历是否已更新 |
| `instrument_ready` | 成分股信息是否已更新 |
| `missing_rate` | 缺失率 |
| `status` | ready / degraded / blocked |
| `reason` | 如有阻断或降级，写明原因 |
| `generated_at` | 本 readiness 记录的生成时间 |
| `source_run_id` | 产生本记录的 run_id |

## Invariants

- 盘前推荐必须基于 T-1 已收盘数据。
- 如果数据不满足 minimum readiness，不得生成假推荐。
- readiness 必须区分 ready / degraded / blocked。
  - **degraded**：可以继续，但必须显式写入报告。
  - **blocked**：必须阻断 train / backtest / preopen 中对应流程。
- readiness 不能只看文件存在，还要看日期、coverage、calendar、universe。

## UI / Monitoring Usage

Ops UI 应展示：

- latest raw date
- latest qlib date
- readiness status（ready / degraded / blocked）
- missing rate
- blocking reason
- 最近一次 sync run

Monitoring 应能基于 blocked / degraded 触发告警。

## Current Legacy Compatibility

当前 readiness 检查分散在多个入口脚本中（`run_daily_trading.py`、`run_post_close.py`），无统一 readiness store。迁移期间各入口可各自检查，但不应扩张新依赖。

## Versioning

第一版以 JSON report 和 audit 文件为主。后续如引入统一 readiness view，需兼容历史 JSON 格式。
