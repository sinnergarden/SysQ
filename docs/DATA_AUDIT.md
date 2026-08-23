# Data Audit Records

> `data/audit/` 中的数据同步审计记录。

---

## 产出者

`scripts/ops/sync_csi800_daily.py` 每次 `--apply` 运行时按实际 universe 写入
`data/audit/sync_{universe}_{target_date}.json`，例如 CSI1800 PIT 同步写入
`sync_csi1800_20260821.json`。

---

## 核心字段

| 字段 | 类型 | 含义 |
|------|------|------|
| `target_date` | str (YYYYMMDD) | 本次同步的目标交易日 |
| `universe` | str | 本次同步的 universe（`csi800` / `csi1800`） |
| `target_date_display` | str (YYYY-MM-DD) | 同上，可读格式 |
| `applied` | bool | 是否真实写入（false = dry-run） |
| `overall_status` | str | `"ready"`、`"degraded"` 或 `"failed"` |
| `started_at` | str (ISO 8601) | 开始时间 |
| `ended_at` | str (ISO 8601) | 结束时间 |
| `steps` | dict | 各步骤详情 |

### `steps` 字段

| 子字段 | 类型 | 含义 |
|--------|------|------|
| `init_qlib.elapsed_s` | 数值 | qlib 初始化耗时 |
| `get_universe.constituent_count` | 整数 | 目标 universe 成分股数 |
| `pre_check.already_up_to_date` | 整数 | 无需更新的股票数 |
| `pre_check.need_fetch` | 整数 | 需要拉取的股票数 |
| `raw_fetch.status` | str | `"success"` / `"failed"` / `"skipped"` |
| `raw_fetch.codes_fetched` | 整数 | 实际拉取股票数 |
| `raw_fetch.elapsed_s` | 数值 | 拉取耗时（秒） |
| `qlib_convert.mode` | str | `"incremental"` / `"fix"` / `"failed"` |
| `qlib_convert.elapsed_s` | 数值 | qlib 转换耗时 |
| `refresh_instruments.status` | str | `"success"` / `"failed"` / `"dry_run"` |
| `readiness_check.elapsed_s` | 数值 | 健康检查耗时 |

### `readiness` 字段

| 子字段 | 类型 | 含义 |
|--------|------|------|
| `blocking` | list[str] | 阻断性问题，有则 `overall=degraded` |
| `warnings` | list[str] | 警告信息，不影响状态 |
| `overall` | str | `"ready"` 或 `"degraded"` |

---

## 消费者

- `sync_csi800_daily.py` 自身写入（`_write_audit`）
- Telegram 通知（`_notify_telegram`）消费部分字段构造消息
- 人工排查：`ls -t data/audit/ | head -1 | xargs cat | python -m json.tool`
