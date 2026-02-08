# 构建与迭代 Skill 的最佳流程

Skill 的开发是一个以失败为起点、评测为牵引，持续迭代优化的工程化过程。
评测并非事后的验证环节，而是 Skill 设计的前提；Skill 也非基于假设的规则集合，而是针对已暴露问题的最小化解决方案。

## 核心原则：评测驱动、失败优先

在构建量化系统（如 SysQ）时，我们遵循以下原则定义 Skill：

1.  **失败优先**：先思考“哪里会挂？”，再写“怎么跑通”。例如数据转换中最常见的是日历为空，模型训练中最常见的是特征名非法。
2.  **边界清晰**：每个 Skill 只解决一个具体问题（如“把 Feather 转为 Qlib Bin”），不贪大求全。
3.  **可回归**：必须有明确的 Output 用于验证成功与否（如“生成了 meta.yaml”或“生成了 plan.csv”）。

---

## Skill 1: 构建高可用数据管道 (Robust Data Pipeline)

**背景**：量化系统的基石是数据。原始数据（CSV/Feather）往往脏乱差，直接用于训练会导致静默失败（如索引越界、日历缺失）。

*   **触发条件 (When)**
    *   当接入新的数据源（如从 csv 切换到 parquet）时。
    *   当系统报错 `IndexError: index 0 is out of bounds` 或 `Calendar missing` 时。
    *   每日数据更新任务 (`run_update.py`)。

*   **执行逻辑 (How)**
    1.  **Schema 校验**：读取第一个文件，检查必要列（Open/Close/Vol/Date）是否存在，类型是否对齐。
    2.  **清洗与标准化**：
        *   填充 NaN（0 或 前值）。
        *   重命名列（`vol` -> `volume`）。
        *   **关键修正**：处理特殊字段名（如 `adj_factor` -> `factor`），确保下游 Qlib 能识别。
    3.  **原子化转换**：将文件逐个转换为标准 CSV，暂存于临时目录。
    4.  **二进制编译**：调用 `dump_bin` 生成 Qlib 格式，同时生成 `calendars`。
    5.  **完整性验证**：**必须**检查生成的 `calendars/day.txt` 文件大小 > 0，否则视为失败，触发全量回滚。

*   **执行约束 (Must Run)**
    *   修改 `qsys/data` 目录下代码时，必须运行 `python -m unittest tests/test_data_update_integration.py`。

*   **输出结果 (What)**
    *   `SysQ/data/qlib_bin/` 目录下包含完整的 `features` 和 `calendars`。
    *   日志显示 "Conversion Completed" 且无 Critical Error。

*   **预设失败策略**
    *   若日历为空：强制删除整个 `qlib_bin` 目录，重新全量生成（防止增量更新导致的索引错位）。
    *   若列名缺失：跳过该文件并记录 Warning，而不是中断整个流程。

---

## Skill 2: 模型训练与制品管理 (Model Artifact Management)

**背景**：训练不是跑完就完了。为了实盘能复现回测结果，必须把“模型权重”和“环境上下文”一起打包。

*   **触发条件 (When)**
    *   开发新因子集（如 Alpha101）或更换模型架构（LGBM -> NN）时。
    *   需要将策略部署到生产环境 (`run_plan.py`) 时。

*   **执行逻辑 (How)**
    1.  **接口标准化**：所有模型必须继承 `IModel`，实现 `fit` 和 `predict`。
    2.  **特征名清洗**：**关键步骤**。在送入 LightGBM/XGBoost 前，必须将含有特殊字符（`$`, `(`, `)`）的特征名映射为安全别名（`feat_0`），并保存映射表。
    3.  **上下文快照**：
        *   保存模型权重 (`model.pkl`)。
        *   保存特征配置 (`feature_config.json`)。
        *   保存预处理参数（如 Z-Score 的 Mean/Std）。
    4.  **元数据生成**：创建 `meta.yaml`，记录训练时间、数据范围、性能指标（IC/Sharpe）。

