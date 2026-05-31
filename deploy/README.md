# SysQ 生产部署

## 定时任务（systemd timer）

所有定时任务通过 systemd user timer 管理，统一使用 `EnvironmentFile=/home/liuming/.openclaw/.env` 加载 Telegram 凭证。

### 当前 timer

| Timer | 时间(CST) | ExecStart | 说明 |
|-------|----------|-----------|------|
| `qsys-csi800-daily-sync.timer` | 交易日 19:00 | `scripts/ops/sync_csi800_daily.py --apply` | 数据同步 |
| `qsys-candidate-preopen.timer` | 交易日 08:00 | `run_daily_batch.py --stage candidate --mode preopen --trade-date auto` | 盘前：信号→计划→shadow |
| `qsys-candidate-postclose.timer` | 交易日 21:00 | `run_daily_batch.py --stage candidate --mode postclose --trade-date auto` | 盘后：对账→归档→摘要 |
| `qsys-candidate-train.timer` | 周一 07:00 | `run_daily_batch.py --stage candidate --mode train` | 周模型训练 |

所有 service 文件使用 `--trade-date auto`（取机器本地当天日期，无需 shell 展开）。

### 安装步骤

```bash
# 1. 创建 Telegram 凭证文件
cat > /home/liuming/.openclaw/.env << 'EOF'
QSYS_TELEGRAM_BOT_TOKEN=你的bot_token
QSYS_TELEGRAM_ALLOWED_CHAT_ID=你的chat_id
EOF
chmod 600 /home/liuming/.openclaw/.env

# 2. 复制 systemd 文件到 user 目录
cp deploy/systemd/qsys-csi800-daily-sync.service ~/.config/systemd/user/
cp deploy/systemd/qsys-csi800-daily-sync.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-preopen.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-preopen.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-postclose.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-postclose.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-train.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-train.timer ~/.config/systemd/user/
systemctl --user daemon-reload

# 3. 启用 timer
systemctl --user enable --now qsys-csi800-daily-sync.timer
systemctl --user enable --now qsys-candidate-preopen.timer
systemctl --user enable --now qsys-candidate-postclose.timer
systemctl --user enable --now qsys-candidate-train.timer

# 4. 确认状态
systemctl --user list-timers --all | grep qsys
```

### 日志

```
/home/liuming/.openclaw/logs/
├── sync_csi800_daily.log         # 数据同步
├── candidate-preopen.log         # 盘前流程
├── candidate-postclose.log       # 盘后流程
└── candidate-train.log           # 周训练
```

### 手动测试

```bash
# 数据同步
python scripts/ops/sync_csi800_daily.py --apply

# 盘前（dry-run）
python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date auto --debug-run --no-notify

# 盘后（dry-run）
python scripts/run_daily.py --strategy alpha_v1 --mode postclose --trade-date auto --debug-run --no-notify

# Batch 模式（与 systemd 相同）
python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date auto --debug-run --no-notify

# Telegram 通知测试
bash scripts/notify_telegram.sh "测试消息"
```

## 配置参考

`env.template` 提供 Telegram 凭证格式。安装步骤与 systemd 文件路径见上。

## 历史

PR #123 将 `deploy/systemd/` 从 legacy 入口（`run_preopen.sh` / `run_postclose.sh` / `run_alpha_v1_weekly_train.py`）切换为当前实际运行的 `run_daily_batch.py` 入口。旧 legacy 文件已从仓库移除（git 历史可追溯）。
