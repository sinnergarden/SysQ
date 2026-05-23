# New Strategy Development Guide

> **Framework Contracts** — this guide references three formal contract
> documents that define the SysQ framework boundaries:
>
> - [Strategy Interface Contract](../contracts/strategy-interface.md) —
>   full ``StrategyCandidate`` protocol spec with hook contracts and
>   "must not" rules.
> - [Artifact Contract](../contracts/artifact-contract.md) — run root
>   layout, per-file CSV/JSON schemas, replay rules.
> - [Data Interface Contract](../contracts/data-interface.md) — date
>   semantics, data cutoff rules, market snapshot and feature frame
>   contracts.

## 1. When to Create a New Strategy

A new `StrategyCandidate` adapter is warranted when:

- You have a new prediction signal that differs from existing alpha_v1's feature/model/blend.
- You need different portfolio construction rules (top‑N, buffer, cap).
- You want a separate shadow account with its own P&L tracking.
- The strategy requires different execution semantics (e.g., different price mode, settlement).

A separate adapter is **not** needed if you are only iterating on model hyper-parameters or feature sets within alpha_v1.

---

## 2. Minimal File Structure

```
qsys/strategy/alpha_v2/
  __init__.py
  adapter.py          # StrategyCandidate implementation
  signal.py            optional — scoring / blending
  portfolio.py         optional — custom portfolio construction

configs/strategies/alpha_v2.yaml
```

The adapter is mandatory. `signal.py` and `portfolio.py` are encouraged when the logic is non-trivial but not required for the first prototype.

---

## 3. StrategyCandidate Contract

Your adapter must implement all members of the `StrategyCandidate` protocol (`qsys/strategy/base.py`, `@runtime_checkable`). The DailyRunner calls these methods in a fixed order during each stage.

### Identity

```python
@property
def strategy_id(self) -> str: ...          # e.g. "alpha_v2"
@property
def account_id(self) -> str: ...           # e.g. "shadow_alpha_v2"
@property
def display_name(self) -> str: ...          # e.g. "Alpha V2"
```

### Configuration

```python
@property
def universe(self) -> str: ...              # e.g. "csi300"
@property
def feature_set(self) -> str: ...           # e.g. "alpha_v2"
@property
def model_version(self) -> str: ...         # e.g. "alpha_v2_candidate_202606"
@property
def signal_version(self) -> str: ...        # e.g. "blend_0.7:0.3"
@property
def rebalance_policy(self) -> dict: ...
    # {"top_n": 20, "buffer_hold": 60, "buffer_buy": 40,
    #  "rebalance_freq": "weekly", "single_stock_cap": 0.07}
```

### Data

```python
def resolve_data_date(self, trade_date: str) -> str:
    """Return nearest trading day with available data."""

def get_stock_name(self, ts_code: str) -> str:
    """Human-readable stock name (may fall back to ts_code)."""

def load_model(self) -> None:
    """Load model(s) from disk, store internally. Print summary."""

def fetch_data(self, data_date: str) -> Any:
    """Fetch feature data. Print row count. Return opaque data object."""
```

**Important**: `fetch_data` should return a type that is **only consumed by `generate_predictions`**. The DailyRunner treats the return value as opaque. Do not include future data; record the data_date / cutoff if possible.

### Predict / Plan

```python
def generate_predictions(self, data: Any) -> Any:
    """Run inference. Must return a DataFrame with at minimum:
    - trade_date
    - instrument
    - score
    - model_name
    - mainline_object_name
    These columns keep ADR-007 sidecar generation working."""

def print_predictions_summary(self, predictions: Any) -> None:
    """Print top picks to console."""

def should_rebalance(self, trade_date: str) -> bool:
    """Check weekly/periodic frequency against ledger."""

def build_plan(self, predictions: Any, target_dir: Any) -> bool:
    """Write plan files to target_dir:
    - target_weights.csv
    - order_intents.csv
    - rebalance_audit.csv
    - plan_meta.json
    Return True if plan was written, False if skipped."""

def load_plan_instruments(self, plan_dir: Any) -> list[str]:
    """Return instrument codes from the plan."""

def save_predictions(self, predictions: Any, run_root: Any, trade_date: str) -> None:
    """Persist predictions to strategy-specific shared location."""

def fetch_open_prices(self, trade_date: str, instruments: list[str]) -> dict[str, float]:
    """Return dict[instrument → open_price]."""
```

