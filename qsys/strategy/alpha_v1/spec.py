"""
Alpha V1 Candidate — frozen strategy spec.

Single source of truth for all alpha_v1_candidate parameters.
Both the rolling backtest and any shadow-trading observation import this;
no code should copy these constants independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AlphaV1TrainingConfig:
    train_days: int = 504
    test_days: int = 5
    step_days: int = 5
    n_estimators: int = 200
    lgb_params: dict[str, Any] = field(default_factory=lambda: {
        "objective": "regression", "metric": "mse",
        "colsample_bytree": 0.8879, "learning_rate": 0.0421,
        "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
        "max_depth": 8, "num_leaves": 210, "num_threads": 8,
        "verbosity": -1, "seed": 42,
    })

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,
            "n_estimators": self.n_estimators,
            "lgb_params": self.lgb_params,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1TrainingConfig":
        return cls(
            train_days=data.get("train_days", 504),
            test_days=data.get("test_days", 5),
            step_days=data.get("step_days", 5),
            n_estimators=data.get("n_estimators", 200),
            lgb_params=data.get(
                "lgb_params",
                {
                    "objective": "regression",
                    "metric": "mse",
                    "colsample_bytree": 0.8879,
                    "learning_rate": 0.0421,
                    "subsample": 0.8789,
                    "lambda_l1": 205.6999,
                    "lambda_l2": 580.9768,
                    "max_depth": 8,
                    "num_leaves": 210,
                    "num_threads": 8,
                    "verbosity": -1,
                    "seed": 42,
                },
            ),
        )


@dataclass(frozen=True)
class AlphaV1PortfolioConfig:
    top_n: int = 20
    single_stock_cap: float = 0.07
    buffer_hold: int = 60
    buffer_buy: int = 40
    rebalance_freq: str = "weekly"

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "single_stock_cap": self.single_stock_cap,
            "buffer_hold": self.buffer_hold,
            "buffer_buy": self.buffer_buy,
            "rebalance_freq": self.rebalance_freq,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1PortfolioConfig":
        return cls(
            top_n=data.get("top_n", 20),
            single_stock_cap=data.get("single_stock_cap", 0.07),
            buffer_hold=data.get("buffer_hold", 60),
            buffer_buy=data.get("buffer_buy", 40),
            rebalance_freq=data.get("rebalance_freq", "weekly"),
        )


@dataclass(frozen=True)
class AlphaV1CostConfig:
    commission: float = 0.0003
    stamp_duty: float = 0.001
    slippage: float = 0.001
    min_commission: float = 5.0

    @property
    def cost_params(self) -> dict[str, float]:
        return {
            "commission": self.commission,
            "stamp_duty": self.stamp_duty,
            "slippage": self.slippage,
            "min_commission": self.min_commission,
        }

    def to_dict(self) -> dict[str, float]:
        return self.cost_params

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1CostConfig":
        return cls(
            commission=data.get("commission", 0.0003),
            stamp_duty=data.get("stamp_duty", 0.001),
            slippage=data.get("slippage", 0.001),
            min_commission=data.get("min_commission", 5.0),
        )


@dataclass(frozen=True)
class AlphaV1BlendConfig:
    blend_5d: float = 0.8
    blend_20d: float = 0.2

    def to_dict(self) -> dict[str, float]:
        return {"5d": self.blend_5d, "20d": self.blend_20d}

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1BlendConfig":
        return cls(
            blend_5d=data.get("blend_5d", 0.8),
            blend_20d=data.get("blend_20d", 0.2),
        )

    @property
    def ratio_str(self) -> str:
        return f"{self.blend_5d}:{self.blend_20d}"


@dataclass(frozen=True)
class AlphaV1HealthThresholds:
    rankic_warn: float = 0.01
    rankic_crit: float = 0.0
    excess_20d_warn: float = -0.05
    excess_60d_crit: float = -0.08
    dd_warn: float = -0.15
    dd_crit: float = -0.20
    feature_missing_warn: float = 0.05
    failed_trade_warn: float = 0.10

    def to_dict(self) -> dict[str, float]:
        return {
            "rankic_warn": self.rankic_warn,
            "rankic_crit": self.rankic_crit,
            "excess_20d_warn": self.excess_20d_warn,
            "excess_60d_crit": self.excess_60d_crit,
            "dd_warn": self.dd_warn,
            "dd_crit": self.dd_crit,
            "feature_missing_warn": self.feature_missing_warn,
            "failed_trade_warn": self.failed_trade_warn,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1HealthThresholds":
        return cls(
            rankic_warn=data.get("rankic_warn", 0.01),
            rankic_crit=data.get("rankic_crit", 0.0),
            excess_20d_warn=data.get("excess_20d_warn", -0.05),
            excess_60d_crit=data.get("excess_60d_crit", -0.08),
            dd_warn=data.get("dd_warn", -0.15),
            dd_crit=data.get("dd_crit", -0.20),
            feature_missing_warn=data.get("feature_missing_warn", 0.05),
            failed_trade_warn=data.get("failed_trade_warn", 0.10),
        )


@dataclass(frozen=True)
class AlphaV1CandidateConfig:
    """Aggregate strategy config — nested dataclasses for each concern."""
    version: str = "alpha_v1_candidate_202605"
    strategy_id: str = "alpha_v1"
    display_name: str = "alpha_v1_candidate_blend20_weekly_top20_buffer"
    target_cash: float = 10_000_000.0

    training: AlphaV1TrainingConfig = field(default_factory=AlphaV1TrainingConfig)
    portfolio: AlphaV1PortfolioConfig = field(default_factory=AlphaV1PortfolioConfig)
    cost: AlphaV1CostConfig = field(default_factory=AlphaV1CostConfig)
    blend: AlphaV1BlendConfig = field(default_factory=AlphaV1BlendConfig)
    health: AlphaV1HealthThresholds = field(default_factory=AlphaV1HealthThresholds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "target_cash": self.target_cash,
            "training": self.training.to_dict(),
            "portfolio": self.portfolio.to_dict(),
            "cost": self.cost.to_dict(),
            "blend": self.blend.to_dict(),
            "health": self.health.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlphaV1CandidateConfig":
        strategy = data.get("strategy", {})
        return cls(
            version=strategy.get("version", "alpha_v1_candidate_202605"),
            strategy_id=strategy.get("strategy_id", "alpha_v1"),
            display_name=strategy.get(
                "display_name",
                "alpha_v1_candidate_blend20_weekly_top20_buffer",
            ),
            target_cash=float(strategy.get("target_cash", 10_000_000.0)),
            training=AlphaV1TrainingConfig.from_dict(data.get("training", {})),
            portfolio=AlphaV1PortfolioConfig.from_dict(data.get("portfolio", {})),
            cost=AlphaV1CostConfig.from_dict(data.get("cost", {})),
            blend=AlphaV1BlendConfig.from_dict(data.get("blend", {})),
            health=AlphaV1HealthThresholds.from_dict(data.get("health", {})),
        )


# ── Helper to build from resolved config dict ──


def build_candidate_from_config(config_dict: dict) -> AlphaV1CandidateConfig:
    """Build ``AlphaV1CandidateConfig`` from a resolved config dict.

    Hierarchy already applied: spec defaults < YAML < CLI overrides.
    """
    return AlphaV1CandidateConfig.from_dict(config_dict)


# ── Harmful feature groups ──

HARMFUL_GROUPS = frozenset({
    "Fundamental", "VolumeAmt", "Valuation", "Margin", "PricePattern",
})


# ── Feature grouping & cleaning (moved from monolithic script) ──

def get_feature_groups(all_features: list[str]) -> dict[str, list[str]]:
    """Classify features into semantic groups by keyword matching."""
    groups: dict[str, list[str]] = {}
    groups["Size"] = [f for f in all_features if any(
        k in f for k in ("$total_mv", "$circ_mv", "log_mktcap",
                         "$total_assets", "$equity", "equity "))]
    groups["Valuation"] = [f for f in all_features if any(
        k in f for k in ("$pe", "$pb", "pe_ttm", "pb_raw", "pcf",
                         "ps_ttm", "operating_cf_to_profit"))]
    groups["Fundamental"] = [f for f in all_features if any(
        k in f for k in ("roa", "$roe", "net_margin", "$grossprofit_margin",
                         "grossprofit", "$revenue", "$net_income", "$op_cashflow",
                         "revenue_yoy", "profit_yoy", "$debt_to_assets",
                         "$current_ratio"))]
    groups["Margin"] = [f for f in all_features if any(
        k in f for k in ("lend_volume", "margin_balance", "margin_buy",
                         "margin_repay", "margin_total"))]
    groups["PriceVol"] = [f for f in all_features if "std(" in f.lower()
                          and "close" in f.lower() and "abs" not in f.lower()]
    groups["DollarVol"] = [f for f in all_features if "std(abs" in f.lower()
                           or ("std($" in f.lower() and "volume" in f.lower())
                           or ("std(" in f.lower() and "abs(" in f.lower())]
    groups["VolumeAmt"] = [f for f in all_features if any(
        k in f for k in ("turnover_rate", "amount_mean", "vol_mean",
                         "$amount", "$volume", "high_limit", "low_limit",
                         "illiquidity"))]
    groups["Momentum"] = [f for f in all_features if any(
        k in f for k in ("_ret_", "Slope(", "Rsquare(", "Resi(",
                         "stock_minus_index_ret"))]
    groups["PricePattern"] = [f for f in all_features if any(
        k in f for k in ("Max(", "Min(", "IdxMax", "IdxMin", "Quantile(",
                         "distance_to", "open_to_close", "close_to_open",
                         "$open/$close", "($close-$open)/$open"))]
    groups["Correlation"] = [f for f in all_features if f.startswith("Corr(")]
    assigned: set[str] = set()
    for v in groups.values():
        assigned.update(v)
    unassigned = [f for f in all_features if f not in assigned]
    if unassigned:
        groups["Other"] = unassigned
    return {k: v for k, v in groups.items() if len(v) >= 3}


def get_clean_features(all_features: list[str]) -> list[str]:
    """Return features after removing HARMFUL_GROUPS."""
    groups = get_feature_groups(all_features)
    to_remove: set[str] = set()
    for grp_name in HARMFUL_GROUPS:
        to_remove.update(groups.get(grp_name, []))
    return [f for f in all_features if f not in to_remove]


# ── Singleton ──

ALPHA_V1_CANDIDATE = AlphaV1CandidateConfig()
"""Single frozen instance shared by backtest and shadow trading."""
