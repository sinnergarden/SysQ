"""Persistent store for label artifacts.

Labels are stored as parquet files partitioned by label_id, with
row-level date/instrument/value fields.  The store manages read/write
against a research run's ``labels/`` subdirectory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.label.schema import LabelRecord, LabelSpec

# Standard column names for label parquet files
DATE_COL = "date"
INSTRUMENT_COL = "instrument"
VALUE_COL = "value"
WEIGHT_COL = "weight"
_LABEL_PARQUET_SCHEMA: list[str] = [DATE_COL, INSTRUMENT_COL, VALUE_COL, WEIGHT_COL]


@dataclass
class LabelStore:
    """Read/write access to label artifacts within a research run.

    Parameters
    ----------
    root:
        Path to the research run's ``labels/`` directory.  Created on
        first write if it does not exist.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        label_id: str,
        records: list[LabelRecord],
        spec: LabelSpec | None = None,
    ) -> Path:
        """Persist label records as parquet.

        Parameters
        ----------
        label_id:
            Identifies the label definition.  The file is written as
            ``<root>/<label_id>.parquet``.
        records:
            Label values to persist.
        spec:
            Optional ``LabelSpec``.  When provided, a JSON sidecar
            ``<root>/<label_id>.spec.json`` is written alongside.

        Returns
        -------
        Path
            Path to the written parquet file.
        """
        df = pd.DataFrame(
            [
                {
                    DATE_COL: r.date,
                    INSTRUMENT_COL: r.instrument,
                    VALUE_COL: r.value,
                    WEIGHT_COL: r.weight,
                }
                for r in records
            ]
        )
        if df.empty:
            df = pd.DataFrame(columns=_LABEL_PARQUET_SCHEMA)

        path = self.root / f"{label_id}.parquet"
        df.to_parquet(path, index=False)

        if spec is not None:
            spec_path = self.root / f"{label_id}.spec.json"
            spec_path.write_text(spec.to_json(), encoding="utf-8")

        return path

    def load(self, label_id: str) -> pd.DataFrame:
        """Load label records as a DataFrame.

        Returns columns ``date``, ``instrument``, ``value``, ``weight``.
        Raises ``FileNotFoundError`` when the parquet file does not exist.
        """
        path = self.root / f"{label_id}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Label file not found: {path}")
        return pd.read_parquet(path)

    def list(self) -> list[str]:
        """Return all label IDs available in this store.

        Scans ``<root>/*.parquet`` and strips the ``.parquet`` suffix.
        """
        return sorted(p.stem for p in self.root.glob("*.parquet"))

    def load_spec(self, label_id: str) -> LabelSpec | None:
        """Load the optional ``LabelSpec`` sidecar for *label_id*.

        Returns ``None`` when no sidecar file exists.
        """
        path = self.root / f"{label_id}.spec.json"
        if not path.exists():
            return None
        return LabelSpec.from_json(path.read_text(encoding="utf-8"))