*   **执行约束 (Must Run)**
    *   修改 `qsys/feature` 或 `qsys/dataview` 或 `notebooks/tutorial.ipynb` 时，必须运行 `python -m unittest tests/test_basic_pipeline.py`。
    *   修改 `qsys/model` 或 `qsys/strategy` 或 `qsys/backtest` 时，必须运行 `python -m unittest tests/test_core_api_contracts.py`。

*   **输出结果 (What)**
    *   `SysQ/data/models/{model_name}/` 目录，包含自包含的部署包。

*   **预设失败策略**
    *   若特征名报错：捕获 LightGBM 的 `JSON characters` 错误，自动启用重命名逻辑。
    *   若 IC 为负：在 `meta.yaml` 中标记 `status: failed`，阻止该模型被自动加载到实盘。

---

## Skill 3: 事件驱动回测仿真 (Event-Driven Backtesting)

**背景**：向量化回测很快但不准。为了逼近实盘，必须构建包含“信号->订单->撮合->结算”的状态机。

*   **触发条件 (When)**
    *   模型验证通过，需要评估资金曲线和交易磨损时。
    *   验证新的风控规则（如“单票持仓限制”）时。

*   **执行逻辑 (How)**
    1.  **数据预取**：按日循环，获取 T 日的特征数据和行情数据（Open/Close/Limit/Suspend）。
    2.  **信号生成**：加载 Skill 2 的制品，进行无状态预测 (`SignalGenerator`)。
    3.  **策略映射**：将分数转换为目标仓位 (`Target Weights`)，应用 TopK 和软过滤（剔除停牌/涨停）。
    4.  **订单生成**：计算 `Target - Current` 的差额，**向下取整**到 100 股（整手逻辑）。
    5.  **撮合执行**：
        *   **硬约束检查**：T+1 可卖余额不足？资金不足？-> 拒单。
        *   **滑点与费用**：扣除佣金（双边）和印花税（卖方），应用滑点惩罚。
    6.  **日终结算**：更新账户 `Cash` 和 `Total Asset`，将今日买入转为明日可卖。

*   **输出结果 (What)**
    *   `backtest_result.csv`：包含每日净值、持仓数、换手率、费用明细。
    *   性能报告（Tearsheet）：Total Return, Sharpe, MaxDD。

*   **预设失败策略**
    *   若某日数据全空：记录 Error 但不中断循环，沿用昨日持仓（模拟停牌或数据缺失）。
    *   若资金不足：按比例缩减买单（Scale Down）或直接拒单。

---

## Skill 4: 实盘计划与对账 (Reality-First Trading)

**背景**：实盘中最大的敌人是“执行偏差”。系统必须能容忍“人手贱”或“外部干扰”，始终以券商的真实状态为准。

*   **触发条件 (When)**
    *   T 日 20:00（生成明日计划）。
    *   T+1 日 16:00（盘后对账）。

*   **执行逻辑 (How)**
    1.  **Reality Wins（现实优先）**：
        *   **读取券商文件**：解析 `holding.csv`，强制覆盖系统内部的持仓记录。哪怕系统没让你买，只要券商账户里有，系统就必须认。
    2.  **影子账本校准**：根据真实持仓重新计算 `Current Value`。
    3.  **差异计算 (Diff)**：`Target Position (Ideal)` - `Real Position (Broker)`。
    4.  **交易计划生成**：
        *   **Buffer 预留**：计算买入资金时预留 2% 防止废单。
        *   **最小交易额过滤**：忽略小于 5000 元的碎股变动。
        *   **排序**：先卖后买（确保资金回笼）。
    5.  **通知推送**：发送 Markdown 格式的计划表到企业微信。

*   **输出结果 (What)**
    *   `plan_{date}.csv`：可直接用于批量下单的指令表。
    *   `run_reconcile` 的对账日报：展示滑点损耗和执行偏差。

*   **预设失败策略**
    *   若券商文件缺失：**报警并停止**。绝不假设当前空仓，防止重复买入导致爆仓。
    *   若预测数据缺失：发送“今日无交易建议”通知，维持现有持仓不动。
