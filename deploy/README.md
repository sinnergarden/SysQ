# SysQ 生产部署

## 定时任务（systemd timer）

所有定时任务通过 systemd user timer 管理，统一使用 `EnvironmentFile=/home/liuming/.openclaw/.env` 加载 Telegram 凭证。

### 当前 timer

| Timer | 时间(CST) | ExecStart | 说明 |
|-------|----------|-----------|------|
| `qsys-csi1800-pit-daily-sync.timer` | 交易日 19:00 | `scripts/data_sync.py --universe csi1800 --apply` | PIT CSI1800（CSI800+CSI1000）T 日同步；独立于推理 |
| `qsys-csi800-daily-sync.timer` | 交易日 19:00 | `scripts/data_sync.py --universe csi800 --apply`，随后 `financial_rc infer` | T 日同步 + T-1 两融回补 + Top200 |
| `qsys-candidate-preopen.timer` | 交易日 08:00 | `run_daily_batch.py --stage candidate --mode preopen --trade-date auto` | 盘前：信号→计划→shadow |
| `qsys-candidate-postclose.timer` | 交易日 21:00 | `run_daily_batch.py --stage candidate --mode postclose --trade-date auto` | 盘后：对账→归档→摘要 |
| `qsys-candidate-train.timer` | 周一 07:00 | `run_daily_batch.py --stage candidate --mode train` | 周模型训练 |

CSI1800 PIT service 固定运行于干净 runtime
`/home/liuming/.openclaw/workspace/SysQ-runtime`，只执行数据同步，不串联
`run_daily.py` 推理。service 通过 `QSYS_SETTINGS_FILE` 和 `QSYS_DATA_ROOT`
显式绑定现有生产配置与唯一数据 SOT，不依赖软链接。其他 service 文件使用
`--trade-date auto`（取机器本地当天日期，无需 shell 展开）。

Timer 默认会激活同名 service；CSI1800 PIT timer 还显式声明了
`Unit=qsys-csi1800-pit-daily-sync.service`，以便部署审计时能验证绑定关系。

### 实际运行验证

| Service | 最近成功运行 | 结果 |
|---------|------------|------|
| `qsys-candidate-preopen.service` | 2026-05-29 08:00 | ✅ exit=0 |
| `qsys-candidate-postclose.service` | 2026-05-29 21:00 | ✅ exit=0 |
| `qsys-csi800-daily-sync.service` | 2026-05-29 19:00 | ✅ exit=0 |
| `qsys-candidate-train.service` | next Mon 07:00 | ⏳ timer active; first scheduled success pending next Monday |

CSI1800 切换后，以 `qsys-csi1800-pit-daily-sync.service` 的 journal、日志和
`data/audit/sync_csi1800_YYYYMMDD.json` 为准；不要将旧 CSI800 service 的成功
状态当作 CSI1800 PIT 数据已更新的证据。

### 安装步骤

先 materialize 并验证固定 revision 的 clean detached runtime，再安装/启用
timer。部署脚本默认只做 preflight；只有显式传入 `--apply` 才会写入
systemd user units 并 enable timer，绝不会在脚本内启动完整同步。

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

# 2. 从明确 revision 创建/更新 runtime，并完成所有路径与 PIT-only 检查
python scripts/deploy_csi1800_pit_runtime.py \
  --revision "$(git rev-parse HEAD)"

# 3. 安装 service/timer、daemon-reload 并 enable timer（不启动同步）
python scripts/deploy_csi1800_pit_runtime.py \
  --revision "$(git rev-parse HEAD)" --apply

# 4. 其余兼容/候选 units 可按需复制到 user 目录
cp deploy/systemd/qsys-csi800-daily-sync.service ~/.config/systemd/user/
cp deploy/systemd/qsys-csi800-daily-sync.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-preopen.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-preopen.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-postclose.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-postclose.timer ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-train.service ~/.config/systemd/user/
cp deploy/systemd/qsys-candidate-train.timer ~/.config/systemd/user/
systemctl --user daemon-reload

# 5. 切换到 CSI1800 PIT timer（不要与旧 CSI800 daily timer 并行）
systemctl --user disable --now qsys-csi800-daily-sync.timer
systemctl --user stop qsys-csi800-daily-sync.service
systemctl --user show qsys-csi800-daily-sync.service -p ActiveState -p SubState
# 仅在旧 service 显示 ActiveState=inactive 后继续
systemctl --user enable --now qsys-csi1800-pit-daily-sync.timer

# 6. 启用其余 timer
systemctl --user enable --now qsys-candidate-preopen.timer
systemctl --user enable --now qsys-candidate-postclose.timer
systemctl --user enable --now qsys-candidate-train.timer

# 7. 确认状态
systemctl --user list-timers --all | grep qsys
```

### 日志

```
/home/liuming/.openclaw/logs/
├── sync_csi1800_pit_daily.log # PIT CSI1800 数据同步
├── sync_csi800_daily.log         # 数据同步
├── candidate-preopen.log         # 盘前流程
├── candidate-postclose.log       # 盘后流程
└── candidate-train.log           # 周训练
```

### 手动测试

```bash
# PIT CSI1800 数据同步（生产 service 使用此命令）
PYTHONPATH=/home/liuming/.openclaw/workspace/SysQ-runtime \
  /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python \
  /home/liuming/.openclaw/workspace/SysQ-runtime/scripts/data_sync.py \
  --universe csi1800 --apply

# 旧 CSI800 数据同步（仅兼容/回滚）
python scripts/data_sync.py --universe csi800 --apply
python scripts/run_daily.py --strategy financial_rc --mode infer --signal-date auto --top-k 200

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

将 `deploy/systemd/` 从 legacy 入口（`run_preopen.sh` / `run_postclose.sh` / `run_alpha_v1_weekly_train.py`）
同步为当前生产实际入口（`run_daily_batch.py --stage candidate`）。旧 legacy 文件已从仓库移除（git 历史可追溯）。
