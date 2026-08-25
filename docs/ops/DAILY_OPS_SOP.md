# DAILY_OPS_SOP

本文档是 daily ops 运行手册，不是架构文档。
系统设计、模块职责和过渡态见 `docs/ARCHITECTURE.md`，模块边界契约见 `docs/CONTRACTS.md`，产物路径规范见 `docs/REPO_LAYOUT.md`。

---

## 1. Purpose

当 daily ops 出问题时，根据本文档应该能回答：

- 今天应该从哪个入口跑；
- 当前实际 systemd 入口是什么；
- 目标唯一入口是什么；
- 每个阶段怎么检查；
- 失败先看哪里；
- 哪些命令可以安全执行；
- 哪些情况必须人工确认；
- 哪些步骤可以重跑，哪些不能随便重跑。

---

## 2. Current Entrypoints

### 2.1 Current systemd path

| 阶段 | Service | Timer | 调用链 |
|------|---------|-------|--------|
| Data sync + financial_rc | `qsys-csi800-daily-sync.service` | Mon-Fri 19:00 | `data_sync.py` 按目标日 PIT CSI1800 快照同步 T、补 T-1 两融，随后用完整 T-1 CSI800 策略快照生成供 T+1 使用的 Top200 |
| Preopen | `qsys-candidate-preopen.service` | Mon-Fri 08:00 | `run_daily_batch.py --stage candidate --mode preopen --trade-date auto` |
| Postclose | `qsys-candidate-postclose.service` | Mon-Fri 21:00 | `run_daily_batch.py --stage candidate --mode postclose --trade-date auto` |
| Weekly train | `qsys-candidate-train.service` | Mon 07:00 | `run_daily_batch.py --stage candidate --mode train` |

> 注意：production stage batch（`run_daily_batch.py --stage production`）需要显式 `--allow-production`，且不表示无人值守实盘。Broker submission 仍需人工确认。

**关键事实**：systemd 已从旧入口切换至 `run_daily_batch.py`。旧 legacy 入口（`run_preopen.sh`、`run_postclose.sh`、`run_alpha_v1_weekly_train.py`）不再被 systemd 调用。

### 2.2 Manual debug path

| 场景 | 命令 |
|------|------|
| Dry-run preopen (no side effects) | `python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date YYYY-MM-DD --debug-run --no-notify` |
| Dry-run batch preopen | `python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD --dry-run --debug-run --no-notify` |
| Dry-run postclose | `python scripts/run_daily.py --strategy alpha_v1 --mode postclose --trade-date YYYY-MM-DD --debug-run --no-notify` |
| Notify only (from existing artifacts) | `python scripts/run_daily.py --strategy alpha_v1 --notify-only --trade-date YYYY-MM-DD` |
| Retrain financial_rc research bundle | `python scripts/run_daily.py --strategy financial_rc --mode train --trade-date YYYY-MM-DD --no-notify` |

`financial_rc` train 会从实际存在且已成熟的标签反推固定交易日窗口，写入
content-addressed model bundle 和 exact training snapshot；它固定使用
`pointer_write_mode=none`，不会自动晋级或改写 shadow/prod pointer。历史训练仍使用
current CSI800 constituents snapshot，因此只能视为 research artifact，不能宣称 PIT OOS。

`--trade-date auto` 取机器本地当天日期（`datetime.now().strftime("%Y-%m-%d")`），用于避免 systemd ExecStart 中的 shell 展开 `$(date ...)`。交易日历感知是后续改进。
`--debug-run` 不修改 shadow/account.json / positions.csv / ledger.csv。`run_daily_batch.py` 另有 `--dry-run` 仅打印将要调度的策略而不执行。

systemd 传入的 `--output-root runs` 只控制 batch summary 位置。正式子进程仍使用
`experiments/{strategy_id}_daily/{trade_date}` 的 canonical run root；仅
`--debug-run` 可把子进程产物重定向到 output root。Batch 只调度
`shadow.yaml` 绑定的单一策略，并在子进程成功后独立验证
`daily_manifest.json`。
`--no-notify` 跳过 Telegram 通知（debug-run 默认行为，也可手动指定）。

