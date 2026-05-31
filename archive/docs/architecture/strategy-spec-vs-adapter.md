# StrategySpec vs StrategyAdapter

## Two concerns

A strategy in SysQ has two distinct representations:

| Concern | ``StrategySpec`` | ``StrategyAdapter`` |
|---------|------------------|---------------------|
| **What it is** | Static definition | Executable behaviour |
| **Contents** | Identity, config, lifecycle, research | Runtime hooks, model loading, inference |
| **Lifecycle** | Exists for all stages | Only exists for candidate / production |
| **Registry** | Not required | Required for runtime |
| **File** | ``qsys/strategy/spec.py`` | ``qsys/strategy/base.py`` (protocol) |
| **Data source** | YAML config | Python class |

---

## StrategySpec

A ``StrategySpec`` is a **dataclass** that captures:

- **Identity**: ``strategy_id``, ``display_name``, ``family``, ``owner``
- **Lifecycle**: ``stage``, ``lifecycle`` history, ``promotion_gates``
- **Configuration**: ``universe``, ``feature_set``, ``model_version``,
  ``signal_version``, ``account_id``, ``paths``
- **Research**: ``hypothesis``, ``label``, ``model``, ``signal``,
  ``portfolio``, ``evaluation``

Every strategy — whether research, candidate, or production — has a
``StrategySpec``.  It is loaded from the YAML config file in
``configs/strategies/``.

Research-stage strategies may **only** have a ``StrategySpec``.  They do
not need a ``StrategyAdapter`` or a registry entry.

```python
from qsys.strategy.spec import load_strategy_spec

spec = load_strategy_spec("configs/strategies/alpha_v1.yaml")
assert spec.stage == "candidate"
```

---

## StrategyAdapter

A ``StrategyAdapter`` is a **Python class** that implements the
``StrategyCandidate`` runtime protocol.  It defines **how** the strategy
loads models, fetches data, generates predictions, builds plans, executes
trades, and sends notifications.

``StrategyAdapter`` is required for candidate and production stages, but
**not** for research.

```python
from qsys.strategy.base import StrategyCandidate

class MyStrategy:
    @property
    def strategy_id(self) -> str:
        return "my_strategy"

    def load_model(self) -> None:
        ...
```

---

## Identity model

- ``strategy_id`` is the **single stable identity**.
- There is no ``candidate_id``.
- The same ``strategy_id`` appears in both ``StrategySpec`` and
  ``StrategyAdapter``.
- ``StrategySpec.strategy_id`` must match ``StrategyAdapter.strategy_id``.

---

## Relationship diagram

```
configs/strategies/alpha_v1.yaml
              │
              ▼
      StrategySpec          StrategyAdapter
      (static def)          (runtime code)
              │                    │
              │  research          │
              │  candidate  ───────┤
              │  production ───────┤
              │  rejected          │
              │  archived          │
              │                    │
              ▼                    ▼
      catalog / docs      DailyRunner / BacktestRunner
```

A research strategy has the left column only.
A candidate or production strategy has both.

---

## File locations

| Concept | Location |
|---------|----------|
| ``StrategySpec`` dataclass | ``qsys/strategy/spec.py`` |
| ``StrategyCandidate`` protocol | ``qsys/strategy/base.py`` |
| Config files | ``configs/strategies/*.yaml`` |
| Strategy catalog | ``qsys/research/catalog.py`` |
| Runtime registry | ``qsys/strategy/registry.py`` |
