"""Deterministic, dependency-scoped review of the current feature universe.

The catalog is deliberately static: it inventories definitions and contracts
without materialising the full feature panel.  Numerical checks remain scoped
to the feature lists that actually enter an experiment.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


CATALOG_COLUMNS = [
    "feature_id",
    "feature_name",
    "feature_kind",
    "family",
    "economic_meaning",
    "formula",
    "prior_direction",
    "raw_dependencies",
    "source_tables",
    "pit_tier",
    "availability_contract",
    "lookback_sessions",
    "min_periods_contract",
    "adjustment_contract",
    "unit",
    "missing_behavior",
    "divide_by_zero_behavior",
    "suspension_behavior",
    "price_limit_behavior",
    "runtime_mapping",
    "config_refs",
    "recommended_horizons",
    "future_reference_check",
    "label_contamination_check",
    "review_status",
    "review_notes",
]

TIERING_COLUMNS = [
    "feature_name",
    "category",
    "raw_dependencies",
    "source_tables",
    "pit_tier",
    "track",
    "saved_version_semantics",
    "availability_rule",
    "historical_versions",
    "evidence_counts",
    "review_status",
]

CONFIG_COVERAGE_COLUMNS = [
    "config_path",
    "config_sha256",
    "declared_id",
    "direct_feature_count",
    "extends",
    "declared_id_collision",
]

_RAW_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_WINDOW_RE = re.compile(r",\s*(\d+)\s*\)")
_NAME_WINDOW_RE = re.compile(r"(?:^|_)(\d+)(?:d|$)")
_FUTURE_REF_RE = re.compile(r"Ref\s*\([^)]*,\s*-\d+\s*\)", re.IGNORECASE)
_LABEL_RE = re.compile(r"(?:label|forward[_ ]?ret|future[_ ]?ret|target)", re.IGNORECASE)
_ROLLING_RE = re.compile(
    r"(?:Ref|Mean|Std|Slope|Rsquare|Resi|Rank|Max|Min|Sum|Corr|Cov)\s*\(",
    re.IGNORECASE,
)

_MARKET_FIELDS = {
    "open", "high", "low", "close", "vwap", "volume", "vol", "amount",
    "factor", "turnover_rate", "total_mv", "circ_mv", "float_shares",
    "total_share", "high_limit", "low_limit", "net_inflow", "big_inflow",
}
_PRICE_FIELDS = {"open", "high", "low", "close", "vwap"}
_DAILY_BASIC_FIELDS = {
    "turnover_rate", "total_mv", "circ_mv", "float_shares", "total_share",
    "pe", "pb", "ps", "ps_ttm",
}
_FINANCIAL_FIELD_TABLE = {
    "roe": "tushare.fina_indicator",
    "grossprofit_margin": "tushare.fina_indicator",
    "debt_to_assets": "tushare.fina_indicator",
    "current_ratio": "tushare.fina_indicator",
    "net_margin": "tushare.fina_indicator|tushare.income",
    "net_income": "tushare.income",
    "revenue": "tushare.income",
    "oper_cost": "tushare.income",
    "profit_yoy": "tushare.income",
    "total_assets": "tushare.balancesheet",
    "equity": "tushare.balancesheet",
    "inventory": "tushare.balancesheet",
    "accounts_receiv": "tushare.balancesheet",
    "ar": "tushare.balancesheet",
    "op_cashflow": "tushare.cashflow",
}
_MARGIN_FIELDS = {
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "margin_total_balance", "lend_volume", "lend_sell_volume",
    "lend_repay_volume", "margin_eligible_flag",
}
_SHAREHOLDER_FIELDS = {
    "holder_num", "holder_num_prev_ann", "holder_num_prev2_ann",
    "avg_shares_per_holder", "top10_holder_ratio",
    "top10_holder_ratio_prev_ann", "holder_real_ann_date",
    "top10_real_ann_date",
}
_INDUSTRY_FIELDS = {"industry", "industry_code"}
_INDEX_FIELDS = {
    "index_close", "index_ma20", "index_limit_up_count", "index_constituents",
    "small_index_close", "large_index_close", "growth_index_close",
    "value_index_close",
}

_NAMED_DEPENDENCY_OVERRIDES = {
    "earnings_yield_proxy": ["net_income", "total_mv"],
    "peg_proxy": ["pe", "profit_yoy"],
    "gross_margin_delta_yoy": ["revenue", "oper_cost"],
    "single_q_revenue_yoy": ["revenue"],
    "ttm_revenue_yoy": ["revenue"],
    "is_profitable_ttm": ["net_income"],
    "roa": ["net_income", "total_assets"],
    "net_margin": ["net_income", "revenue"],
    "continuation_candidate_score": ["close", "volume"],
    "repair_candidate_score": ["close", "pe"],
    "overheat_risk_score": ["close", "volume"],
    "value_trap_risk_score": [
        "close", "pe", "roe", "net_income", "revenue",
    ],
    "margin_crowding_score": ["margin_balance", "circ_mv"],
    "margin_trend_confirm_score": ["margin_balance", "close"],
    "margin_overheat_risk_score": ["margin_balance", "close"],
    "margin_holder_trend_confirm": ["margin_balance", "top10_holder_ratio", "close"],
    "margin_pullback_recovery_confirm": ["margin_balance", "close"],
}

_CATALOG_GROUP_DEFAULTS = {
    "growth_confirmation_v0": ["close"],
    "microstructure": ["open", "high", "low", "close"],
    "liquidity": ["close", "volume", "amount", "turnover_rate", "industry"],
    "tradability": ["open", "close", "volume", "high_limit", "low_limit"],
    "relative_strength": ["close", "volume", "amount", "industry"],
    "regime": ["index_close"],
    "industry_context": ["close", "industry"],
    "fundamental_context": ["total_mv", "circ_mv", "pe", "pb"],
    "v3a_margin": ["margin_balance", "margin_buy_amount", "margin_repay_amount"],
    "v3a_shareholder": ["holder_num", "top10_holder_ratio"],
    "v3b_price_volume": ["close", "volume", "amount"],
    "v3b_interaction": ["close", "volume"],
    "industry_momentum": ["close", "amount", "industry"],
}

_GROWTH_FORECAST_FEATURES = {
    "forecast_type_score", "forecast_stale_days", "has_forecast",
}
_GROWTH_MARKET_FEATURES = {"breakout_252d_high", "days_since_252d_high"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _joined(values: list[str] | set[str] | tuple[str, ...]) -> str:
    return "|".join(_stable_unique([str(value) for value in values if str(value)]))


def _feature_id(name: str) -> str:
    return f"feature_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]}"


class FeatureCatalog:
    """Generate a complete static catalog through the analytics entrypoint."""

    def __init__(self, config_path: str | Path, *, root: str | Path = "data/research"):
        self.repo_root = Path(__file__).resolve().parents[2]
        self.config_path = Path(config_path).resolve()
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(self.config, dict):
            raise TypeError("feature catalog config must be a mapping")
        self.catalog_id = str(self.config.get("catalog_id", "")).strip()
        if not self.catalog_id:
            raise ValueError("feature catalog config requires catalog_id")
        self.output_dir = Path(root).resolve() / "feature_catalogs" / self.catalog_id
        self.feature_config_root = (
            self.repo_root / str(self.config.get("feature_config_root", "configs/features"))
        ).resolve()

    @classmethod
    def from_config(
        cls, config_path: str | Path, *, root: str | Path = "data/research"
    ) -> "FeatureCatalog":
        return cls(config_path, root=root)

    def _feature_sources(self) -> tuple[list[str], dict[str, list[str]], list[dict[str, Any]]]:
        from qsys.feature.library import FeatureLibrary
        from qsys.feature.registry import FEATURE_GROUPS

        runtime = list(FeatureLibrary.get_semantic_all_features_config())
        refs: dict[str, list[str]] = defaultdict(list)
        coverage: list[dict[str, Any]] = []
        config_features: list[str] = []
        for path in sorted(self.feature_config_root.rglob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict):
                raise TypeError(f"feature config must be a mapping: {path}")
            features = payload.get("features", [])
            if not isinstance(features, list) or not all(
                isinstance(value, str) for value in features
            ):
                raise TypeError(f"feature config features must be list[str]: {path}")
            relative = path.relative_to(self.repo_root).as_posix()
            for feature in features:
                refs[feature].append(relative)
                config_features.append(feature)
            coverage.append(
                {
                    "config_path": relative,
                    "config_sha256": _sha256(path),
                    "declared_id": str(
                        payload.get("feature_list_id")
                        or payload.get("feature_set_id")
                        or ""
                    ),
                    "direct_feature_count": len(features),
                    "extends": str(payload.get("extends", "")),
                }
            )
        registry = [
            feature
            for group in FEATURE_GROUPS.values()
            for feature in group.get("features", [])
        ]
        declared_id_counts: dict[str, int] = defaultdict(int)
        for item in coverage:
            if item["declared_id"]:
                declared_id_counts[item["declared_id"]] += 1
        for item in coverage:
            item["declared_id_collision"] = (
                "true"
                if item["declared_id"]
                and declared_id_counts[item["declared_id"]] > 1
                else "false"
            )
        universe = _stable_unique(runtime + registry + config_features)
        return universe, refs, coverage

    @staticmethod
    def _groups() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        from qsys.feature.registry import FEATURE_GROUPS

        feature_groups: dict[str, list[str]] = defaultdict(list)
        for group_name, payload in FEATURE_GROUPS.items():
            for feature in payload.get("features", []):
                feature_groups[feature].append(group_name)
        return feature_groups, {
            name: list(payload.get("features", []))
            for name, payload in FEATURE_GROUPS.items()
        }

    @staticmethod
    def _kind(feature: str) -> str:
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", feature):
            return "raw_qlib_field"
        if "$" in feature or re.search(r"[A-Za-z]+\s*\(", feature):
            return "qlib_expression"
        return "named_builder_feature"

    @staticmethod
    def _dependencies(feature: str, kind: str, group: str) -> list[str]:
        if kind != "named_builder_feature":
            return _stable_unique(_RAW_RE.findall(feature))
        if feature in _NAMED_DEPENDENCY_OVERRIDES:
            dependencies = list(_NAMED_DEPENDENCY_OVERRIDES[feature])
        elif feature in _GROWTH_FORECAST_FEATURES:
            dependencies = ["forecast_ann_date"]
        elif feature in _GROWTH_MARKET_FEATURES:
            dependencies = ["close"]
        else:
            from qsys.feature.resolver import _required_fields

            dependencies = _required_fields(feature, group or None)
            if not dependencies:
                dependencies = list(_CATALOG_GROUP_DEFAULTS.get(group, []))
        if set(dependencies) & _PRICE_FIELDS:
            dependencies = [*dependencies, "factor"]
        return _stable_unique(dependencies)

    @staticmethod
    def _family(feature: str, kind: str, groups: list[str], deps: list[str]) -> str:
        if groups:
            return groups[0]
        lowered = feature.lower()
        dep_set = set(deps)
        if dep_set & _SHAREHOLDER_FIELDS:
            return "shareholder_structure"
        if dep_set & _MARGIN_FIELDS:
            return "financing_crowding"
        if dep_set & set(_FINANCIAL_FIELD_TABLE):
            return "fundamental_quality_growth"
        if dep_set & _INDUSTRY_FIELDS or "industry" in lowered:
            return "industry_context"
        if dep_set & {"pe", "pb", "ps", "total_mv", "circ_mv"}:
            return "valuation_size"
        if dep_set & {"volume", "vol", "amount", "turnover_rate"} and not dep_set & _PRICE_FIELDS:
            return "liquidity_participation"
        if dep_set & _PRICE_FIELDS:
            if _ROLLING_RE.search(feature):
                return "market_path_momentum_volatility"
            return "intraday_price_structure"
        return "other_or_raw"

    @staticmethod
    def _meaning(feature: str, kind: str, group: str) -> str:
        if kind == "named_builder_feature":
            from qsys.feature.resolver import _formula

            return _formula(feature, group or None)
        if kind == "raw_qlib_field":
            return f"Direct historical observation of {feature[1:]}"
        return "Exact Qlib expression; economic interpretation follows its family and operators"

    @staticmethod
    def _source_table(dependency: str) -> str:
        if dependency.startswith("top10_"):
            return "tushare.top10_holders"
        if dependency in _SHAREHOLDER_FIELDS:
            return "tushare.stk_holdernumber"
        if dependency in _MARGIN_FIELDS:
            return "tushare.margin"
        if dependency in _FINANCIAL_FIELD_TABLE:
            return _FINANCIAL_FIELD_TABLE[dependency]
        if dependency in _INDUSTRY_FIELDS:
            return "tushare.bak_basic"
        if dependency in _DAILY_BASIC_FIELDS:
            return "tushare.daily_basic"
        if dependency in {"high_limit", "low_limit"}:
            return "tushare.stk_limit"
        if dependency in {"net_inflow", "big_inflow"}:
            return "tushare.moneyflow"
        if dependency == "factor":
            return "tushare.adj_factor"
        if dependency in _MARKET_FIELDS or dependency in _INDEX_FIELDS:
            return "tushare.daily"
        if dependency.startswith("forecast"):
            return "forecast_source_unbound"
        return "unresolved_dependency"

    @classmethod
    def _tier(cls, feature: str, deps: list[str]) -> tuple[str, str]:
        source_tables = _stable_unique(
            table
            for dependency in deps
            for table in cls._source_table(dependency).split("|")
        )
        if any(dependency in _SHAREHOLDER_FIELDS for dependency in deps):
            return "PIT-X", _joined(source_tables)
        if any(dependency.startswith("forecast") for dependency in deps):
            return "PIT-X", _joined(source_tables)
        if any(dependency in _FINANCIAL_FIELD_TABLE for dependency in deps):
            return "PIT-B", _joined(source_tables)
        if "unresolved_dependency" in source_tables or not deps:
            return "PIT-X", _joined(source_tables or ["unresolved_dependency"])
        return "PIT-A", _joined(source_tables)

    @staticmethod
    def _availability(tier: str, tables: str) -> str:
        if tier == "PIT-B":
            return (
                "first-publication-only; publication after close is consumable only "
                "from the next trading session; blocked/unorderable keys remain missing"
            )
        if tier == "PIT-X":
            return "unresolved historical visibility; provisional track only"
        if "tushare.margin" in tables:
            return "daily snapshot consumed with one exact open-session lag"
        if "tushare.bak_basic" in tables:
            return "after-close daily industry snapshot consumed next session; daily taxonomy bound"
        return "historical daily snapshot consumed only after the signal cutoff"

    @staticmethod
    def _lookback(feature: str, kind: str) -> tuple[str, str]:
        windows = [int(value) for value in _WINDOW_RE.findall(feature)]
        if kind == "named_builder_feature":
            windows.extend(int(value) for value in _NAME_WINDOW_RE.findall(feature))
        if not windows:
            if "qoq" in feature:
                return "1 announcement interval", "2 non-null announcement events"
            if "2q" in feature:
                return "2 announcement intervals", "3 non-null announcement events"
            return "0", "1 observed value"
        maximum = max(windows)
        if maximum == 756:
            minimum = "180 sessions where rolling implementation applies"
        elif maximum == 252:
            minimum = "60 sessions where rolling implementation applies"
        elif maximum >= 120:
            minimum = f"{max(40, maximum // 4)} sessions or implementation-specific shift history"
        elif maximum >= 60:
            minimum = f"{max(20, maximum // 3)} sessions or implementation-specific shift history"
        elif maximum >= 20:
            minimum = f"{max(5, maximum // 2)} sessions or full Qlib operator window"
        else:
            minimum = "full operator window"
        return str(maximum), minimum

    @staticmethod
    def _adjustment(feature: str, deps: list[str]) -> str:
        if not set(deps) & _PRICE_FIELDS:
            return "not_applicable"
        cross_date = bool(_ROLLING_RE.search(feature)) or bool(_NAME_WINDOW_RE.search(feature))
        if not cross_date:
            return "same-session price relation; scale invariant or raw observation"
        if "factor" in deps:
            return "explicit adjusted-price history via factor"
        return "cross-date price history without explicit factor; dependency review required"

    @staticmethod
    def _unit(feature: str, kind: str, deps: list[str]) -> str:
        if kind == "raw_qlib_field":
            raw = feature[1:]
            if raw in _PRICE_FIELDS:
                return "CNY/share (source price basis)"
            if raw in {"volume", "vol"}:
                return "shares"
            if raw in {"amount", "total_mv", "circ_mv"} or raw in _MARGIN_FIELDS:
                return "source monetary/quantity unit; adapter contract applies"
            if raw in {"roe", "grossprofit_margin", "debt_to_assets"}:
                return "ratio; fina_indicator percent-points converted once at canonical boundary"
            if raw == "industry":
                return "taxonomy category"
        if any(token in feature.lower() for token in ("rank", "ratio", "ret", "score", "percentile")):
            return "dimensionless"
        if "/" in feature or kind == "qlib_expression":
            return "dimensionless unless expression is a direct level"
        return "implementation-defined; see raw dependency units"

    @staticmethod
    def _direction(feature: str, family: str) -> str:
        lowered = feature.lower()
        if any(token in lowered for token in ("risk", "illiquidity", "drawdown", "stale_days")):
            return "negative prior"
        if any(token in lowered for token in ("momentum", "rps", "profit", "roe", "quality", "ret_")):
            return "positive prior"
        if any(token in lowered for token in ("pe_", "pb_", "$pe", "$pb")):
            return "negative valuation prior"
        if family == "market_path_momentum_volatility":
            return "empirical; sign depends on path operator and horizon"
        return "empirical; evaluate both signs without outcome-based relabeling"

    @staticmethod
    def _horizons(family: str, lookback: str, tier: str) -> str:
        if "shareholder" in family:
            return "120|180 (provisional only)"
        if "fundamental" in family:
            return "60|120|180"
        if "financing" in family:
            return "20|60"
        if "industry" in family or family == "regime":
            return "20|60|120|180"
        if family in {"microstructure", "tradability", "intraday_price_structure"}:
            return "5|10"
        try:
            window = int(lookback)
        except ValueError:
            window = 0
        if window <= 10:
            return "5|10|20"
        if window <= 60:
            return "10|20|60"
        return "60|120|180"

    @staticmethod
    def _review(
        tier: str,
        future_check: str,
        label_check: str,
        adjustment: str,
    ) -> tuple[str, str]:
        notes: list[str] = []
        if future_check != "pass" or label_check != "pass":
            return "rejected", "static lookahead or label-contamination pattern detected"
        if tier == "PIT-X":
            return "data-blocked", "dependency visibility is unresolved; provisional track only"
        if tier == "PIT-B":
            notes.append("conservative first-publication-only contract")
        if "review required" in adjustment:
            notes.append("cross-date adjustment semantics require numerical review before use")
        if notes:
            return "dependency-review-required", "; ".join(notes)
        return "reviewed-static", "static definition and dependency contract reviewed"

    def _row(
        self,
        feature: str,
        *,
        runtime: set[str],
        refs: dict[str, list[str]],
        feature_groups: dict[str, list[str]],
    ) -> dict[str, Any]:
        groups = feature_groups.get(feature, [])
        group = groups[0] if groups else ""
        kind = self._kind(feature)
        deps = self._dependencies(feature, kind, group)
        family = self._family(feature, kind, groups, deps)
        tier, source_tables = self._tier(feature, deps)
        lookback, min_periods = self._lookback(feature, kind)
        adjustment = self._adjustment(feature, deps)
        future_check = "fail" if _FUTURE_REF_RE.search(feature) else "pass"
        label_check = "fail" if _LABEL_RE.search(feature) else "pass"
        review_status, review_notes = self._review(
            tier, future_check, label_check, adjustment
        )
        mappings: list[str] = []
        if feature in runtime:
            mappings.append("current_runtime_universe")
        mappings.extend(f"registry:{value}" for value in groups)
        if kind == "qlib_expression":
            mappings.append("direct_qlib_expression")
        elif kind == "raw_qlib_field":
            mappings.append("direct_qlib_field")
        else:
            mappings.append("phase1_builder")
        formula = feature if kind != "named_builder_feature" else self._meaning(feature, kind, group)
        return {
            "feature_id": _feature_id(feature),
            "feature_name": feature,
            "feature_kind": kind,
            "family": family,
            "economic_meaning": self._meaning(feature, kind, group),
            "formula": formula,
            "prior_direction": self._direction(feature, family),
            "raw_dependencies": _joined(deps),
            "source_tables": source_tables,
            "pit_tier": tier,
            "availability_contract": self._availability(tier, source_tables),
            "lookback_sessions": lookback,
            "min_periods_contract": min_periods,
            "adjustment_contract": adjustment,
            "unit": self._unit(feature, kind, deps),
            "missing_behavior": (
                "preserve NaN; no cross-date backfill except an explicitly declared PIT event projection"
            ),
            "divide_by_zero_behavior": (
                "denominator zero must become NaN" if "/" in formula.lower() or "ratio" in feature.lower()
                else "not_applicable_or_implementation_guarded"
            ),
            "suspension_behavior": (
                "no fabricated bar; PIT membership/entry eligibility governs consumption"
                if set(deps) & _MARKET_FIELDS else
                "event value may remain known; trading eligibility is evaluated separately"
            ),
            "price_limit_behavior": (
                "retain observed limit state; never filter using future exit status"
                if set(deps) & (_MARKET_FIELDS | {"high_limit", "low_limit"}) else
                "not_applicable_to_feature_value; execution layer remains authoritative"
            ),
            "runtime_mapping": _joined(mappings),
            "config_refs": _joined(refs.get(feature, [])),
            "recommended_horizons": self._horizons(family, lookback, tier),
            "future_reference_check": future_check,
            "label_contamination_check": label_check,
            "review_status": review_status,
            "review_notes": review_notes,
        }

    def _tiering_rows(self, catalog_by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        audit_path = self.repo_root / str(self.config["evidence"]["source_revision_audit"])
        audit = yaml.safe_load(audit_path.read_text(encoding="utf-8")) or {}
        financial_counts = audit["financial"]["expected_r3_counts"]
        shareholder_counts = audit["shareholder"]["expected_r3_counts"]
        rows: list[dict[str, Any]] = []
        disputed = self.config.get("disputed_features", {})
        for item in disputed.get("financial", []):
            feature = str(item["feature"])
            catalog = catalog_by_name[feature]
            endpoints = list(item.get("endpoints", []))
            endpoint_counts = financial_counts.get("by_endpoint", {})
            evidence = []
            for endpoint in endpoints:
                counts = endpoint_counts.get(endpoint, {})
                evidence.append(
                    f"{endpoint}:complete={counts.get('complete_keys', 0)},"
                    f"blocked={counts.get('blocked_keys', 0)},"
                    f"conflicts={counts.get('same_publication_conflict_keys', 0)}"
                )
            rows.append(
                {
                    "feature_name": feature,
                    "category": "financial",
                    "raw_dependencies": catalog["raw_dependencies"],
                    "source_tables": catalog["source_tables"],
                    "pit_tier": "PIT-B",
                    "track": "certified_core_first_publication_only",
                    "saved_version_semantics": (
                        "earliest proven initial value only; later revisions are intentionally ignored; "
                        "right-censored/conflicting keys are missing"
                    ),
                    "availability_rule": (
                        "latest upstream first-publication boundary; after close and strictly "
                        "earlier than the consuming trade date"
                        if len(endpoints) > 1
                        else "ann_date after close; strictly later trade date"
                        if endpoints == ["fina_indicator"]
                        else "max(ann_date,f_ann_date) after close; strictly later trade date"
                    ),
                    "historical_versions": (
                        "source contains orderable events for complete keys; feature projection keeps "
                        "the first proven publication only"
                    ),
                    "evidence_counts": ";".join(evidence),
                    "review_status": "PIT-B accepted with explicit downgrade",
                }
            )
        for item in disputed.get("shareholder", []):
            feature = str(item["feature"])
            catalog = catalog_by_name[feature]
            endpoints = list(item.get("endpoints", []))
            rows.append(
                {
                    "feature_name": feature,
                    "category": "shareholder",
                    "raw_dependencies": catalog["raw_dependencies"],
                    "source_tables": catalog["source_tables"],
                    "pit_tier": "PIT-X",
                    "track": "provisional_value_of_data",
                    "saved_version_semantics": (
                        "one frozen supplier snapshot; two receipted vintages are byte-identical"
                    ),
                    "availability_rule": (
                        "ann_date is a declared announcement date, not a revision timestamp"
                    ),
                    "historical_versions": (
                        f"historical_revision_timeline_proven_keys="
                        f"{shareholder_counts['historical_revision_timeline_proven_keys']}"
                    ),
                    "evidence_counts": (
                        f"source_vintages={shareholder_counts['source_vintages']};"
                        f"exact_event_keys={shareholder_counts['exact_event_keys']}"
                    ),
                    "review_status": "data-blocked for confirmed research",
                }
            )
        return rows

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def _input_hashes(self, coverage: list[dict[str, Any]]) -> dict[str, str]:
        paths = [
            self.config_path,
            Path(__file__).resolve(),
            self.repo_root / "qsys/feature/library.py",
            self.repo_root / "qsys/feature/registry.py",
            self.repo_root / "qsys/feature/resolver.py",
            self.repo_root / "qsys/feature/builder.py",
            self.repo_root / "qsys/research/feature_catalog_validation.py",
            self.repo_root / "scripts/run_signal_analytics.py",
        ]
        for value in self.config.get("evidence", {}).values():
            paths.append(self.repo_root / str(value))
        for item in coverage:
            paths.append(self.repo_root / item["config_path"])
        return {
            path.relative_to(self.repo_root).as_posix()
            if path.is_relative_to(self.repo_root)
            else path.as_posix(): _sha256(path)
            for path in sorted(set(paths))
        }

    def run(self) -> dict[str, Any]:
        from qsys.feature.library import FeatureLibrary

        universe, refs, coverage = self._feature_sources()
        runtime_list = list(FeatureLibrary.get_semantic_all_features_config())
        runtime = set(runtime_list)
        feature_groups, groups = self._groups()
        rows = [
            self._row(
                feature,
                runtime=runtime,
                refs=refs,
                feature_groups=feature_groups,
            )
            for feature in universe
        ]
        by_name = {row["feature_name"]: row for row in rows}
        tiering = self._tiering_rows(by_name)
        expected_min = int(self.config.get("expected_min_unique_features", 300))
        if len(rows) < expected_min:
            raise ValueError(
                f"feature universe is unexpectedly small: {len(rows)} < {expected_min}"
            )
        if len(by_name) != len(rows):
            raise ValueError("feature universe contains duplicate feature names")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        catalog_csv = self.output_dir / "feature_catalog.csv"
        catalog_json = self.output_dir / "feature_catalog.json"
        tiering_csv = self.output_dir / "pit_tiering.csv"
        coverage_csv = self.output_dir / "config_coverage.csv"
        summary_json = self.output_dir / "review_summary.json"
        manifest_json = self.output_dir / "manifest.json"
        self._write_csv(catalog_csv, rows, CATALOG_COLUMNS)
        catalog_json.write_text(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_csv(tiering_csv, tiering, TIERING_COLUMNS)
        self._write_csv(coverage_csv, coverage, CONFIG_COVERAGE_COLUMNS)

        def counts(column: str) -> dict[str, int]:
            result: dict[str, int] = defaultdict(int)
            for row in rows:
                result[str(row[column])] += 1
            return dict(sorted(result.items()))

        summary = {
            "schema_version": "feature_universe_review_summary_v1",
            "catalog_id": self.catalog_id,
            "unique_feature_count": len(rows),
            "runtime_feature_count": len(runtime_list),
            "registry_group_count": len(groups),
            "registry_unique_feature_count": len(set().union(*map(set, groups.values()))),
            "feature_config_count": len(coverage),
            "feature_config_reference_count": sum(item["direct_feature_count"] for item in coverage),
            "feature_config_id_collisions": {
                declared_id: sorted(
                    item["config_path"]
                    for item in coverage
                    if item["declared_id"] == declared_id
                )
                for declared_id in sorted(
                    {
                        item["declared_id"]
                        for item in coverage
                        if item["declared_id_collision"] == "true"
                    }
                )
            },
            "feature_kind_counts": counts("feature_kind"),
            "pit_tier_counts": counts("pit_tier"),
            "review_status_counts": counts("review_status"),
            "future_reference_failures": sum(row["future_reference_check"] != "pass" for row in rows),
            "label_contamination_failures": sum(row["label_contamination_check"] != "pass" for row in rows),
            "explicit_adjustment_review_count": sum(
                "review required" in row["adjustment_contract"] for row in rows
            ),
            "disputed_financial_count": sum(row["category"] == "financial" for row in tiering),
            "disputed_shareholder_count": sum(row["category"] == "shareholder" for row in tiering),
            "catalog_rows_sha256": _canonical_sha256(rows),
            "pit_tiering_rows_sha256": _canonical_sha256(tiering),
        }
        summary_json.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        inputs = self._input_hashes(coverage)
        artifact_hashes = {
            path.name: _sha256(path)
            for path in (catalog_csv, catalog_json, tiering_csv, coverage_csv, summary_json)
        }
        identity_basis = {
            "catalog_id": self.catalog_id,
            "inputs": inputs,
            "artifact_hashes": artifact_hashes,
            "catalog_rows_sha256": summary["catalog_rows_sha256"],
            "pit_tiering_rows_sha256": summary["pit_tiering_rows_sha256"],
        }
        manifest = {
            "schema_version": "feature_universe_catalog_manifest_v1",
            "catalog_id": self.catalog_id,
            "catalog_identity_sha256": _canonical_sha256(identity_basis),
            "qlib_version": importlib.metadata.version("pyqlib"),
            "config_path": self.config_path.relative_to(self.repo_root).as_posix(),
            "output_scope": "static_dependency_review_no_bulk_feature_materialization",
            "inputs": inputs,
            "artifacts": artifact_hashes,
            "summary": summary,
        }
        manifest_json.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "catalog_identity_sha256": manifest["catalog_identity_sha256"],
            "manifest": str(manifest_json),
            "summary": summary,
        }
