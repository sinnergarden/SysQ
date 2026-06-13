"""DuckDB-powered cross-signal analytics layer.

Reads signal and label parquet files directly via DuckDB's
``read_parquet``.  No data migration needed — queries run
in-place on existing artifacts.

Usage
-----
::

    sa = SignalAnalytics("data/research")
    sa.list_signals()
    sa.list_labels()
    sa.compute_ic_matrix()         # N×M IC/ICIR matrix
    sa.compute_rank_ic_matrix()    # Spearman rank IC
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


# ── Quote helper ──────────────────────────────────────────────────────


def _sq(s: str) -> str:
    """Wrap a string in single quotes for safe SQL literal use."""
    return "'" + s.replace("'", "''") + "'"


class SignalAnalytics:
    """DuckDB-powered cross-signal analytics.

    Only supports parquet artifacts (``predictions.parquet``,
    ``labels.parquet``).  CSV artifacts are not scanned.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).  Must contain
        ``signals/`` and ``labels/`` subdirectories with parquet artifacts.
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._signals_dir = self.root / "signals"
        self._labels_dir = self.root / "labels"
        import duckdb

        self._con = duckdb.connect()

    # ── Discovery ───────────────────────────────────────────────────────

    def list_signals(self) -> pd.DataFrame:
        """List available signals with their run IDs and date ranges.

        Returns
        -------
        pd.DataFrame
            Columns: ``signal_id``, ``signal_run_id``, ``row_count``,
            ``date_min``, ``date_max``.
        """
        rows: list[dict[str, Any]] = []
        if not self._signals_dir.exists():
            return pd.DataFrame(columns=["signal_id", "signal_run_id", "row_count", "date_min", "date_max"])

        for sig_dir in sorted(self._signals_dir.iterdir()):
            if not sig_dir.is_dir():
                continue
            sid = sig_dir.name
            for run_dir in sorted(sig_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                parquet = run_dir / "predictions.parquet"
                if not parquet.exists():
                    continue
                try:
                    df = self._con.execute(
                        f"SELECT count(*) AS cnt, min(trade_date) AS dmin, max(trade_date) AS dmax "
                        f"FROM read_parquet({_sq(str(parquet))})"
                    ).fetchdf()
                    rows.append({
                        "signal_id": sid,
                        "signal_run_id": run_dir.name,
                        "row_count": int(df["cnt"].iloc[0]),
                        "date_min": str(df["dmin"].iloc[0]),
                        "date_max": str(df["dmax"].iloc[0]),
                    })
                except Exception:
                    rows.append({
                        "signal_id": sid,
                        "signal_run_id": run_dir.name,
                        "row_count": -1,
                        "date_min": None,
                        "date_max": None,
                    })
        return pd.DataFrame(rows)

    def list_labels(self) -> pd.DataFrame:
        """List available labels.

        Returns
        -------
        pd.DataFrame
            Columns: ``label_id``, ``row_count``, ``date_min``, ``date_max``.
        """
        rows: list[dict[str, Any]] = []
        if not self._labels_dir.exists():
            return pd.DataFrame(columns=["label_id", "row_count", "date_min", "date_max"])

        for lbl_dir in sorted(self._labels_dir.iterdir()):
            if not lbl_dir.is_dir():
                continue
            lid = lbl_dir.name
            parquet = lbl_dir / "labels.parquet"
            if not parquet.exists():
                continue
            try:
                df = self._con.execute(
                    f"SELECT count(*) AS cnt, min(trade_date) AS dmin, max(trade_date) AS dmax "
                    f"FROM read_parquet({_sq(str(parquet))})"
                ).fetchdf()
                rows.append({
                    "label_id": lid,
                    "row_count": int(df["cnt"].iloc[0]),
                    "date_min": str(df["dmin"].iloc[0]),
                    "date_max": str(df["dmax"].iloc[0]),
                })
            except Exception:
                pass
        return pd.DataFrame(rows)

    # ── IC matrix ───────────────────────────────────────────────────────

    def compute_ic_matrix(
        self,
        signal_ids: list[str] | None = None,
        signal_run_ids: dict[str, str] | None = None,
        label_ids: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 5,
    ) -> pd.DataFrame:
        """Compute N×M IC matrix (Pearson correlation mean across dates).

        Parameters
        ----------
        signal_ids:
            Signals to include.  ``None`` = all discovered.
        signal_run_ids:
            Optional mapping from ``signal_id`` to specific ``signal_run_id``.
            When absent, uses the most recently created run for each signal.
        label_ids:
            Labels to include.  ``None`` = all discovered.
        start_date, end_date:
            Optional date range filter (YYYY-MM-DD).
        min_count:
            Minimum observations per date to compute IC (default 5).

        Returns
        -------
        pd.DataFrame
            Columns: ``signal_id``, ``label_id``, ``ic_mean``, ``ic_std``, ``icir``.
        """
        sigs = self._resolve_signals(signal_ids, signal_run_ids)
        lbls = self._resolve_labels(label_ids)

        rows: list[dict[str, Any]] = []
        for sig in sigs:
            for lbl in lbls:
                row = self._ic_single(
                    signal_path=sig["path"],
                    sid=sig["signal_id"],
                    label_path=lbl["path"],
                    lid=lbl["label_id"],
                    start_date=start_date,
                    end_date=end_date,
                    min_count=min_count,
                )
                if row is not None:
                    rows.append(row)

        if not rows:
            return pd.DataFrame(columns=["signal_id", "label_id", "ic_mean", "ic_std", "icir"])
        return pd.DataFrame(rows)

    def compute_rank_ic_matrix(
        self,
        signal_ids: list[str] | None = None,
        signal_run_ids: dict[str, str] | None = None,
        label_ids: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 5,
    ) -> pd.DataFrame:
        """Spearman rank IC matrix."""
        sigs = self._resolve_signals(signal_ids, signal_run_ids)
        lbls = self._resolve_labels(label_ids)

        rows: list[dict[str, Any]] = []
        for sig in sigs:
            for lbl in lbls:
                row = self._rank_ic_single(
                    signal_path=sig["path"],
                    sid=sig["signal_id"],
                    label_path=lbl["path"],
                    lid=lbl["label_id"],
                    start_date=start_date,
                    end_date=end_date,
                    min_count=min_count,
                )
                if row is not None:
                    rows.append(row)

        if not rows:
            return pd.DataFrame(columns=["signal_id", "label_id", "rank_ic_mean", "rank_ic_std", "rank_icir"])
        return pd.DataFrame(rows)

    # ── Single-signal IC queries ────────────────────────────────────────

    def daily_ic(
        self,
        signal_id: str,
        signal_run_id: str | None = None,
        label_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Daily IC series for one signal × one label."""
        sig_path = self._resolve_single_signal(signal_id, signal_run_id)
        lbls = self._resolve_labels([label_id] if label_id else None)
        if not lbls:
            return pd.DataFrame(columns=["trade_date", "ic", "n"])
        lbl = lbls[0]
        sql = self._build_daily_ic_sql(
            signal_path=sig_path,
            signal_id=signal_id,
            label_path=lbl["path"],
            label_id=lbl["label_id"],
            start_date=start_date,
            end_date=end_date,
            as_series=True,
        )
        return self._con.execute(sql).fetchdf()

    # ── Raw SQL access ──────────────────────────────────────────────────

    def query(self, sql: str) -> pd.DataFrame:
        """Execute arbitrary DuckDB SQL.

        Signal/label parquet files are accessible via ``read_parquet('/path/to/file.parquet')``.
        """
        return self._con.execute(sql).fetchdf()

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._con.close()

    # ── Internal helpers ────────────────────────────────────────────────

    def _signal_path(self, signal_id: str, signal_run_id: str) -> Path:
        return self.root / "signals" / signal_id / signal_run_id / "predictions.parquet"

    def _label_path(self, label_id: str) -> Path:
        return self.root / "labels" / label_id / "labels.parquet"

    def _resolve_signals(
        self,
        signal_ids: list[str] | None,
        signal_run_ids: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        """Resolve signal paths by most-recently-created run."""
        discovered = self.list_signals()
        if discovered.empty:
            return []
        if signal_ids:
            discovered = discovered[discovered["signal_id"].isin(signal_ids)]
        result: list[dict[str, Any]] = []
        for sig_id in discovered["signal_id"].unique():
            runs = discovered[discovered["signal_id"] == sig_id]
            run_id = (signal_run_ids or {}).get(sig_id)
            if run_id is None:
                # Pick most recently created run directory (mtime)
                run_dirs = sorted(
                    d for d in (self._signals_dir / sig_id).iterdir()
                    if d.is_dir() and (d / "predictions.parquet").exists()
                )
                run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                if not run_dirs:
                    continue
                run_id = run_dirs[0].name
            path = self._signal_path(sig_id, run_id)
            if path.exists():
                result.append({"path": str(path), "signal_id": sig_id, "signal_run_id": run_id})
        return result

    def _resolve_labels(self, label_ids: list[str] | None) -> list[dict[str, Any]]:
        discovered = self.list_labels()
        if discovered.empty:
            return []
        if label_ids:
            discovered = discovered[discovered["label_id"].isin(label_ids)]
        result: list[dict[str, Any]] = []
        for _, row in discovered.iterrows():
            path = self._label_path(row["label_id"])
            if path.exists():
                result.append({"path": str(path), "label_id": row["label_id"]})
        return result

    def _resolve_single_signal(self, signal_id: str, signal_run_id: str | None) -> str:
        if signal_run_id:
            path = self._signal_path(signal_id, signal_run_id)
            if path.exists():
                return str(path)
        discovered = self.list_signals()
        runs = discovered[discovered["signal_id"] == signal_id]
        if runs.empty:
            raise FileNotFoundError(f"No signal data for {signal_id}")
        if signal_run_id:
            runs = runs[runs["signal_run_id"] == signal_run_id]
            if runs.empty:
                raise FileNotFoundError(f"No signal run {signal_id}/{signal_run_id}")
        # Use mtime for latest-run (parquet-valid only)
        sig_dir = self._signals_dir / signal_id
        if sig_dir.exists():
            run_dirs = sorted(
                d for d in sig_dir.iterdir()
                if d.is_dir() and (d / "predictions.parquet").exists()
            )
            run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if not run_dirs:
                raise FileNotFoundError(f"No valid signal parquet for {signal_id}")
            run_id = run_dirs[0].name
        return str(self._signal_path(signal_id, run_id))

    # ── IC computation ──────────────────────────────────────────────────

    @staticmethod
    def _build_daily_ic_sql(
        signal_path: str, signal_id: str,
        label_path: str, label_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        as_series: bool = False,
        min_count: int = 5,
    ) -> str:
        """Build SQL for daily IC aggregation.

        When *as_series* is True, returns daily IC values (for time-series).
        When False, returns aggregated (mean, std, icir).
        """
        date_filter = ""
        if start_date and end_date:
            date_filter = f"AND s.trade_date BETWEEN {_sq(start_date)} AND {_sq(end_date)}"
        elif start_date:
            date_filter = f"AND s.trade_date >= {_sq(start_date)}"
        elif end_date:
            date_filter = f"AND s.trade_date <= {_sq(end_date)}"

        joined_sql = f"""
        SELECT s.trade_date, s.score, l.label_value
        FROM read_parquet({_sq(signal_path)}) s
        JOIN read_parquet({_sq(label_path)}) l
          ON s.trade_date = l.trade_date AND s.instrument = l.instrument
        WHERE l.label_id = {_sq(label_id)}
          AND s.score IS NOT NULL
          AND l.label_value IS NOT NULL
          {date_filter}
        """

        if as_series:
            return f"""
            SELECT trade_date,
                   corr(score, label_value) AS ic,
                   count(*) AS n
            FROM ({joined_sql})
            GROUP BY trade_date
            ORDER BY trade_date
            """
        return f"""
        WITH daily AS (
            SELECT trade_date,
                   corr(score, label_value) AS ic,
                   count(*) AS n
            FROM ({joined_sql})
            GROUP BY trade_date
            HAVING count(*) >= {int(min_count)}
        ),
        daily_clean AS (
            SELECT ic, n FROM daily
            WHERE ic IS NOT NULL AND NOT isnan(ic)
        )
        SELECT AVG(ic) AS ic_mean,
               COALESCE(STDDEV(ic), 0) AS ic_std,
               AVG(ic) / NULLIF(STDDEV(ic), 0) AS icir
        FROM daily_clean
        """

    def _ic_single(
        self,
        signal_path: str, sid: str,
        label_path: str, lid: str,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 5,
    ) -> dict[str, Any] | None:
        """Compute IC result for one signal-label pair."""
        sql = self._build_daily_ic_sql(
            signal_path, sid, label_path, lid,
            start_date, end_date, min_count=min_count,
        )
        try:
            df = self._con.execute(sql).fetchdf()
        except Exception as e:
            log.warning("IC computation failed for %s vs %s: %s", sid, lid, e)
            return None
        if df.empty or df["ic_mean"].isna().all():
            return None
        return {
            "signal_id": sid,
            "label_id": lid,
            "ic_mean": float(df["ic_mean"].iloc[0]) if pd.notna(df["ic_mean"].iloc[0]) else None,
            "ic_std": float(df["ic_std"].iloc[0]) if pd.notna(df["ic_std"].iloc[0]) else None,
            "icir": float(df["icir"].iloc[0]) if pd.notna(df["icir"].iloc[0]) else None,
        }

    def _rank_ic_single(
        self,
        signal_path: str, sid: str,
        label_path: str, lid: str,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 5,
    ) -> dict[str, Any] | None:
        """Compute Spearman rank IC for one signal-label pair."""
        date_filter = ""
        if start_date and end_date:
            date_filter = f"AND s.trade_date BETWEEN {_sq(start_date)} AND {_sq(end_date)}"
        elif start_date:
            date_filter = f"AND s.trade_date >= {_sq(start_date)}"
        elif end_date:
            date_filter = f"AND s.trade_date <= {_sq(end_date)}"

        sql = f"""
        WITH joined AS (
            SELECT s.trade_date, s.score, l.label_value
            FROM read_parquet({_sq(signal_path)}) s
            JOIN read_parquet({_sq(label_path)}) l
              ON s.trade_date = l.trade_date AND s.instrument = l.instrument
            WHERE l.label_id = {_sq(lid)}
              AND s.score IS NOT NULL
              AND l.label_value IS NOT NULL
              {date_filter}
        ),
        ranked AS (
            SELECT trade_date,
                   score,
                   label_value,
                   rank() OVER (PARTITION BY trade_date ORDER BY score) AS score_rank,
                   rank() OVER (PARTITION BY trade_date ORDER BY label_value) AS label_rank
            FROM joined
        ),
        daily_rank_ic AS (
            SELECT trade_date,
                   corr(score_rank, label_rank) AS rank_ic,
                   count(*) AS n
            FROM ranked
            GROUP BY trade_date
            HAVING count(*) >= {int(min_count)}
        ),
        daily_clean AS (
            SELECT rank_ic, n FROM daily_rank_ic
            WHERE rank_ic IS NOT NULL AND NOT isnan(rank_ic)
        )
        SELECT AVG(rank_ic) AS rank_ic_mean,
               COALESCE(STDDEV(rank_ic), 0) AS rank_ic_std,
               AVG(rank_ic) / NULLIF(STDDEV(rank_ic), 0) AS rank_icir
        FROM daily_clean
        """
        try:
            df = self._con.execute(sql).fetchdf()
        except Exception as e:
            log.warning("Rank IC computation failed for %s vs %s: %s", sid, lid, e)
            return None
        if df.empty or df["rank_ic_mean"].isna().all():
            log.warning("Rank IC result empty for %s vs %s", sid, lid)
            return None
        return {
            "signal_id": sid,
            "label_id": lid,
            "rank_ic_mean": float(df["rank_ic_mean"].iloc[0]) if pd.notna(df["rank_ic_mean"].iloc[0]) else None,
            "rank_ic_std": float(df["rank_ic_std"].iloc[0]) if pd.notna(df["rank_ic_std"].iloc[0]) else None,
            "rank_icir": float(df["rank_icir"].iloc[0]) if pd.notna(df["rank_icir"].iloc[0]) else None,
        }

    def __enter__(self) -> SignalAnalytics:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