### 2.3 CSI1800 data evidence runbook

正式 CSI1800 日同步使用 wrapper；显式 `target-date` 是要认证的已完成交易日，不能回退
到较早日期或把盘中未完成日期当作完成日：

```bash
python scripts/data_sync.py \
  --universe csi1800 \
  --target-date YYYY-MM-DD \
  --apply
```

wrapper 为整个 market child、history/margin/financial/shareholder repair、Qlib mutation
和最终 readiness 持有同一个 `data/audit/data_sync.lock`。出现
`data sync already holds writer lock` 时，先确认另一正式运行是否仍在执行；不要删除 lock
文件或绕过锁并发写。inner 只发布 source/field gates，只有 wrapper 在所有外层 repair 和
readiness 完成后拥有 terminal receipt 与 watermark 的最终写入权。

每次运行的不可变 receipt 位于
`data/audit/source_runs/{run_id}/receipt.json`，其中 `trust_state=trusted` 才能支持对应
source/field/range 水位；crash、partial、failed gate 和 legacy receipt 都是 untrusted。
supplier 原始响应位于
`data/raw/evidence/tushare/{endpoint}/{run_id}/{receipt_id}.parquet`，receipt 中的相对路径
和 SHA-256 是复核链接；不要编辑或覆盖。SQLite SOT 位于 `data/audit/audit.db`。

若 universe-history catch-up 实际开始写 canonical，当前 target-day source receipt 不能替
这段历史背书。运行会先写 `untrusted_outer_repair_scope`（planned symbols 是 collector
无 changed-set 返回时的保守上界），阻断水位并非零退出。即使 after-check 随后失败或
collector 只写了一部分，该 scope 仍保留；修复后必须重新执行完整 wrapper，产生新的
run_id 和证据链。纯 Qlib/registry rebuild 的 `canonical_mutated_symbols=[]`，不会借此阻断。

crash 恢复先检查 receipt 和 journal，确认不确定的 canonical 范围。仅在需要重新取得目标
日 supplier response 时，通过正式 wrapper 强制拉取：

```bash
python scripts/data_sync.py \
  --universe csi1800 \
  --target-date YYYY-MM-DD \
  --apply --force-fetch
```

`--force-fetch` 只在 applied CSI800/CSI1800 路径转发给使用 shared run-id 的 market child；
同一个 wrapper 继续持有 data-root writer lock，并在 outer repairs 与最终 readiness 完成后
才生成唯一 terminal receipt、推进对应 trusted watermark。不要单独运行 inner 来建立正式
恢复证据链。

水位未推进时，不要手工改 SQLite 或复制旧 receipt。先检查本 run 的 terminal gates 和事件：

```bash
jq '{run_id,trust_state,terminal_gates,audit_journal}' \
  data/audit/source_runs/<run_id>/receipt.json

sqlite3 data/audit/audit.db \
  "SELECT seq,event_type,payload_json FROM audit_journal WHERE run_id='<run_id>' ORDER BY seq;"

sqlite3 data/audit/audit.db \
  "SELECT source,field_name,scope_key,range_start,trusted_through,run_id FROM trusted_watermarks ORDER BY source,field_name,scope_key;"
```

按失败 gate 排查：`fetch/raw_payloads` 看 required daily/adj_factor/suspend_d receipt 与
requested scope；`canonical_commit` 看 mutation journal；`qlib_readback` 看 target rows；
`readiness` 看 coverage；`contiguous_range` 检查上一开市日水位。任何缺口都应通过新 run
补证，而不是用 min/max 吞 gap。


---

## 3. Daily Timeline

```
       Mon 07:00   Mon-Fri 08:00       Mon-Fri 19:00    Mon-Fri 21:00
         │             │                    │                │
  weekly train     preopen              data sync        postclose
  (qsys-candidate- (qsys-candidate-      (qsys-csi800-    (qsys-candidate-
   train.service)    preopen.service)      daily-sync.    postclose.service)
                                           service)
```

