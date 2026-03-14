# SysQ 上下文与核心 API 契约

本文档定义了 SysQ 系统的 **不可变核心 API**。
这些 API 构成了系统的骨架。它们可以被扩展，但在没有全面的 RFC（征求意见稿）和迁移计划的情况下，**绝不允许**更改其签名和预期行为。

对 `qsys/` 中代码的任何修改都必须通过 `tests/test_core_api_contracts.py` 中定义的契约测试。

## 1. 交易账户（状态容器）
**类**：`qsys.trader.account.Account`
**职责**：管理现金、持仓和每日结算。

### 不可变 API
*   `__init__(self, init_cash: float)`
*   `total_assets` (属性) -> `float`：返回当前总资产（现金 + 市值）。
*   `positions` (属性) -> `Dict[str, Position]`：获取当前持仓。
*   `update_after_deal(self, symbol, amount, price, fee, side)`：在交易后更新状态。
*   `settlement(self)`：执行每日结算（例如 T+1 解锁）。
*   `get_metrics(self)` -> `Dict`：返回绩效指标（总回报率、最大回撤等）。

## 2. 策略引擎（决策者）
**类**：`qsys.strategy.engine.StrategyEngine`
**职责**：将原始信号/评分转换为目标投资组合权重。

### 不可变 API
*   `generate_target_weights(self, scores: pd.Series, market_status: pd.DataFrame) -> Dict[str, float]`
    *   **输入**：
        *   `scores`: 以标的 ID 为索引的 Series（例如 `600519.SH`），值为模型预测分。
        *   `market_status`: 包含市场状态的 DataFrame（例如 `is_suspended`, `is_limit_up`）。
    *   **输出**：标的 ID 到目标权重（0.0 - 1.0）的映射字典。

## 3. 特征库（数据配置）
**类**：`qsys.feature.library.SysAlpha`
**职责**：定义用于模型训练/推理的特征和标签。

### 不可变 API
*   `get_feature_config(self)` -> `Tuple[List, List]`：返回用于 Qlib 数据加载器的 `(fields, names)`。


## 4. 回测引擎（编排者）
**类**：`qsys.backtest.BacktestEngine`
**职责**：编排事件驱动的模拟循环。

### 不可变 API
*   `__init__(self, ...)`：必须接受 `account` 和 `daily_predictions`。
*   `run(self) -> pd.DataFrame`：执行回测并返回每日绩效历史。

## 5. 实盘账户（持久化状态）
**类**：`qsys.live.account.RealAccount`
**职责**：管理实盘交易账户的持久化状态（SQLite）。

### 不可变 API
*   `sync_broker_state(self, date, cash, positions, ..., account_name="default")`：从外部事实来源（券商）更新状态。
*   `get_state(self, date, account_name="default")`：获取指定日期的状态快照。

## 6. 实盘管理器（编排者）
**类**：`qsys.live.manager.LiveManager`
**职责**：协调每日交易的数据加载、模型推理和计划生成。

### 不可变 API
*   `run_daily_plan(self, date)`：执行指定日期的全流程管道并返回交易计划。

## 7. 影子模拟器（模拟交易）
**类**：`qsys.live.simulation.ShadowSimulator`
**职责**：在历史/实时市场数据上模拟交易计划的执行，以追踪理论表现。

### 不可变 API
*   `simulate_execution(self, plan_csv, date)`：在 T 日的市场数据上执行 T-1 日的计划。

## 8. 实验管理器（运行追踪）
**类**：`qsys.experiment.manager.ExperimentManager`
**职责**：管理实验配置、产物和排行榜。

### 不可变 API
*   `create_run(self, name, config, description)`：创建一个新的运行目录和配置文件。
*   `update_leaderboard(self, run_name, metrics)`：将结果追加到中央 CSV 文件。

---

## 开发准则
1.  **不要修改**上述方法的签名。
2.  **增加而非更改**：如果需要新功能，请添加新方法或可选参数（带默认值）。
3.  **测试优先**：在提交更改之前，运行 `python3 tests/test_core_api_contracts.py` 以确保合规性。
