# ARCHITECTURE

本文档描述 Qsys 的**顶层系统设计、职责分层、代码骨架与不变量**。
具体功能细节统一下沉到 `docs/features/`。

---

## 1. 目标与设计原则

Qsys 当前不是纯研究仓库，也不是全自动实盘系统，而是一个正在收敛中的：

- 日常运营系统
- 量化投研系统
- 模型晋级与替换系统

因此顶层设计遵循以下原则：

- **研究、候选、生产分层**，避免互相污染
- **数据、训练、评估、运营分链路**，避免脚本一锅炖
- **默认 out-of-sample 评估**，禁止 train/test 混用自评
- **少入口、强约束、可回滚**，优先收敛脚本而不是继续长脚本
- **重要流程必须产出结构化结果**，而不是只看 stdout
- **文档、脚本、测试同步演进**，功能变更不能只改代码

---

## 2. 运行域划分

Qsys 按用途分为三层运行域：

### 2.1 Research

用途：
- 新因子
- 新模型
- 新策略
- 参数实验
- 研究性回测

特点：
- 可以快速试验
- 可以失败
- 可以有多版本并存
- **不能直接影响线上生产**

产物：
- 实验脚本
- 研究模型
- 对比报告
- 候选方案说明

### 2.2 Candidate

用途：
- 通过统一口径做严格评估
- 与当前 baseline / production 做对比
- 准备晋级到生产

特点：
- 必须有清晰评估窗口
- 必须有固定指标
- 必须可复现
- **不能绕过评估直接上线**

产物：
- candidate model artifact
- evaluation report
- promotion decision input

### 2.3 Production

用途：
- 周一到周五盘前/盘后运营
- 生成计划
- 同步账户
- 对账
- 影子组合跟踪

特点：
- 稳定优先
- 可观测优先
- 可审计优先
- 可回滚优先

产物：
- active production model
- daily plan
- account snapshot
- reconciliation result
- daily ops report

**硬规则：**
- production 不认“最新模型目录”
- production 只认**明确批准的模型版本 / manifest**
- research 产物不能直接进生产

---

## 3. 四条主链路

### 3.1 Data pipeline

职责：
- 数据抓取
- raw 存储
- qlib 转换
- universe 维护
- readiness / health check

当前代码骨架：
- `scripts/run_update.py`
- `scripts/update_data_all.py`
- `scripts/create_instrument_csi300.py`
- `qsys/data/collector.py`
- `qsys/data/adapter.py`
- `qsys/data/storage.py`
- `qsys/data/health.py`

核心产物：
- `data/raw/`
- `data/qlib_bin/`
- instruments / calendar / feature dump
- data status report

### 3.2 Research pipeline

职责：
- 特征定义
- 模型训练
- 因子/模型实验
- baseline 对比
- 严格评估

当前代码骨架：
- `scripts/run_train.py`
- `scripts/run_backtest.py`
- `scripts/run_strict_eval.py`
- `qsys/feature/library.py`
- `qsys/model/`
- `qsys/backtest.py`
- `qsys/strategy/`

核心产物：
- model artifact
- training summary
- backtest summary
- strict evaluation result

### 3.3 Promotion pipeline

职责：
- research -> candidate -> production 的晋级与替换
- 控制上线门槛与回滚边界

当前状态：
- **设计已明确，落地仍不足**
- 当前仍偏人工控制

目标代码骨架：
- `qsys/live/scheduler.py`
- 未来应补：
  - model registry / manifest
  - promotion checker
  - approval record

核心产物：
- approved candidate
- production manifest
- rollback target

### 3.4 Daily operation pipeline

职责：
- 盘前数据检查
- 计划生成
- 账户同步
- 盘后对账
- 影子组合跟踪

当前代码骨架：
- `scripts/run_daily_trading.py`
- `scripts/run_post_close.py`
- `qsys/live/manager.py`
- `qsys/live/account.py`
- `qsys/live/reconciliation.py`
- `qsys/live/simulation.py`

核心产物：
- `data/plan_*.csv`
- `data/real_sync_template_*.csv`
- reconciliation outputs
- daily operation logs

---

## 4. 统一对象模型

Qsys 顶层协作统一围绕以下对象：

