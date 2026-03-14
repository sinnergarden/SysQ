# SysQ：系统化量化交易系统

**SysQ** 是一个基于微软 Qlib 构建的稳健、生产级量化交易系统。它旨在弥合研究与实盘交易之间的鸿沟，提供从数据处理、信号生成、回测到半自动执行的无缝工作流。

## 🎯 项目目标

1.  **研究与实盘一致**：同一套特征、模型、策略逻辑可同时用于回测与日常交易。
2.  **流程可复用可审计**：数据更新、模型训练、计划生成、账户落库有明确入口脚本和产物。
3.  **工程可维护**：核心 API 契约稳定，改动可通过测试和文档追踪。

完整目标定义请见 [PROJECT_TARGETS.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/PROJECT_TARGETS.md)。

## 🧰 开发环境定义

项目依赖、Python 版本、目录约定和测试命令请见 [ENVIRONMENT.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ENVIRONMENT.md)。

## 🏗 系统架构（四大序列）

SysQ 遵循严格的四阶段流水线：

### 序列 1：数据存储与管理
*   **目标**：构建统一、高性能的数据湖。
*   **核心**：`QlibAdapter` 将异构数据（CSV/Feather）转换为 Qlib 的二进制格式。
*   **数据流**：原始数据 (Feather) -> `QlibAdapter` -> Qlib 二进制 (Bin)。

### 序列 2：模型研究与训练
*   **目标**：使用标准接口训练预测模型。
*   **核心**：`IModel` 接口（LGBM 等）和 `FeatureCalculator`。
*   **输出**：可部署的模型产物（`model.pkl`, `meta.yaml`）存放在 `data/models/`。
*   **脚本**：`run_train.py`（训练并保存模型）。

### 序列 3：策略与回测引擎
*   **目标**：交易逻辑的真实模拟。
*   **核心**：
    *   `SignalGenerator`：基于模型产物的无状态预测。
    *   `StrategyEngine`：投资组合构建（TopK，权重分配）。
    *   `MatchEngine`：模拟 T+1、费用、涨跌停、停牌等机制。
*   **脚本**：`run_backtest.py`（每日滚动回测）。

### 序列 4：交易层（实盘/模拟）
*   **目标**：执行管理和账本一致性。
*   **核心**：
    *   `LiveManager`：协调每日工作流。
    *   `RealAccount`：基于 SQLite 的持久化状态追踪。
    *   `ShadowSimulator`：具有幂等性检查的模拟交易器。
    *   `ModelScheduler`：自动模型新鲜度检查与重训练。
*   **脚本**：`run_daily_trading.py`（日常操作的主入口）。

---

## 📂 目录结构

```text
SysQ/
├── config/             # 全局配置
├── data/               # 数据湖
│   ├── raw/            # 原始 Feather 文件
│   ├── qlib_bin/       # 编译后的 Qlib 二进制数据
│   ├── models/         # 训练好的模型产物
│   └── real_account.db # 账户状态 SQLite 数据库
├── experiments/        # 回测结果与日志
├── notebooks/          # 教程与分析 Notebooks
├── qsys/               # 核心代码包
│   ├── data/           # 数据适配器
│   ├── feature/        # 特征库 (Alpha158 等)
│   ├── live/           # 实盘交易组件 (Manager, Account, Scheduler)
│   ├── model/          # 模型库 (LGBM, MLP 等)
│   ├── strategy/       # 策略逻辑
│   ├── trader/         # 基础交易组件
│   └── analysis/       # 绩效分析报表
├── scripts/            # CLI 命令行入口脚本
├── tests/              # 单元与集成测试
└── docs/               # 项目文档与规则
```

---

## 🚀 快速开始

### 1. 环境设置
```bash
pip install -r requirements.txt
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

### 2. 数据准备
确保原始 feather 数据位于 `data/raw/daily/`。
用于测试目的，您可以生成模拟数据：
```bash
python scripts/mock_data.py
```

### 3. 模型训练
训练一个 LightGBM 基线模型。
```bash
python scripts/run_train.py --model qlib_lgbm --start 2023-01-01 --end 2026-02-28
```

### 4. 每日交易流程（模拟/实盘）
运行每日交易脚本。它会自动处理数据更新、模型重训练、模拟交易和计划生成。

```bash
# 标准运行
python scripts/run_daily_trading.py

# 小资金测试（例如 2万人民币，持仓2只股票）
python scripts/run_daily_trading.py --shadow_cash 20000 --top_k 2 --min_trade 2000
```

### 5. 回测（可选）
运行历史回测以验证策略表现。
```bash
python scripts/run_backtest.py
```

## 🧪 测试
运行测试套件以确保系统完整性。
```bash
python -m unittest discover tests
```

更多开发规范请参考：
- [CONTRIBUTING.md](file:///Users/liuming/Documents/trae_projects/SysQ/CONTRIBUTING.md)
- [PROJECT_RULES.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/rules/PROJECT_RULES.md)
- [DEV_WORKFLOW.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/manuals/DEV_WORKFLOW.md)
- [ARTIFACT_POLICY.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/rules/ARTIFACT_POLICY.md)
- [ENTRYPOINT_POLICY.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/manuals/ENTRYPOINT_POLICY.md)
- [FIRST_COMMIT_PLAN.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/manuals/FIRST_COMMIT_PLAN.md)
