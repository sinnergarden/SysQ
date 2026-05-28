"""Signal expression / derived signal generation for Framework Stable 2.0.

Combines one or more saved SignalRuns into a new DerivedSignal using a
simple SQL expression evaluated via DuckDB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.signal.store import SignalStore

# ── Safety: reject dangerous SQL keywords ─────────────────────────────

_UNSAFE_PATTERNS = [
    ";", "DROP ", "DELETE ", "INSERT ", "UPDATE ",
    "CREATE ", "ATTACH ", "COPY ", "INSTALL ", "LOAD ",
]


def _check_expression_safe(expr: str) -> None:
    upper = expr.upper()
    for pat in _UNSAFE_PATTERNS:
        if pat.upper() in upper:
            raise ValueError(
                f"Expression contains unsafe pattern {pat!r}: {expr!r}"
            )


# ── Dataclasses ───────────────────────────────────────────────────────


@dataclass
class SignalInputSpec:
    """One input signal for a derived signal expression.

    Attributes
    ----------
    alias:
        Local alias used in the expression SQL (e.g. ``alpha``).
    signal_id:
        Source signal definition identifier.
    signal_run_id:
        Source signal run identifier.
    score_column:
        Column name to load from the source run (default ``score``).
    """
    alias: str
    signal_id: str
    signal_run_id: str
    score_column: str = "score"

    def to_dict(self) -> dict[str, Any]:
        return {"alias": self.alias, "signal_id": self.signal_id,
                "signal_run_id": self.signal_run_id, "score_column": self.score_column}


@dataclass
class SignalExpressionSpec:
    """Full specification for a derived signal expression run.

    Attributes
    ----------
    expression_id:
        Unique identifier for this expression recipe.
    output_signal_id:
        Signal ID for the derived output.
    output_signal_run_id:
        Run ID for this concrete generation.
    inputs:
        List of source SignalInputSpecs.
    expression:
        SQL expression string referencing input aliases.
    postprocess:
        Optional postprocessing config dict with keys
        ``winsorize`` (float) and/or ``daily_zscore`` (bool).
    label_id:
        Reference label for lineage (optional).
    universe:
        Reference universe for lineage (optional).
    """
    expression_id: str
    output_signal_id: str
    output_signal_run_id: str
    inputs: list[SignalInputSpec]
    expression: str
    postprocess: dict[str, Any] | None = None
    label_id: str | None = None
    universe: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SignalExpressionSpec:
        data = dict(payload)
        inputs_raw = data.pop("inputs", [])
        inputs = [SignalInputSpec(**i) for i in inputs_raw]
        return cls(inputs=inputs, **data)

    @classmethod
    def from_file(cls, path: Path) -> SignalExpressionSpec:
        """Load spec from a YAML or JSON file."""
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            import yaml
            payload = yaml.safe_load(text)
        elif suffix == ".json":
            payload = json.loads(text)
        else:
            raise ValueError(f"Unsupported config format: {path} (use .yaml or .json)")
        return cls.from_dict(payload)


# ── Core helpers ──────────────────────────────────────────────────────


def align_input_signals(
    inputs: list[SignalInputSpec],
    store: SignalStore,
) -> pd.DataFrame:
    """Load all input signals and inner-join on ``(trade_date, instrument)``.

    Each input alias becomes a column (the selected ``score_column``).
    The ``data_date`` column is the per-row maximum across inputs.
    """
    aligned = None

    for inp in inputs:
        df = store.load_signal_run(inp.signal_id, inp.signal_run_id)
        if df.empty:
            raise ValueError(
                f"Input signal {inp.signal_id}/{inp.signal_run_id} is empty"
            )
        if inp.score_column not in df.columns:
            raise ValueError(
                f"score_column {inp.score_column!r} not found in "
                f"signal {inp.signal_id}/{inp.signal_run_id} "
                f"(columns: {list(df.columns)})"
            )

        sub = df[["trade_date", "instrument", "data_date", inp.score_column]].copy()
        sub = sub.rename(columns={inp.score_column: inp.alias, "data_date": f"_dd_{inp.alias}"})

        if aligned is None:
            aligned = sub
        else:
            aligned = pd.merge(
                aligned, sub,
                on=["trade_date", "instrument"],
                how="inner",
            )

    if aligned is None or aligned.empty:
        raise ValueError("No data after aligning input signals")

    # Compute data_date = max of input data_dates per row
    dd_cols = [c for c in aligned.columns if c.startswith("_dd_")]
    if dd_cols:
        aligned["data_date"] = aligned[dd_cols].max(axis=1)
        aligned = aligned.drop(columns=dd_cols)

    return aligned.reset_index(drop=True)


def evaluate_expression(
    aligned: pd.DataFrame,
    expression: str,
) -> pd.Series:
    """Evaluate a SQL expression over the aligned frame using DuckDB.

    Returns a Series of ``score_raw`` values (index matches *aligned*).
    """
    import duckdb

    _check_expression_safe(expression)

    # Build a SELECT that wraps the user expression as score_raw
    sql = f"SELECT {expression} AS __score_raw FROM aligned"

    result = duckdb.sql(sql).fetchdf()
    return result["__score_raw"].rename("score_raw")


def apply_postprocess(
    frame: pd.DataFrame,
    *,
    raw_col: str = "score_raw",
    postprocess: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Apply optional per-date postprocessing.

    ``score_raw`` is always preserved as the original expression result.
    ``score`` is the final postprocessed value.
    """
    if postprocess is None:
        postprocess = {}

    result = frame.copy()
    result["score_raw"] = result[raw_col]

    # Start with raw score as the processing base
    processed = result["score_raw"].copy()

    # Winsorize (operate on processed, not score_raw)
    winsorize_q = postprocess.get("winsorize", 0.0)
    if winsorize_q:
        if not (0.0 < winsorize_q <= 0.2):
            raise ValueError(f"winsorize q must be in (0, 0.2], got {winsorize_q}")
        for d in result["trade_date"].unique():
            mask = result["trade_date"] == d
            vals = processed.loc[mask].astype(float)
            lo = vals.quantile(winsorize_q)
            hi = vals.quantile(1.0 - winsorize_q)
            processed.loc[mask] = vals.clip(lo, hi)

    # Daily z-score (operate on processed, not score_raw)
    daily_zscore = postprocess.get("daily_zscore", False)
    if daily_zscore:
        processed = processed.groupby(result["trade_date"]).transform(
            lambda x: (x - x.mean()) / x.std()
            if x.std() > 1e-12
            else pd.Series(0.0, index=x.index)
        )

    # Final score is the processed value
    result["score"] = processed

    # Per-day rank
    result["score_rank"] = result.groupby("trade_date")["score"].transform(
        lambda x: x.rank(pct=True)
    )

    # Per-day z-score of final score
    result["score_z"] = result.groupby("trade_date")["score"].transform(
        lambda x: (x - x.mean()) / x.std()
        if x.std() > 1e-12
        else pd.Series(0.0, index=x.index)
    )

    # is_valid / invalid_reason
    result["is_valid"] = np.isfinite(result["score"])
    has_invalid = ~result["is_valid"]
    if has_invalid.any():
        result["invalid_reason"] = None
        result.loc[has_invalid, "invalid_reason"] = "non_finite_score"

    return result


