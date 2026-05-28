"""AlphaV1ResearchAdapter — parameterized alpha_v1 variant for backtest research.

Extends ``AlphaV1StrategyAdapter`` with configurable portfolio parameters,
bypassing the strict frozen-spec guard that the production adapter enforces.

Usage::

    adapter = AlphaV1ResearchAdapter.from_config({
        "display_name": "No Buffer",
        "portfolio": {"top_n": 20, "buffer_hold": 20, "buffer_buy": 20},
    })
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter


class AlphaV1ResearchAdapter(AlphaV1StrategyAdapter):
    """Research adapter with configurable portfolio parameters.

    Designed for backtest-only use.  All prediction, data-loading, and
    ML-model methods are inherited unchanged from AlphaV1StrategyAdapter.
    Only portfolio-related identity and plan-building methods are overridden.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__(project_root)
        # Portfolio overrides set by from_config
        self._research_portfolio: dict[str, Any] = {}
        self._research_display_name: str | None = None

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        config: dict | None = None,
        project_root: Path | None = None,
    ) -> "AlphaV1ResearchAdapter":
        """Construct with optional portfolio overrides (no frozen-spec guard).

        Accepts the same config keys as AlphaV1StrategyAdapter.from_config,
        but also accepts a ``portfolio`` section with arbitrary values.
        """
        self = cls(project_root=project_root)
        cfg = config or {}
        self._config = cfg
        self._config_display_name = cfg.get("display_name")
        self._research_display_name = cfg.get("display_name")

        # Path overrides (same as parent, duplicated to avoid the
        # from_config guard triggering on portfolio values)
        paths = cfg.get("paths", {})
        pr = self._project_root

        raw_model_dir = paths.get("model_dir")
        if raw_model_dir:
            p = Path(raw_model_dir)
            self._config_model_dir = p if p.is_absolute() else pr / raw_model_dir

        raw_pred_dir = paths.get("predictions_dir")
        if raw_pred_dir:
            p = Path(raw_pred_dir)
            self._config_predictions_dir = p if p.is_absolute() else pr / raw_pred_dir

        raw_ledger = paths.get("ledger_db")
        if raw_ledger:
            p = Path(raw_ledger)
            self._config_ledger_db_path = str(p if p.is_absolute() else pr / raw_ledger)

        # Portfolio overrides (NO frozen-spec guard)
        portfolio = cfg.get("portfolio", {})
        self._research_portfolio = {
            "top_n": int(portfolio.get("top_n", 20)),
            "buffer_hold": int(portfolio.get("buffer_hold", 60)),
            "buffer_buy": int(portfolio.get("buffer_buy", 40)),
            "single_stock_cap": float(portfolio.get("single_stock_cap", 0.07)),
            "rebalance_freq": str(portfolio.get("rebalance_freq", "weekly")),
        }

        return self

    # ── Identity (variant-aware) ───────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "alpha_v1_research"

    @property
    def account_id(self) -> str:
        return "shadow_alpha_v1_research"

    @property
    def display_name(self) -> str:
        if self._research_display_name:
            return self._research_display_name
        p = self._research_portfolio
        return (
            f"Research_t{p['top_n']}_bh{p['buffer_hold']}"
            f"_c{p['single_stock_cap']}_{p['rebalance_freq']}"
        )

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        return dict(self._research_portfolio)

    @property
    def signal_version(self) -> str:
        p = self._research_portfolio
        return (
            f"research_t{p['top_n']}_bh{p['buffer_hold']}"
            f"_bb{p['buffer_buy']}_c{p['single_stock_cap']}"
        )

    # ── Plan building (uses research portfolio params) ─────────────────────

    def build_plan_for_backtest(
        self,
        predictions: pd.DataFrame,
        account: Any,
        trade_date: str,
        output_dir: Any,
    ) -> Path:
        """Build plan using configurable portfolio parameters."""
        from qsys.backtest.portfolio import build_rank_weight_portfolio
        from qsys.ops.plan_builder import build_plan_from_predictions

        p = self._research_portfolio
        data_date = self._parse_date(predictions["trade_date"].iloc[0])
        return build_plan_from_predictions(
            shadow_dir=Path("/tmp/_bt_shadow"),
            trade_date=trade_date,
            reference_date=data_date,
            predictions=predictions,
            output_dir=Path(output_dir),
            portfolio_fn=build_rank_weight_portfolio,
            top_n=p["top_n"],
            buffer_hold=p["buffer_hold"],
            buffer_buy=p["buffer_buy"],
            single_stock_cap=p["single_stock_cap"],
            strategy_id=self.strategy_id,
            strategy_version=self.model_version,
            portfolio_method="rank_weight_buffer",
            account=account,
        )
