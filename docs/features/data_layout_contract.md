# Data Layout Contract

## Goal

- 收敛 `data/`、`daily/`、`experiments/`、`runs/` 的职责边界。
- 让 daily ops 默认产物不再继续污染 `data/` 根目录。
- 给脚本、文档、排障过程一套稳定的目录契约与关键文件模式。

## Use Cases

- 盘前执行 `scripts/run_daily_trading.py` 时，plan、template、signal basket、order intents、report、manifest 自动落到按交易日组织的目录。
- 盘后执行 `scripts/run_post_close.py` 时，对账 summary、positions、real trades、real sync snapshot、report、manifest 自动落到同一交易日目录下的 `post_close/`。
- 排障时，能基于 `daily/{date}/` 快速查看该交易日完整运行证据。
- 研究与实验输出默认写入 `experiments/`，不再和 canonical data / ops evidence 混放。
- 历史散落在 `data/` 根目录的 daily 产物可以迁移到新目录，同时保留最小兼容读取。

## API Change

- 不修改受保护的核心接口签名。
- 调整 daily ops 相关 CLI 的默认输出路径：
  - `scripts/run_daily_trading.py`
  - `scripts/run_post_close.py`
- 调整相关 artifact helper 的默认目录解析与兼容读取逻辑。
- 允许旧路径作为 legacy 输入读取，但新的写入路径统一切到新目录。

## UI

- 无图形界面变更。
- CLI help、runbook、SOP、数据目录文档需要同步更新默认路径与文件模式。

## Constraints

- 不删除 `data/raw/`、`data/qlib_bin/`、`data/models/` 等核心资产。
- 不做大规模业务重构，只调整目录契约、默认路径、文档和最小迁移逻辑。
- 对历史产物优先迁移或兼容读取，不粗暴删除。
- 保持单机、直白、可 debug 的实现风格。

## Done Criteria

- daily ops 新产物默认写入 `daily/{execution_date}/pre_open|post_close/...`。
- `data/` 根目录不再新增 plan / template / order intents / signal basket / reconciliation 等带日期的 daily 中间产物。
- `experiments/` 成为研究输出默认目录，文档说明 `data/experiments/` 为 legacy。
- `docs/RUNBOOK.md`、`docs/ops/PRE_OPEN_SOP.md`、`docs/ops/POST_CLOSE_SOP.md`、新增数据目录文档全部同步。
- 提供最小迁移能力或执行结果，能把常见 legacy daily 产物整理进新目录。
- 相关测试覆盖默认路径或兼容读取行为，并通过最小回归。
