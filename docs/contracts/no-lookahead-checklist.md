# No-Lookahead Checklist

Use this checklist for every PR that touches time-sensitive or
data-dependent code.

---

## Date Semantics

- [ ] Each date field in the output has a documented meaning (trade_date,
      data_date, or something else?)
- [ ] Preopen predictions use `resolve_data_date(T, mode="previous")`.
- [ ] MTM evaluations use `resolve_data_date(T, mode="asof")`.
- [ ] Label computations use forward window `T+1 ... T+horizon`.
- [ ] No code path mixes `asof` and `previous` for the same date.

## Signal Schema

- [ ] Every signal record carries both `trade_date` and `data_date`.
- [ ] `data_date <= previous_trading_day(trade_date)` is satisfied for
      every row.
- [ ] Signal files pass `check_signal_schema.py`.
- [ ] Signal files pass `check_no_lookahead.py`.

## Label Usage

- [ ] Labels are not used as input features for any model.
- [ ] Label generation is isolated from feature generation.
- [ ] Label files pass `check_label_schema.py`.
- [ ] Label `horizon` is consistent with the label definition.

## Rolling Train

- [ ] `train_end < prediction_start` is enforced.
- [ ] Training data cutoff is before the first inference date.
- [ ] No lookahead features are present in training data.
- [ ] Rolling train results are marked `rolling_train` in manifests.

## Backtest

- [ ] Backtest from cached signals is marked `fixed_model`.
- [ ] Signal cache is read-only during backtest.
- [ ] No on-the-fly model training during backtest execution.
- [ ] Backtest dates are completed trading days only.
- [ ] Slippage and commission affect reported returns.

## Artifact Lineage

- [ ] Every run artifact has a `manifest.json` or is under a manifest.
- [ ] Manifest includes `model_id`, `feature_set_id`, `label_id` when
      applicable.
- [ ] Signal parquet has a matching `signal_id` field.
- [ ] Label parquet has a matching `label_id` field.

## Red-Zone Awareness

- [ ] No unintended changes to:
  - `qsys/data/calendar.py`
  - `qsys/ops/daily_runner.py`
  - `qsys/trader/matcher.py`
  - `qsys/trader/diff.py`
  - `qsys/ledger/`
  - `qsys/ops/mtm.py`
  - `qsys/ops/commit_guard.py`
  - `qsys/backtest/engine.py`
- [ ] If a red-zone file was changed, see
      [strategy-boundary-contract.md](strategy-boundary-contract.md)
      section 8.
