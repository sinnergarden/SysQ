import pandas as pd
import sqlite3
import os
import tempfile
from typing import Any, Optional, cast
from pathlib import Path
from qsys.config import cfg
from qsys.utils.logger import log

class StockDataStore:
    def __init__(self):
        canonical_dir = cfg.get_path("canonical_dir")
        if canonical_dir is None:
            raise ValueError("canonical_dir path not found in settings")
        self.canonical_dir = cast(Path, canonical_dir)
        root_path = cfg.get_path("root")
        if root_path is None:
            raise ValueError("root path not found in settings")
        self.meta_db_path = cast(Path, root_path) / "meta.db"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite tables for metadata"""
        try:
            with sqlite3.connect(self.meta_db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS stock_basic (
                        ts_code TEXT PRIMARY KEY,
                        symbol TEXT,
                        name TEXT,
                        area TEXT,
                        industry TEXT,
                        market TEXT,
                        list_date TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS trade_cal (
                        exchange TEXT,
                        cal_date TEXT,
                        is_open INTEGER,
                        PRIMARY KEY (exchange, cal_date)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS data_latest (
                        ts_code TEXT PRIMARY KEY,
                        latest_date TEXT
                    )
                """)
        except Exception as e:
            log.error(f"Failed to init DB: {e}")

    def save_daily(
        self,
        df: pd.DataFrame,
        code: str,
        existing_df: Optional[pd.DataFrame] = None,
    ) -> list[dict[str, Any]]:
        """
        Save daily data to feather.
        If file exists, merge and deduplicate.

        Returns canonical mutation receipts.  Receipts contain only the exact
        affected symbol/date/field names and hashes of those affected windows;
        they never persist full before/after values.
        """
        if df.empty:
            return []

        file_path = self.canonical_dir / f"{code}.feather"
        incoming_df = df.copy()
        old_df: Optional[pd.DataFrame] = None

        # Ensure data types
        # Tushare returns object for some floats sometimes, ensure conversion
        # df = df.convert_dtypes() # Safe but slow?

        if file_path.exists():
            try:
                old_df = existing_df if existing_df is not None else pd.read_feather(file_path)
                # Merge: concat old and new
                df = pd.concat([old_df, df], ignore_index=True)
                # Deduplicate by trade_date, keep last (newest)
                df = df.drop_duplicates(subset=['trade_date'], keep='last')
                # Sort by date
                df = df.sort_values('trade_date').reset_index(drop=True)
            except Exception as e:
                log.error(f"Failed to read existing file for {code}: {e}")
                # We raise here because we don't want to silently overwrite history
                raise e

        # Canonical cleaning: coalesce merge artifacts before writing
        from qsys.data.cleaner import coalesce_merge_suffix_columns

        df = coalesce_merge_suffix_columns(df)
        old_canonical_df = (
            coalesce_merge_suffix_columns(old_df.copy()) if old_df is not None else None
        )

        if "circ_mv" in df.columns:
            df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce").fillna(0.0)
            df.loc[df["circ_mv"] < 0, "circ_mv"] = 0.0

        from qsys.data.source_audit import build_canonical_mutations, utc_now

        mutations = build_canonical_mutations(
            symbol=code,
            incoming=incoming_df,
            before=old_canonical_df,
            after=df,
        )

        # Atomic write
        self._atomic_write(df, file_path)
        committed_at = utc_now()
        for mutation in mutations:
            mutation["ingested_at"] = committed_at
        latest_date = df['trade_date'].astype(str).max()
        if latest_date:
            self.update_latest_date(code, latest_date)
        return mutations

    def load_daily(self, code: str) -> Optional[pd.DataFrame]:
        file_path = self.canonical_dir / f"{code}.feather"
        if not file_path.exists():
            return None
        return pd.read_feather(file_path)

    def load_daily_window(
        self,
        code: str,
        *,
        start_date: str,
        end_date: str,
        columns: list[str] | None = None,
    ) -> Optional[pd.DataFrame]:
        file_path = self.canonical_dir / f"{code}.feather"
        if not file_path.exists():
            return None
        requested = list(dict.fromkeys(["trade_date", *(columns or [])]))
        frame = pd.read_feather(file_path, columns=requested)
        dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        return frame.loc[(dates >= start_date) & (dates <= end_date)].copy()

    def merge_daily_industry(
        self,
        df: pd.DataFrame,
        code: str,
        *,
        source_run_id: str,
        source_receipt_id: str,
    ) -> list[dict[str, Any]]:
        """Atomically project audited PIT industry values into canonical daily rows.

        Rows outside the existing daily frame are intentionally ignored: ``bak_basic``
        describes every calendar snapshot, while the canonical SOT contains trading
        observations only. Supplier corrections overwrite the same key and are
        captured by the canonical mutation ledger.
        """

        required = {"ts_code", "trade_date", "industry"}
        if df is None or df.empty:
            return []
        if not required.issubset(df.columns):
            raise ValueError("industry history requires ts_code, trade_date, industry")
        symbol = str(code).strip().upper()
        if not symbol or not source_run_id or not source_receipt_id:
            raise ValueError("industry history requires source lineage")
        incoming = df.loc[:, ["ts_code", "trade_date", "industry"]].copy()
        incoming["ts_code"] = incoming["ts_code"].astype("string").str.strip().str.upper()
        incoming["trade_date"] = (
            incoming["trade_date"]
            .astype("string")
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace(r"\.0$", "", regex=True)
        )
        incoming["industry"] = incoming["industry"].astype("string").str.strip()
        if (
            incoming["ts_code"].isna().any()
            or not incoming["ts_code"].eq(symbol).all()
            or incoming["trade_date"].isna().any()
            or not incoming["trade_date"].str.fullmatch(r"\d{8}").all()
            or incoming["industry"].isna().any()
            or incoming["industry"].eq("").any()
            or incoming.duplicated(["ts_code", "trade_date"]).any()
        ):
            raise ValueError("industry history rows are invalid or duplicated")
        target = self.canonical_dir / f"{symbol}.feather"
        if not target.is_file():
            raise ValueError(f"canonical daily frame missing for {symbol}")
        existing = pd.read_feather(target)
        if existing.empty or "trade_date" not in existing.columns:
            raise ValueError(f"canonical daily frame invalid for {symbol}")
        before = existing.copy()
        canonical_dates = (
            existing["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
        )
        lookup = incoming.set_index("trade_date")["industry"]
        projected = canonical_dates.map(lookup)
        if "industry" in existing.columns:
            prior = existing["industry"].astype("string").str.strip()
            changed = projected.notna() & (prior.isna() | prior.eq("") | prior.ne(projected))
            existing["industry"] = prior.where(~changed, projected)
        else:
            existing["industry"] = projected
            changed = projected.notna()
        touched = projected.notna()
        for column, value in (
            ("industry_source_run_id", str(source_run_id)),
            ("industry_source_receipt_id", str(source_receipt_id)),
        ):
            if column not in existing.columns:
                existing[column] = pd.NA
            missing = existing[column].isna() | existing[column].astype("string").str.strip().eq("")
            existing.loc[touched & (changed | missing), column] = value

        from qsys.data.source_audit import build_canonical_mutations, utc_now

        committed_at = utc_now()
        value_columns = ["trade_date", "industry"]
        mutations = build_canonical_mutations(
            symbol=symbol,
            incoming=pd.DataFrame({
                "ts_code": symbol,
                "trade_date": canonical_dates.loc[touched],
                "industry": projected.loc[touched],
            }),
            before=before.loc[:, [column for column in value_columns if column in before.columns]],
            after=existing.loc[:, value_columns],
            ingested_at=committed_at,
        )
        for mutation in mutations:
            mutation.update({
                "dataset": "canonical_daily",
                "endpoint": "bak_basic",
                "fetch_receipt_id": str(source_receipt_id),
            })
        self._atomic_write(existing, target)
        return mutations

    def _atomic_write(self, df: pd.DataFrame, target_path: Path):
        # Write to temp file first
        # Use directory of target_path to ensure same filesystem (for atomic rename)
        fd, temp_path = tempfile.mkstemp(dir=target_path.parent, suffix=".tmp")
        try:
            os.close(fd)
            df.to_feather(temp_path)
            # Rename (atomic on POSIX)
            Path(temp_path).rename(target_path)
        except Exception as e:
            log.error(f"Atomic write failed for {target_path}: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def update_latest_date(self, code: str, latest_date: str):
        with sqlite3.connect(self.meta_db_path) as conn:
            conn.execute(
                "INSERT INTO data_latest (ts_code, latest_date) VALUES (?, ?) "
                "ON CONFLICT(ts_code) DO UPDATE SET latest_date=excluded.latest_date",
                (code, latest_date)
            )

    def get_global_latest_date(self) -> Optional[str]:
        with sqlite3.connect(self.meta_db_path) as conn:
            row = conn.execute("SELECT MAX(latest_date) FROM data_latest").fetchone()
        if row and row[0]:
            return row[0]
        return None

    def save_meta_stocks(self, df: pd.DataFrame):
        with sqlite3.connect(self.meta_db_path) as conn:
            df.to_sql('stock_basic', conn, if_exists='replace', index=False)

    def save_meta_calendar(self, df: pd.DataFrame):
        with sqlite3.connect(self.meta_db_path) as conn:
            df.to_sql('trade_cal', conn, if_exists='replace', index=False)
            
    def get_stock_list(self):
        with sqlite3.connect(self.meta_db_path) as conn:
            return pd.read_sql("SELECT * FROM stock_basic", conn)

    def get_calendar(self):
        with sqlite3.connect(self.meta_db_path) as conn:
            return pd.read_sql("SELECT * FROM trade_cal", conn)

    # === Dragon-Tiger List (龙虎榜) Storage ===

    _TOP_INST_COLS = ["trade_date", "ts_code", "exalter", "buy", "sell", "net_buy"]
    _TOP_LIST_COLS = ["trade_date", "ts_code", "name", "close", "pct_chg",
                      "turnover_rate", "amount", "buyer_sum", "seller_sum",
                      "net_amount", "reason"]

    def _save_with_upsert(self, df: pd.DataFrame, table: str, schema_cols: list[str], pk_cols: list[str]):
        """Save DataFrame to SQLite table with INSERT OR REPLACE, ensuring column alignment."""
        # Add any missing columns with NULL
        for col in schema_cols:
            if col not in df.columns:
                df[col] = None
        # Reorder to match schema
        df = df[schema_cols]
        # Drop rows where all PK columns are null
        df = df.dropna(subset=pk_cols, how='any')
        if df.empty:
            return

        with sqlite3.connect(self.meta_db_path) as conn:
            # Create table if not exists
            col_defs = ", ".join(f'"{c}" REAL' if c in ("buy", "sell", "net_buy", "close", "pct_chg",
                              "turnover_rate", "amount", "buyer_sum", "seller_sum", "net_amount")
                                 else f'"{c}" TEXT' for c in schema_cols)
            pk_def = ", ".join(f'"{c}"' for c in pk_cols)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    {col_defs},
                    PRIMARY KEY ({pk_def})
                )
            """)
            # Use a temp table + INSERT OR REPLACE to safely upsert
            df.to_sql(f"_tmp_{table}", conn, if_exists='replace', index=False)
            col_list = ", ".join(f'"{c}"' for c in schema_cols)
            conn.execute(f"INSERT OR REPLACE INTO {table} ({col_list}) SELECT {col_list} FROM _tmp_{table}")
            conn.execute(f"DROP TABLE _tmp_{table}")

    def save_top_inst(self, df: pd.DataFrame, trade_date: str):
        """Save top_inst (机构席位) data to SQLite."""
        if df is None or df.empty:
            return
        try:
            self._save_with_upsert(df, "top_inst", self._TOP_INST_COLS,
                                   ["trade_date", "ts_code", "exalter"])
            log.info(f"Saved top_inst: {len(df)} records for {trade_date}")
        except Exception as e:
            log.error(f"Failed to save top_inst: {e}")

    def save_top_list(self, df: pd.DataFrame, trade_date: str):
        """Save top_list (龙虎榜列表) data to SQLite."""
        if df is None or df.empty:
            return
        try:
            self._save_with_upsert(df, "top_list", self._TOP_LIST_COLS,
                                   ["trade_date", "ts_code", "reason"])
            log.info(f"Saved top_list: {len(df)} records for {trade_date}")
        except Exception as e:
            log.error(f"Failed to save top_list: {e}")

    def load_top_inst(self, trade_date: str = None) -> Optional[pd.DataFrame]:
        """Load top_inst data, optionally filtered by trade_date."""
        try:
            with sqlite3.connect(self.meta_db_path) as conn:
                if trade_date:
                    return pd.read_sql(
                        "SELECT * FROM top_inst WHERE trade_date = ?", conn, params=(trade_date,)
                    )
                return pd.read_sql("SELECT * FROM top_inst", conn)
        except Exception as e:
            log.error(f"Failed to load top_inst: {e}")
            return None

    def load_top_list(self, trade_date: str = None) -> Optional[pd.DataFrame]:
        """Load top_list data, optionally filtered by trade_date."""
        try:
            with sqlite3.connect(self.meta_db_path) as conn:
                if trade_date:
                    return pd.read_sql(
                        "SELECT * FROM top_list WHERE trade_date = ?", conn, params=(trade_date,)
                    )
                return pd.read_sql("SELECT * FROM top_list", conn)
        except Exception as e:
            log.error(f"Failed to load top_list: {e}")
            return None
