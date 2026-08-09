"""Persistent store for signal research artifacts.

See Also
--------
docs/CONTRACTS.md : required columns and layout
docs/USE_CASES.md UC-4 : signal research lifecycle
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.paths import ResearchPaths
from qsys.research.manifest import read_manifest, write_manifest, with_standard_metadata

_REQUIRED_COLUMNS = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
_OPTIONAL_COLUMNS = {
    "model_id", "model_version", "feature_set_id", "label_id",
    "universe", "score_raw", "score_rank", "score_z",
    "is_valid", "invalid_reason",
}
PARQUET_AVAILABLE: bool | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parquet_available() -> bool:
    global PARQUET_AVAILABLE
    if PARQUET_AVAILABLE is None:
        try:
            import pyarrow  # noqa: F401
            PARQUET_AVAILABLE = True
        except ImportError:
            try:
                import fastparquet  # noqa: F401
                PARQUET_AVAILABLE = True
            except ImportError:
                PARQUET_AVAILABLE = False
    return PARQUET_AVAILABLE


def _build_manifest(
    signal_id: str,
    signal_run_id: str,
    predictions: pd.DataFrame,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    extra_data = dict(extra) if extra else {}
    signal_kind = extra_data.pop("signal_kind", "raw")

    data: dict[str, Any] = {
        "artifact_type": "signal_run",
        "signal_id": signal_id,
        "signal_run_id": signal_run_id,
        "signal_kind": signal_kind,
        "row_count": len(predictions),
        "columns": list(predictions.columns),
    }
    if "trade_date" in predictions.columns and len(predictions) > 0:
        data["prediction_start"] = str(predictions["trade_date"].min())
        data["prediction_end"] = str(predictions["trade_date"].max())
    # Pull optional lineage fields from frame metadata if present
    for col in ("model_id", "feature_set_id", "label_id", "universe", "data_cutoff_policy"):
        if col in predictions.columns:
            val = predictions[col].dropna().unique()
            if len(val) == 1:
                data[col] = str(val[0])
    for k, v in extra_data.items():
        if k not in data:
            data[k] = v
    return with_standard_metadata(data)


def _check_no_lookahead_on_frame(frame: pd.DataFrame) -> None:
    """Enforce data_date <= previous_trading_day(trade_date) per row.

    Raises ``ValueError`` with violation details on first bad row.
    Uses simple weekday fallback when qlib calendar is unavailable or
    too stale (most recent entry > 3 trading days before trade_date).
    """
    if "trade_date" not in frame.columns or "data_date" not in frame.columns:
        return  # cannot check, skip

    cal_set: set[str] | None = None
    try:
        from qsys.data.calendar import get_trading_calendar
        cal = get_trading_calendar("2000-01-01", "2030-01-01")
        cal_set = set(cal) if cal else None
    except Exception:
        pass

    # Historical SignalRuns routinely contain millions of rows but only a few
    # thousand distinct execution dates.  Resolve the calendar contract once
    # per date, then compare whole columns.  The previous row-wise iterrows()
    # implementation made cache materialization take minutes.
    trade_raw = frame["trade_date"].reset_index(drop=True)
    data_raw = frame["data_date"].reset_index(drop=True)
    trade_dates = pd.to_datetime(trade_raw, errors="coerce")
    data_dates = pd.to_datetime(data_raw, errors="coerce")
    invalid_trade = trade_raw.notna() & trade_dates.isna()
    invalid_data = data_raw.notna() & data_dates.isna()
    if invalid_trade.any() or invalid_data.any():
        position = int((invalid_trade | invalid_data).to_numpy().nonzero()[0][0])
        idx = frame.index[position]
        raise ValueError(
            f"invalid signal date at row {idx}: "
            f"trade_date={trade_raw.iloc[position]!r}, "
            f"data_date={data_raw.iloc[position]!r}"
        )

    trade_text = trade_dates.dt.strftime("%Y-%m-%d")
    data_text = data_dates.dt.strftime("%Y-%m-%d")
    previous_by_trade_date = {
        trade_date: _resolve_prev_trading_day(trade_date, cal_set)
        for trade_date in trade_text.dropna().unique()
    }
    previous_text = trade_text.map(previous_by_trade_date)
    comparable = data_text.notna() & previous_text.notna()
    violations = pd.Series(False, index=frame.reset_index(drop=True).index)
    violations.loc[comparable] = data_text.loc[comparable].gt(
        previous_text.loc[comparable]
    )
    if violations.any():
        position = int(violations.to_numpy().nonzero()[0][0])
        idx = frame.index[position]
        raise ValueError(
            f"lookahead violation at row {idx}: "
            f"data_date={data_raw.iloc[position]} > "
            f"previous_trading_day({trade_raw.iloc[position]})="
            f"{previous_text.iloc[position]}"
        )


def _resolve_prev_trading_day(
    trade_date: str, cal_set: set[str] | None,
) -> str | None:
    """Return the previous trading day for *trade_date*.

    Uses *cal_set* (qlib calendar) when available and not stale.
    Falls back to simple weekday logic otherwise.
    """
    if cal_set:
        candidates = sorted(d for d in cal_set if d < trade_date)
        if candidates:
            prev = candidates[-1]
            # If the calendar's latest entry is far behind trade_date,
            # qlib calendar is stale — fall through to weekday logic.
            from datetime import datetime, timedelta
            prev_dt = datetime.strptime(prev, "%Y-%m-%d")
            td_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            gap = (td_dt - prev_dt).days
            if gap < 14:
                return prev
    # Fallback: simple weekday logic
    from datetime import datetime, timedelta
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    offset = 1
    while True:
        prev_dt = dt - timedelta(days=offset)
        if prev_dt.weekday() < 5:
            return prev_dt.strftime("%Y-%m-%d")
        offset += 1


class SignalStore:
    """Read/write access to signal artifacts under a research root.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).  See
        :class:`qsys.research.paths.ResearchPaths`.
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.paths = ResearchPaths(root)

    # ── Write ───────────────────────────────────────────────────────────

    def save_signal_run(
        self,
        signal_id: str,
        signal_run_id: str,
        predictions: pd.DataFrame,
        manifest: dict[str, Any] | None = None,
        *,
        overwrite: bool = False,
        file_format: str = "parquet",
        check_no_lookahead: bool = True,
    ) -> Path:
        """Persist a signal run's predictions.

        Parameters
        ----------
        signal_id:
            Signal definition identifier.  Must match the ``signal_id``
            column in *predictions*.
        signal_run_id:
            Unique identifier for this run.  Must match the
            ``signal_run_id`` column in *predictions*.
        predictions:
            DataFrame with required columns.
        manifest:
            Optional extra fields merged into the manifest.
        overwrite:
            When ``False`` (default), raise ``FileExistsError`` if the
            output path already exists.
        file_format:
            ``"parquet"`` (default) or ``"csv"``.
        check_no_lookahead:
            When ``True`` (default), validate that
            ``data_date <= previous_trading_day(trade_date)`` for every row.

        Returns
        -------
        Path
            Path to the written predictions file.
        """
        if check_no_lookahead:
            _check_no_lookahead_on_frame(predictions)

        self._validate_frame(signal_id, signal_run_id, predictions)

        if file_format == "parquet" and not _parquet_available():
            file_format = "csv"

        sig_dir = self.paths.signal_dir(signal_id, signal_run_id)
        sig_dir.mkdir(parents=True, exist_ok=True)
        data_path = self.paths.signal_file(signal_id, signal_run_id, fmt=file_format)
        alternate_format = "csv" if file_format == "parquet" else "parquet"
        alternate_path = self.paths.signal_file(
            signal_id, signal_run_id, fmt=alternate_format
        )

        if (data_path.exists() or alternate_path.exists()) and not overwrite:
            raise FileExistsError(
                f"Signal file already exists under {sig_dir} (use overwrite=True)"
            )
        if overwrite and alternate_path.exists():
            alternate_path.unlink()

        if file_format == "parquet":
            predictions.to_parquet(data_path, index=False)
        else:
            predictions.to_csv(data_path, index=False)

        mf = _build_manifest(signal_id, signal_run_id, predictions, manifest)
        mf["predictions_file"] = data_path.name
        mf["predictions_sha256"] = _sha256_file(data_path)
        write_manifest(self.paths.signal_manifest(signal_id, signal_run_id), mf)

        return data_path

    # ── Read ────────────────────────────────────────────────────────────

    def load_signal_run(
        self,
        signal_id: str,
        signal_run_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        instruments: list[str] | None = None,
    ) -> pd.DataFrame:
        """Load a full signal run.

        Parameters
        ----------
        signal_id, signal_run_id:
            Identifies the run.
        start_date, end_date:
            Optional date filter (YYYY-MM-DD).
        instruments:
            Optional instrument filter.

        Returns
        -------
        pd.DataFrame
        """
        data_path = self._resolve_data_path(signal_id, signal_run_id)
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

    def load_signal_for_date(
        self,
        signal_id: str,
        signal_run_id: str,
        trade_date: str,
    ) -> pd.DataFrame:
        """Load signal rows for a single trade date."""
        df = self.load_signal_run(signal_id, signal_run_id)
        return df[df["trade_date"] == trade_date].reset_index(drop=True)

    def load_manifest(self, signal_id: str, signal_run_id: str) -> dict[str, Any]:
        """Load the manifest for a signal run."""
        return read_manifest(self.paths.signal_manifest(signal_id, signal_run_id))

    def signal_data_sha256(self, signal_id: str, signal_run_id: str) -> str:
        """Return the SHA-256 of the persisted predictions file."""
        return _sha256_file(self._resolve_data_path(signal_id, signal_run_id))

    # ── List ────────────────────────────────────────────────────────────

    def list_signal_runs(self, signal_id: str | None = None) -> pd.DataFrame:
        """Return a DataFrame listing all signal runs under the research root.

        Parameters
        ----------
        signal_id:
            When provided, only list runs for this signal definition.

        Columns
        -------
        signal_id : str
        signal_run_id : str
        row_count : int or None
        prediction_start : str or None
        prediction_end : str or None
        created_at : str or None
        """
        rows = []
        signals_root = self.paths.root / "signals"
        if not signals_root.exists():
            return pd.DataFrame(columns=[
                "signal_id", "signal_run_id", "row_count",
                "prediction_start", "prediction_end", "created_at",
            ])

        for sig_dir in sorted(signals_root.iterdir()):
            if not sig_dir.is_dir():
                continue
            sid = sig_dir.name
            if signal_id and sid != signal_id:
                continue

            for run_dir in sorted(sig_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                rid = run_dir.name
                mf_path = run_dir / "manifest.json"
                if mf_path.exists():
                    try:
                        mf = json.loads(mf_path.read_text())
                    except Exception:
                        mf = {}
                else:
                    mf = {}

                rows.append({
                    "signal_id": sid,
                    "signal_run_id": rid,
                    "row_count": mf.get("row_count"),
                    "prediction_start": mf.get("prediction_start"),
                    "prediction_end": mf.get("prediction_end"),
                    "created_at": mf.get("created_at"),
                })

        return pd.DataFrame(rows)

    # ── Validation ──────────────────────────────────────────────────────

    def _validate_frame(
        self, signal_id: str, signal_run_id: str, frame: pd.DataFrame
    ) -> None:
        missing = _REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        if "signal_id" in frame.columns:
            bad = frame[frame["signal_id"] != signal_id]
            if len(bad) > 0:
                raise ValueError(
                    f"Frame contains rows with signal_id != {signal_id!r} "
                    f"({len(bad)} rows)"
                )

        if "signal_run_id" in frame.columns:
            bad = frame[frame["signal_run_id"] != signal_run_id]
            if len(bad) > 0:
                raise ValueError(
                    f"Frame contains rows with signal_run_id != {signal_run_id!r} "
                    f"({len(bad)} rows)"
                )

        for col in ("trade_date", "data_date", "instrument"):
            if col in frame.columns and frame[col].isna().any():
                raise ValueError(f"{col} column contains null values")

        if "score" in frame.columns:
            valid = frame[frame.get("is_valid", True).astype(bool)] \
                if "is_valid" in frame.columns else frame
            if valid["score"].isna().any():
                raise ValueError("score column contains null values for valid rows")

    def _resolve_data_path(
        self, signal_id: str, signal_run_id: str, must_exist: bool = True
    ) -> Path:
        parquet_path = self.paths.signal_file(signal_id, signal_run_id, fmt="parquet")
        if parquet_path.exists():
            return parquet_path
        csv_path = self.paths.signal_file(signal_id, signal_run_id, fmt="csv")
        if csv_path.exists():
            return csv_path
        if must_exist:
            raise FileNotFoundError(
                f"No signal data found for {signal_id}/{signal_run_id} "
                f"(tried parquet and csv in {self.paths.signal_dir(signal_id, signal_run_id)})"
            )
        return None
