# Readiness 口径说明

## 1. 目的

统一 SysQ 对“能不能继续往下跑”的判断口径，避免把可告警问题误判成阻断，也避免把阻断问题伪装成 ready。

## 2. 总原则

SysQ 的 readiness 不只有一个布尔值，而是分层判断：

- 主链路是否可跑
- 扩展层是否健康
- 失败原因是否可解释

最终判断时，以**主链路优先**。

## 3. 分层定义

### 3.1 core_daily_status

这是盘前日常交易主链路的 readiness。

阻断条件包括：

- 无法解析 expected latest trading date
- qlib 最新日期早于预期日期
- 请求日无特征行
- 核心字段缺失过高
- 请求表达式无数据且 probe 字段有数据

解释：

- `core_daily_status = ok`：主链路可进入盘前
- `core_daily_status = blocked`：必须先修数据或特征，再继续

### 3.2 pit_status

针对 PIT 基本面层的覆盖情况。

口径：

- `ok`：请求到的 PIT 字段覆盖正常
- `partial` / `warning`：覆盖弱，但不直接阻断日常交易
- `not_requested`：本次特征集中未请求，不参与判断

### 3.3 margin_status

针对融资融券层的覆盖情况。

口径与 PIT 一致：

- 仅作告警层
- 不能掩盖主链路 stale 或缺数问题

## 4. 结构化字段

readiness 结果至少要包含：

- `raw_latest`
- `last_qlib_date`
- `expected_latest_date`
- `aligned`
- `feature_rows`
- `missing_ratio`
- `core_daily_status`
- `pit_status`
- `margin_status`
- `blocking_issues`
- `warnings`

## 5. 判定规则

### 可以继续

满足以下条件时，可认为盘前可继续：

- `report.ok == true`
- `blocking_issues` 为空
- `core_daily_status == ok`

### 需要人工复核但可继续

例如：

- `pit_status == warning`
- `margin_status == warning`

这类情况应该在报告中提示，但不自动阻断盘前。

### 必须阻断

例如：

- `last_qlib_date < expected_latest_date`
- `feature_rows == 0`
- `Required qlib columns unusable`

## 6. 对开发的约束

任何改动若影响以下内容，都必须补回归测试：

- `core_daily_status` 是否阻断
- `pit_status` / `margin_status` 是否仍保持非阻断分层
- 关键 blocking message 是否仍清晰可读

## 7. 人工接管口径

人工处理 readiness 异常时，不要只写“数据有问题”，至少要写清：

- 卡在哪一层
- 是否阻断主链路
- 是否可以先暂停扩展层要求
- 下一步由谁处理
