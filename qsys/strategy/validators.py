"""StrategySpec validation — stage-specific config checks.

These validators check that a ``StrategySpec`` has all required fields for its
lifecycle stage.  They are **not** pydantic — plain Python functions returning
a list of error messages.

Usage::

    from qsys.strategy.validators import validate_strategy_spec

    errors = validate_strategy_spec(spec)
    if errors:
        for err in errors:
            print(f"  ✗ {err}")
"""

from __future__ import annotations

from qsys.strategy.spec import StrategySpec

# ── Helpers ──────────────────────────────────────────────────────────────────


def _missing(name: str) -> str:
    return f"missing required field: {name}"


def _empty(value: object) -> bool:
    """Return ``True`` if *value* is None or an empty string."""
    return value is None or (isinstance(value, str) and not value)


# ── Stage validators ─────────────────────────────────────────────────────────


def validate_research_spec(spec: StrategySpec) -> list[str]:
    """Validate a **research**-stage ``StrategySpec``.

    Research requires core identity fields and research metadata but does
    **not** require a registry entry, ``account_id``, or production risk
    limits.
    """
    errors: list[str] = []

    if _empty(spec.strategy_id):
        errors.append(_missing("strategy_id"))
    if spec.stage != "research":
        errors.append(f"expected stage='research', got {spec.stage!r}")
    if _empty(spec.family):
        errors.append(_missing("family"))
    if _empty(spec.display_name):
        errors.append(_missing("display_name"))
    if _empty(spec.universe):
        errors.append(_missing("universe"))
    if _empty(spec.feature_set):
        errors.append(_missing("feature_set"))
    if _empty(spec.hypothesis):
        errors.append(_missing("hypothesis (research hypothesis)"))
    if not spec.label:
        errors.append(_missing("label (label configuration)"))
    if not spec.model:
        errors.append(_missing("model (model configuration)"))
    if not spec.signal:
        errors.append(_missing("signal (signal configuration)"))
    if not spec.portfolio:
        errors.append(_missing("portfolio (portfolio construction parameters)"))

    return errors


def validate_candidate_spec(spec: StrategySpec) -> list[str]:
    """Validate a **candidate**-stage ``StrategySpec``.

    Candidate requires all research-level fields plus runtime identity and
    shadow configuration.  The strategy must be registered in
    ``qsys.strategy.registry``.
    """
    errors: list[str] = []

    # Research-level fields (subset relevant to runtime)
    if _empty(spec.strategy_id):
        errors.append(_missing("strategy_id"))
    if spec.stage != "candidate":
        errors.append(f"expected stage='candidate', got {spec.stage!r}")
    if _empty(spec.display_name):
        errors.append(_missing("display_name"))
    if _empty(spec.universe):
        errors.append(_missing("universe"))
    if _empty(spec.feature_set):
        errors.append(_missing("feature_set"))
    if not spec.portfolio:
        errors.append(_missing("portfolio (portfolio construction parameters)"))

    # Candidate-specific requirements
    if _empty(spec.account_id):
        errors.append(_missing("account_id (shadow account id)"))
    elif not spec.account_id.startswith("shadow_"):
        errors.append(
            f"account_id should start with 'shadow_' (got {spec.account_id!r})"
        )

    if not spec.paths:
        errors.append(_missing("paths (predictions_dir, ledger_db)"))
    else:
        # Models are resolved from the pinned model_version/manifest identity;
        # a mutable filesystem model_dir is not a candidate requirement.
        for key in ("predictions_dir", "ledger_db"):
            if key not in spec.paths:
                errors.append(_missing(f"paths.{key}"))

    # Model / signal version
    has_model_version = not _empty(spec.model_version) or (
        isinstance(spec.model, dict) and not _empty(spec.model.get("version"))
    )
    if not has_model_version:
        errors.append(
            _missing("model_version or model.version (model version identifier)")
        )

    has_signal_version = not _empty(spec.signal_version) or (
        isinstance(spec.signal, dict) and not _empty(spec.signal.get("version"))
    )
    if not has_signal_version:
        errors.append(
            _missing("signal_version or signal.version (signal version identifier)")
        )

    # Portfolio parameters
    if isinstance(spec.portfolio, dict):
        for key in ("top_n",):
            if key not in spec.portfolio:
                errors.append(_missing(f"portfolio.{key}"))

    # Must be registered
    errors.extend(validate_runtime_registry(spec))

    # Must not have production broker config
    if _has_production_config(spec):
        errors.append(
            "production broker config detected — candidate stage must not "
            "have broker_policy, execution_policy, or risk_limits"
        )

    return errors