### Execute / MTM

```python
def execute_plan(self, context: Any) -> Any:
    """Execute the plan. Return ShadowRebalanceArtifacts-like object.
    Writes staging artifacts:
    - account_before.json / account_after.json
    - positions_before.csv / positions_after.csv
    - execution_summary.json
    - ledger_rows.csv / ledger_payload.json"""

def commit_execution(self, context: Any, staging_dir: Any) -> None:
    """Move staging → production paths."""

def mark_to_market(self, context: Any) -> dict | None:
    """Compute MTM snapshot. Writes mtm/mtm_snapshot.json."""

def load_artifacts_for_notification(self, context: Any) -> Any | None:
    """Load execution artifacts for postclose notification message."""
```

### Notifications

```python
def build_preopen_message(self, context: Any, rebalance_skipped: bool, predictions: Any) -> str: ...
def build_postclose_message(self, context: Any, ...) -> str: ...
def send_notification(self, text: str) -> None: ...
```

---

## 3.5 Training Interface (`train`)

The ``train`` hook is intentionally **coarse** — different model families need
very different training logic:

```python
def train(self, context: Any) -> Any:
    """Run strategy-specific training.

    Returns a ``TrainingResult``-like object (see ``qsys.model.training``).
    """
    trainer = MyStrategyTrainer(...)
    return trainer.run(context)
```

**Why coarse?** LightGBM and Transformer training share almost nothing:

| Concern | LightGBM | Transformer |
|---------|----------|-------------|
| Input | Tabular feature matrix | Sequence tensors |
| Label | Forward return (scalar) | Multi-horizon return (vector) |
| Model | Booster tree ensemble | Attention-based checkpoint |
| Validation | RankIC, MSE | Spearman, MAPE, Sharpe |
| Output | ``model_5d.txt`` + scalers | checkpoint.pt + tokenizer/schema |

The DailyRunner calls ``strategy.train(ctx)`` and gets back a
``TrainingResult``.  It does **not** know:

- What model type was trained
- What features or labels were used
- What the training window was
- What evaluation metrics mean

### TrainingResult contract

Defined in ``qsys/model/training.py``:

```python
@dataclass
class TrainingResult:
    strategy_id: str
    model_version: str
    model_dir: str
    train_start: str | None = None
    train_end: str | None = None
    valid_start: str | None = None
    valid_end: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    status: str = "success"       # "success" | "failed"
    message: str | None = None

@dataclass
class ModelManifest:
    strategy_id: str
    model_version: str
    model_type: str               # "lightgbm_dual", "transformer", "dnn"
    feature_set: str
    label: dict[str, Any]
    train_window: dict[str, Any]
    created_at: str
    artifacts: dict[str, str]
    metrics: dict[str, Any]
    git_commit: str | None = None
```

### Strategy adapter owns training delegation

```python
class AlphaV1StrategyAdapter:
    def train(self, context: Any) -> TrainingResult:
        from qsys.model.alpha_v1_trainer import AlphaV1Trainer
        trainer = AlphaV1Trainer(project_root=..., config=...)
        return trainer.run(context)
```

### Future Transformer strategy

```python
class AlphaV2StrategyAdapter:
    def train(self, context: Any) -> TrainingResult:
        trainer = TransformerTrainer(
            project_root=...,
            config=...,
            sequence_length=60,
            d_model=128,
        )
        return trainer.run(context)
```

No changes to DailyRunner needed.  The training pipeline is entirely
strategy-owned.

---

## 4. Plan Interface (`build_plan`)

`build_plan` must write the following files to `target_dir`:

