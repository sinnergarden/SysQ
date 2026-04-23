# Mainline Strategy Tuning Exclusion Round

## Scope

This note records the closed strategy-layer exclusion round for the two fixed research objects:

- `feature_173`
- `feature_254_trimmed`

The round only compares small shared strategy variants and does not change feature definitions, decision states, or signal construction.

## Stable artifacts

Artifacts are written under `experiments/mainline_strategy_tuning/`:

- `strategy_tuning_summary.csv`
- `strategy_tuning_summary.md`
- `window_stability_summary.csv`
- `window_stability_summary.md`

## Business conclusion

- `feature_254_trimmed` currently works best with weekly / sparse rebalance (`k5_weekly_b000`).
- Even under that best strategy-layer setup, `feature_254_trimmed` still remains materially weaker than `feature_173`.
- The main bottleneck is therefore now judged to be the signal layer, not the portfolio-construction layer.