def validate_production_spec(spec: StrategySpec) -> list[str]:
    """Validate a **production**-stage ``StrategySpec``.

    Production requires all candidate-level fields plus risk controls,
    capital allocation, broker/execution policy, and approval policy.
    """
    errors: list[str] = []

    # Candidate-level fields
    if _empty(spec.strategy_id):
        errors.append(_missing("strategy_id"))
    if spec.stage != "production":
        errors.append(f"expected stage='production', got {spec.stage!r}")
    if _empty(spec.account_id):
        errors.append(_missing("account_id"))
    if not spec.portfolio:
        errors.append(_missing("portfolio"))

    # Must be registered
    errors.extend(validate_runtime_registry(spec))

    # Production-specific requirements
    limits = _get_nested(spec.raw_config, "risk_limits", {})
    if not limits:
        errors.append(_missing("risk_limits (production risk limits)"))

    allocation = _get_nested(spec.raw_config, "capital_allocation", {})
    if not allocation:
        errors.append(_missing("capital_allocation"))

    broker_policy = _get_nested(spec.raw_config, "broker_policy", {})
    exec_policy = _get_nested(spec.raw_config, "execution_policy", {})
    if not broker_policy and not exec_policy:
        errors.append(
            _missing("broker_policy or execution_policy (execution policy)")
        )

    approval = _get_nested(spec.raw_config, "approval_policy", {})
    if not approval:
        errors.append(_missing("approval_policy (production approval gate)"))

    return errors


def validate_rejected_spec(spec: StrategySpec) -> list[str]:
    """Validate a **rejected**-stage ``StrategySpec``.

    Only ``strategy_id`` and ``stage`` are required.
    """
    errors: list[str] = []
    if _empty(spec.strategy_id):
        errors.append(_missing("strategy_id"))
    if spec.stage != "rejected":
        errors.append(f"expected stage='rejected', got {spec.stage!r}")
    return errors


def validate_archived_spec(spec: StrategySpec) -> list[str]:
    """Validate an **archived**-stage ``StrategySpec``.

    Only ``strategy_id`` and ``stage`` are required.
    """
    errors: list[str] = []
    if _empty(spec.strategy_id):
        errors.append(_missing("strategy_id"))
    if spec.stage != "archived":
        errors.append(f"expected stage='archived', got {spec.stage!r}")
    return errors


def validate_runtime_registry(spec: StrategySpec) -> list[str]:
    """Check that *spec* is registered in the runtime strategy registry.

    Returns a list with one error message if the strategy is unknown.
    """
    from qsys.strategy.registry import get_strategy_class

    try:
        get_strategy_class(spec.strategy_id)
        return []
    except ValueError:
        return [
            f"strategy {spec.strategy_id!r} is not registered in "
            f"qsys.strategy.registry"
        ]


# ── Composite validator ──────────────────────────────────────────────────────


_VALIDATORS = {
    "research": validate_research_spec,
    "candidate": validate_candidate_spec,
    "production": validate_production_spec,
    "rejected": validate_rejected_spec,
    "archived": validate_archived_spec,
}


def validate_strategy_spec(
    spec: StrategySpec,
    *,
    strict: bool = False,
) -> list[str]:
    """Validate *spec* against its stage requirements.

    Parameters
    ----------
    spec
        The ``StrategySpec`` to validate.
    strict
        If ``True``, raise ``ValueError`` when errors are found.

    Returns
    -------
    list[str]
        Validation error messages (empty if valid).

    Raises
    ------
    ValueError
        If *strict* is ``True`` and validation errors exist.
    """
    validator = _VALIDATORS.get(spec.stage)
    if validator is None:
        return [f"unknown stage {spec.stage!r}; cannot validate"]

    errors = validator(spec)

    if strict and errors:
        msg = "\n".join(f"  ✗ {e}" for e in errors)
        raise ValueError(
            f"StrategySpec validation failed for {spec.strategy_id!r} "
            f"(stage={spec.stage!r}):\n{msg}"
        )

    return errors


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_nested(config: dict, key: str, default: object = None) -> object:
    """Get a key from *config* dict, supporting dotted paths."""
    if "." in key:
        parts = key.split(".")
        current = config
        for part in parts:
            if not isinstance(current, dict):
                return default
            current = current.get(part, {})
        return current if current else default
    return config.get(key, default)


def _has_production_config(spec: StrategySpec) -> bool:
    """Check if *spec* contains production-style broker/risk config."""
    rc = spec.raw_config
    for key in ("risk_limits", "broker_policy", "execution_policy"):
        if rc.get(key):
            return True
    return False