| File | Required | Content |
|------|----------|---------|
| `target_weights.csv` | Yes | instrument, target_weight, target_value |
| `order_intents.csv` | Yes | trade_date, instrument, side, target_weight, diff_value, requested_qty |
| `rebalance_audit.csv` | Yes | audit trail of rebalance decisions |
| `plan_meta.json` | Yes | trade_date, strategy_id, portfolio params, build_ts |

---

## 5. Execution Interface (`execute_plan`)

`execute_plan` must write staging artifacts. After `commit_execution`, the runner does ADR-007 sidecar generation automatically.

### Staging artifacts (under `<run_root>/execution/staging/`):

| File | Content |
|------|---------|
| `account_before.json` | Account state before execution |
| `positions_before.csv` | Positions before execution |
| `account_after.json` | Account state after execution |
| `positions_after.csv` | Positions after execution |
| `execution_summary.json` | Summary statistics |
| `ledger_rows.csv` | Ledger rows for the execution |
| `ledger_payload.json` | Ledger payload for downstream commit |

---

## 6. MTM Interface (`mark_to_market`)

Must write `mtm/mtm_snapshot.json` under `<run_root>/mtm/`. The ADR-007 portfolio snapshot sidecar is generated automatically by the runner.

---

## 7. Replay Requirement

Any change that touches the runtime (DailyRunner, adapter, execution) requires 5-day replay validation comparing baseline vs candidate:

- Compare predictions, plans, executions, MTM snapshots, ADR-007 sidecars, and final account state.
- Trading-critical outputs must be identical.
- Only metadata differences (timestamps, git_commit, created_at) are allowed.

Research-layer changes (new features, model training) do not require replay.

### Replay: critical `--end-date` caveat

When running replay for a past date range, **always pass `--end-date` to the training script**:

```bash
# WRONG — uses datetime.now() as end date, leaks future data into training
python scripts/run_alpha_v1_weekly_train.py

# RIGHT — trains only on data up to the Friday before the test week
python scripts/run_alpha_v1_weekly_train.py --end-date 2026-05-15
```

Without `--end-date`, the training script defaults to `datetime.now()`, which includes all
future trading days in the training set — producing lookahead bias and inflated PnL. This
is safe for real production (where "now" is truly the current date) but catastrophic for
historical replay.

---

## 8. How to Run Through `run_daily.py`

Once registered in `qsys/strategy/registry.py`:

```bash
# Preopen — generate predictions + build plan
python scripts/run_daily.py --strategy alpha_v2 --mode preopen --trade-date 2026-05-22

# Postclose — execute plan + MTM
python scripts/run_daily.py --strategy alpha_v2 --mode postclose --trade-date 2026-05-22

# Debug run (does not modify shadow state)
python scripts/run_daily.py --strategy alpha_v2 --mode preopen --trade-date 2026-05-22 --debug-run

# Notify-only (re-build notification from existing artifacts)
python scripts/run_daily.py --strategy alpha_v2 --notify-only --trade-date 2026-05-22
```

---

## 9. What NOT to Do

- **Do not copy `scripts/run_alpha_v1_daily.py`.** Use `scripts/run_daily.py`.
- **Do not modify DailyRunner** for strategy-specific behavior. Use the StrategyCandidate protocol.
- **Do not put model/signal logic in YAML.** Config is for parameters, not behavior.
- **Do not bypass the ledger.** Account state must go through `shadow_rebalance` / ledger.
- **Do not write account state to JSON/CSV as source of truth.** The ledger DB is authoritative.
- **Do not change matcher/T+1/cost assumptions** per strategy — these are runtime invariants.

---

## 10. Code-First / Config-Assisted Principle

```
Python class owns behavior.
YAML config owns parameters.
```

- Strategy behavior (how to load models, generate predictions, blend scores, build plans) lives in Python.
- Config (model_dir, predictions_dir, display_name, universe, version strings) lives in YAML.
- Config should be hashable and snapshot-able for reproducibility.
- Avoid "YAML programming" — do not encode control flow, conditionals, or DAGs in config.
- If a parameter changes trading behavior, it must match the frozen spec or fail at startup.

