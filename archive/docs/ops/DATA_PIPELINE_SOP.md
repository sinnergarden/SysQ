# 数据链路 SOP — CSI800 日频同步

## 1. 目的

保证每日盘前使用的 CSI800 数据是最新、对齐、可解释的。数据链路是 SysQ 的第一阻断关。

## 2. 范围

覆盖每日增量同步全流程：
- 交易日解析
- 成分股获取
- 预检（跳过已有数据的股票）
- Raw 数据拉取（Tushare）
- Qlib 增量转换
- 工具文件刷新
- Readiness 检查
- Audit 记录
- Telegram 通知

## 3. 入口

### 自动触发（生产路径）

每天 21:30 由 systemd timer 触发：

```
deploy/systemd/qsys-csi800-daily-sync.timer
  → qsys-csi800-daily-sync.service
    → python scripts/ops/sync_csi800_daily.py --apply
```

日志：`~/openclaw/logs/sync_csi800_daily.log`

### 手动触发

```bash
# 标准运行
python scripts/ops/sync_csi800_daily.py --apply

# 指定日期
python scripts/ops/sync_csi800_daily.py --apply --target-date 2026-05-15

# 仅预览（不写数据）
python scripts/ops/sync_csi800_daily.py

# 强制重拉所有股票（跳过预检）
python scripts/ops/sync_csi800_daily.py --apply --force-fetch
```

## 4. 标准流程（9 步）

### Step 0: 初始化 Qlib

解析目标交易日 → 初始化 Qlib 环境。

目标交易日逻辑（`_resolve_target_date`）：
1. 如果有显式 `--target-date`，直接使用
2. 否则读 `meta.db` 交易日历，取最后一个 `is_open=1` 且小于今天的日期
3. 如果日历不可用，回退到昨天

关键代码：`scripts/ops/sync_csi800_daily.py:_resolve_target_date()`

### Step 1: 获取 CSI800 成分股

通过 Tushare `index_weight` 接口获取 CSI800 最新成分股列表。

```python
collector = TushareCollector()
codes = collector.get_universe("csi800")
```

输出：成分股代码列表（~800 只）

### Step 2: 预检 — 跳过已有数据的股票

逐只股票读 feather 文件的 `trade_date` 列，检查是否已包含目标日期。

```python
# 判断标准：trade_date.max() >= target_dt
_stats = {"have": [...], "missing": [...]}
```

- 所有股票都有 → 跳过 Step 3（raw fetch）
- 仅部分缺失 → 只拉缺失的股票
- `--force-fetch` → 跳过预检，全量拉取

### Step 3: Raw 数据拉取

对缺失数据的股票，调用 `TushareCollector.update_universe_history()` 批量拉取。

拉取的数据类型（每日增量）：

| 接口 | 内容 |
|------|------|
| `daily` | OHLCV 日线 |
| `adj_factor` | 复权因子 |
| `daily_basic` | 换手率、市值等 |
| `moneyflow` | 资金流 |
| `margin` | 融资融券 |
| `stk_limit` | 涨跌停价 |

参数：`batch_size=200`，`incremental=False`（单日精确拉取），包含资金流和两融。

### Step 4: Qlib 增量转换

将 raw 数据转换为 qlib bin 格式。

尝试顺序：
1. **incremental**（快速增量）：`adapter.convert_incremental(since)` — 只处理目标日期及之后
2. **fix**（慢但稳）：如果 incremental 失败，fallback 到 `adapter.convert_fix(since)` — 全量重建目标日期附近窗口

### Step 5: 刷新工具文件

更新 qlib instrument 文件：

```python
adapter._refresh_universe_instruments("csi800")
adapter._refresh_universe_instruments("csi300")
```

确保 `data/qlib_bin/instruments/csi800.txt` 和 `csi300.txt` 的 `end_date` 已包含目标日期。

### Step 6: Readiness 检查

检查项（`_readiness_check`）：

| 检查 | 通过条件 | 阻断 |
|------|---------|------|
| Raw 最新日期 | `raw_latest >= target_dt` | 是 |
| Qlib 日历 | `last_qlib_date >= target_date` | 是 |
| all.txt 工具文件 | `end_date >= target_date` | 是 |
| csi800.txt 工具文件 | `end_date >= target_date` | 是 |
| 活跃成分股数量 | `active_count >= 750` | 是 |
| 6 核心字段 null 率 | `null_pct < 5%`（open/high/low/close/volume/factor） | 是 |

全部通过 → `overall_status = "ready"`，退出码 0
有失败 → `overall_status = "degraded"`，退出码 2

Telegram 通知中会将检查拆为两组展示：**Data checks**（前 5 项）和 **Core fields**（6 字段 null 率）。

### Step 7: 写 Audit 记录

结构化 JSON 写入 `data/audit/sync_csi800_{YYYYMMDD}.json`。

包含：
- run_id、target_date、applied
- 每步耗时
- 成分股数量、预检统计、拉取统计
- readiness 检查明细（逐字段 null 率）
- 整体状态

### Step 8: Telegram 通知

发送 sync 结果摘要到配置的 Telegram channel。

消息格式：
```
Qsys CSI800 Daily Sync
Date: 2026-05-15
Status: ready
Constituents: 800 | Up-to-date: 795 | Fetched: 5
Qlib convert: 12.3s
Readiness: 6/6 passed
```

只在 `--apply` 模式下发送，通知失败不影响退出码。

## 5. 成功标准

- 所有 6 项 readiness 检查通过
- `overall_status = "ready"`
- Audit 记录写入 `data/audit/`
- Telegram 通知为可选集成（需配置 `QSYS_TELEGRAM_BOT_TOKEN`），通知失败不阻断流程

## 6. 常见故障

### Raw fetch 失败

表现：`_do_raw_fetch` 返回 `status: "failed"`

处理：检查 Tushare API 限额、网络连接。重试 `--apply`。

### Qlib incremental 失败但 fix 成功

表现：`qlib_convert.mode = "fix"`

处理：不阻断，但建议关注后续 readiness 检查结果。连续 fix 模式下可考虑 `--force-fetch` 重拉。

### Qlib 转换完全失败

表现：`qlib_convert.status = "failed"`

处理：阻断。检查 raw 数据完整性，确认目标日期有数据后重试。

### 活跃成分股不足 750

表现：`active_instruments.count < 750`

处理：可能是交易日解析错误（非交易日），或工具文件损坏。检查 `--target-date` 和 calendar 文件。

### 核心字段 null 率过高

表现：`field_null_rates.{field}.passed = false`

处理：检查 Tushare 该日数据质量，确认非停牌日。重跑 `--force-fetch`。

## 7. 查看结果

```bash
# 最新 audit
cat data/audit/sync_csi800_$(date +%Y%m%d).json | python -m json.tool

# sync 日志
tail -50 ~/openclaw/logs/sync_csi800_daily.log
```

## 8. 人工接管

人工干预时应记录：
- 目标日期
- 干预原因（API 限额 / 数据异常 / 重跑需要）
- 执行命令
- 最终 readiness 状态
