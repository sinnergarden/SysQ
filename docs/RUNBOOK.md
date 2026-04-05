# SysQ 运行总手册（RUNBOOK）

本文档是 SysQ 的中文总运行手册。目标不是教人“背命令”，而是把日常运营、异常处理、目录边界、日期语义、产物契约写成一套**人也能独立执行、agent 改代码后也不能轻易破坏**的规则。

## 1. 适用范围

本手册覆盖：

- 盘前计划生成
- 数据链路检查与修复分流
- 模型日常运营与周级维护
- 收盘后真实账户回填与对账
- readiness 口径说明
- 开发侧必须遵守的 CLI / 报告 / 路径契约

不覆盖：

- 无关实验记录
- 临时研究草稿
- 运营账本细节

## 2. 文档入口

当前以这几份文档为准：

- `docs/RUNBOOK.md`：总手册，定义原则、边界、统一口径
- `docs/ops/PRE_OPEN_SOP.md`：盘前 SOP
- `docs/ops/DATA_PIPELINE_SOP.md`：数据链路 SOP
- `docs/ops/MODEL_OPS_SOP.md`：模型操作 SOP
- `docs/ops/POST_CLOSE_SOP.md`：收盘后 SOP
- `docs/ops/READINESS.md`：readiness 分层口径说明
- `docs/ops/FEATURE_PIPELINE_SOP.md`：特征链路 SOP，定义 raw / feature engineering / bin / 填充率审计 / 训练前检查
- `docs/features/feature_system.md`：Qsys 特征系统长期说明，定义 raw / feature engineering / bin / 研究与生产分层

旧文档处理原则：

- `docs/SOP_DAILY_OPS.md`、`docs/SOP_DAILY_OPS_v1.0.md` 保留作历史参考，不再作为当前执行依据
- 后续如与本手册冲突，以本手册及 `docs/ops/*.md` 为准

## 3. 目录约定

### 3.1 仓库内开发目录

这些路径属于 SysQ 仓库内可被代码直接管理的开发产物：

- `qsys/`：核心代码
- `scripts/`：标准 CLI 入口
- `tests/`：回归测试
- `docs/`：规范文档
- `data/models/`：模型产物
- `data/reports/`：结构化 JSON 报告
- `data/`：仓库内默认计划与模板产物

### 3.2 仓库外运营目录

真实运营时，建议把以下内容重定向到仓库外运营目录：

- 账户数据库：`--db_path`
- 盘前产物目录：`--output_dir`
- 报告目录：`--report_dir`
- 盘后对账输出目录：`run_post_close.py --output_dir`

原因：

- 运营产物与开发仓库分离
- 避免测试或 agent 改代码时覆盖真实运行数据
- 更容易审计与回滚

## 4. 核心状态定义

### 4.1 日期语义

必须固定以下含义：

- `signal_date`：信号来源日期，即用于生成计划的收盘日
- `execution_date`：计划执行日期，即下一交易日的人类/实盘执行日
- `plan_date`：当前与 `signal_date` 同义，用于产物命名

硬规则：

- 盘前推荐默认基于 **T-1 收盘**
- 若用户直接传未来交易日给 `run_daily_trading.py --date`，该日期视为 `execution_date`，脚本必须回退上一交易日作为 `signal_date`
- 不允许把 `signal_date` 与 `execution_date` 混写
- 盘后报告若读取盘前计划，应优先以计划里的 `signal_date` 为准

### 4.2 readiness 分层

SysQ 当前把 readiness 分为三层：

- `core_daily_status`：日常交易主链路，阻断级
- `pit_status`：基本面 PIT 层，告警级
- `margin_status`：融资融券层，告警级

解释见 `docs/ops/READINESS.md`。

### 4.3 运行状态

结构化报告统一使用：

- `success`
- `partial`
- `failed`
- `skipped`
- `pending`

建议理解：

- `success`：流程完成且契约满足
- `partial`：流程产出不完整或存在 blocker / 单侧空计划 / 对账异常
- `failed`：运行直接失败，无法继续
- `skipped`：因空计划或明确跳过而无实际执行

## 5. 标准输入输出契约

### 5.1 盘前 CLI：`scripts/run_daily_trading.py`

输入至少包括：

- `--date`
- 可选 `--execution_date`
- 可选 `--model_path`
- 可选 `--db_path`
- 可选 `--output_dir`
- 可选 `--report_dir`
- 可选 `--require_update_success`

输出必须至少包括：

- `plan_<signal_date>_shadow.csv`
- `plan_<signal_date>_real.csv`
- `real_sync_template_<signal_date>_shadow.csv`
- `real_sync_template_<signal_date>_real.csv`
- `order_intents_<execution_date>_shadow.json`
- `order_intents_<execution_date>_real.json`
- `daily_ops_pre_open_*.json`

计划 CSV 必须保留关键字段：

- `symbol`
- `side`
- `amount`
- `price`
- `weight`
- `score`
- `score_rank`
- `signal_date`
- `execution_date`
- `plan_role`
- `price_basis_date`
- `price_basis_field`
- `price_basis_label`

### 5.2 盘后 CLI：`scripts/run_post_close.py`

输入至少包括：

