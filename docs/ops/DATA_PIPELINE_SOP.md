# 数据链路 SOP

## 1. 目的

保证盘前链路使用的数据是最新、对齐、可解释的。数据链路是 SysQ 的第一阻断关。

## 2. 范围

覆盖：

- raw 数据更新
- qlib 对齐
- 请求日特征可用性
- readiness 分层判定

## 3. 输入

- 请求交易日 / `signal_date`
- feature config 或探针字段
- universe

## 4. 输出

- 数据状态摘要
- readiness 结论
- blocking issues / warnings
- 是否允许进入盘前计划生成

## 5. 状态定义

主状态：

- `raw_latest`
- `last_qlib_date`
- `expected_latest_date`
- `aligned`
- `feature_rows`
- `missing_ratio`

readiness 分层：

- `core_daily_status`：主链路，阻断级
- `pit_status`：PIT 基本面，告警级
- `margin_status`：融资融券，告警级

## 6. 标准流程

### Step A：刷新 raw -> qlib

目标：先闭合数据链路，再谈模型与计划。

### Step B：检查日期是否对齐

通过条件：

- `last_qlib_date == expected_latest_date`
- 若 `raw_latest` 存在，也应与 `last_qlib_date` 一致

### Step C：检查请求日是否有特征行

通过条件：

- `feature_rows > 0`
- 核心字段可用

### Step D：区分阻断与告警

- 核心日线字段不可用：阻断
- PIT / margin 覆盖弱：告警，不直接阻断日常交易

## 7. 成功标准

- 盘前请求日可用
- 主链路 ready
- 分层状态可解释
- 失败时能明确指出是 stale、字段损坏还是表达式问题

## 8. 常见故障

### stale data

表现：`Qlib data is stale`

处理：重新刷新 raw / qlib，检查增量转换。

### probe 有数据但表达式无数据

表现：探针字段存在，但请求表达式 `features.empty`

处理：排查特征表达式依赖、字段命名或转换结果。

### 核心字段缺失过高

表现：`Required qlib columns unusable`

处理：视为阻断，不能生成盘前计划。

## 9. 人工接管

人工接管时应记录：

- 请求日
- 期望最新日期
- 实际 raw / qlib 最新日期
- 阻断字段
- 是否允许先暂停日常交易链路
