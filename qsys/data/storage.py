import pandas as pd
import sqlite3
import os
import tempfile
from typing import Optional, cast
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

    def save_daily(self, df: pd.DataFrame, code: str, existing_df: Optional[pd.DataFrame] = None):
        """
        Save daily data to feather.
        If file exists, merge and deduplicate.
        """
        if df.empty:
            return

        file_path = self.canonical_dir / f"{code}.feather"

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

        if "circ_mv" in df.columns:
            df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce").fillna(0.0)
            df.loc[df["circ_mv"] < 0, "circ_mv"] = 0.0

        # Atomic write
        self._atomic_write(df, file_path)
        latest_date = df['trade_date'].astype(str).max()
        if latest_date:
            self.update_latest_date(code, latest_date)

    def load_daily(self, code: str) -> Optional[pd.DataFrame]:
        file_path = self.canonical_dir / f"{code}.feather"
        if not file_path.exists():
            return None
        return pd.read_feather(file_path)

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
