# Strategy Lifecycle

## Overview

Every strategy in SysQ follows a defined lifecycle from research idea to
production deployment.  The lifecycle stage is stored in the strategy's
``StrategySpec`` and determines what operations are available.

```
  research ──► candidate ──► production
                    │               │
                    └── rejected    └── archived
```

---

## 1. Research

A **research** strategy is a structured idea with a ``StrategySpec`` config.
It may or may not have a ``StrategyAdapter`` implementation.

| Attribute | Value |
|-----------|-------|
| Has ``StrategySpec`` / YAML config | Yes |
| Has ``StrategyAdapter`` | Not required |
| Registered in ``qsys/strategy/registry.py`` | Not required |
| Can run ``run_daily.py`` | No |
| Has shadow account | No |
| Evaluation method | ``BacktestRunner`` / ``EvaluationRunner`` |

Research-stage strategies appear in the strategy catalog but do not produce
daily artifacts, MTM snapshots, or ledger entries.

### Promotion gates → Candidate

- [ ] Evaluation report exists
- [ ] Backtest result exists
- [ ] No future-data violation in backtest
- [ ] Contract tests pass (``tests/contracts/``)
- [ ] ``StrategyAdapter`` exists
- [ ] Registry entry exists
- [ ] ``--debug-run`` passes for at least one trade date

---

## 2. Candidate

A **candidate** is a runtime-ready shadow strategy.  It has a full
``StrategyAdapter``, a registry entry, and a shadow account.

| Attribute | Value |
|-----------|-------|
| Has ``StrategySpec`` / YAML config | Yes |
| Has ``StrategyAdapter`` | Yes |
| Registered in ``qsys/strategy/registry.py`` | Yes |
| Can run ``run_daily.py`` preopen/postclose/train | Yes |
| Has shadow account | Yes |
| Writes artifacts, MTM, shadow ledger | Yes |
| Sends Telegram notifications | Yes |

Candidate strategies run alongside production strategies in the daily
runtime, using shadow accounts to track simulated P&L.

### Promotion gates → Production

- [ ] Sufficient candidate observation days (typically 20+ trading days)
- [ ] Stable MTM (no extreme drawdown vs benchmark)
- [ ] Controlled turnover (within policy)
- [ ] Acceptable drawdown (within risk limits)
- [ ] Risk limits defined and documented
- [ ] Manual approval obtained
- [ ] Broker dry-run path (or manual execution plan) defined

---

## 3. Production

A **production** strategy is a live / quasi-live strategy.  It requires
additional infrastructure: risk limits, capital allocation, broker policy,
and a rollback plan.

| Attribute | Value |
|-----------|-------|
| Has ``StrategySpec`` / YAML config | Yes |
| Has ``StrategyAdapter`` | Yes |
| Registered in registry | Yes |
| Runs ``run_daily.py`` | Yes |
| Real-money / live account | Requires additional controls |

**Production execution is not yet implemented in this framework.**
See the broker integration roadmap.

---

## 4. Rejected

A **rejected** strategy failed evaluation or promotion gates.
It is retained in the catalog for auditability but is not runnable.

---

## 5. Archived

An **archived** strategy is a previously runnable strategy that has been
retired.  Its artifacts and history are preserved but it is no longer
executed.

---

## Stage transitions

| Transition | Trigger | Evidence |
|-----------|---------|----------|
| research → candidate | Evaluation passes + adapter exists | ``PromotionRecord`` |
| candidate → production | Observation period + approval | ``PromotionRecord`` + sign-off |
| → rejected | Evaluation failure / gate not met | ``PromotionRecord`` |
| → archived | Retirement / replacement | ``PromotionRecord`` |

Each transition creates a ``PromotionRecord`` that is appended to the
strategy's promotion history file.
