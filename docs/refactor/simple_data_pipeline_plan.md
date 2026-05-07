# Simple Data Pipeline Refactor Plan

## Goal

把数据链路收敛回最小主线：

`raw -> qlib_bin -> validate -> daily shadow / weekly retrain`

原则：

- raw 是唯一可信源头
- qlib 是可重建产物
- qlib 坏了就从 raw 重建，不再维护复杂 qlib 内部修补状态
- 减少 candidate / switch / rollback / 过细 audit / 临时 PR4X 脚本

---

## 1. 当前必须保留的文件

### 数据主链路

- `qsys/data/collector.py`
- `qsys/data/storage.py`
- `qsys/data/adapter.py`
- `qsys/ops/raw_sync.py`
- `qsys/ops/qlib_sync.py`
- `qsys/ops/data_coverage.py`
- `qsys/ops/instrument_coverage.py`
- `qsys/ops/trade_date.py`
- `qsys/ops/universe_sync.py`（如仍承担基础 instrument 写入；后续可并回 build_qlib）

### shadow / retrain / execution 主链路

- `qsys/ops/shadow_presync.py`
- `qsys/ops/inference.py`
- `qsys/ops/shadow_rebalance.py`
- `qsys/ops/training.py`
- `qsys/ops/model_registry.py`
- `qsys/ops/manifest.py`
- `qsys/ops/state.py`
- `qsys/ops/digest.py`（如果只保留简洁汇总）
- `qsys/ops/telegram.py`（仅保留简洁通知能力，不保留复杂 gateway）

### 入口脚本（现状下仍应保留，后续逐步收敛）

- `scripts/ops/run_shadow_presync.py`
- `scripts/ops/run_shadow_daily.py`
- `scripts/ops/run_shadow_retrain_weekly.py`
- `scripts/ops/audit_inference_coverage.py`（短期保留为 smoke 工具，后续可并入 validate）

### 文档 / 契约

- `docs/ops/DATA_PIPELINE_SOP.md`
- `docs/ops/PRE_OPEN_SOP.md`
- `docs/ops/MODEL_OPS_SOP.md`
- `docs/features/shadow_daily_ops.md`

---

## 2. 建议删除 / 归档的文件

> 这轮先列清单，不马上硬删。建议迁到 `archive/pr4x/` 或 `archive/ops_experimental/`。

### candidate / switch / rollback 相关

- `qsys/ops/qlib_candidate.py`
- `qsys/ops/candidate_coverage_gap.py`
- `scripts/ops/build_qlib_candidate.py`
- `scripts/ops/validate_qlib_candidate.py`
- `scripts/ops/switch_qlib_candidate.py`
- `tests/test_qlib_candidate_build.py`
- `tests/test_qlib_candidate_validation.py`
- `tests/test_candidate_coverage_gap_audit.py`

### selected-symbol / incremental qlib 修补类

- `scripts/ops/repair_qlib_instrument_coverage.py`
- `scripts/ops/run_full_universe_backfill.py`
- `scripts/ops/run_raw_full_update.py`
- `qsys/ops/full_universe_backfill.py`

### 过细 ops diagnostics / PR4X 临时审计

- `scripts/ops/audit_candidate_coverage_gap.py`
- `scripts/ops/audit_qlib_instrument_coverage.py`
- `scripts/ops/audit_raw_to_qlib_coverage.py`
- `scripts/ops/audit_feature_readiness.py`（若功能已被日常 presync 覆盖）
- `scripts/check_amount.py`
- `scripts/debug_data_quality.py`
- `scripts/debug_model_performance.py`
- `scripts/rebuild_qlib_bin.py`
- `scripts/dump_bin.py`
- `scripts/update_data_all.py`
- `scripts/run_update.py`

### 通知 / 网关复杂化部分

- `scripts/ops/run_telegram_gateway.py`
- `qsys/ops/notification.py`（WeCom）
- 复杂 Telegram command gateway 相关逻辑（若在 `qsys/ops/telegram.py` 中，应切分只留 send-notify）

### 可明显瘦身的测试

