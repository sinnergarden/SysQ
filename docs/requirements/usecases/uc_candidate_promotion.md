# UC_CANDIDATE_PROMOTION: Candidate Promotion

## Status
stable

## User Goal
基于信号研究和回测证据，生成 Candidate 并将其 promotion 到 shadow（或后续 production）。晋级链路有审计记录，可回滚。

## Scope
包含：
- Candidate 创建（`create` 子命令）
- Candidate → Shadow promotion（`promote --target shadow`）
- shadow pointer 写入
- candidate evidence 打包（回测 metrics、信号引用、策略配置）

不包含：
- 自动 gate 判断（IC/IR/drawdown 阈值 — 仍为人工判断）
- Production promotion（CLI 支持 `--target production` 但未开放）
- 晋级后的 daily ops 自动衔接（当前是人工执行 `run_daily.py`）

## Inputs
- 回测 artifact path
- 信号引用（signal_id, signal_run_id）
- 策略配置引用
- 人工决策

## Outputs
- `data/research/candidates/{candidate_id}.yaml`
- `data/research/promotions/shadow.yaml` — shadow pointer
- （远期）`data/research/promotions/production.yaml`

## Canonical Entrypoints

| Entrypoint | 职责 | Inputs | Outputs / Artifacts |
|-----------|------|--------|---------------------|
| `scripts/promote_candidate.py create|promote` | 创建 Candidate + 晋级到 shadow | 回测路径、信号引用、策略配置 | `data/research/candidates/{id}.yaml`, `data/research/promotions/shadow.yaml` |

对齐 `docs/USE_CASES.md` §7。

## Key Artifacts
- `data/research/candidates/{candidate_id}.yaml`
- `data/research/promotions/shadow.yaml`

## Required Checks
- TBD: candidate artifact schema check
- TBD: promotion lineage check（signal_run_id, backtest_id 必须可解析）

## Owner Agent
operator_agent

## Allowed Paths
- `scripts/promote_candidate.py`
- `qsys/research/candidate.py`
- `configs/strategies/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`（读 backtest 产物可以，改 backtest 逻辑不行）
- `qsys/ops/daily_runner.py`

## Open Questions
- （已定）自动 gate 评估（IC/IR/drawdown check）以后做，当前留 TODO 标记即可。