- `--date`
- `--real_sync`
- 可选 `--db_path`
- 可选 `--plan_dir`
- 可选 `--output_dir`
- 可选 `--report_dir`

输出必须至少包括：

- reconciliation 汇总产物
- 结构化 `daily_ops_post_close_*.json`
- 对账结果中的 real / shadow 差异摘要

### 5.3 order_intents 契约

`order_intents` 是 pre-open 到 broker bridge 的稳定输入，不应要求下游再解析 plan markdown 或 stdout。

必须至少包含：

- `artifact_type=order_intents`
- `signal_date`
- `execution_date`
- `account_name`
- `model_info`
- `assumptions`
- `intent_count`
- `intents[]`

每条 intent 至少包含：

- `intent_id`
- `symbol`
- `side`
- `amount`
- `price`
- `execution_bucket`
- `cash_dependency`
- `t1_rule`
- `price_basis`
- `status`

### 5.4 报告契约

所有关键流程都应能回答：

- 用了什么数据
- 用了哪个模型
- `signal_date` / `execution_date` 是什么
- 产物落在哪里
- blocker 是什么
- 下一步怎么做

### 5.4 长任务进度 / 日志契约

长任务默认采用“低噪音、结构化、对人有用”的表达方式：

- 只在阶段开始 / 完成 / 阻断时输出日志
- 单条日志优先写成 `event | key=value ...`，方便人看和后续机器解析
- 进度日志优先包含：阶段名、状态、关键日期、核心计数、产物路径
- 单笔交易、逐标的明细默认不刷屏；需要时进 report / artifact，不在主流程 stdout 里展开
- 失败时必须明确卡在哪个阶段，而不是只说“失败了”

对 daily ops / train / backtest / strict eval 的开发要求：

- stdout 只保留阶段摘要和关键异常
- 结构化 JSON report 承担完整留痕
- 若扩展层降级但主链路仍可跑，应明确标为 warning / partial，而不是制造噪音或误阻断

## 6. 成功标准

### 6.1 盘前成功

- 数据 readiness 通过
- 模型可加载
- real / shadow 都能生成计划，或空计划有明确原因
- 报告中日期语义与计划日期一致
- 产物路径与 CLI 指定目录一致

### 6.2 数据链路成功

- `raw_latest == last_qlib_date == expected_latest_date`
- 请求日有可用特征行
- 核心日线字段可用
- PIT / margin 异常只作为告警，不冒充主链路 ready

### 6.3 模型运营成功

- 生产模型可解析
- 训练/评估结果可追溯
- 替换生产模型前有明确对比和回滚点

### 6.4 盘后成功

- 真实账户现金/持仓/总资产已回填
- real trade log 可追溯
- real vs shadow 已对账
- 次日可以继续接续运行

## 7. 常见故障

### 7.1 数据不齐

表现：

- `last_qlib_date < expected_latest_date`
- `feature_rows == 0`
- `No probe rows available`

处理：

- 先走 `DATA_PIPELINE_SOP`
- 不允许带着 stale 数据继续盘前生成

### 7.2 计划产物写错目录

表现：

- 文档要求写运营目录，实际却落到 `SysQ/data/`
- 报告中的 artifact path 与实际 CLI 配置不一致

处理：

- 检查 `--db_path` / `--output_dir` / `--report_dir`
- 检查报告中的 artifact path 是否为绝对路径且指向指定目录

### 7.3 日期语义混乱

表现：

- `signal_date` 与 `execution_date` 被写成同一天
- 盘后对账把执行日误当信号日

处理：

- 先核对计划 CSV 中两个字段
- 再核对结构化报告中的两个字段
- 若不一致，视为开发缺陷，必须补测试再修

### 7.4 readiness 误用

表现：

- PIT / margin 缺失被当成主链路阻断
- 或核心日线 stale 却仍显示 ready

处理：

- 以 `core_daily_status` 为主链路阻断口径
- PIT / margin 仅为分层告警，不能掩盖核心 stale

## 8. 人工接管方式

当自动链路失败时，人工应按以下顺序接管：

1. 先确认数据是否 ready
2. 再确认模型是否可用
3. 再确认账户数据库与昨日状态是否连续
4. 最后决定是否继续生成计划或直接阻断

人工接管必须留下：

- 原因
- 接管人
- 接管时间
- 使用的输入文件/目录
- 是否恢复自动运行

## 9. 对开发的硬约束

后续任何 agent / 开发者改动以下内容时，必须同步更新测试或文档：

- `run_daily_trading.py` / `run_post_close.py` CLI 语义
- 关键报告字段
- `signal_date` / `execution_date` 契约
- `plan_*.csv` 与 `real_sync_template_*.csv` 命名与路径契约
- readiness 分层口径

最低要求：

- 现有回归测试必须继续通过
- 新增契约必须先写文档，再补最小测试

## 10. 建议执行顺序

- 盘前：看 `PRE_OPEN_SOP.md`
- 数据异常：看 `DATA_PIPELINE_SOP.md`
- 模型切换/重训：看 `MODEL_OPS_SOP.md`
- 收盘后：看 `POST_CLOSE_SOP.md`
- 不确定 readiness 判断：看 `READINESS.md`
