# RunManifest Schema

## 目的

RunManifest 记录每次运行的元信息，实现运行的可追溯性和审计。这是连接 artifacts 和运行上下文的关键元数据。

## 格式

JSON（推荐结构化输出）。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_id` | STRING | 运行标识，如 `2026-05-18.alpha_v1.shadow` |
| `trade_date` | DATE | 交易日，格式 `YYYY-MM-DD` |
| `stage` | STRING | 运行阶段：`preopen` / `postclose` / `train` / `backtest` |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 账户标识 |
| `git_commit` | STRING | 代码版本 Git commit hash |
| `config_hash` | STRING | 配置哈希 |
| `data_version` | STRING | 数据版本标识 |
| `model_version` | STRING | 模型版本标识 |
| `signal_version` | STRING | 信号版本标识 |
| `input_artifacts` | JSON | 输入产物路径列表 |
| `output_artifacts` | JSON | 输出产物路径列表 |
| `status` | STRING | 运行状态：`started` / `completed` / `failed` |
| `error` | TEXT | 错误信息（成功时为 `null`） |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 更新时间 |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `notes` | TEXT | 人工备注或自动生成的注释 |
| `mode` | STRING | 运行模式，如 `shadow` / `paper` / `production` |
| `force_rerun` | BOOLEAN | 是否为 force rerun |

## 字段说明

- **run_id**: 应包含足够信息用于唯一标识一次运行。推荐格式：`{trade_date}.{strategy_id}.{account_id}.{stage}`。
- **stage**: 标识运行阶段，用于区分盘前、盘后、训练、回测等。
- **input_artifacts / output_artifacts**: JSON 数组，每个元素包含 `path`（相对路径）和 `type`（artifact 类型）。

## 示例

```json
{
  "run_id": "2026-05-18.alpha_v1.shadow.postclose",
  "trade_date": "2026-05-18",
  "stage": "postclose",
  "strategy_id": "alpha_v1",
  "account_id": "shadow_alpha_v1",
  "git_commit": "a1b2c3d4e5f6...",
  "config_hash": "sha256:a1b2c3d4e5f6...",
  "data_version": "20260518_v1",
  "model_version": "20260515",
  "signal_version": "v1.0",
  "input_artifacts": [
    {"path": "experiments/alpha_v1_daily/2026-05-18/plan/order_intents.csv", "type": "OrderIntentArtifact"},
    {"path": "experiments/alpha_v1_shadow_predictions/predictions_2026-05-18.csv", "type": "SignalArtifact"}
  ],
  "output_artifacts": [
    {"path": "experiments/alpha_v1_daily/2026-05-18/execution/execution_summary.json", "type": "ExecutionArtifact"},
    {"path": "experiments/alpha_v1_daily/2026-05-18/mtm/mtm_snapshot.json", "type": "PortfolioSnapshot"}
  ],
  "status": "completed",
  "error": null,
  "created_at": "2026-05-18T15:30:00",
  "updated_at": "2026-05-18T15:30:05"
}
```

## 验证规则

- `run_id` 应在系统内唯一。
- `status` 只能为 `started` / `completed` / `failed`。
- `error` 仅在 `status=failed` 时应有内容，否则为 `null`。
- `created_at` 和 `updated_at` 应为 ISO 8601 格式。
- `output_artifacts` 中引用的文件应存在于文件系统中。