- `tests/test_inference_coverage_audit.py`
- `tests/test_feature_readiness_audit.py`
- `tests/test_ops_manifest.py`（仅保留最小 manifest 契约）
- `tests/test_shadow_ops_digest.py`
- `tests/test_shadow_ops_notification.py`
- `tests/test_shadow_ops_status.py`
- `tests/test_telegram_ops.py`（只保留简洁通知测试）
- 所有围绕 candidate / switch / rollback / 细粒度 audit contract 的测试

---

## 3. 新的最小数据链路设计

### 3.1 设计目标

只保留三步：

1. `sync_raw`
2. `build_qlib`
3. `validate_qlib`

数据语义：

- raw：唯一长期存储真源
- qlib_bin：纯构建产物，可覆盖重建
- validate：只判断“这个 qlib_bin 能否支持当前 universe / 日期范围 / 核心字段”

### 3.2 新主流程

#### Step A: sync raw

输入：

- universe
- start_date
- end_date

行为：

- 从 Tushare / 当前 raw provider 拉取并落盘到 `data/raw`
- 只负责 raw 完整性，不负责 qlib 修补

输出：

- raw 最新日期
- symbol 数量
- 更新成功/失败摘要

#### Step B: build qlib

输入：

- raw
- universe
- start_date
- end_date
- output qlib dir

行为：

- 从 raw 全量或窗口式重建 `qlib_bin`
- 同步 `calendars/`、`instruments/`、`features/`
- 不做 selected-symbol refresh，不做 candidate 切换，不做 patch-up

输出：

- qlib 目录
- build summary

#### Step C: validate qlib

输入：

- qlib_dir
- universe
- start_date
- end_date

行为：

- 检查 calendar first/last
- 检查 instrument 数量与 active 数量
- 检查 6 个核心字段：
  - `$open`
  - `$high`
  - `$low`
  - `$close`
  - `$volume`
  - `$amount`
- 输出统一 coverage / latest-date / duplicate / future-date 结果
- 只给出 `Go / No-Go`

输出：

- validation summary
- symbol coverage summary（可简化）
- failed symbols（只在必要时输出）

### 3.3 明确删掉的设计

- selected-symbol qlib refresh
- qlib candidate build
- candidate validation / denominator 双口径
- qlib switch / rollback
- 复杂 post-switch 审计树
- “修 qlib 内部状态直到过关”的思路

---

## 4. 新的脚本入口

目标收敛为：

```bash
python scripts/data/sync_raw.py --universe csi800 --start-date 2025-01-01 --end-date 2026-04-30
python scripts/data/build_qlib.py --universe csi800 --start-date 2025-01-01 --end-date 2026-04-30 --output data/qlib_bin
python scripts/data/validate_qlib.py --qlib-dir data/qlib_bin --universe csi800 --start-date 2025-01-01 --end-date 2026-04-30
```

### 入口与内部模块映射建议

- `scripts/data/sync_raw.py`
  - 调 `qsys.ops.raw_sync` 的最小公共接口
- `scripts/data/build_qlib.py`
  - 调 `qsys.ops.qlib_sync` 的全量构建接口
  - 可吸收 `universe_sync` 的最小 instrument 生成逻辑
- `scripts/data/validate_qlib.py`
  - 调 `qsys.ops.data_coverage` + `qsys.ops.instrument_coverage`
  - 返回单一 summary

### 目录建议

新增：

- `scripts/data/`

保留：

- `scripts/ops/run_shadow_daily.py`
- `scripts/ops/run_shadow_presync.py`
- `scripts/ops/run_shadow_retrain_weekly.py`

含义变成：

- `scripts/data/*` 负责数据层
- `scripts/ops/*` 负责运行层

---

## 5. 哪些测试保留，哪些测试删除

### 保留

#### 数据层核心测试

- `tests/test_raw_sync.py`
- `tests/test_qlib_sync.py`
- `tests/test_adapter_coverage.py`
- `tests/test_data_contract_ready.py`
- `tests/test_data_health.py`
- `tests/test_data_quality.py`
- `tests/test_qlib_instrument_coverage.py`
- `tests/test_raw_to_qlib_coverage.py`（可压缩为更少、更核心 case）