---

## 11. Concrete `alpha_v2` Template

### `qsys/strategy/alpha_v2/__init__.py`

```python
from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter

__all__ = ["AlphaV2StrategyAdapter"]
```

### `qsys/strategy/alpha_v2/adapter.py`

```python
"""AlphaV2StrategyAdapter — template for a new strategy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


class AlphaV2StrategyAdapter:
    """Minimal StrategyCandidate implementation."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path(__file__).resolve().parents[3]
        self._loaded_models: dict = {}

    # ── Identity ──────────────────────────────────────────────────────
    @property
    def strategy_id(self) -> str:
        return "alpha_v2"

    @property
    def account_id(self) -> str:
        return "shadow_alpha_v2"

    @property
    def display_name(self) -> str:
        return "Alpha V2"

    # ── Configuration ─────────────────────────────────────────────────
    @property
    def universe(self) -> str:
        return "csi300"

    @property
    def feature_set(self) -> str:
        return "alpha_v2"

    @property
    def model_version(self) -> str:
        return "alpha_v2_candidate_202606"

    @property
    def signal_version(self) -> str:
        return "blend_0.7:0.3"

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        return {
            "top_n": 20,
            "buffer_hold": 60,
            "buffer_buy": 40,
            "rebalance_freq": "weekly",
            "single_stock_cap": 0.07,
        }

    # ── Data ──────────────────────────────────────────────────────────
    def resolve_data_date(self, trade_date: str) -> str:
        return trade_date

    def get_stock_name(self, ts_code: str) -> str:
        return ts_code

    def load_model(self) -> None:
        print("  [AlphaV2] model loaded (placeholder)")

    def fetch_data(self, data_date: str) -> Any:
        return pd.DataFrame()

    # ── Predict + Plan ────────────────────────────────────────────────
    def generate_predictions(self, data: Any) -> Any:
        return pd.DataFrame()

    def print_predictions_summary(self, predictions: Any) -> None:
        print("  [AlphaV2] no predictions")

    def should_rebalance(self, trade_date: str) -> bool:
        return False

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        return False

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        return []

    def save_predictions(
        self, predictions: Any, run_root: Any, trade_date: str
    ) -> None:
        pass

    def fetch_open_prices(
        self, trade_date: str, instruments: list[str]
    ) -> dict[str, float]:
        return {}

    # ── Execute + MTM ─────────────────────────────────────────────────
    def execute_plan(self, context: Any) -> Any:
        return None

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        pass

    def mark_to_market(self, context: Any) -> dict | None:
        return None

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        return None

    # ── Notifications ─────────────────────────────────────────────────
    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any
    ) -> str:
        return "[AlphaV2] preopen placeholder"

    def build_postclose_message(
        self, context: Any, mtm: dict | None = None,
        artifacts: Any = None, stale_check: dict | None = None,
        execution_committed: bool = False, execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        return "[AlphaV2] postclose placeholder"

    def send_notification(self, text: str) -> None:
        print(f"  [AlphaV2] notify: {text}")
```

### `configs/strategies/alpha_v2.yaml`

```yaml
strategy_id: alpha_v2
display_name: Alpha V2
account_id: shadow_alpha_v2
universe: csi300
feature_set: alpha_v2
model_version: alpha_v2_candidate_202606
signal_version: blend_0.7:0.3

paths:
  model_dir: experiments/alpha_v2_models/latest
  predictions_dir: experiments/alpha_v2_shadow_predictions
  ledger_db: data/trade.db

portfolio:
  top_n: 20
  buffer_hold: 60
  buffer_buy: 40
  single_stock_cap: 0.07
  rebalance_freq: weekly
```

### Register the strategy

Add to `qsys/strategy/registry.py`:

```python
from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter
register("alpha_v2", AlphaV2StrategyAdapter)
```
