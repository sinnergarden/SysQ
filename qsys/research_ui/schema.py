from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal


PriceMode = Literal["raw", "fq"]
RunType = Literal["daily_ops", "feature_run", "signal_run", "backtest", "decision_replay", "case_bundle"]
ArtifactKind = Literal[
    "manifest",
    "report",
    "signal_basket",
    "order_intents",
    "plan",
    "bars",
    "feature_registry",
    "feature_snapshot",
    "feature_health",
    "backtest_daily",
    "decision_replay",
    "case_bundle",
    "ledger",
    "other",
]
FeatureSourceLayer = Literal["raw", "qlib_native", "semantic_derived", "daily_derived"]
ValueKind = Literal["scalar", "boolean", "category", "series"]


@dataclass(frozen=True)
class RunArtifactRef:
    artifact_id: str
    kind: ArtifactKind
    logical_path: str
    title: str = ""
    media_type: str = "application/json"
    stage: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    run_type: RunType
    status: str
    signal_date: str | None = None
    execution_date: str | None = None
    trade_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    account_name: str | None = None
    model_info: dict[str, Any] = field(default_factory=dict)
    data_status: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    artifacts: list[RunArtifactRef] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifacts"] = [item.to_dict() for item in self.artifacts]
        return payload


@dataclass(frozen=True)
class FeatureRegistryEntry:
    feature_id: str
    feature_name: str
    display_name: str
    group_name: str
    source_layer: FeatureSourceLayer
    dtype: str
    value_kind: ValueKind
    description: str
    formula: str = ""
    dependencies: list[str] = field(default_factory=list)
    supports_snapshot: bool = True
    tags: list[str] = field(default_factory=list)
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureHealthEntry:
    feature_name: str
    coverage_ratio: float
    nan_ratio: float
    inf_ratio: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureHealthSummary:
    run_id: str
    trade_date: str
    universe: str
    price_mode_context: PriceMode
    feature_count: int
    instrument_count: int
    overall_missing_ratio: float
    features: list[FeatureHealthEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    manifest_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["features"] = [item.to_dict() for item in self.features]
        return payload


@dataclass(frozen=True)
class BacktestDailyPoint:
    trade_date: str
    equity: float | None = None
    zero_cost_equity: float | None = None
    daily_return: float | None = None
    drawdown: float | None = None
    benchmark_equity: float | None = None
    benchmark_daily_return: float | None = None
    benchmark2_equity: float | None = None
    benchmark2_daily_return: float | None = None
    turnover: float | None = None
    ic: float | None = None
    rank_ic: float | None = None
    trade_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestRunSummary:
    run_id: str
    run_type: str
    model_name: str
    feature_set: str
    universe: str
    train_range: dict[str, str | None] = field(default_factory=dict)
    test_range: dict[str, str | None] = field(default_factory=dict)
    top_k: int | None = None
    price_mode: PriceMode = "fq"
    display_label: str | None = None
    parameter_summary: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    signal_metrics: dict[str, Any] = field(default_factory=dict)
    group_returns_summary: dict[str, Any] = field(default_factory=dict)
    artifacts: list[RunArtifactRef] = field(default_factory=list)
    manifest_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifacts"] = [item.to_dict() for item in self.artifacts]
        return payload


@dataclass(frozen=True)
class DecisionCandidate:
    instrument_id: str
    raw_score: float | None = None
    adjusted_score: float | None = None
    rank: int | None = None
    selected: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)
    constraint_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionOrder:
    instrument_id: str
    side: str
    quantity: int
    price: float | None = None
    est_value: float | None = None
    status: str = "planned"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionReplay:
    run_id: str
    trade_date: str
    signal_date: str | None = None
    execution_date: str | None = None
    account_name: str | None = None
    previous_positions: list[dict[str, Any]] = field(default_factory=list)
    candidate_pool: list[str] = field(default_factory=list)
    scored_candidates: list[DecisionCandidate] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    selected_targets: list[str] = field(default_factory=list)
    final_orders: list[DecisionOrder] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    manifest_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scored_candidates"] = [item.to_dict() for item in self.scored_candidates]
        payload["final_orders"] = [item.to_dict() for item in self.final_orders]
        return payload


@dataclass(frozen=True)
class CaseBundleLink:
    label: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseBundle:
    case_id: str
    run_id: str
    instrument_id: str
    trade_date: str
    signal_date: str | None = None
    execution_date: str | None = None
    price_mode: PriceMode = "fq"
    bars: list[dict[str, Any]] = field(default_factory=list)
    benchmark_bars: list[dict[str, Any]] = field(default_factory=list)
    benchmark_label: str = "CSI300"
    secondary_benchmark_bars: list[dict[str, Any]] = field(default_factory=list)
    secondary_benchmark_label: str = "SSE"
    signal_snapshot: dict[str, Any] = field(default_factory=dict)
    feature_snapshot: dict[str, Any] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    links: list[CaseBundleLink] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["links"] = [item.to_dict() for item in self.links]
        return payload


def schema_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return asdict(value)
    if isinstance(value, list):
        return [schema_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: schema_to_dict(item) for key, item in value.items()}
    return value