#### shadow / retrain 主链路测试

- `tests/test_shadow_presync.py`
- `tests/test_shadow_daily_inference.py`
- `tests/test_shadow_daily_rebalance.py`
- `tests/test_workflow_preopen.py`
- `tests/test_train_bundle_snapshot.py`
- `tests/test_production_manifest.py`

#### Telegram 简洁通知测试

- 保留 `tests/test_telegram_ops.py` 的最小发送/摘要测试

### 删除 / 归档

#### candidate / switch / rollback 类

- `tests/test_qlib_candidate_build.py`
- `tests/test_qlib_candidate_validation.py`
- `tests/test_candidate_coverage_gap_audit.py`

#### 过细 audit / artifact contract 类

- `tests/test_inference_coverage_audit.py`
- `tests/test_feature_readiness_audit.py`
- 任何只验证临时 CSV/JSON artifact 字段名的测试

#### 通知 / gateway 复杂逻辑类

- `tests/test_shadow_ops_notification.py`（若仅剩 Telegram 简洁通知，则重写为更小测试）
- `tests/test_shadow_ops_digest.py`
- `tests/test_shadow_ops_status.py`
- 复杂 Telegram command gateway 相关测试

### 瘦身原则

- 优先保留“业务链路正确性”测试
- 删除“临时脚本字段契约”测试
- 删除“某次 PR 调试过程产物”测试
- 每个入口保留 1~3 个核心集成测试即可

---

## 6. 迁移步骤

### Phase 1：冻结扩展

- 停止继续增加 candidate / switch / rollback / diagnostics 逻辑
- 不再新增 PR4X 临时脚本

### Phase 2：新增最小入口

- 新建 `scripts/data/sync_raw.py`
- 新建 `scripts/data/build_qlib.py`
- 新建 `scripts/data/validate_qlib.py`
- 先复用现有 `qsys.ops.raw_sync / qlib_sync / data_coverage / instrument_coverage`

### Phase 3：把 daily / retrain 改成依赖统一 validate 结果

- `run_shadow_presync.py` 不再依赖多套 audit 脚本
- 只读取统一 validate summary
- daily / weekly 只关心：
  - qlib latest 是否对齐
  - 核心字段是否可用
  - universe 是否够用

### Phase 4：归档复杂工具

先迁到 `archive/`：

- candidate / switch / rollback
- selected refresh
- 细粒度 audit
- WeCom / Telegram gateway 复杂入口

确认无调用后再删。

### Phase 5：测试瘦身

- 删除 candidate / switch / rollback tests
- 合并 coverage tests
- 合并 shadow ops artifact tests

### Phase 6：文档收口

- 更新 `docs/SCRIPT_INVENTORY.md`
- 更新 `docs/ops/DATA_PIPELINE_SOP.md`
- 新增最小 runbook：`raw -> qlib -> validate -> daily`

---

## 7. 是否需要回滚某些 PR

不建议 git 历史级回滚整批 PR。

原因：

- 风险高，容易把仍有价值的修复一并抹掉
- 现在更适合“保留底层稳定模块 + 归档上层复杂入口”

建议策略：

- 不回滚底层 raw / qlib / shadow / training 基础修复
- 归档 PR4R / PR4S / PR4T 引入的 candidate / switch / audit 工具层
- 用新 `scripts/data/*` 入口重新定义主链路

换句话说：

- **回收复杂入口，不回滚有效底层修复**

---

## 8. 最小实现顺序建议

1. 新建 `scripts/data/sync_raw.py`
2. 新建 `scripts/data/build_qlib.py`
3. 新建 `scripts/data/validate_qlib.py`
4. 让 `run_shadow_presync.py` 只依赖统一 validate summary
5. 把 candidate / switch / rollback 相关脚本移动到 `archive/`
6. 删除对应 tests
7. 更新 docs / runbook

这个顺序最稳，因为：

- 先建立新入口
- 再替换 daily 依赖
- 最后清理旧复杂工具
