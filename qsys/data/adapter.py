import pandas as pd
import numpy as np
import json
from pathlib import Path
from typing import Optional
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

    def refresh_qlib_date(self):
        """
        Explicitly refresh Qlib bin by checking for new raw data and updating.
        Should be called after raw data updates to close the loop.
        """
        raw_latest = self._get_raw_latest_date()
        if raw_latest is None:
            log.info("No raw data found, skipping qlib refresh.")
            return

        log.info(f"Raw data latest date: {raw_latest}")
        self.check_and_update(force=False)

        # Verify the update
        qlib_latest = self.get_last_qlib_date()
        if qlib_latest:
            log.info(f"Qlib bin latest date after refresh: {qlib_latest.date()}")
        else:
            log.warning("Failed to get qlib latest date after refresh")

    def _get_raw_latest_date(self) -> Optional[pd.Timestamp]:
        """Get the latest date from raw feather data"""
        try:
            store = StockDataStore()
            return store.get_global_latest_date()
        except Exception as e:
            log.warning(f"Failed to get raw latest date: {e}")
            return None

    def get_data_status_report(self, target_date: str = None) -> dict:
        """
        Get a comprehensive status report of data alignment.
        
        Returns dict with:
        - raw_latest: latest date in raw feather data
        - qlib_latest: latest date in qlib bin
        - target_signal_date: the date we want data to be available for
        - aligned: whether raw and qlib are aligned
        - gap: days between raw and qlib
        """
        store = StockDataStore()
        raw_latest_str = store.get_global_latest_date()
        raw_latest = pd.Timestamp(raw_latest_str) if raw_latest_str else None
        raw_latest_fmt = raw_latest.strftime("%Y-%m-%d") if raw_latest is not None else None

        qlib_latest_ts = self.get_last_qlib_date()
        qlib_latest = qlib_latest_ts.strftime("%Y-%m-%d") if qlib_latest_ts else None

        # Determine target signal date
        if target_date:
            target_signal = pd.Timestamp(target_date)
        else:
            # Default: yesterday (assuming today might not have data yet)
            target_signal = pd.Timestamp.now() - pd.Timedelta(days=1)
            # Adjust to last trading day if needed
            try:
                from qlib.data import D
                cal = D.calendar(start_time=target_signal - pd.Timedelta(days=7), end_time=target_signal)
                if cal:
                    target_signal = pd.Timestamp(cal[-1])
            except Exception:
                pass

        gap = None
        aligned = False
        if raw_latest and qlib_latest:
            gap = (raw_latest - qlib_latest_ts).days
            aligned = raw_latest_fmt == qlib_latest

        return {
            "raw_latest": raw_latest_fmt,
            "qlib_latest": qlib_latest,
            "target_signal_date": target_signal.strftime("%Y-%m-%d") if target_signal else None,
            "aligned": aligned,
            "gap_days": gap,
        }

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
            raw_latest = self._get_raw_latest_date()
            if raw_latest is not None and pd.Timestamp(raw_latest) <= last_date:
                log.info("Detected raw data repair on the latest qlib date. Running dump_fix to refresh same-day features...")
                self.convert_fix(last_date)
            else:
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

    @staticmethod
    def _coalesce_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
        if not df.columns.duplicated().any():
            return df

        collapsed = pd.DataFrame(index=df.index)
        for col in df.columns.unique():
            selected = df.loc[:, col]
            if isinstance(selected, pd.DataFrame):
                series = selected.iloc[:, 0]
                for idx in range(1, selected.shape[1]):
                    series = series.combine_first(selected.iloc[:, idx])
                collapsed[col] = series
            else:
                collapsed[col] = selected
        return collapsed

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
                df = self._coalesce_duplicate_columns(df)

                # Resolve duplicated columns produced by merges
                def _collapse_column(name: str):
                    if name not in df.columns:
                        return None
                    obj = df.loc[:, name]
                    if isinstance(obj, pd.DataFrame):
                        series = None
                        for i in range(obj.shape[1]):
                            cur = pd.to_numeric(obj.iloc[:, i], errors='coerce')
                            series = cur if series is None else series.combine_first(cur)
                        return series
                    return pd.to_numeric(obj, errors='coerce')

                def _coalesce_column(target_col: str, candidates: list[str]) -> None:
                    merged = _collapse_column(target_col)
                    for candidate in candidates:
                        cur = _collapse_column(candidate)
                        if cur is None:
                            continue
                        merged = cur if merged is None else merged.combine_first(cur)
                    if merged is not None:
                        df[target_col] = merged

                _coalesce_column("close", ["close_x", "close_y"])
                _coalesce_column("open", ["open_x", "open_y"])
                _coalesce_column("high", ["high_x", "high_y"])
                _coalesce_column("low", ["low_x", "low_y"])
                _coalesce_column("date", ["trade_date"])
                _coalesce_column("factor", ["adj_factor"])
                _coalesce_column("volume", ["vol"])
                _coalesce_column("high_limit", ["up_limit"])
                _coalesce_column("low_limit", ["down_limit"])

                # Collapse any remaining duplicated columns so downstream numeric ops always see Series.
                deduped = {}
                for col_name in list(dict.fromkeys(df.columns.tolist())):
                    collapsed = _collapse_column(col_name)
                    if collapsed is None:
                        obj = df.loc[:, col_name]
                        if isinstance(obj, pd.DataFrame):
                            deduped[col_name] = obj.iloc[:, 0]
                        else:
                            deduped[col_name] = obj
                    else:
                        deduped[col_name] = collapsed
                df = pd.DataFrame(deduped)
                
                # Unit Conversion (Tushare -> Qlib Standard)
                # Tushare vol is in lots (100 shares), Qlib expects shares
                if 'volume' in df.columns:
                    df['volume'] = df['volume'] * 100
                
                # Tushare amount is in thousands (1000 RMB), Qlib expects RMB
                if 'amount' in df.columns:
                    df['amount'] = df['amount'] * 1000

                # Derive VWAP explicitly for Alpha158/Alpha360 families.
                if 'amount' in df.columns and 'volume' in df.columns:
                    amount_num = pd.to_numeric(df['amount'], errors='coerce')
                    volume_num = pd.to_numeric(df['volume'], errors='coerce')
                    volume_safe = volume_num.replace(0, np.nan)
                    df['vwap'] = amount_num / volume_safe
                
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
                    existing_roe = pd.to_numeric(df["roe"], errors="coerce") if "roe" in df.columns else None
                    if "roe_waa" in df.columns:
                        roe_waa = pd.to_numeric(df["roe_waa"], errors="coerce")
                        df["roe"] = roe_waa if existing_roe is None else existing_roe.combine_first(roe_waa)
                    elif existing_roe is not None:
                        df["roe"] = existing_roe
                    if {"net_income", "equity"}.issubset(df.columns):
                        ni = pd.to_numeric(df["net_income"], errors="coerce")
                        eq = pd.to_numeric(df["equity"], errors="coerce")
                        derived = ni / eq.replace(0, np.nan)
                        df["roe"] = derived if "roe" not in df.columns else pd.to_numeric(df["roe"], errors="coerce").combine_first(derived)
                if "grossprofit_margin" in target_fields and {"revenue", "oper_cost"}.issubset(df.columns):
                    revenue = pd.to_numeric(df["revenue"], errors="coerce")
                    oper_cost = pd.to_numeric(df["oper_cost"], errors="coerce")
                    derived = (revenue - oper_cost) / revenue.replace(0, np.nan)
                    if "grossprofit_margin" in df.columns:
                        df["grossprofit_margin"] = pd.to_numeric(df["grossprofit_margin"], errors="coerce").combine_first(derived)
                    else:
                        df["grossprofit_margin"] = derived
                if "debt_to_assets" in target_fields and {"total_assets", "equity"}.issubset(df.columns):
                    total_assets = pd.to_numeric(df["total_assets"], errors="coerce")
                    equity = pd.to_numeric(df["equity"], errors="coerce")
                    derived = (total_assets - equity) / total_assets.replace(0, np.nan)
                    if "debt_to_assets" in df.columns:
                        df["debt_to_assets"] = pd.to_numeric(df["debt_to_assets"], errors="coerce").combine_first(derived)
                    else:
                        df["debt_to_assets"] = derived
                if "current_ratio" in target_fields and {"total_cur_assets", "total_cur_liab"}.issubset(df.columns):
                    total_cur_assets = pd.to_numeric(df["total_cur_assets"], errors="coerce")
                    total_cur_liab = pd.to_numeric(df["total_cur_liab"], errors="coerce")
                    derived = total_cur_assets / total_cur_liab.replace(0, np.nan)
                    if "current_ratio" in df.columns:
                        df["current_ratio"] = pd.to_numeric(df["current_ratio"], errors="coerce").combine_first(derived)
                    else:
                        df["current_ratio"] = derived
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

                # Ensure date format with explicit YYYYMMDD handling.
                date_series = df['date']
                if not pd.api.types.is_datetime64_any_dtype(date_series):
                    date_as_str = date_series.astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                    ymd_mask = date_as_str.str.fullmatch(r'\d{8}')
                    converted = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')
                    if ymd_mask.any():
                        converted.loc[ymd_mask] = pd.to_datetime(date_as_str.loc[ymd_mask], format='%Y%m%d', errors='coerce')
                    if (~ymd_mask).any():
                        converted.loc[~ymd_mask] = pd.to_datetime(date_as_str.loc[~ymd_mask], errors='coerce')
                    df['date'] = converted
                
                # Incremental Filter
                if since_date:
                    # Include the latest qlib date itself so repaired raw rows on the same
                    # trading day can overwrite stale or partially converted values.
                    df = df[df['date'] >= since_date]
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
            if self._should_rebuild_corrupted_aligned_data(since_date):
                log.warning(
                    "Detected unusable qlib core fields on aligned latest date. "
                    "Running full rebuild to backfill repaired conversion logic."
                )
                self.convert_all()
                return
            # Touch qlib dir to update mtime so we don't check again immediately
            os.utime(self.qlib_dir, None)
            return

        log.info(f"Found {count} stocks with new data. Running dump_update...")
        self._run_dump_script(csv_dir, mode="dump_update")

    def convert_fix(self, since_date):
        """Repair latest-date feature files using dump_fix when raw rows changed in-place."""
        log.info(f"Starting same-date repair update (since {since_date})...")
        csv_dir, count = self._prepare_csvs(since_date)

        if count == 0:
            log.info("No repaired data found to update.")
            return

        log.info(f"Found {count} stocks with repaired data. Running dump_fix...")
        self._run_dump_script(csv_dir, mode="dump_fix")

    def _should_rebuild_corrupted_aligned_data(self, latest_date, missing_threshold: float = 0.2) -> bool:
        """
        When raw/qlib dates are aligned but recent qlib rows are unusable, trigger a full rebuild.
        This protects against previously-buggy conversions that produced empty/NaN core fields.
        """
        if latest_date is None:
            return False
        try:
            self.init_qlib()
            check_date = pd.Timestamp(latest_date).strftime("%Y-%m-%d")
            core_fields = ["$close", "$open", "$high", "$low", "$volume", "$factor"]
            probe = self.get_features("all", core_fields, start_time=check_date, end_time=check_date)

            if probe is None or probe.empty:
                return True

            for field in core_fields:
                if field not in probe.columns:
                    return True
                missing_ratio = float(probe[field].isna().mean())
                if missing_ratio > missing_threshold:
                    return True
        except Exception as err:
            log.warning(f"Skip aligned-corruption rebuild probe due to error: {err}")
            return False
        return False

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
            self._refresh_universe_instruments()
        except subprocess.CalledProcessError as e:
            log.error(f"Qlib dump failed: {e}")
        finally:
            if csv_dir.exists():
                shutil.rmtree(csv_dir)
            self._clean_artifacts()


    def _refresh_universe_instruments(self):
        """Refresh derived universe instrument files after qlib conversion."""
        script_path = cfg.project_root / "scripts" / "create_instrument_csi300.py"
        if not script_path.exists():
            log.warning(f"CSI300 instrument refresh script not found: {script_path}")
            return
        try:
            subprocess.run([sys.executable, str(script_path)], check=True)
            log.info("Refreshed csi300 instrument file after qlib conversion.")
        except subprocess.CalledProcessError as e:
            log.warning(f"Failed to refresh csi300 instrument file: {e}")

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
