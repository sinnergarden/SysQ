# RUNBOOK

## 1. 先记住这五条

- `data/raw/` 和 `data/qlib_bin/` 是长期数据层，不放 daily evidence。
- `daily/{date}/` 是单日证据包，盘前和盘后只写到当天目录。
- 长期账户主库固定为 `data/meta/real_account.db`。
- `data/derived/` 只沉淀稳定结构化字段，不替代原始 daily evidence。
- 研究、训练、回测输出统一放 `experiments/`，结构化 JSON 报告默认在 `experiments/reports/`。

## 2. 日常操作顺序

1. 先做数据健康检查。
2. 跑盘前：生成 signal basket、plan、order intents、report、manifest。
3. 盘中执行订单，不复制账户主库到 `daily/`。
4. 跑盘后：回写 snapshot、做 reconciliation、写报告与 manifest。
5. 如需跨日分析，再执行 rollup 到 `data/derived/`。

## 3. 当前 daily 目录

盘前只保留：

- `daily/{execution_date}/pre_open/plans`
- `daily/{execution_date}/pre_open/order_intents`
- `daily/{execution_date}/pre_open/signals`
- `daily/{execution_date}/pre_open/reports`
- `daily/{execution_date}/pre_open/manifests`

盘后只保留：

- `daily/{execution_date}/post_close/reconciliation`
- `daily/{execution_date}/post_close/snapshots`
- `daily/{execution_date}/post_close/reports`
- `daily/{execution_date}/post_close/manifests`

目录细节见 `docs/DATA_LAYOUT.md`；执行步骤见各 SOP。

## 4. 入口命令

盘前：

```bash
python3 scripts/run_daily_trading.py --date 2026-04-02
```

盘后：

```bash
python3 scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

单独检查信号质量：

```bash
python3 scripts/run_signal_quality.py --date 2026-04-03
```

rollup：

```bash
python3 scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```

## 5. 导航

- 数据链路：`docs/ops/DATA_PIPELINE_SOP.md`
- 盘前：`docs/ops/PRE_OPEN_SOP.md`
- 盘后：`docs/ops/POST_CLOSE_SOP.md`
- 目录契约：`docs/DATA_LAYOUT.md`

## 6. 排障顺序

- 查单日问题：先看 `daily/{date}/...`
- 查跨日趋势：再看 `data/derived/`
- 查研究结果：看 `experiments/`
- 查账户主状态：看 `data/meta/real_account.db`
