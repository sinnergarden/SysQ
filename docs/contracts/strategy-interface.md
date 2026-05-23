# Strategy Interface Contract

## 1. Purpose

The `StrategyCandidate` protocol (`qsys/strategy/base.py`) is the boundary
between **orchestration** and **strategy-specific logic**:

- **DailyRunner** owns orchestration — it calls hooks in a fixed order, manages
  run directories, handles errors, and writes manifests.
- **StrategyAdapter** owns strategy behavior — model loading, inference,
  prediction generation, plan construction, execution, and notification.
- **Config** owns parameters — YAML files supply tunable values, not control flow.
- **Trainer** owns training — each strategy selects its own training framework.

DailyRunner must not know model type, feature schema, signal logic, portfolio
internals, or training details. StrategyAdapter must not mutate DailyRunner
behavior, bypass the ledger, or write outside its own directories.

---

## 2. Required Identity Fields

Every strategy adapter must expose these properties:

| Field | Type | Example | Rule |
|-------|------|---------|------|
| `strategy_id` | `str` | `"alpha_v2"` | Stable, lowercase snake_case. Never changes once registered. |
| `account_id` | `str` | `"shadow_alpha_v2"` | Usually `shadow_{strategy_id}` for shadow strategies. |
| `display_name` | `str` | `"Alpha V2"` | Human-readable for notifications. |
| `universe` | `str` | `"csi300"` | Instrument universe identifier. |
| `feature_set` | `str` | `"alpha_v2"` | Feature set / config name. |
| `model_version` | `str` | `"alpha_v2_smoke_202606"` | Visible in artifacts. Updated on re-train. |
| `signal_version` | `str` | `"momentum_20d_rank"` | Visible in artifacts. Documents blend / signal algorithm. |
| `rebalance_policy` | `dict` | `{"top_n": 20, ...}` | Portfolio construction parameters. |

### Naming Rules

- `strategy_id` must be stable and lowercase snake_case. Once registered in
  `qsys/strategy/registry.py`, changing it breaks run history continuity.
- `account_id` should normally follow the pattern `shadow_{strategy_id}`.
  If a different pattern is needed, document the rationale explicitly.
- `model_version` and `signal_version` are written into plan artifacts and
  execution summaries. They must be updated when the model or signal changes.

---

## 3. Required Runtime Hooks

Every hook is documented below with purpose, inputs, expected outputs, exception
behavior, and which DailyRunner stage calls it.

### `resolve_data_date(trade_date: str) -> str`

| | |
|---|---|
| **Purpose** | Resolve a requested trade date to the nearest trading day with available data. |
| **Called by** | DailyRunner preopen, before `fetch_data`. |
| **Input** | `trade_date` — requested date string (`"YYYY-MM-DD"`). |
| **Output** | Resolved date string — same format. May be the input date or an earlier date. |
| **Exception** | Should raise if no data is available within a reasonable window. |

### `load_model() -> None`

| | |
|---|---|
| **Purpose** | Load strategy-specific model(s) from disk into internal state. |
| **Called by** | DailyRunner preopen, after data date resolution. |
| **Input** | None (reads from configured paths). |
| **Output** | `None` — stores model internally. Prints a summary line. |
| **Exception** | Should raise if model files are missing or corrupt. |

### `fetch_data(data_date: str) -> Any`

| | |
|---|---|
| **Purpose** | Fetch feature data for a given date. Return type is opaque to DailyRunner. |
| **Called by** | DailyRunner preopen, after `load_model`. |
| **Input** | `data_date` — resolved data date from `resolve_data_date`. |
| **Output** | Any type. Only consumed by `generate_predictions`. Must print row count. |
| **Exception** | Should raise if data is unavailable or corrupt. |

### `generate_predictions(data: Any) -> Any`

| | |
|---|---|
| **Purpose** | Run inference on fetched data using the stored model(s). |
| **Called by** | DailyRunner preopen, after `fetch_data`. |
| **Input** | Opaque data object from `fetch_data`. |
| **Output** | A `pd.DataFrame` with **minimum columns**: `trade_date`, `instrument`, `score`, `model_name`, `mainline_object_name`. These keep ADR-007 sidecar generation working. |
| **Exception** | Should raise if inference fails. |
| **Note** | The DataFrame schema is consumed by `print_predictions_summary`, `save_predictions`, and `build_plan`. |

### `print_predictions_summary(predictions: Any) -> None`

| | |
|---|---|
| **Purpose** | Print top picks summary to console (top 5 with scores). |
| **Called by** | DailyRunner preopen, after `generate_predictions`. |
| **Input** | Predictions DataFrame from `generate_predictions`. |
| **Output** | `None` — prints to stdout/stderr. |
| **Exception** | Should not raise; wrap in try/except if fragile. |

