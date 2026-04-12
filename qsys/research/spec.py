from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from pathlib import Path

V1_IMPL1_FIXED_LABEL_HORIZON = "1d_fixed_in_v1_impl1"

SUPPORTED_FEATURE_SETS = {"baseline", "extended", "extended_absnorm", "phase123", "phase123_absnorm"}
SUPPORTED_MODEL_TYPES = {"qlib_lgbm", "qlib_xgb", "qlib_tabular_nn"}
SUPPORTED_LABEL_TYPES = {"forward_return", "relative_return", "binary_event"}
SUPPORTED_STRATEGY_TYPES = {"rank_topk", "rank_topk_with_cash_gate", "rank_plus_binary_gate"}
SUPPORTED_REBALANCE_MODES = {"full_rebalance", "hold_if_no_trigger"}
SUPPORTED_FREQUENCIES = {"daily", "weekly"}
SUPPORTED_UNIVERSES = {"csi300", "all_a"}


@dataclass
class TransactionCostAssumptions:
    fee_rate: float = 0.0003
    slippage: float = 0.0
    tax_rate: float = 0.001
    volume_participation_cap: float | str = "not_available"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExperimentSpec:
    run_name: str
    feature_set: str
    model_type: str
    label_type: str
    strategy_type: str
    universe: str
    output_dir: str
    top_k: int = 5
    label_horizon: str = V1_IMPL1_FIXED_LABEL_HORIZON
    label_benchmark: str = "none"
    label_threshold: float | str = "not_applicable"
    rebalance_mode: str = "full_rebalance"
    rebalance_freq: str = "weekly"
    retrain_freq: str = "weekly"
    inference_freq: str = "daily"
    transaction_cost_assumptions: TransactionCostAssumptions = field(default_factory=TransactionCostAssumptions)
    strategy_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.feature_set not in SUPPORTED_FEATURE_SETS:
            raise ValueError(f"Unsupported feature_set: {self.feature_set}")
        if self.model_type not in SUPPORTED_MODEL_TYPES:
            raise ValueError(f"Unsupported model_type: {self.model_type}")
        if self.label_type not in SUPPORTED_LABEL_TYPES:
            raise ValueError(f"Unsupported label_type: {self.label_type}")
        if self.strategy_type not in SUPPORTED_STRATEGY_TYPES:
            raise ValueError(f"Unsupported strategy_type: {self.strategy_type}")
        if self.universe not in SUPPORTED_UNIVERSES:
            raise ValueError(f"Unsupported universe: {self.universe}")
        if self.rebalance_mode not in SUPPORTED_REBALANCE_MODES:
            raise ValueError(f"Unsupported rebalance_mode: {self.rebalance_mode}")
        for field_name in ("rebalance_freq", "retrain_freq", "inference_freq"):
            if getattr(self, field_name) not in SUPPORTED_FREQUENCIES:
                raise ValueError(f"Unsupported {field_name}: {getattr(self, field_name)}")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.label_horizon != V1_IMPL1_FIXED_LABEL_HORIZON:
            raise ValueError(
                f"label_horizon={self.label_horizon} is not supported in v1 impl1; use {V1_IMPL1_FIXED_LABEL_HORIZON}"
            )
        self.strategy_params = self._normalized_strategy_params(self.strategy_params)

    @staticmethod
    def _normalized_strategy_params(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(params or {})
        if "min_signal_threshold" in payload:
            payload["min_signal_threshold"] = float(payload["min_signal_threshold"])
        if "min_selected_count" in payload:
            payload["min_selected_count"] = int(payload["min_selected_count"])
        if "allow_empty_portfolio" in payload:
            payload["allow_empty_portfolio"] = bool(payload["allow_empty_portfolio"])
        if "min_trade_buffer_ratio" in payload:
            payload["min_trade_buffer_ratio"] = float(payload["min_trade_buffer_ratio"])
        return payload

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["output_dir"] = str(Path(self.output_dir))
        return payload


ResearchSpec = ExperimentSpec
