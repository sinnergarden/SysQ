# SysQ: Systematic Quantitative Trading System

**SysQ** is a robust, production-ready quantitative trading system built on top of Microsoft's Qlib. It is designed to bridge the gap between research and live trading, providing a seamless workflow from data processing to signal generation, backtesting, and semi-automated execution.

## 🏗 System Architecture (The 4 Sequences)

SysQ follows a strict 4-stage pipeline:

### Sequence 1: Data Storage & Management
*   **Goal**: Create a unified, high-performance data lake.
*   **Core**: `QlibAdapter` converts heterogeneous data (CSV/Feather) into Qlib's binary format.
*   **Scripts**: `run_update.py` (Daily data sync).

### Sequence 2: Model Research & Training
*   **Goal**: Train predictive models with standard interfaces.
*   **Core**: `IModel` interface (LGBM, etc.) and `FeatureCalculator`.
*   **Output**: Deployable artifacts (`model.pkl`, `meta.yaml`) in `SysQ/data/models/`.
*   **Scripts**: `run_train.py` (Train and save models).

### Sequence 3: Strategy & Backtest Engine
*   **Goal**: Realistic simulation of trading logic.
*   **Core**: 
    *   `SignalGenerator`: Stateless prediction from artifacts.
    *   `StrategyEngine`: Portfolio construction (TopK, Weighting).
    *   `MatchEngine`: T+1, Fees, Limits, Suspension simulation.
*   **Scripts**: `run_backtest.py` (Daily rolling backtest).

### Sequence 4: Trading Layer (Live/Paper)
*   **Goal**: Execution management and ledger consistency.
*   **Core**: 
    *   `PlanGenerator`: Generates actionable trade lists.
    *   `BrokerAdapter`: Parses real broker statements.
    *   `Notifier`: WeChat alerts.
*   **Scripts**: 
    *   `run_plan.py` (T+0 20:00: Generate Plan).
    *   `run_reconcile.py` (T+1 16:00: Daily Reconciliation).

---

## 📂 Directory Structure

```text
SysQ/
├── config/             # Global configurations
├── data/               # Data Lake
│   ├── raw/            # Original Feather files
│   ├── qlib_bin/       # Compiled Qlib Binary Data
│   ├── models/         # Trained Model Artifacts
│   └── meta.db         # Metadata (SQLite)
├── experiments/        # Backtest results & logs
├── notebooks/          # Tutorials & Analysis
├── qsys/               # Core Package
│   ├── data/           # Data Adapters
│   ├── feature/        # Feature Library (Alpha158, etc.)
│   ├── model/          # Model Zoo (LGBM, MLP, etc.)
│   ├── strategy/       # Strategy Logic
│   ├── trader/         # Live Trading Components
│   └── analysis/       # Performance Tearsheets
└── scripts/            # CLI Entry Points
```

---

## 🚀 Quick Start

### 1. Environment Setup
```bash
pip install qlib lightgbm pandas pyyaml tqdm
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

### 2. Data Preparation
Ensure your raw feather data is in `SysQ/data/raw/daily/`.
```bash
python3 SysQ/scripts/run_update.py
```

### 3. Model Training
Train a LightGBM baseline model on CSI300 universe (or all).
```bash
python3 SysQ/scripts/run_train.py --model lgbm_baseline --start 2020-01-01 --end 2022-01-01
```

### 4. Backtesting
Run a daily rolling backtest using the trained model.
```bash
python3 SysQ/scripts/run_backtest.py
```
Results will be saved to `SysQ/experiments/`.

### 5. Live Trading (Paper)
**Step A: Plan (T-Day 20:00)**
Generate tomorrow's trading plan based on your current broker holdings.
```bash
python3 SysQ/scripts/run_plan.py
```

**Step B: Reconcile (T+1 16:00)**
Export today's orders from broker and reconcile.
```bash
python3 SysQ/scripts/run_reconcile.py
```

---

## 💡 Best Practices for Scale

### Managing Multiple Experiments
When testing different strategies or model hyperparameters, avoid overwriting:
1.  **Naming Convention**: Use descriptive model names (e.g., `lgbm_alpha158_v1`, `mlp_highfreq_v2`).
2.  **Config Files**: Create separate YAML configs in `SysQ/config/` for different runs.
3.  **Output Isolation**: `run_backtest.py` supports saving to distinct folders.

### Feature Engineering
*   Always use `FeatureLibrary` to define feature sets.
*   Avoid "Lookahead Bias": Ensure features use only past data (e.g., `Ref($close, 1)` is forbidden).

### Trading Safety
*   **Reality Wins**: The system always trusts the Broker's position file over its own memory.
*   **Cash Buffer**: The `PlanGenerator` reserves 2% cash by default to prevent "Insufficient Funds" rejections.

