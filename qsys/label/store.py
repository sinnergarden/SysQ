"""Persistent store for label research artifacts.

See Also
--------
docs/CONTRACTS.md : required columns and layout
docs/USE_CASES.md UC-3 : label lifecycle
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.research.paths import ResearchPaths
from qsys.research.manifest import read_manifest, write_manifest, with_standard_metadata

_REQUIRED_COLUMNS = {"trade_date", "instrument", "label_id", "horizon", "label_value"}
_OPTIONAL_COLUMNS = {
    "return_start_date", "return_end_date",
    "universe", "is_valid", "invalid_reason",
}
_PARQUET_AVAILABLE: bool | None = None


def _parquet_available() -> bool:
    global _PARQUET_AVAILABLE
    if _PARQUET_AVAILABLE is None:
        try:
            import pyarrow  # noqa: F401
            _PARQUET_AVAILABLE = True
        except ImportError:
            try:
                import fastparquet  # noqa: F401
                _PARQUET_AVAILABLE = True
            except ImportError:
                _PARQUET_AVAILABLE = False
    return _PARQUET_AVAILABLE


def _build_manifest(
    label_id: str,
    frame: pd.DataFrame,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    data = {
        "artifact_type": "label",
        "label_id": label_id,
        "row_count": len(frame),
        "columns": list(frame.columns),
        "prediction_start": str(frame["trade_date"].min()) if "trade_date" in frame.columns and len(frame) > 0 else None,
        "prediction_end": str(frame["trade_date"].max()) if "trade_date" in frame.columns and len(frame) > 0 else None,
    }
    if extra:
        data.update(extra)
    return with_standard_metadata(data)


class LabelStore:
    """Read/write access to label artifacts under a research root.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).  See
        :class:`qsys.research.paths.ResearchPaths`.
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.paths = ResearchPaths(root)

    # ── Write ───────────────────────────────────────────────────────────

    def save_labels(
        self,
        label_id: str,
        frame: pd.DataFrame,
        manifest: dict[str, Any] | None = None,
        *,
        overwrite: bool = False,
        file_format: str = "parquet",
    ) -> Path:
        """Persist label data for *label_id*.

        Parameters
        ----------
        label_id:
            Unique label identifier.  Must match the ``label_id`` column
            in *frame*.
        frame:
            DataFrame with required columns (see module docstring).
        manifest:
            Optional extra fields merged into the manifest.
        overwrite:
            When ``False`` (default), raise ``FileExistsError`` if the
            output path already exists.
        file_format:
            ``"parquet"`` (default) or ``"csv"``.

        Returns
        -------
        Path
            Path to the written data file.
        """
        self._validate_frame(label_id, frame)

        if file_format == "parquet" and not _parquet_available():
            file_format = "csv"

        label_dir = self.paths.label_dir(label_id)
        label_dir.mkdir(parents=True, exist_ok=True)
        data_path = self.paths.label_file(label_id, fmt=file_format)

        if data_path.exists() and not overwrite:
            raise FileExistsError(
                f"Label file already exists: {data_path} (use overwrite=True)"
            )

        if file_format == "parquet":
            frame.to_parquet(data_path, index=False)
        else:
            frame.to_csv(data_path, index=False)

        # Write manifest
        mf = _build_manifest(label_id, frame, manifest)
        write_manifest(self.paths.label_manifest(label_id), mf)

        return data_path

    # ── Read ────────────────────────────────────────────────────────────

    def load_labels(
        self,
        label_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        instruments: list[str] | None = None,
    ) -> pd.DataFrame:
        """Load label data for *label_id*.

        Parameters
        ----------
        label_id:
            Label identifier.
        start_date, end_date:
            Optional date filter (YYYY-MM-DD).
        instruments:
            Optional instrument filter.

        Returns
        -------
        pd.DataFrame
        """
        data_path = self._resolve_data_path(label_id)
        if data_path.suffix == ".parquet":
            df = pd.read_parquet(data_path)
        else:
            df = pd.read_csv(data_path)

        if start_date:
            df = df[df["trade_date"] >= start_date]
        if end_date:
            df = df[df["trade_date"] <= end_date]
        if instruments:
            df = df[df["instrument"].isin(instruments)]

        return df.reset_index(drop=True)

    def load_manifest(self, label_id: str) -> dict[str, Any]:
        """Load the manifest for *label_id*."""
        return read_manifest(self.paths.label_manifest(label_id))

    # ── List ────────────────────────────────────────────────────────────

    def list_labels(self) -> pd.DataFrame:
        """Return a DataFrame listing all labels under the research root.

        Columns
        -------
        label_id : str
        row_count : int
        prediction_start : str or None
        prediction_end : str or None
        created_at : str or None
        """
        rows = []
        labels_root = self.paths.root / "labels"
        if not labels_root.exists():
            return pd.DataFrame(columns=["label_id", "row_count", "prediction_start", "prediction_end", "created_at"])

        for child in sorted(labels_root.iterdir()):
            if not child.is_dir():
                continue
            lid = child.name
            data_path = self._resolve_data_path(lid, must_exist=False)
            if not data_path or not data_path.exists():
                continue
            mf_path = self.paths.label_manifest(lid)
            if mf_path.exists():
                try:
                    mf = json.loads(mf_path.read_text())
                except Exception:
                    mf = {}
            else:
                mf = {}

            rows.append({
                "label_id": lid,
                "row_count": mf.get("row_count"),
                "prediction_start": mf.get("prediction_start"),
                "prediction_end": mf.get("prediction_end"),
                "created_at": mf.get("created_at"),
            })

        return pd.DataFrame(rows)

    # ── Config-driven computation ──────────────────────────────────

    def compute_and_save_from_config(self, config: dict[str, Any], *, overwrite: bool = False) -> Path:
        """Compute and save label from YAML config dict.

        Delegates to ``qsys.label.compute`` for label calculation.
        """
        label_id = str(config["label_id"])
        formula = config.get("formula", {})
        ftype = formula.get("type", "forward_return")
        horizon = int(formula.get("horizon", 5))
        price_field = str(formula.get("price", "close"))
        norm = config.get("normalization", {})
        norm_type = str(norm.get("type", "")) if norm else ""
        clip_val = float(norm["clip"]) if norm and "clip" in norm else None
        universe = str(config.get("universe", "csi300"))
        dr = config.get("date_range", {}); start = str(dr.get("start_date", "2018-01-01")); end = str(dr.get("end_date", "2026-01-01"))

        if ftype == "forward_return":
            from qsys.label.compute import compute_forward_return
            result = compute_forward_return(
                universe=universe, horizon=horizon, start=start, end=end,
                price_field=price_field, norm_type=norm_type, clip_val=clip_val,
                # A config label_id (e.g. fwd_ret_180d_raw_pit) is used
                # verbatim so the store row label_id matches save_labels.
                label_id_override=label_id,
            )
        else:
            raise ValueError(f"Unsupported formula type: {ftype}")

        mf = {"horizon": horizon, "universe": universe, "formula": config.get("formula", {}),
              "normalization": config.get("normalization", {}), "prediction_start": start, "prediction_end": end,
              "n_dates": int(result["trade_date"].nunique()),
              "n_instruments": int(result["instrument"].nunique()),
              "coverage": round(len(result) / max(result["trade_date"].nunique() * result["instrument"].nunique(), 1), 4)}
        mf.update(config.get("manifest", {}))
        return self.save_labels(label_id, result, manifest=mf, overwrite=overwrite)

    # ── Validation ──────────────────────────────────────────────────────

    def _validate_frame(self, label_id: str, frame: pd.DataFrame) -> None:
        missing = _REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        if "label_id" in frame.columns:
            bad = frame[frame["label_id"] != label_id]
            if len(bad) > 0:
                raise ValueError(
                    f"Frame contains rows with label_id != {label_id!r} "
                    f"({len(bad)} rows)"
                )

        if frame["trade_date"].isna().any():
            raise ValueError("trade_date column contains null values")
        if frame["instrument"].isna().any():
            raise ValueError("instrument column contains null values")
        if "horizon" in frame.columns and frame["horizon"].isna().any():
            raise ValueError("horizon column contains null values")

    def _resolve_data_path(self, label_id: str, must_exist: bool = True) -> Path | None:
        parquet_path = self.paths.label_file(label_id, fmt="parquet")
        if parquet_path.exists():
            return parquet_path
        csv_path = self.paths.label_file(label_id, fmt="csv")
        if csv_path.exists():
            return csv_path
        if must_exist:
            raise FileNotFoundError(
                f"No label data found for {label_id} "
                f"(tried parquet and csv in {self.paths.label_dir(label_id)})"
            )
        return None

    # ── Existence ──────────────────────────────────────────────────

    def label_exists(self, label_id: str) -> bool:
        """Check whether label data exists for *label_id*."""
        try:
            return self._resolve_data_path(label_id, must_exist=False) is not None
        except Exception:
            return False

    # ── Validation ─────────────────────────────────────────────────

    def validate_label(
        self,
        label_id: str,
        *,
        universe: str | None = None,
        start: str | None = None,
        end: str | None = None,
        min_coverage: float | None = None,
    ) -> dict[str, Any]:
        """Validate a label artifact.

        Returns a report dict.  Raises if critical checks fail.

        Parameters
        ----------
        label_id:
            Label identifier.
        universe:
            If set, manifest must match universe.
        start, end:
            If set, label coverage must include [start, end].
        min_coverage:
            If set, fail when actual coverage < this ratio.

        Returns
        -------
        dict
            Validation report with keys: passed, label_id, exists,
            columns_ok, date_coverage, coverage.
        """
        report: dict[str, Any] = {
            "label_id": label_id,
            "passed": False,
            "exists": False,
            "columns_ok": False,
            "date_coverage": None,
            "coverage": None,
        }

        # 1. Existence check
        data_path = self._resolve_data_path(label_id, must_exist=False)
        if data_path is None or not data_path.exists():
            raise FileNotFoundError(
                f"validate_label: no label data for {label_id}. "
                f"Run compute_labels.py first."
            )
        report["exists"] = True

        # 2. Load manifest
        try:
            mf = self.load_manifest(label_id)
        except Exception as e:
            raise FileNotFoundError(
                f"validate_label: manifest for {label_id} is missing or corrupt: {e}"
            ) from e

        # 3. Universe check
        if universe is not None:
            mf_univ = mf.get("universe", "")
            if mf_univ and mf_univ != universe:
                raise ValueError(
                    f"validate_label: {label_id} was computed for universe "
                    f"{mf_univ!r}, requested {universe!r}"
                )

        # 4. Load sample data to verify columns
        try:
            df = self.load_labels(label_id)
        except Exception as e:
            raise RuntimeError(
                f"validate_label: could not load label data for {label_id}: {e}"
            ) from e

        required = {"trade_date", "instrument", "label_id", "horizon", "label_value"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"validate_label: {label_id} missing required columns: {sorted(missing)}"
            )
        report["columns_ok"] = True

        if df["label_value"].isna().all():
            raise ValueError(f"validate_label: {label_id} label_value is all NaN")

        # 5. Date coverage (allow forward-tail gap for return labels)
        if start is not None and end is not None:
            df_dates = set(df["trade_date"].unique())
            from qsys.data.calendar import get_trading_calendar
            cal = get_trading_calendar(start, end)
            if cal:
                cal_set = set(cal)
                missing_dates = cal_set - df_dates

                horizon = mf.get("horizon", 0)
                if missing_dates and isinstance(horizon, int) and horizon > 0:
                    sorted_cal = sorted(cal_set)
                    tail_start = max(0, len(sorted_cal) - horizon)
                    expected_tail = set(sorted_cal[tail_start:])
                    middle_missing = missing_dates - expected_tail
                    if not middle_missing:
                        missing_dates = set()  # Only tail gap — OK
                    else:
                        missing_dates = middle_missing  # Report only middle missing

                if missing_dates:
                    if horizon > 0:
                        raise ValueError(
                            f"validate_label: {label_id} missing {len(missing_dates)} "
                            f"trading dates in [{start}, {end}] (expected forward-tail "
                            f"gap of {horizon}d already allowed). "
                            f"Middle-missing e.g. {sorted(missing_dates)[:3]}"
                        )
                    raise ValueError(
                        f"validate_label: {label_id} missing {len(missing_dates)} "
                        f"trading dates in [{start}, {end}] "
                        f"(e.g. {sorted(missing_dates)[:3]})"
                    )
            report["date_coverage"] = f"{start} → {end}"

        # 6. Coverage ratio
        cov = mf.get("coverage")
        if cov is not None:
            report["coverage"] = cov
            if min_coverage is not None and cov < min_coverage:
                raise ValueError(
                    f"validate_label: {label_id} coverage={cov:.1%} "
                    f"< min_coverage={min_coverage:.1%}"
                )

        report["passed"] = True
        return report
