# REPO_SLIMMING_PLAN

> PR #122 archive scope. Not a full audit.

## 本次 Archive 规则

见 `archive/README.md`。

## Not Archived (still under review)

- systemd legacy entries (`run_daily_trading.py`, `run_preopen.sh`, etc.) — still active
- feature pipeline scripts (`run_feature_*.py`) — pending review
- `run_train.py`, `run_backtest.py`, `run_strict_eval.py` — still referenced by tests/docs
- `research/factors/` objects — on disk, no consumer yet

## 后续清理方向

- systemd cutover 完成后删除旧 systemd 入口
- feature pipeline 脚本 review 后归档或删除
- `data/` / `experiments/` / `runs/` 本地清理（非 git-tracked）