# ── Runner ────────────────────────────────────────────────────────────


class SignalExpressionRunner:
    """Combine one or more saved SignalRuns into a DerivedSignal via SQL.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._signal_store = SignalStore(str(self.root))

    def run(
        self,
        spec: SignalExpressionSpec | dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> Path:
        """Execute a signal expression and persist the derived signal.

        Parameters
        ----------
        spec:
            Expression specification (dataclass or dict).
        overwrite:
            When ``True``, overwrite existing output.

        Returns
        -------
        Path
            Path to the saved predictions parquet file.
        """
        if isinstance(spec, dict):
            spec = SignalExpressionSpec.from_dict(spec)

        # 1. Align inputs
        aligned = align_input_signals(spec.inputs, self._signal_store)

        # 2. Evaluate expression
        score_raw = evaluate_expression(aligned, spec.expression)
        aligned["score_raw"] = score_raw.values

        # 3. Postprocess
        result = apply_postprocess(aligned, postprocess=spec.postprocess)

        # 4. Add required SignalStore columns
        result["signal_id"] = spec.output_signal_id
        result["signal_run_id"] = spec.output_signal_run_id
        result["source_expression_id"] = spec.expression_id

        # Drop invalid rows that SignalStore can't store (score null for valid rows)
        # SignalStore.save_signal_run requires score non-null for is_valid=True rows.
        # We keep all rows but set invalid ones' score to NaN (store handles this).
        # The manifest records the dropped count.

        # 5. Build manifest
        manifest = {
            "signal_kind": "derived",
            "source_expression_id": spec.expression_id,
            "expression_id": spec.expression_id,
            "output_signal_id": spec.output_signal_id,
            "output_signal_run_id": spec.output_signal_run_id,
            "inputs": [inp.to_dict() for inp in spec.inputs],
            "expression": spec.expression,
            "postprocess": spec.postprocess,
            "row_count": len(result),
            "valid_count": int(result["is_valid"].sum()),
            "invalid_count": int((~result["is_valid"]).sum()),
        }
        if spec.label_id:
            manifest["label_id"] = spec.label_id
        if spec.universe:
            manifest["universe"] = spec.universe

        # 6. Save via SignalStore
        save_cols = [
            "trade_date", "data_date", "instrument",
            "signal_id", "signal_run_id", "score",
            "score_raw", "score_rank", "score_z",
            "source_expression_id", "is_valid", "invalid_reason",
        ]
        to_save = result[[c for c in save_cols if c in result.columns]]

        self._signal_store.save_signal_run(
            spec.output_signal_id,
            spec.output_signal_run_id,
            to_save,
            manifest=manifest,
            overwrite=overwrite,
            check_no_lookahead=True,
        )

        return self._signal_store.paths.signal_file(
            spec.output_signal_id, spec.output_signal_run_id,
        )
