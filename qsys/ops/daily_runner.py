"""DailyRunner — reusable multi-strategy daily pipeline skeleton.

Currently a skeleton.  Phase B+ will wire this as the orchestration backbone
for preopen / postclose / train.  Today it provides shared scaffolding
(context validation, artifact paths) while the alpha_v1 daily script retains
its inline stage sequence.
"""

from __future__ import annotations

from pathlib import Path

from qsys.ops.run_context import DailyRunContext


class DailyRunner:
    """Orchestration skeleton for daily trading pipeline stages.

    Each method accepts a *DailyRunContext* and a *StrategyCandidate*.
    The skeleton validates the context and reports stage boundaries;
    concrete stage logic is added in Phase B.
    """

    def run_preopen(self, ctx: DailyRunContext) -> None:
        """Pre-open stage: inference → plan → notify.

        Phase B will implement::

            1. Validate context
            2. Load model  (strategy.model_version)
            3. Fetch data   (strategy.universe, strategy.feature_set)
            4. Generate predictions
            5. Save predictions → run_dir/predictions/
            6. Build plan   (rebalance or skip)
            7. Notify via Telegram
        """
        self._validate(ctx, allowed={"preopen"})
        self._log_stage("preopen", ctx)
        self._ensure_dirs(ctx)
        # Phase B: delegate to strategy hooks

    def run_postclose(self, ctx: DailyRunContext) -> None:
        """Post-close stage: execute → MTM → notify.

        Phase B will implement::

            1. Load preopen plan
            2. Validate execution prerequisites (open prices, stale check)
            3. Execute trades
            4. Write ledger
            5. Mark to market
            6. Notify via Telegram
        """
        self._validate(ctx, allowed={"postclose"})
        self._log_stage("postclose", ctx)
        self._ensure_dirs(ctx)
        # Phase B: delegate to strategy hooks

    def run_train(self, ctx: DailyRunContext) -> None:
        """Train stage: weekly model retraining.

        Phase B will implement::

            1. Load training config
            2. Fetch training data
            3. Train dual models (5d, 20d)
            4. Evaluate + notify
        """
        self._validate(ctx, allowed={"train"})
        self._log_stage("train", ctx)
        self._ensure_dirs(ctx)
        # Phase B: delegate to strategy hooks

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _validate(ctx: DailyRunContext, allowed: set[str]) -> None:
        if ctx.mode not in allowed:
            raise ValueError(
                f"DailyRunner.{ctx.mode} is not a valid mode for this method. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _log_stage(stage: str, ctx: DailyRunContext) -> None:
        tag = "DEBUG" if ctx.debug_run else "PROD"
        print(f"\n[{tag}] DailyRunner.{stage} | {ctx.trade_date} | "
              f"strategy={ctx.strategy_id} | account={ctx.account_id}")

    @staticmethod
    def _ensure_dirs(ctx: DailyRunContext) -> None:
        ctx.run_root.mkdir(parents=True, exist_ok=True)
