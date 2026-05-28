"""Persistent store for signal artifacts.

Signals are stored as parquet files partitioned by signal_id, with
row-level date/instrument/value fields.  The store manages read/write
against a research run's ``signals/`` subdirectory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from qsys.signal.schema import SignalRecord, SignalSpec

# Standard column names for signal parquet files
DATE_COL = "date"
INSTRUMENT_COL = "instrument"
VALUE_COL = "value"
_SIGNAL_PARQUET_SCHEMA: list[str] = [DATE_COL, INSTRUMENT_COL, VALUE_COL]


@dataclass
class SignalStore:
    """Read/write access to signal artifacts within a research run.

    Parameters
    ----------
    root:
        Path to the research run's ``signals/`` directory.  Created on
        first write if it does not exist.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        signal_id: str,
        records: list[SignalRecord],
        spec: SignalSpec | None = None,
    ) -> Path:
        """Persist signal records as parquet.

        Parameters
        ----------
        signal_id:
            Identifies the signal definition.  The file is written as
            ``<root>/<signal_id>.parquet``.
        records:
            Signal values to persist.
        spec:
            Optional ``SignalSpec``.  When provided, a JSON sidecar
            ``<root>/<signal_id>.spec.json`` is written alongside.

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
                }
                for r in records
            ]
        )
        if df.empty:
            df = pd.DataFrame(columns=_SIGNAL_PARQUET_SCHEMA)

        path = self.root / f"{signal_id}.parquet"
        df.to_parquet(path, index=False)

        if spec is not None:
            spec_path = self.root / f"{signal_id}.spec.json"
            spec_path.write_text(spec.to_json(), encoding="utf-8")

        return path

    def load(self, signal_id: str) -> pd.DataFrame:
        """Load signal records as a DataFrame.

        Returns columns ``date``, ``instrument``, ``value``.
        Raises ``FileNotFoundError`` when the parquet file does not exist.
        """
        path = self.root / f"{signal_id}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Signal file not found: {path}")
        return pd.read_parquet(path)

    def list(self) -> list[str]:
        """Return all signal IDs available in this store.

        Scans ``<root>/*.parquet`` and strips the ``.parquet`` suffix.
        """
        return sorted(p.stem for p in self.root.glob("*.parquet"))

    def load_spec(self, signal_id: str) -> SignalSpec | None:
        """Load the optional ``SignalSpec`` sidecar for *signal_id*.

        Returns ``None`` when no sidecar file exists.
        """
        path = self.root / f"{signal_id}.spec.json"
        if not path.exists():
            return None
        return SignalSpec.from_json(path.read_text(encoding="utf-8"))
