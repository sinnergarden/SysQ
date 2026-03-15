# ARCHITECTURE

本文档是项目的**架构北极星**，旨在定义核心模块的边界、职责与协作方式，防止系统随时间腐化。

## 1. 架构风格：模块化单体 (Modular Monolith)

我们采用模块化单体架构。这意味着：
- **物理上**：所有代码在一个 Git 仓库，作为一个 Python 包 (`qsys`) 发布。
- **逻辑上**：划分为边界清晰的领域模块（Domain Modules），模块间通过明确的 API 交互。

## 2. 领域上下文地图 (Context Map)

系统划分为以下核心领域（Domain）：

| 领域 (Domain) | 职责 (Responsibility) | 核心模块 (`qsys/*`) | 关键产物 |
| :--- | :--- | :--- | :--- |
| **Data (基础设施)** | 数据清洗、存储、适配 | `data`, `feature` | Qlib Bin, Feature Config |
| **Model (预测)** | 训练模型、推理信号 | `model` | Model Artifacts (`.pkl`, `meta.yaml`) |
| **Strategy (决策)** | 信号 -> 目标持仓 (Target Pos) | `strategy` | Target Weights (`dict`) |
| **Trading (执行)** | 目标持仓 -> 订单 -> 账户更新 | `trader`, `live` | Trading Plan (`.csv`), Ledger (`.db`) |
| **Analysis (反馈)** | 绩效归因、回测报告 | `backtest`, `analysis` | Tearsheet, Metrics |

### 依赖原则 (Dependency Rules)

- **上层依赖下层**：`Trading` 依赖 `Strategy`，`Strategy` 依赖 `Model`，`Model` 依赖 `Data`。
- **禁止反向依赖**：`Data` 绝不依赖 `Strategy`。
- **禁止循环依赖**：如发现 A->B->A，必须提取公共层或使用事件解耦。

## 3. 核心业务流程 (Core Workflows)

### 3.1. 模型生产流 (Model Production)
**目标**：从数据中提炼出可预测未来的模型产物。

```mermaid
graph LR
    RawData[Raw Feather] -->|qsys.data| QlibBin[Qlib Bin]
    QlibBin -->|qsys.feature| Dataset[Features & Labels]
    Dataset -->|qsys.model| Trainer[Model Trainer]
    Trainer -->|fit| Artifact[Artifact (Model + Meta)]
```

### 3.2. 每日交易流 (Daily Trading Loop)
**目标**：根据最新数据和模型，生成明日交易计划。

```mermaid
graph TD
    Update[Data Update] -->|qsys.data| LatestData
    LatestData -->|qsys.model.predict| Signal[Pred Score]
    Signal -->|qsys.strategy| TargetPos[Target Position]
    TargetPos + CurrentPos[Account State] -->|qsys.trader| Diff[Calc Diff]
    Diff -->|Constraints| Plan[Trading Plan (.csv)]
```

### 3.3. 回测验证流 (Backtest Simulation)
**目标**：在历史数据上模拟交易流，评估策略有效性。

*复用逻辑*：直接复用 `qsys.model` 和 `qsys.strategy`，仅将 `qsys.trader` 替换为 `qsys.backtest` 中的虚拟撮合逻辑。

## 4. 目录结构与职责 (Code Layout)

```text
SysQ/
├── qsys/                   # [Core] 核心业务逻辑
│   ├── config/             # 全局配置管理 (不含业务规则)
│   ├── data/               # [Domain: Data] 数据接入与适配
│   ├── feature/            # [Domain: Data] 特征定义与计算
│   ├── model/              # [Domain: Model] 模型生命周期管理
│   ├── strategy/           # [Domain: Strategy] 仓位管理与选股逻辑
│   ├── trader/             # [Domain: Trading] 订单生成与实盘风控
│   ├── live/               # [Domain: Trading] 实盘编排与持久化
│   ├── backtest/           # [Domain: Analysis] 回测引擎
│   └── analysis/           # [Domain: Analysis] 绩效指标计算
├── scripts/                # [Entrypoint] 命令行入口 (仅编排，无业务)
├── tests/                  # [Verify] 测试用例
└── docs/                   # [Doc] 文档与决策记录
```

## 5. 关键设计决策 (Key Decisions)

- **配置中心化**：所有模块通过 `qsys.config.manager` 获取配置，禁止模块私自读取文件。
- **模型即产物**：模型训练后必须序列化为“自包含”的产物目录（含预处理参数、元数据），实盘仅加载产物。
- **策略无状态**：`Strategy` 模块应设计为纯函数（Input: Signal + Context -> Output: Weights），状态由 `Account` 管理。
- **现实优先 (Reality First)**：实盘交易中，永远以券商查询到的持仓为准，本地账本仅作校对和模拟。

## 6. 扩展指南 (Extension Guide)

### 如果你要添加新数据源...
1. 在 `qsys/data/adapter.py` 实现适配器。
2. 确保输出格式符合 Qlib 标准。
3. **勿** 修改模型或策略代码。

### 如果你要添加新因子...
1. 在 `qsys/feature/library.py` 定义新因子。
2. 在 `tests/` 添加计算逻辑测试。
3. 重新训练模型生成新产物。

### 如果你要修改下单规则...
1. 修改 `qsys/trader/plan.py` 或 `qsys/strategy`。
2. 必须运行 `tests/test_live_trading.py` 确保不破坏实盘逻辑。