### 4.1 Dataset version
- 含义：raw + qlib 的一次可用数据状态
- 由谁生成：data pipeline
- 被谁消费：training / backtest / daily plan

### 4.2 Universe version
- 含义：某个 universe（如 `csi300`）在某时间段的成分定义
- 由谁生成：universe/instrument pipeline
- 被谁消费：feature fetch / train / backtest / plan

### 4.3 Model artifact
- 含义：训练产物及其元信息
- 最低要求：
  - `model.pkl`
  - `meta.yaml`
  - `training_summary.csv`
- 被谁消费：backtest / daily ops / promotion

### 4.4 Evaluation report
- 含义：对某模型在固定口径下的评估结果
- 最低要求：
  - 时间窗口
  - feature set
  - baseline 对比
  - 收益/Sharpe/回撤/换手/费用
  - 是否允许晋级

### 4.5 Daily plan
- 含义：某个 signal date 对应的执行计划
- 最低要求：
  - `symbol`
  - `side`
  - `amount`
  - `price`
  - `weight`
  - `score` / `rank`

### 4.6 Account snapshot
- 含义：real / shadow 在某日收盘后的真实账户状态
- 被谁消费：下一日计划、对账、偏差分析

---

## 5. 代码骨架与模块职责

### 5.1 `scripts/` 入口层

职责：
- 只做 orchestration
- 参数解析
- 结果落盘
- 串联业务层

要求：
- 不承载复杂业务规则
- 新能力优先并入已有入口，不轻易新长脚本

当前建议保留的主入口：
- `run_update.py`
- `run_train.py`
- `run_backtest.py`
- `run_daily_trading.py`
- `run_post_close.py`
- `run_strict_eval.py`

### 5.2 `qsys/data`

职责：
- tushare/raw 抓取
- storage
- qlib conversion
- data health
- universe readiness

要求：
- 数据质量逻辑下沉到这里
- daily / train / backtest 不应各自重复写数据检查

### 5.3 `qsys/feature`

职责：
- 因子集合定义
- baseline / extended feature set
- 因子研究辅助

要求：
- 特征集合集中定义
- 避免脚本里各自拼 feature list

### 5.4 `qsys/model`

职责：
- 训练
- 保存 / 加载
- 预处理契约
- 推理

要求：
- 模型产物必须自描述
- inference 不依赖训练时进程内的临时对象

### 5.5 `qsys/strategy`

职责：
- score -> target weights / target positions
- top_k 约束
- 换仓逻辑

### 5.6 `qsys/backtest`

职责：
- 历史回测
- 日频收益与交易轨迹输出
- 统一评估接口

### 5.7 `qsys/live`

职责：
- 每日计划
- real/shadow 账户管理
- reconciliation
- scheduler

要求：
- 生产逻辑只在这里汇总，不散落到研究脚本

### 5.8 `tests/`

职责：
- 配置测试
- 数据契约测试
- 训练/推理契约测试
- CLI 语义测试
- 关键流程回归

要求：
- 新流程改动必须补最小回归

---

## 6. 当前架构不合理处

以下是当前已识别、后续要继续修的架构问题：

1. **research / candidate / production 仍未完全隔离**
2. **production model 选择仍偏弱，manifest 机制未完全落地**
3. **评估口径尚未完全产品化，仍有脚本漂移风险**
4. **run report 还没有形成统一结构化产物**
5. **daily ops 仍更像“能跑通”，还不像“强约束运营系统”**

---

## 7. 架构不变量

这些规则后续默认不轻易破坏：

- `scripts/` 只做编排，不做复杂业务核心
- 数据 readiness 是训练、回测、daily ops 的前置条件
- 模型产物必须包含可追溯元信息
- 默认采用 out-of-sample 评估
- 默认优先使用统一 feature set 定义，而不是脚本内临时拼接
- daily ops 只允许消费 production-approved model
- 新脚本增加前，先检查是否能并入已有入口
- 功能变更必须同步更新：代码、文档、测试

---

## 8. 与 RUNBOOK / ROADMAP 的关系

- `docs/RUNBOOK.md`：描述 daily / weekly / research 的实际操作流程
- `ROADMAP.md`：描述当前优先级与具体待办
- `docs/features/`：描述具体功能与实现细节

架构文档负责回答：
- 系统怎么分层
- 代码应该改哪里
- 哪些职责不能混
