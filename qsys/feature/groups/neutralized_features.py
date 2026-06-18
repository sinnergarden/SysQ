"""Cross-sectional neutralized features via OLS residualization.

Each feature is computed by fitting a ``LinearRegression`` within each
trade_date and returning residuals.  Cross-sections with fewer than 50
observations yield NaN.  All regressions use
``sklearn.linear_model.LinearRegression``.

Neutralization types
--------------------
- **Market-cap neutralization**: ``residual(y ~ log_mktcap)``
- **Industry + size neutralization**:
  ``residual(y ~ log_mktcap + industry_dummies)``

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
Requires the source columns (*ret_60d*, *ret_120d*, *roe*,
*holder_concentration_score*) and neutralization covariates
(*log_mktcap*, *industry*) already present in the DataFrame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression


# ── Residualisation helpers ──────────────────────────────────────────────


def _ols_residuals(
    y: pd.Series,
    X: pd.DataFrame,
    *,
    min_samples: int = 50,
) -> pd.Series:
    """Fit OLS and return residuals.

    Parameters
    ----------
    y : pd.Series
        Target variable.
    X : pd.DataFrame
        Design matrix.
    min_samples : int
        Minimum number of valid observations.  Below this threshold
        (or if the fit fails) all entries are NaN.

    Returns
    -------
    pd.Series
        Residuals (aligned with *y.index*), NaN where the regression
        could not be fit.
    """
    if len(y) < min_samples:
        return pd.Series(np.nan, index=y.index, dtype=np.float64)
    try:
        lr = LinearRegression()
        lr.fit(X, y)
        residuals = y.values - lr.predict(X).ravel()
        return pd.Series(residuals, index=y.index, dtype=np.float64)
    except Exception:
        return pd.Series(np.nan, index=y.index, dtype=np.float64)


def _neutralize_mktcap(
    df: pd.DataFrame,
    y_col: str,
    *,
    min_samples: int = 50,
) -> pd.Series:
    """Residual of *y_col* ~ log_mktcap, cross-sectionally per trade_date."""
    out = pd.Series(np.nan, index=df.index, dtype=np.float64)
    for _date, grp in df.groupby("trade_date"):
        valid = (
            grp[[y_col, "log_mktcap"]]
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        if len(valid) < min_samples:
            continue
        resid = _ols_residuals(
            valid[y_col].astype(np.float64),
            valid[["log_mktcap"]].astype(np.float64),
            min_samples=min_samples,
        )
        out.loc[valid.index] = resid.values
    return out


def _neutralize_mktcap_industry(
    df: pd.DataFrame,
    y_col: str,
    *,
    min_samples: int = 50,
) -> pd.Series:
    """Residual of *y_col* ~ log_mktcap + industry_dummies per trade_date."""
    out = pd.Series(np.nan, index=df.index, dtype=np.float64)
    for _date, grp in df.groupby("trade_date"):
        valid = (
            grp[[y_col, "log_mktcap", "industry"]]
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        idx = valid.index
        if len(valid) < min_samples:
            continue
        try:
            dummies = pd.get_dummies(valid["industry"], prefix="ind")
            X = pd.concat(
                [valid[["log_mktcap"]].astype(np.float64), dummies],
                axis=1,
            )
            resid = _ols_residuals(
                valid[y_col].astype(np.float64),
                X,
                min_samples=min_samples,
            )
            out.loc[idx] = resid.values
        except Exception:
            continue
    return out


# ── Main builder ─────────────────────────────────────────────────────────


def build_neutralized_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build daily cross-sectional neutralized features.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain **trade_date**, **log_mktcap**, **industry**,
        and at least one of the source columns listed below.
        Missing source columns are silently skipped.

    Source columns consumed
    -----------------------
    ret_60d, ret_120d          — momentum
    roe                        — profitability
    holder_concentration_score — shareholder concentration composite
    """
    out = df.copy()

    if "trade_date" not in out.columns or "log_mktcap" not in out.columns:
        return out

    # ── Market-cap neutralised (residual ~ log_mktcap) ──────────────
    _mktcap_sources = [
        ("ret_60d", "mktcap_neutral_ret_60d"),
        ("ret_120d", "mktcap_neutral_ret_120d"),
        ("roe", "mktcap_neutral_roe"),
        ("holder_concentration_score", "mktcap_neutral_holder_score"),
    ]
    for src, tgt in _mktcap_sources:
        if src in out.columns:
            out[tgt] = _neutralize_mktcap(out, src)

    # ── Industry + size neutralised (residual ~ log_mktcap + industry) ──
    has_industry = "industry" in out.columns
    _industry_sources = [
        ("ret_60d", "industry_size_neutral_ret_60d"),
        ("ret_120d", "industry_size_neutral_ret_120d"),
        ("roe", "industry_size_neutral_roe"),
        ("holder_concentration_score", "industry_size_neutral_holder_score"),
    ]
    if has_industry:
        for src, tgt in _industry_sources:
            if src in out.columns:
                out[tgt] = _neutralize_mktcap_industry(out, src)

    return out
