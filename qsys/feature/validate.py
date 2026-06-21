"""Feature matrix column validation utilities.

Rules (all fail fast):
1. Must contain ``trade_date`` and ``ts_code``.
2. Must contain ALL ``resolved_features``.
3. Output columns must be exactly ``["trade_date", "ts_code"] + resolved_features``.
4. No extra columns allowed in the matrix.
"""

import pandas as pd
from qsys.utils.logger import log


def validate_feature_matrix_columns(
    df: pd.DataFrame,
    resolved_features: list[str],
) -> pd.DataFrame:
    """Validate and trim *df* to the canonical feature matrix format.

    Returns a DataFrame with columns = ``["trade_date", "ts_code"] + resolved_features``,
    in that exact order.

    Raises ``ValueError`` if any required column is missing.
    """
    required = {"trade_date", "ts_code"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Feature matrix missing index columns: {missing}"
        )

    missing_feats = [f for f in resolved_features if f not in df.columns]
    if missing_feats:
        raise ValueError(
            f"Feature matrix missing resolved features: {missing_feats}"
        )

    base = ["trade_date", "ts_code"]
    expected = base + list(resolved_features)
    extra = [c for c in df.columns if c not in expected]
    if extra:
        log.warning(
            "Trimming %d unexpected columns from feature matrix: %s",
            len(extra), extra,
        )

    return df[expected].copy()
