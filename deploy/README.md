# SysQ 生产部署

## 定时任务（systemd timer）

所有定时任务通过 systemd timer 管理，统一使用 `EnvironmentFile=/home/liuming/.openclaw/.env` 加载 Telegram 凭证。

### 三个 timer

| Timer | 时间(CST) | 脚本 | 说明 |
|---|---|---|---|
| `qsys-csi800-daily-sync.timer` | 交易日 21:30 | `scripts/ops/sync_csi800_daily.py` | 数据同步 |
| `qsys-preopen.timer` | 交易日 08:00 | `scripts/run_preopen.sh` | 盘前：信号→计划→shadow 模拟 |
| `qsys-post-close.timer` | 交易日 15:30 | `scripts/run_postclose.sh` | 盘后：对账→归档→摘要 |

### 安装步骤

```bash
# 1. 创建 Telegram 凭证文件
#    先通过 @BotFather 创建 bot，获取 token
#    给 bot 发消息，curl https://api.telegram.org/bot<TOKEN>/getUpdates 获取 chat_id
#    然后写入：
cat > /home/liuming/.openclaw/.env << 'EOF'
QSYS_TELEGRAM_BOT_TOKEN=你的bot_token
QSYS_TELEGRAM_ALLOWED_CHAT_ID=你的chat_id
EOF
chmod 600 /home/liuming/.openclaw/.env

# 2. 安装 systemd 文件
sudo cp deploy/systemd/qsys-csi800-daily-sync.service /etc/systemd/system/
sudo cp deploy/systemd/qsys-csi800-daily-sync.timer /etc/systemd/system/
sudo cp deploy/systemd/qsys-preopen.service /etc/systemd/system/
sudo cp deploy/systemd/qsys-preopen.timer /etc/systemd/system/
sudo cp deploy/systemd/qsys-post-close.service /etc/systemd/system/
sudo cp deploy/systemd/qsys-post-close.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 3. 启用 timer
sudo systemctl enable --now qsys-csi800-daily-sync.timer
sudo systemctl enable --now qsys-preopen.timer
sudo systemctl enable --now qsys-post-close.timer

# 4. 确认状态
systemctl list-timers --all | grep qsys
```

### 通知

所有三个服务在完成后都会通过 Telegram 发送通知：
- `sync_csi800_daily.py` — 内置 `_notify_telegram()`，发送同步状态和 readiness 摘要
- `run_preopen.sh` — 包装脚本内调用 `notify_telegram.sh`，发送完成/失败信息
- `run_postclose.sh` — 包装脚本内调用 `notify_telegram.sh`，发送完成/失败信息

凭证来源（按优先级）:
1. `QSYS_TELEGRAM_BOT_TOKEN` / `QSYS_TELEGRAM_ALLOWED_CHAT_ID` 环境变量
2. `config/settings.yaml` → `ops.notification.telegram.bot_token`
3. `config/settings.yaml` → `telegram_bot_token`（顶层 key）

### 日志

```
/home/liuming/.openclaw/logs/
├── sync_csi800_daily.log     # 数据同步
├── preopen.log               # 盘前流程
└── postclose.log             # 盘后流程
```

### 手动测试

```bash
# 数据同步（测试）
python scripts/ops/sync_csi800_daily.py --apply

# 盘前（测试，指定日期）
python scripts/run_daily_trading.py --date 2026-05-15 --execution_date 2026-05-18 --skip_update

# 盘后（测试，需要 real_sync 文件）
python scripts/run_post_close.py --date 2026-05-15 --real_sync /path/to/sync.csv

# Telegram 通知测试
bash scripts/notify_telegram.sh "测试消息"
```

## 修复记录

### 2026-05-17: 自动重训 feature set 修复

**问题**: `ModelScheduler.check_and_retrain()` 子进程调用 `run_train.py` 时未传 `--feature_set`，导致重训使用默认 173 特征（extended）而非原模型的 254 特征（semantic_all_features）。

**修复文件**: 
- `qsys/live/scheduler.py`（line 63-102）— 从模型目录名提取 feature_set，传给子进程；重训后直接返回原路径，不再用字母序找模型
- `scripts/run_daily_trading.py`（line 177-185）— `_resolve_model_feature_set()` 添加目录名回退逻辑，metadata 没有 feature_set 时从目录名提取

### feature 版本管理

Feature set 通过名称绑定：
1. `feature_set` 名称 → `FeatureLibrary` 中的具体表达式列表
2. 训练时，表达式列表固化到模型 artifact 中
3. 模型目录名编码了 feature_set 名称：`qlib_lgbm_{feature_set_name}`
4. 预测时从模型目录名解析出 feature_set，使用同组表达式

新增 feature set 只需在 `FeatureLibrary` 中定义，训练时 `--feature_set {name}` 即可。多账户支持通过 `--model_path` 和 `--account_name` 参数实现（目前架构为单账户串行，多账户需 N 个 systemd 服务并行）。

### Run Archive 结构

```
runs/{execution_date}_{mode}_{account_id}_{seq}/
├── manifest.json       # run_id, execution_date, model_path, feature_set, top_k...
├── inputs/
│   ├── account_snapshot_before.json
│   └── signal_basket.csv
├── outputs/
│   ├── plan.csv
│   ├── order_intents.json
│   ├── execution_results.json
│   └── reconciliation_report.json
└── summary.json        # 计划数, 成交数, 偏离, 换手...
```