### 盘前顺序

```
data sync (T-1 21:30)
  → weekly train (Mon 07:00)
  → preopen (08:00)
```

---

## 4. Daily Ops Flow

```mermaid
flowchart TD
    A["Data Sync<br/>(T-1 21:30)"] --> B["Data Readiness Check"]
    B --> C{readiness?}
    C -->|ready| D["Approved Manifest<br/>+ Model Freshness"]
    C -->|degraded| D
    C -->|blocked| STOP["⚠ Blocked: stop + notify operator"]
    
    D --> E{model ok?}
    E -->|yes| F["Signal Generation"]
    E -->|stale| F
    E -->|no manifest| STOP
    
    F --> G["Plan / Order Intents"]
    G --> H{empty plan?}
    H -->|expected no-trade| NOTIFY["Report + notify"]
    H -->|abnormal| MANUAL["Operator review"]
    H -->|has orders| I["Execution Mode"]
    
    I --> I1["Shadow<br/>(auto sim)"]
    I --> I2["Production<br/>(manual confirm)"]
    I --> I3["Dry-run<br/>(debug only)"]
    
    I1 --> J["Postclose<br/>(15:30)"]
    I2 --> J
    I3 --> DEBUG["Debug output only"]
    
    J --> K["Fills + Close Prices"]
    K --> L["MTM"]
    L --> M["Reconciliation"]
    M --> N{recon ok?}
    N -->|shadow gap| GAP_SHADOW["Record gap + notify<br/>archive evidence"]
    GAP_SHADOW --> O
    N -->|production gap| GAP_PROD["⚠ Blocking: stop<br/>+ notify operator"]
    M -->|blocked| STOP2["⚠ Blocked: stop"]
    
    O --> P["Report + Notification"]
```

### 文本版（Mermaid 后备）

1. **Data Sync** (T 日 19:00) → 同步 T 日常规数据、补齐 T-1 两融 → 用完整 T-1 feature snapshot 生成供 T+1 使用的 Top200 → **Readiness Check**
2. **Ready/Degraded** → continue；**Blocked** → stop + notify operator
3. **Approved Manifest + Model Freshness** → Signal Generation
4. **Plan / Order Intents** → 检查是否空 plan
   - Expected no-trade → notify only
   - Abnormal empty → operator review
   - Has orders → 进入 execution mode（shadow / production manual / dry-run）
5. **Postclose** (15:30) → fills + close prices → MTM → reconciliation
   - Reconciliation matched → ledger commit
   - Gap in shadow mode → 记录 gap + notify，仍可归档
   - Gap in production/broker mode → **blocking，不得继续 production execution**
   - Blocked reconciliation → stop
6. **Report + Notification**

---

## 5. Preopen Runbook

### Inputs

- T-1 数据（data sync 结果）
- approved model manifest
- model artifact（来自 weekly train 或 manual refresh）
- 上一交易日账户状态（从 ledger 读取）
- strategy config

### Checks

按顺序执行：

| 检查 | 方法 | 阻断条件 |
|------|------|---------|
| Latest raw date | 检查 readiness | raw < T-1 则 blocked |
| Latest qlib date | 检查 readiness | qlib < T-1 则 blocked |
| Model freshness | 检查 approved manifest 中的 model_version | 无 manifest 阻断；model 过时降级 |
| Account state | `sqlite3 data/trade.db` 查询上一 snapshot | ledger 不可用尝试 legacy fallback |
| Signal generation | `run_daily.py` 自动完成 | 失败阻断 |
| Plan / order intents | `run_daily.py` 自动完成 | 空 plan 区分 expected vs abnormal |

### Expected outputs

```
daily/{date}/pre_open/signals/signal_basket_{data_date}.csv  ← 当前策略 adapter predictions archive
daily/{date}/pre_open/signals/                              ← 单策略信号
daily/{date}/pre_open/plans/
daily/{date}/pre_open/order_intents/
daily/{date}/pre_open/manifests/
```