### `should_rebalance(trade_date: str) -> bool`

| | |
|---|---|
| **Purpose** | Check whether rebalancing should occur on *trade_date* (e.g. weekly frequency, last-run check). |
| **Called by** | DailyRunner preopen, after predictions summary. |
| **Input** | `trade_date` — the current trade date. |
| **Output** | `True` if the strategy should build a plan, `False` to skip. |
| **Exception** | Should not raise. |

### `save_predictions(predictions: Any, run_root: Any, trade_date: str) -> None`

| | |
|---|---|
| **Purpose** | Persist predictions to a strategy-specific shared location (outside run directory). |
| **Called by** | DailyRunner preopen, before `build_plan`. |
| **Input** | Predictions DataFrame, run root path, trade date string. |
| **Output** | `None`. Writes CSV (and optionally ADR-007 sidecar) to strategy predictions dir. |
| **Exception** | Should raise if write fails. |

### `build_plan(predictions: Any, target_dir: Any) -> bool`

| | |
|---|---|
| **Purpose** | Build a trading plan from predictions. Write plan artifacts to *target_dir*. |
| **Called by** | DailyRunner preopen, after `save_predictions`. |
| **Input** | Predictions DataFrame, target directory path. |
| **Output** | `True` if a plan was written, `False` if skipped. Must write 4 files (see artifact contract). |
| **Exception** | Should raise if plan construction fails. |
| **Required files** | `target_weights.csv`, `order_intents.csv`, `rebalance_audit.csv`, `plan_meta.json`. |

### `load_plan_instruments(plan_dir: Any) -> list[str]`

| | |
|---|---|
| **Purpose** | Return instrument codes from the saved plan. |
| **Called by** | DailyRunner postclose, before `execute_plan`. |
| **Input** | Plan directory path. |
| **Output** | List of instrument code strings. |
| **Exception** | Should raise if plan is missing or unreadable. |

### `fetch_open_prices(trade_date: str, instruments: list[str]) -> dict[str, float]`

| | |
|---|---|
| **Purpose** | Fetch open prices for a set of instruments on *trade_date*. |
| **Called by** | DailyRunner postclose, before plan execution. |
| **Input** | Trade date string, instrument code list. |
| **Output** | `dict[instrument → open_price]`. Empty dict if no instruments. |
| **Exception** | Should raise if data source is unavailable (not per-instrument — missing instruments are skipped). |

### `execute_plan(context: Any) -> Any`

| | |
|---|---|
| **Purpose** | Execute the trading plan against current market prices. Update account state. |
| **Called by** | DailyRunner postclose, after open price fetch. |
| **Input** | `DailyRunContext` — contains run_root, trade_date, strategy/account metadata. |
| **Output** | `ShadowRebalanceArtifacts`-like object with execution summary. |
| **Exception** | Should raise on execution failure (caught by DailyRunner). |

### `commit_execution(context: Any, staging_dir: Any) -> None`

| | |
|---|---|
| **Purpose** | Commit staged execution artifacts to production paths (ledger write, artifact copy). |
| **Called by** | DailyRunner postclose, after `execute_plan`. |
| **Input** | `DailyRunContext`, staging directory path. |
| **Output** | `None`. Handles `COMMITTING` → `COMMITTED` marker rename. |
| **Exception** | Should raise on commit failure. DailyRunner handles cleanup. |

### `mark_to_market(context: Any) -> dict | None`

| | |
|---|---|
| **Purpose** | Compute MTM snapshot at close prices. |
| **Called by** | DailyRunner postclose, after commit. |
| **Input** | `DailyRunContext`. |
| **Output** | `dict` (MTM snapshot) or `None` if not applicable. |
| **Exception** | Should not raise; return `None` on failure. |

### `load_artifacts_for_notification(context: Any) -> Any | None`

| | |
|---|---|
| **Purpose** | Load execution artifacts for postclose notification message construction. |
| **Called by** | DailyRunner notify-only, or postclose after MTM. |
| **Input** | `DailyRunContext`. |
| **Output** | Artifacts object or `None` if not applicable. |
| **Exception** | Should not raise; return `None` on failure. |

### `build_preopen_message(context: Any, rebalance_skipped: bool, predictions: Any) -> str`

| | |
|---|---|
| **Purpose** | Format preopen notification text. |
| **Called by** | DailyRunner notify-only. |
| **Input** | `DailyRunContext`, rebalance_skipped flag, predictions DataFrame. |
| **Output** | Notification message string. |
| **Exception** | Should not raise. |

### `build_postclose_message(context: Any, ...) -> str`

