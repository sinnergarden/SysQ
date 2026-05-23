"""AlphaV1StrategyAdapter — wraps ALPHA_V1_CANDIDATE into StrategyCandidate protocol.

This is a bridge until strategies become first-class plugin objects.
No trading logic lives here — only identity, config access, and optional
lifecycle hooks.
"""

from __future__ import annotations

from typing import Any

from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE


class AlphaV1StrategyAdapter:
    """Adapter exposing the alpha_v1 config singleton as a StrategyCandidate.

    Usage::

        candidate: StrategyCandidate = AlphaV1StrategyAdapter()
        runner.run_preopen(context, candidate)
    """

    # ── Identity ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return ALPHA_V1_CANDIDATE.strategy_id

    @property
    def account_id(self) -> str:
        return ALPHA_V1_CANDIDATE.shadow_account_id

    # ── Configuration ──────────────────────────────────────────────────

    @property
    def universe(self) -> str:
        """CSI 300 is the hard-coded alpha_v1 universe."""
        return "csi300"

    @property
    def feature_set(self) -> str:
        """Feature set identifier, matching the model's training features."""
        return "alpha_v1"

    @property
    def model_version(self) -> str:
        """Current model version tag from the candidate config."""
        return ALPHA_V1_CANDIDATE.version

    @property
    def signal_version(self) -> str:
        """Prediction blend version (5d + 20d weighted)."""
        return f"blend_{ALPHA_V1_CANDIDATE.blend.ratio_str}"

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        """Rebalance parameters for the DailyRunner."""
        p = ALPHA_V1_CANDIDATE.portfolio
        return {
            "top_n": p.top_n,
            "buffer_hold": p.buffer_hold,
            "buffer_buy": p.buffer_buy,
            "rebalance_freq": p.rebalance_freq,
            "single_stock_cap": p.single_stock_cap,
        }