### Failure handling

| Symptom | Severity | First check | Safe action | Must not do |
|---------|----------|-------------|-------------|-------------|
| Data blocked | blocking | `journalctl --user -u qsys-csi800-daily-sync.service` 检查 sync 日志 | 重跑 sync；仍失败则跳过 preopen | 生成假推荐 |
| No approved manifest | blocking | `ls daily/{date}/pre_open/manifests/` 或 model registry | 确认是否从 weekly train 生成 | 用"最新模型目录"替代 |
| Stale model | degraded | 检查 model_version 的 train_end 日期 | 只有 approved manifest 中存在 fallback model 时才降级使用，否则 blocking 或需人工确认 | 用 in-sample signal 当可交易 signal |
| Missing previous account state | blocking | `sqlite3 data/trade.db "SELECT * FROM snapshots ORDER BY snapshot_time DESC LIMIT 1;"` | 尝试 legacy fallback (`data/meta/real_account.db`) | 直接写 ledger |
| Signal generation failed | blocking | `journalctl --user -u qsys-candidate-preopen.service` | 检查 data readiness、model artifact | 跳过 signal 直接生成 plan |
| Empty plan | warning | 检查 plan JSON 中的 reason 字段 | expected no-trade → 正常通知；abnormal → 人工确认 | 视为失败阻断当天 |
| Order intent generation failed | blocking | 检查 signal 和 allocation 中间产物 | 修复后重跑 | 生成假 order intent |

**关于空 plan**：空 plan 不一定是失败。必须区分：
- **Expected no-trade**：条件不满足（如 signal 无有效信号、risk constraint 阻止），plan 中应包含 reason 字段。正常通知，记录 evidence。
- **Abnormal empty**：pipeline 提前退出，plan 文件不完整或缺失。需人工排查。

---

## 6. Postclose Runbook

### Inputs

- 当日 order intents（来自 preopen）
- Fills / execution report（来自 broker bridge 或 shadow simulation）
- 当日收盘价（来自 qlib）
- 上一交易日组合状态（来自 ledger）
- Broker snapshot（如有）

### Checks

按顺序执行：

| 检查 | 方法 | 阻断条件 |
|------|------|---------|
| Fills complete | 检查 fills 记录与 order intents 对比 | missing fill → 记录，继续 |
| Close price available | 检查 qlib 数据 | 缺失 → 跳过对应标的 MTM |
| MTM result | `run_daily.py` 自动计算 | 计算失败阻断 |
| Reconciliation result | `run_post_close.py` 自动计算 | blocked reconciliation 阻断 |
| Ledger commit | `run_daily.py` 自动提交 | 写入失败阻断 |
| Report generated | 检查 `daily/{date}/post_close/` 产物 | 可重跑 |

### Commit Boundary

- **postclose 是允许提交 execution / portfolio snapshot 的阶段**。
- Ledger 写入必须经过 `run_daily.py` → `DailyRunner` / `LedgerService` 的 controlled commit path。
- 策略 adapter 不允许直接写 ledger。
- preopen 阶段只能读 ledger，不能写。

### Failure handling

| Symptom | Severity | First check | Safe action | Must not do |
|---------|----------|-------------|-------------|-------------|
| Missing fills | warning | `daily/{date}/post_close/` 下检查 fills 文件 | 重新拉取 broker snapshot | 手动补填 fill 记录 |
| Missing close price | degraded | `daily/{date}/post_close/` 下检查 MTM | 跳过对应标的 | 用前日收盘价替代 |
| Ledger write failed | blocking | `journalctl --user -u qsys-candidate-postclose.service` | 检查 DB 锁或磁盘空间 | 直接写 `data/trade.db` |
| Reconciliation gap | varies by mode | 检查 `reconciliation_result` 输出 | shadow mode → 记录 gap、通知 operator、归档；production/broker mode → blocking，不得继续 | 自动修正 ledger |
| Broker snapshot unavailable | degraded | `ls -la /home/liuming/.openclaw/broker/` 检查同步文件 | 跳过 broker reconciliation，只做 shadow | 中断当天流程 |
| Report generation failed | non-blocking | 检查 report 产出路径 | 重新生成 | 阻塞 postclose |
| Notification failed | non-blocking | 检查通知脚本日志 | 单独重发通知 | 影响主流程 |

