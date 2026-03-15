import pandas as pd
import numpy as np
import json
from pathlib import Path
from qsys.config import cfg
from qsys.utils.logger import log
from qsys.data.storage import StockDataStore
import qlib
from qlib.utils import exists_qlib_data
from qlib.data import D
from qlib.data.data import DatasetD
import sys
import os
import shutil
import subprocess

class QlibAdapter:
    def __init__(self):
        self.qlib_dir = Path(str(cfg.get_path("qlib_bin")))
        self.raw_dir = Path(str(cfg.get_path("raw_daily")))
        self.meta_db_path = Path(str(cfg.get_path("root"))) / "meta.db"

    def get_last_qlib_date(self):
        """Get the last date in Qlib calendar"""
        if not self.qlib_dir:
            return None
        cal_path = self.qlib_dir / "calendars" / "day.txt"
        if not cal_path.exists():
            return None
        try:
            df = pd.read_csv(cal_path, header=None)
            if df.empty:
                return None
            # Assuming sorted
            val = df.iloc[-1, 0]
            return pd.Timestamp(str(val))
        except Exception:
            return None

    def check_and_update(self, force=False):
        """
        Check if Feather data is newer than Qlib bin.
        If so, trigger incremental or full conversion.
        """
        # Ensure qlib_dir exists
        if not self.qlib_dir.exists():
            self.qlib_dir.mkdir(parents=True, exist_ok=True)

        if force:
             log.info("Force update requested, starting full conversion...")
             self.convert_all()
             return

        # Check if basic qlib structure exists
        features_dir = self.qlib_dir / "features"
        last_date = self.get_last_qlib_date()
        
        if not features_dir.exists() or not any(features_dir.iterdir()) or last_date is None:
             log.info("Qlib data incomplete or missing, starting full conversion...")
             self.convert_all()
             return

        raw_mtime = self.raw_dir.stat().st_mtime
        qlib_mtime = self.qlib_dir.stat().st_mtime
        
        # If raw data folder is modified, we check for updates
        if raw_mtime > qlib_mtime:
            log.info(f"Raw data updated. Checking for new data since {last_date.date()}...")
            self.convert_incremental(last_date)
        else:
            log.info("Qlib bin is up to date.")

    def normalize_instruments(self, instruments):
        if isinstance(instruments, str):
            low = instruments.lower()
            if low in ("all", "csi300", "csi500"):
                inst_path = self.qlib_dir / "instruments" / f"{low}.txt"
                if low != "all" and not inst_path.exists():
                    low = "all"
                try:
                    return D.instruments(low)
                except Exception:
                    return D.instruments("all")
            if "," in instruments:
                return instruments.split(",")
            return [instruments]
        return instruments

    def get_features(self, instruments, fields, start_time=None, end_time=None, freq="day", inst_processors=None):
        inst = self.normalize_instruments(instruments)
        return DatasetD.dataset(
            inst,
            fields,
            start_time=start_time,
            end_time=end_time,
            freq=freq,
            inst_processors=inst_processors or []
        )

    def _prepare_csvs(self, since_date=None):
        """
        Prepare CSVs from Feather files.
        If since_date is provided, only include rows AFTER this date.
        Returns path to csv_dir and count of files generated.
        """
        csv_dir = self.qlib_dir.parent / "qlib_csv_tmp"
        if csv_dir.exists():
            try:
                shutil.rmtree(csv_dir)
            except Exception as e:
                log.warning(f"Failed to remove {csv_dir}: {e}")
        
        try:
            csv_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"Failed to create {csv_dir}: {e}")
            return csv_dir, 0
            
        log.info(f"Prepared CSV directory: {csv_dir}")

        adapter_cfg = cfg.get_tushare_feature_config().get("adapter", {})
        feature_fields = adapter_cfg.get("feature_fields", [])
        qlib_fields = adapter_cfg.get("qlib_fields", [])
        target_fields = set(feature_fields) | set(qlib_fields)

        store = StockDataStore()
        stock_df = store.get_stock_list()
        industry_map = self._load_industry_map(stock_df)
        code_to_industry = {}
        if stock_df is not None and not stock_df.empty and "ts_code" in stock_df.columns and "industry" in stock_df.columns:
            code_to_industry = stock_df.set_index("ts_code")["industry"].to_dict()
        
        files = list(self.raw_dir.glob("*.feather"))
        if not files:
            log.warning("No feather files found.")
            return csv_dir, 0

        converted_count = 0
        for f in files:
            try:
                df = pd.read_feather(f)
                if df.empty:
                    continue
                
                # Standardize columns
                rename_map = dict(adapter_cfg.get("rename_map", {}) or {})
                fallback_rename = {
                    "trade_date": "date",
                    "adj_factor": "factor",
                    "vol": "volume",
                }
                for src, dst in fallback_rename.items():
                    if src in df.columns and dst not in df.columns:
                        rename_map[src] = dst
                df = df.rename(columns=rename_map)

                # Resolve duplicated market columns produced by merges
                if "close" not in df.columns:
                    if "close_x" in df.columns:
                        df["close"] = df["close_x"]
                    elif "close_y" in df.columns:
                        df["close"] = df["close_y"]
                if "date" not in df.columns and "trade_date" in df.columns:
                    df["date"] = df["trade_date"]
                if "factor" not in df.columns and "adj_factor" in df.columns:
                    df["factor"] = df["adj_factor"]
                if "volume" not in df.columns and "vol" in df.columns:
                    df["volume"] = df["vol"]
                
                # Unit Conversion (Tushare -> Qlib Standard)
                # Tushare vol is in lots (100 shares), Qlib expects shares
                if 'volume' in df.columns:
                    df['volume'] = df['volume'] * 100
                
                # Tushare amount is in thousands (1000 RMB), Qlib expects RMB
                if 'amount' in df.columns:
                    df['amount'] = df['amount'] * 1000
                
                # Scaling Check: Convert Wan to Yuan for Market Value (Tushare returns Wan)
                # Tushare strictly returns total_mv/circ_mv in 10,000 Yuan (Wan).
                # We unconditionally convert to Yuan to ensure consistency.
                for col in ['total_mv', 'circ_mv']:
                    if col in df.columns:
                        df[col] = df[col] * 10000

                if "ln_circ_mv" in target_fields and "circ_mv" in df.columns:
                    circ_mv = pd.to_numeric(df["circ_mv"], errors="coerce")
                    df["circ_mv"] = circ_mv
                    df["ln_circ_mv"] = np.where(circ_mv > 0, np.log(circ_mv), 0.0)
                if "ln_total_mv" in target_fields and "total_mv" in df.columns:
                    total_mv = pd.to_numeric(df["total_mv"], errors="coerce")
                    df["total_mv"] = total_mv
                    df["ln_total_mv"] = np.where(total_mv > 0, np.log(total_mv), 0.0)
                if "pcf" in target_fields and "pcf" not in df.columns:
                    if {"close", "total_share", "op_cashflow"}.issubset(df.columns):
                        share = pd.to_numeric(df["total_share"], errors="coerce")
                        ocf = pd.to_numeric(df["op_cashflow"], errors="coerce")
                        price = pd.to_numeric(df["close"], errors="coerce")
                        denom = ocf.replace(0, np.nan)
                        df["pcf"] = (price * share) / denom
                if "roe" in target_fields:
                    if "roe_waa" in df.columns:
                        df["roe"] = df["roe_waa"]
                    elif "roe" not in df.columns and {"net_income", "equity"}.issubset(df.columns):
                        ni = pd.to_numeric(df["net_income"], errors="coerce")
                        eq = pd.to_numeric(df["equity"], errors="coerce")
                        df["roe"] = ni / eq.replace(0, np.nan)
                if "ps" in target_fields and "ps" not in df.columns:
                    if {"total_mv", "revenue"}.issubset(df.columns):
                        total_mv = pd.to_numeric(df["total_mv"], errors="coerce")
                        revenue = pd.to_numeric(df["revenue"], errors="coerce")
                        denom = revenue.replace(0, np.nan)
                        df["ps"] = total_mv / denom
                if "ps_ttm" in target_fields and "ps_ttm" not in df.columns and "ps" in df.columns:
                    df["ps_ttm"] = df["ps"]
                if "pe_ttm" in target_fields and "pe_ttm" not in df.columns and "pe" in df.columns:
                    df["pe_ttm"] = df["pe"]
                symbol = f.stem
                if "industry" in target_fields:
                    industry_name = code_to_industry.get(symbol)
                    industry_id = industry_map.get(industry_name, 0) if industry_name is not None else 0
                    df["industry"] = industry_id

                # Ensure date format
                if not pd.api.types.is_datetime64_any_dtype(df['date']):
                    df['date'] = pd.to_datetime(df['date'])
                
                # Incremental Filter
                if since_date:
                    df = df[df['date'] > since_date]
                    if df.empty:
                        continue
                        
                df['date'] = df['date'].dt.strftime('%Y-%m-%d')
                
                # Fill NaNs
                if 'volume' in df.columns:
                    df['volume'] = df['volume'].fillna(0)
                
                # Select columns
                cols_to_include = list(qlib_fields)
                if "date" not in cols_to_include:
                    cols_to_include.append("date")
                final_cols = [c for c in cols_to_include if c in df.columns]
                
                # Save
                df[final_cols].to_csv(csv_dir / f"{symbol}.csv", index=False)
                converted_count += 1
                
            except Exception as e:
                if "adj_factor" not in str(e): # Ignore expected adj_factor missing
                    log.warning(f"Failed to convert {f.name}: {e}")

        return csv_dir, converted_count

    def _load_industry_map(self, stock_df: pd.DataFrame):
        meta_dir = cfg.get_path("meta")
        if meta_dir is None:
            return {}
        map_path = meta_dir / "industry_map.json"
        if map_path.exists():
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        if stock_df is None or stock_df.empty or "industry" not in stock_df.columns:
            return {}
        industry_series = stock_df["industry"].dropna().astype(str)
        industry_names = sorted(set([v for v in industry_series.tolist() if v and v != "nan"]))
        industry_map = {name: idx + 1 for idx, name in enumerate(industry_names)}
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(industry_map, f, ensure_ascii=False)
        return industry_map

    def convert_incremental(self, since_date):
        """Incremental update using dump_update"""
        log.info(f"Starting incremental update (since {since_date})...")
        csv_dir, count = self._prepare_csvs(since_date)
        
        if count == 0:
            log.info("No new data found to update.")
            # Touch qlib dir to update mtime so we don't check again immediately
            os.utime(self.qlib_dir, None)
            return

        log.info(f"Found {count} stocks with new data. Running dump_update...")
        self._run_dump_script(csv_dir, mode="dump_update")

    def convert_all(self):
        """Full update using dump_all"""
        log.info("Starting full Qlib data conversion...")
        csv_dir, count = self._prepare_csvs(since_date=None)
        
        if count == 0:
            log.error("No CSV files generated for full dump.")
            return

        log.info(f"Generated {count} CSV files. Running dump_all...")
        self._run_dump_script(csv_dir, mode="dump_all")

    def _run_dump_script(self, csv_dir, mode="dump_all"):
        """Helper to run the dump_bin.py script"""
        # Use cfg.project_root to find the script reliably
        dump_script = cfg.project_root / "scripts" / "dump_bin.py"
        
        if not dump_script.exists():
             # Fallback: check if we are in development mode and script is in relative path
             # e.g. if running from project root
             fallback = Path("scripts/dump_bin.py").resolve()
             if fallback.exists():
                 dump_script = fallback
        
        if not dump_script.exists():
             raise FileNotFoundError(f"dump_bin.py not found at {dump_script}")
        
        adapter_cfg = cfg.get_tushare_feature_config().get("adapter", {})
        qlib_fields = adapter_cfg.get("qlib_fields", [])
        include_fields = [f for f in qlib_fields if f != "date"]
        cmd = [
            sys.executable, str(dump_script), mode,
            "--data_path", str(csv_dir),
            "--qlib_dir", str(self.qlib_dir),
            "--include_fields", ",".join(include_fields),
            "--symbol_field_name", "symbol",
            "--date_field_name", "date"
        ]
            
        # log.info(f"Running command: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
            log.info(f"Qlib {mode} completed successfully.")
        except subprocess.CalledProcessError as e:
            log.error(f"Qlib dump failed: {e}")
        finally:
            if csv_dir.exists():
                shutil.rmtree(csv_dir)
            self._clean_artifacts()


    def _clean_artifacts(self):
        """Clean up mlruns and Users directories often generated by Qlib/MLflow"""
        project_root = cfg.project_root
        
        dirs_to_remove = [
            project_root / "mlruns",
            project_root / "Users",
            project_root / "notebooks" / "mlruns",
            project_root / "notebooks" / "Users"
        ]
        
        for d in dirs_to_remove:
            if d.exists() and d.is_dir():
                try:
                    shutil.rmtree(d)
                    log.info(f"Cleaned up artifact directory: {d}")
                except Exception as e:
                    log.warning(f"Failed to remove {d}: {e}")

    def init_qlib(self):
        """Initialize Qlib environment"""
        # Monkeypatch to stop git diff noise from Qlib Recorder
        try:
            import qlib.workflow.recorder as recorder_module
            
            recorder_cls = getattr(recorder_module, "Recorder", None)
            if recorder_cls is not None:
                try:
                    setattr(recorder_cls, "save_code", lambda self, **kwargs: None)
                except Exception:
                    pass
            
            mlflow_recorder_cls = getattr(recorder_module, "MLflowRecorder", None)
            if mlflow_recorder_cls is not None:
                try:
                    setattr(mlflow_recorder_cls, "save_code", lambda self, **kwargs: None)
                except Exception:
                    pass
                
        except (ImportError, AttributeError):
            pass

        try:
            from qlib.config import C
            if hasattr(C, "_config") and isinstance(C._config, dict) and "registered" not in C._config:
                C._config["registered"] = getattr(C, "_registered", False)
        except Exception:
            pass

        self._clean_artifacts()
        provider_uri = str(self.qlib_dir)
        try:
            qlib.init(provider_uri=provider_uri, region='cn')
        except Exception as e:
            if "QlibRecorder is already activated" in str(e):
                log.info("Qlib already initialized. Skip reinitialization.")
                return
            raise
        try:
            from qlib.config import C
            if hasattr(C, "_config") and isinstance(C._config, dict) and "registered" not in C._config:
                C._config["registered"] = getattr(C, "_registered", False)
        except Exception:
            pass
        self._clean_artifacts()
