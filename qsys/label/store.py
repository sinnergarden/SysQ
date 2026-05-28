"""Persistent store for label research artifacts.

See Also
--------
docs/contracts/research-artifact-contract.md : required columns and layout
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