| | |
|---|---|
| **Purpose** | Format postclose notification text. |
| **Called by** | DailyRunner notify-only. |
| **Input** | `DailyRunContext`, optional MTM snapshot, artifacts, stale_check, and execution flags. |
| **Output** | Notification message string. |
| **Exception** | Should not raise. |

### `send_notification(text: str) -> None`

| | |
|---|---|
| **Purpose** | Send a notification via Telegram (or configured channel). |
| **Called by** | DailyRunner at the end of every stage. |
| **Input** | Notification text string. |
| **Output** | `None`. |
| **Exception** | Caught by DailyRunner; must not propagate. |

### `train(context: Any) -> Any`

| | |
|---|---|
| **Purpose** | Run strategy-specific training. Completely strategy-owned — DailyRunner does not inspect internals. |
| **Called by** | DailyRunner train mode. |
| **Input** | `DailyRunContext` — contains trade_date, run_root, strategy identity, optional params. |
| **Output** | A `TrainingResult`-like object (see `qsys.model.training`). Must include at minimum `strategy_id`, `model_version`, `model_dir`, `status`. |
| **Exception** | Should raise on training failure. |

---

## 4. What StrategyAdapter Must NOT Do

- **Must not mutate DailyRunner behavior** — no monkey-patching, no overriding
  runner methods, no changing global state.
- **Must not bypass ledger commit boundary** — account state goes through
  `commit_execution` / `ShadowRebalanceArtifacts`. Direct DB writes outside the
  commit flow are forbidden.
- **Must not silently auto-create production accounts** — unless explicitly
  approved and documented.
- **Must not write strategy-specific files outside its own directories** —
  predictions go to the strategy predictions dir, plan artifacts go to the run
  directory's plan subdir, execution artifacts go to execution subdirs.
- **Must not use future data** — `fetch_data` must respect the data cutoff.
- **Must not hide real plan/execution failures as "skipped"** — a legitimate
  error must propagate or be recorded as "failed".
- **Must not modify matcher / T+1 / cost semantics** — these are runtime
  invariants shared across all strategies.
- **Must not encode behavior in YAML** — config is for parameters, not control
  flow. No "YAML programming".

---

## 5. Minimal Strategy Stub

Every new strategy starts as a class that satisfies `StrategyCandidate`:

```python
class MyStrategy:
    """Minimal StrategyCandidate implementation."""

    @property
    def strategy_id(self) -> str:
        return "my_strategy"

    @property
    def account_id(self) -> str:
        return "shadow_my_strategy"

    @property
    def display_name(self) -> str:
        return "My Strategy"

    @property
    def universe(self) -> str:
        return "csi300"

    @property
    def feature_set(self) -> str:
        return "my_features"

    @property
    def model_version(self) -> str:
        return "my_model_202606"

    @property
    def signal_version(self) -> str:
        return "blend_1.0"

    @property
    def rebalance_policy(self) -> dict:
        return {"top_n": 20, "buffer_hold": 60, "buffer_buy": 40,
                "single_stock_cap": 0.07, "rebalance_freq": "weekly"}

    def resolve_data_date(self, trade_date: str) -> str:
        return trade_date

    def load_model(self) -> None:
        print("  [MyStrategy] model loaded")

    def fetch_data(self, data_date: str) -> Any:
        return pd.DataFrame()

    def generate_predictions(self, data: Any) -> Any:
        return pd.DataFrame(columns=["trade_date", "instrument", "score",
                                      "model_name", "mainline_object_name"])

    def print_predictions_summary(self, predictions: Any) -> None:
        print("  [MyStrategy] no predictions")

    def should_rebalance(self, trade_date: str) -> bool:
        return False

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        return False

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        return []

    def save_predictions(self, predictions: Any,
                         run_root: Any, trade_date: str) -> None:
        pass

    def fetch_open_prices(self, trade_date: str,
                          instruments: list[str]) -> dict[str, float]:
        return {}

    def execute_plan(self, context: Any) -> Any:
        return None

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        pass

    def mark_to_market(self, context: Any) -> dict | None:
        return None

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        return None

    def build_preopen_message(self, context: Any, rebalance_skipped: bool,
                              predictions: Any) -> str:
        return "[MyStrategy] preopen"

    def build_postclose_message(self, context: Any, **kwargs) -> str:
        return "[MyStrategy] postclose"

    def send_notification(self, text: str) -> None:
        print(f"  [MyStrategy] notify: {text}")

    def train(self, context: Any) -> Any:
        from qsys.model.training import TrainingResult
        return TrainingResult(strategy_id=self.strategy_id,
                              model_version=self.model_version,
                              model_dir="/tmp/models/my_strategy",
                              status="success",
                              message="no training required")
```

Register in `qsys/strategy/registry.py`:

```python
from qsys.strategy.my_strategy import MyStrategy
register("my_strategy", MyStrategy)
```