---

## 7. Failure Handling Flow

```mermaid
flowchart TD
    FAILURE["Failure occurs"] --> CLASSIFY{"Severity?"}
    
    CLASSIFY -->|non-blocking| CONTINUE["Continue pipeline<br/>log warning"]
    CLASSIFY -->|degraded| CONTINUE_DEGRADED["Continue pipeline<br/>report degradation"]
    CLASSIFY -->|blocking| STOP["⚠ STOP pipeline<br/>notify operator"]
    
    CONTINUE --> DOMAIN{domain}
    CONTINUE_DEGRADED --> DOMAIN
    
    DOMAIN -->|data| D1["Data readiness degraded<br/>skip dependent steps"]
    DOMAIN -->|model| M1["Model stale<br/>use approved fallback"]
    DOMAIN -->|signal-plan| S1["Empty plan or failed signal<br/>operator review"]
    DOMAIN -->|execution| E1["Partial fills / price missing<br/>log and continue"]
    DOMAIN -->|ledger| L1["⚠ Must stop<br/>manual investigation"]
    DOMAIN -->|reconciliation| R1["⚠ Must stop<br/>manual investigation"]
    
    L1 --> MANUAL["🔧 Human operator"]
    R1 --> MANUAL
    STOP --> MANUAL
    
    MANUAL --> RESOLVE["Resolve → verify → retry"]
```

### 文本版（Mermaid 后备）

**Non-blocking**：记录警告，流程继续（如 report gen failed、notification failed）。

**Degraded**：流程继续但必须记录降级原因（如 stale model、missing close price、broker snapshot unavailable）。

**Blocking**：流程停止，通知 operator。覆盖六类 domain：
- **Data**：data blocked → 重跑 sync 或跳过当天
- **Model**：no approved manifest → 确认训练是否完成
- **Signal/Plan**：empty plan 或 failed signal → 人工确认原因
- **Execution**：missing fills → 可尝试重新拉取，不影响 ledger
- **Ledger**：ledger write failed → **必须人工确认**，不自动重试
- **Reconciliation**：blocked reconciliation → **必须人工比对**，不自动修正；production/broker mode 下 gap 默认 blocking

---

## 8. Command Cheat Sheet

### 查看 systemd 状态

```bash
# 所有 SysQ 服务状态
systemctl --user list-units 'qsys-*'

# 单个服务状态
systemctl --user status qsys-candidate-preopen.service
systemctl --user status qsys-candidate-postclose.service
systemctl --user status qsys-csi800-daily-sync.service

# 查看最近一次运行日志
journalctl --user -u qsys-candidate-preopen.service -n 50 --no-pager
journalctl --user -u qsys-candidate-postclose.service -n 50 --no-pager
journalctl --user -u qsys-csi800-daily-sync.service -n 50 --no-pager

# 持续跟踪日志
journalctl --user -u qsys-candidate-preopen.service -f
```

### 查看当天产物

```bash
# 列出当天 preopen 产物
ls daily/YYYY-MM-DD/pre_open/signals/
ls daily/YYYY-MM-DD/pre_open/plans/
ls daily/YYYY-MM-DD/pre_open/order_intents/

# 列出当天 postclose 产物
ls daily/YYYY-MM-DD/post_close/

# 查看 daily ops digest（如有）
cat daily/YYYY-MM-DD/post_close/daily_ops_digest_*.json | python -m json.tool
```

### 检查数据 readiness

