"""Cross-sectional neutralized features via OLS residualization.

Each feature is computed by fitting OLS within each trade_date
and returning residuals.  Cross-sections with fewer than 50
observations yield NaN.

Neutralization types
--------------------
- **Market-cap neutralization**: ``residual(y ~ 1 + log_mktcap)``
- **Industry + size neutralization**:
  ``residual(y ~ 1 + log_mktcap + industry_dummies)``

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
Requires the source columns and covariates already present in the DataFrame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _ols_residuals(
    y: pd.Series,
    X: np.ndarray,
    *,
    min_samples: int = 50,
) -> pd.Series:
    """Return y - X @ (X'X)⁻¹ X'y (OLS residuals), using numpy lstsq."""
    if len(y) < min_samples:
        return pd.Series(np.nan, index=y.index, dtype=np.float64)
    try:
        beta, *_ = np.linalg.lstsq(X, y.values, rcond=None)
        resid = y.values - X @ beta
        return pd.Series(resid, index=y.index, dtype=np.float64)
    except Exception:
        return pd.Series(np.nan, index=y.index, dtype=np.float64)


def _neutralize(
    df: pd.DataFrame,
    y_col: str,
    covariates: list[str],
    has_dummies: bool = False,
    *,
    min_samples: int = 50,
) -> pd.DataFrame:
    """Add neutralized columns for *y_col* per trade_date.

    Parameters
    ----------
    df : DataFrame with trade_date, y_col, covariates.
    covariates : column names of continuous X variables (e.g. ``["log_mktcap"]``).
    has_dummies : if True, one-hot encode ``industry`` column.

    Returns a Series of residuals aligned with *df.index*.
    """
    out = pd.Series(np.nan, index=df.index, dtype=np.float64)
    _to_float = {c: np.float64 for c in covariates}
    for _date, grp in df.groupby("trade_date"):
        valid = grp[[y_col] + covariates].replace([np.inf, -np.inf], np.nan)
        if has_dummies and "industry" in grp.columns:
            valid = valid.join(grp["industry"])
        valid = valid.dropna()
        n = len(valid)
        if n < min_samples:
            continue
        idx = valid.index
        yv = valid[y_col].astype(np.float64).values
        # Build design matrix: intercept + covariates
        cols = [np.ones(n)]
        for c in covariates:
            cols.append(valid[c].astype(np.float64).values)
        if has_dummies:
            dummies = pd.get_dummies(valid["industry"], prefix="ind")
            for c in dummies.columns:
                cols.append(dummies[c].values.astype(np.float64))
        X = np.column_stack(cols)
        try:
            beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
            resid = yv - X @ beta
            out.loc[idx] = resid
        except Exception:
            continue
    return out


def build_neutralized_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build daily cross-sectional neutralized features.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain **trade_date**, **log_mktcap**, **industry**,
        and at least one of the source columns.  Missing source columns
        are silently skipped.

    Source columns consumed
    -----------------------
    ret_60d, ret_120d          — momentum
    roe                        — profitability
    holder_concentration_score — shareholder concentration composite
    """
    out = df.copy()

    if "trade_date" not in out.columns or "log_mktcap" not in out.columns:
        return out

    _covariates = ["log_mktcap"]
    _sources = [
        ("ret_60d", "mktcap_neutral_ret_60d"),
        ("ret_120d", "mktcap_neutral_ret_120d"),
        ("roe", "mktcap_neutral_roe"),
        ("holder_concentration_score", "mktcap_neutral_holder_score"),
    ]
    for src, tgt in _sources:
        if src in out.columns:
            out[tgt] = _neutralize(out, src, _covariates, has_dummies=False)

    if "industry" in out.columns:
        for src, tgt in [
            ("ret_60d", "industry_size_neutral_ret_60d"),
            ("ret_120d", "industry_size_neutral_ret_120d"),
            ("roe", "industry_size_neutral_roe"),
            ("holder_concentration_score", "industry_size_neutral_holder_score"),
        ]:
            if src in out.columns:
                out[tgt] = _neutralize(out, src, _covariates, has_dummies=True)

    return out
