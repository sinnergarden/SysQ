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
        raw_daily_dir = cfg.get_path("raw_daily")
        if raw_daily_dir is None:
            raise ValueError("raw_daily path not found in settings")
        self.raw_daily_dir = cast(Path, raw_daily_dir)
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

        file_path = self.raw_daily_dir / f"{code}.feather"
        
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

        if "circ_mv" in df.columns:
            df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce").fillna(0.0)
            df.loc[df["circ_mv"] < 0, "circ_mv"] = 0.0

        # Atomic write
        self._atomic_write(df, file_path)
        latest_date = df['trade_date'].astype(str).max()
        if latest_date:
            self.update_latest_date(code, latest_date)

    def load_daily(self, code: str) -> Optional[pd.DataFrame]:
        file_path = self.raw_daily_dir / f"{code}.feather"
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