```bash
# 手动触发数据同步
python scripts/data_sync.py --universe csi1800 --apply
python scripts/run_daily.py --strategy financial_rc --mode infer --signal-date auto --top-k 200

# 数据健康检查（已实现）
python -c "from qsys.data.health import inspect_qlib_data_health; r = inspect_qlib_data_health('$(date +%Y-%m-%d)', ['\$open', '\$high', '\$low', '\$close', '\$volume', '\$factor'], universe='csi800'); print(r.to_markdown())"

# 快速检查 qlib 与 raw 对齐状态
python -c "from qsys.data.adapter import QlibAdapter; a = QlibAdapter(); print(a.get_data_status_report())"

# 检查最近 audit 记录
ls -t data/audit/ | head -3
python -c "import json; print(json.load(open('data/audit/' + sorted(__import__('os').listdir('data/audit/'))[-1])))"

# 实际 qlib bin 路径：data/qlib_bin/
```

CSI1800 同步不会把“今天的 1800 只”倒灌到历史。目标交易日 `T` 分别解析
`000906.SH` 与 `000852.SH` 在 `T` 当日或之前最新发布的 `index_weight` 月度快照，
要求严格 800 + 1000 且互不重叠，并写入不可覆盖的
`data/research/universes/csi1800_pit_daily/YYYYMMDD/`。每日 audit 必须记录两张源
快照日期、semantic hash、membership parquet hash 与 qlib registry hash；若同一
目标日重新解析出的集合不同则 fail closed，不能静默覆盖。

### 跑 preopen

```bash
# systemd 实际路径（batch 模式）
python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date auto

# 单策略 dry-run
python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date YYYY-MM-DD --debug-run --no-notify

# 单策略正式运行
python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date auto

# Batch dry-run
python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD --dry-run
```

### 跑 postclose

```bash
# systemd 实际路径（batch 模式）
python scripts/run_daily_batch.py --stage candidate --mode postclose --trade-date auto

# 单策略 dry-run
python scripts/run_daily.py --strategy alpha_v1 --mode postclose --trade-date YYYY-MM-DD --debug-run --no-notify

# 单策略正式运行
python scripts/run_daily.py --strategy alpha_v1 --mode postclose --trade-date auto
```

### 检查 ledger

```bash
# 查看 trade.db 表结构
sqlite3 data/trade.db ".tables"

# 按实际 schema 查询（表名以实际为准）
sqlite3 data/trade.db "SELECT * FROM snapshots ORDER BY snapshot_time DESC LIMIT 5;"
sqlite3 data/trade.db "SELECT * FROM fills ORDER BY execution_date DESC LIMIT 10;"
sqlite3 data/trade.db "SELECT * FROM positions ORDER BY execution_date DESC LIMIT 10;"
```

### 通知

```bash
# Telegram 测试
bash scripts/notify_telegram.sh "测试消息"

# 仅重新发送通知（不执行交易）
python scripts/run_daily.py --strategy alpha_v1 --notify-only --trade-date YYYY-MM-DD
```

### 产检检查（Checkers）

```bash
# 检查 order intents 字段完整性
python scripts/checks/check_order_intents.py --path daily/<date>/pre_open/order_intents/<file>.json

# 检查 postclose 对账结果（接受目录路径）
python scripts/checks/check_reconciliation_result.py --path daily/<date>/post_close/

# 检查 portfolio snapshot 完整性（消费 snapshot_index.json）
python scripts/checks/check_portfolio_snapshot.py --path daily/<date>/snapshot_index.json

# 检查 daily read model
python scripts/checks/check_daily_read_model.py --path daily/<date>/post_close/daily_ops_manifest.json

# 检查 signal schema
python scripts/checks/check_signal_schema.py --path daily/<date>/pre_open/signals/<file>.csv

# 检查实验索引（strict 模式）
python scripts/checks/check_experiment_index.py --path <experiment_dir> --strict

# 路径状态审计（只读，不写任何状态）
python scripts/ops/audit_state_paths.py
```

---

## 9. Safe Retry Rules

### Safe to retry

