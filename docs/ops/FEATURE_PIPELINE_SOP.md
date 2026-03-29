# 特征链路 SOP

## 目标

确保 SysQ 的特征链路在进入训练或盘前推理前，满足：
- raw 已拉齐
- feature engineering 已完成
- bin 已生成
- 填充率、常数列、对齐口径已检查

## 标准流程

### Step 1: 拉 raw
- 先补齐原始行情、估值、资金流、两融、财报等 raw 数据
- raw 只负责原始信息，不混入派生增量逻辑

### Step 2: 做特征工程
- 基于 raw 统一生成组合特征
- 支持 float 特征与 sequence/list 特征
- 保留 feature registry / feature list 作为唯一权威表

### Step 3: 转 qlib/bin
- 先生成中间 CSV
- 再生成 qlib bin
- 确认 `qlib_bin/features/` 实际落地

### Step 4: 跑 readiness audit
- 推荐脚本：`scripts/run_feature_readiness_audit.py`
- 检查：
  - missing_ratio
  - 常数列
  - ready / warning / blocked 分类

### Step 5: 决定训练输入
- 只让 `ready` 特征直接进训练
- `warning` 特征单独评估
- `blocked` 特征先修，不进训练

## 通过标准

- 核心主行情特征接近 0 缺失
- 日频扩展特征不应大面积长期 100% 缺失
- PIT/行业特征允许稀疏，但必须可解释
- 不允许把假警或列名错位当成特征缺失

## 运维要求

- 日常更新后，若 feature 层有结构性变化，应重跑 readiness audit
- 训练前默认保留 audit 产物
- 模型产物应记录使用了哪些 feature / feature group
