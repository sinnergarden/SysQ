# SysQ Context & Core API Contracts

This document defines the **Immutable Core APIs** of the SysQ system. 
These APIs represent the backbone of the system. They can be extended, but their signatures and expected behaviors **MUST NOT** be changed without a comprehensive RFC (Request For Comment) and migration plan.

Any modification to code in `qsys/` must pass the contract tests defined in `tests/test_core_api_contracts.py`.

## 1. Trading Account (State Container)
**Class**: `qsys.trader.account.Account`
**Responsibility**: Manages cash, positions, and daily settlement.

### Immutable APIs
*   `__init__(self, init_cash: float)`
*   `total_assets` (property) -> `float`: Returns current total assets (Cash + Market Value).
*   `positions` (property/attribute) -> `Dict[str, Position]`: Access to current holdings.
*   `update_after_deal(self, symbol, amount, price, fee, side)`: Updates state after a trade.
*   `settlement(self)`: Performs daily settlement (e.g., T+1 unlock).
*   `get_metrics(self)` -> `Dict`: Returns performance metrics (Total Return, MaxDD).

## 2. Strategy Engine (Decision Maker)
**Class**: `qsys.strategy.engine.StrategyEngine`
**Responsibility**: Converts raw signals/scores into target portfolio weights.

### Immutable APIs
*   `generate_target_weights(self, scores: pd.Series, market_status: pd.DataFrame) -> Dict[str, float]`
    *   **Input**:
        *   `scores`: Series indexed by Instrument ID (e.g., `600519.SH`), values are model predictions.
        *   `market_status`: DataFrame containing market state (e.g., `is_suspended`, `is_limit_up`).
    *   **Output**: Dictionary mapping Instrument ID to target weight (0.0 - 1.0).

## 3. Feature Library (Data Configuration)
**Class**: `qsys.feature.library.FeatureLibrary` (Base Class)
**Responsibility**: Defines the features and labels for model training/inference.

### Immutable APIs
*   `get_feature_config(self)` -> `Tuple[List, List]`: Returns `(fields, names)` for Qlib data loader.
*   `get_label_config(self)` -> `Tuple[List, List]`: Returns `(fields, names)` for label data.

## 4. Backtest Engine (Orchestrator)
**Class**: `qsys.backtest.BacktestEngine`
**Responsibility**: Orchestrates the event-driven simulation loop.

### Immutable APIs
*   `__init__(self, ...)`: Must accept `account` and `daily_predictions`.
*   `run(self) -> pd.DataFrame`: Executes the backtest and returns daily performance history.

## 5. Real Account (Live Persistence)
**Class**: `qsys.live.account.RealAccount`
**Responsibility**: Manages the persistent state of the real trading account (SQLite).

### Immutable APIs
*   `sync_broker_state(self, date, cash, positions, ..., account_name="default")`: Updates state from external source of truth.
*   `get_state(self, date, account_name="default")`: Retrieves the snapshot for a given date.

## 6. Experiment Manager (Run Tracking)
**Class**: `qsys.experiment.manager.ExperimentManager`
**Responsibility**: Manages experiment configurations, artifacts, and leaderboards.

### Immutable APIs
*   `create_run(self, name, config, description)`: Creates a new run directory and config file.
*   `update_leaderboard(self, run_name, metrics)`: Appends results to the central CSV.

---

## Development Guidelines
1.  **Do not modify the signatures** of the above methods.
2.  **Add, don't change**: If new functionality is needed, add new methods or optional arguments (with default values).
3.  **Test First**: Before committing changes, run `python3 tests/test_core_api_contracts.py` to ensure compliance.
