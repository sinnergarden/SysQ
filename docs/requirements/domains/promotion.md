# Domain: Candidate Promotion

## Domain Scope
策略晋级链路：Candidate 创建、shadow/prod pointer 写入、晋级证据打包、回滚轨迹。
不包含：自动 gate 判断（未来）、生产级实盘执行。

## UC_CANDIDATE_PROMOTION

### Status
stable

### Source
`docs/USE_CASES.md` UC-10（Candidate Promotion）。

### User Goal
基于信号研究和回测证据，生成 Candidate 并将其 promotion 到 shadow（或后续 production）。晋级链路有审计记录，可回滚。

### Scope
包含：
- Candidate 创建（`create` 子命令）
- Candidate → Shadow promotion（`promote --target shadow`）
- shadow pointer 写入
- candidate evidence 打包（回测 metrics、信号引用、策略配置）

不包含：
- 自动 gate 判断（IC/IR/drawdown 阈值 — 仍为人工判断）
- Production promotion（CLI 支持 `--target production` 但未开放）
- 晋级后的 daily ops 自动衔接

### Inputs
- 回测 artifact path
- 信号引用（signal_id, signal_run_id）
- 策略配置引用及文件 SHA-256
- 明确的 shadow model pointer、model path 和 artifact SHA-256（禁止 latest symlink）
- 人工决策

### Outputs
- `data/research/candidates/{candidate_id}/candidate.yaml`
- `data/research/promotions/shadow.yaml`
- `artifacts/registry/models/{strategy_id}/prod.json`（远期）

`shadow.yaml` 不是一个只指 candidate ID 的别名。它必须保存
`runtime_binding`，并在每次 daily dispatch 前重算策略配置、model directory
和 model pointer 的 hash。任一不一致都必须 fail closed。

### Canonical Entrypoints
- `scripts/promote_candidate.py create|promote`

### Key Artifacts
- `data/research/candidates/{candidate_id}/candidate.yaml`
- `data/research/promotions/shadow.yaml`
- `artifacts/registry/models/{strategy_id}/prod.json`

### Required Checks
- TBD: candidate artifact schema check
- `resolve_shadow_promotion()` runtime binding check（strategy/config/model/pointer）
- TBD: full signal/backtest artifact resolution

### Owner Agent
operator_agent

### Allowed Paths
- `scripts/promote_candidate.py`
- `qsys/research/candidate.py`
- `configs/strategies/`
- `data/research/candidates/`
- `data/research/promotions/`
- `harness/checks/`

### Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`（读产物可以，改逻辑不行）
- `qsys/ops/daily_runner.py`

### Open Questions
- （已定）自动 gate 评估以后做，留 TODO。