- Read-only check（检查 readiness、check ledger、check 产物）
- Report regeneration
- Notification resend
- Data readiness check
- Dry-run / debug-run

### Retry with caution

- Preopen plan regeneration（确认上一轮没有已下发订单）
- Shadow execution replay
- Postclose MTM recompute

### Do not retry without human confirmation

- **Ledger commit**：确认上一轮写入状态，避免 double commit
- **Broker order submission**：确认上一轮订单状态，避免重复下单
- **Reconciliation correction**：人工比对后再操作
- **Legacy state migration**：需确认 migration script 安全
- **Cash / position correction**：人工确认差异原因

---

## 10. Manual Recovery

| 场景 | 处理步骤 |
|------|---------|
| **Data blocked** | `journalctl --user -u qsys-csi800-daily-sync.service` 检查 sync 失败原因 → 修复（网络、quota、数据源）→ 手动重跑 `data_sync.py --universe csi800 --apply` → 仍失败则跳过当天候选 |
| **No approved manifest** | 检查 weekly train 是否成功 → 如未训练，手动触发：`python scripts/run_alpha_v1_weekly_train.py` 或 `python scripts/run_daily.py --strategy alpha_v1 --mode train` → 确认 manifest 生成 |
| **Stale model** | 若有 approved fallback model（通过 manifest / strategy config），通知 operator 确认是否使用 fallback；当前 `run_daily.py` 不提供 CLI fallback 参数，fallback 需通过配置或 manifest 选择 |
| **Empty plan** | 检查 plan 日志中的 reason → expected no-trade → 无需操作；abnormal → 检查 signal / readiness / manifest |
| **Reconciliation gap** | 只记录，不自动修正 → 人工比对 broker snapshot 和 ledger → 走 reconciliation 修正流程 |
| **Ledger mismatch** | 先备份 `data/trade.db` → 人工分析差异原因 → 走修正流程，不直接覆盖 |
| **Notification failure** | `bash scripts/notify_telegram.sh "消息内容"`，不影响 ledger |
| **Legacy state inconsistency** | 以 `data/trade.db` 为准，记录差异，不自动迁移 |

---

## 11. UI / Monitoring

Ops UI 应只读展示：

- Data readiness（ready / degraded / blocked）
- Latest data date
- Daily plan
- Order intents
- Shadow / production status
- Ledger snapshot
- Postclose report
- Reconciliation gap
- Blocking reason

**UI 不写 ledger，不下单，不改策略。**

Monitoring 告警触发点：

| 告警 | 触发条件 | 严重度 |
|------|---------|--------|
| Data blocked | readiness = blocked | blocking |
| Stale data | latest_raw_date < T-2 | degraded |
| Stale model | model 过时超过阈值 | degraded |
| Empty plan | preopen 无 plan 生成 | warning |
| Reconciliation gap | position_gap ≠ 0 或 cash_gap ≠ 0 | warning |
| Reconciliation blocked | status = blocked | blocking |
| Account stale | 超过 2 个交易日无 snapshot | degraded |
| Notification failure | notify call 返回非 0 | non-blocking |

---

## 12. Do Not

- **不无人值守下单**。自动 broker execution 必须经过人工确认。
- **不直接编辑 ledger**。所有写入必须通过 `run_daily.py` / `LedgerService`。
- **不删除 legacy state**。`data/meta/real_account.db` 和 `shadow/` 删除前必须完成 consumer 切换、数据迁移和回归验证。
- **不绕过 artifact contract**。所有 preopen / postclose 产物应符合 `docs/CONTRACTS.md`。
- **不把旧入口扩张成新长期接口**。`run_preopen.sh`、`run_postclose.sh`、`run_alpha_v1_daily.py` 是 DEPRECATED，不扩展新依赖。
- **不在 reconciliation gap 未处理时推进 production execution**；production/broker mode 下 gap 默认 blocking。
- **production stage batch 必须显式确认 `--allow-production`**，且不代表无人值守实盘。
- **不在 blocked 时生成假推荐或假 plan**。
