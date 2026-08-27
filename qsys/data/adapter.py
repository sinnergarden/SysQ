import pandas as pd
import numpy as np
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from qsys.config import cfg
from qsys.utils.logger import log
from qsys.data.storage import StockDataStore
from qsys.data.collector import TushareCollector
from qsys.feature.availability import apply_margin_source_lag
from qsys.feature.builder import build_phase1_features
from qsys.feature.config import RESEARCH_FEATURE_FLAGS
from qsys.feature.registry import list_feature_groups
import qlib
from qlib.utils import exists_qlib_data
from qlib.data import D
from qlib.data.data import DatasetD
import sys
import os
import shutil
import subprocess
import tempfile


@dataclass
class InstrumentCoverageReport:
    calendar_latest: Optional[str]
    all_latest: Optional[str]
    universe: str
    universe_latest: Optional[str]

    @property
    def is_closed(self) -> bool:
        latest_values = [self.calendar_latest, self.all_latest, self.universe_latest]
        return all(latest_values) and len(set(latest_values)) == 1

    def blocker_message(self) -> str:
        return (
            "Qlib instrument coverage mismatch blocks planning: "
            f"calendar={self.calendar_latest}, all={self.all_latest}, "
            f"{self.universe}={self.universe_latest}"
        )


class QlibAdapter:
    _PERCENT_FINANCIAL_COLS = {
        "roe",
        "roe_waa",
        "roe_ttm",
        "grossprofit_margin",
        "debt_to_assets",
        "q_gr_yoy",
        "dt_netprofit_yoy",
        "profit_to_gr",
        "net_profit_margin",
    }
    _PERCENT_LIKE_THRESHOLD = 3.0
    # The longest semantic transform currently uses a 756-session shift.
    # 820 calendar days only contains roughly 585 A-share sessions and made
    # the 3-year fundamental deltas entirely NaN at inference time.  Four
    # years of calendar history leaves a conservative holiday/suspension
    # buffer while the builder still trims the returned frame to the caller's
    # requested dates.
    _SEMANTIC_LOOKBACK_CALENDAR_DAYS = 1461
    _PIT_INDUSTRY_START = "2018-03-13"

    def __init__(
        self,
        *,
        qlib_dir: str | Path | None = None,
        raw_dir: str | Path | None = None,
        shareholder_holder_path: str | Path | None = None,
        shareholder_top10_path: str | Path | None = None,
        income_sidecar_path: str | Path | None = None,
        income_sidecar_sha256: str = "",
        income_sidecar_manifest_path: str | Path | None = None,
        income_sidecar_manifest_sha256: str = "",
        income_source_mode: str = "legacy_unverified_global_v0",
        income_sidecar_required_history_start: str = "",
    ):
        self.qlib_dir = Path(qlib_dir).expanduser() if qlib_dir is not None else Path(str(cfg.get_path("qlib_bin")))
        self.raw_dir = Path(raw_dir).expanduser() if raw_dir is not None else Path(str(cfg.get_path("canonical_dir")))
        self.shareholder_holder_path = (
            Path(shareholder_holder_path).expanduser()
            if shareholder_holder_path is not None
            else None
        )
        self.shareholder_top10_path = (
            Path(shareholder_top10_path).expanduser()
            if shareholder_top10_path is not None
            else None
        )
        self.income_sidecar_path = (
            Path(income_sidecar_path).expanduser()
            if income_sidecar_path is not None
            else None
        )
        self.income_sidecar_sha256 = str(income_sidecar_sha256 or "")
        self.income_sidecar_manifest_path = (
            Path(income_sidecar_manifest_path).expanduser()
            if income_sidecar_manifest_path is not None
            else None
        )
        self.income_sidecar_manifest_sha256 = str(
            income_sidecar_manifest_sha256 or ""
        )
        self.income_source_mode = str(income_source_mode or "")
        self.income_sidecar_required_history_start = str(
            income_sidecar_required_history_start or ""
        )
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
            val = df.iloc[-1, 0]
            return pd.Timestamp(str(val))
        except Exception:
            return None

    def get_instrument_latest_end_date(self, instrument: str) -> Optional[pd.Timestamp]:
        inst_path = self.qlib_dir / "instruments" / f"{instrument}.txt"
        if not inst_path.exists():
            return None
        try:
            df = pd.read_csv(inst_path, sep="\t", header=None, names=["symbol", "start_date", "end_date"])
            if df.empty or "end_date" not in df.columns:
                return None
            end_dates = pd.to_datetime(df["end_date"], errors="coerce")
            if end_dates.isna().all():
                return None
            return end_dates.max()
        except Exception:
            return None

    def get_instrument_coverage_report(self, universe: str = "csi300") -> InstrumentCoverageReport:
        calendar_latest = self.get_last_qlib_date()
        all_latest = self.get_instrument_latest_end_date("all")
        universe_latest = self.get_instrument_latest_end_date(universe)
        return InstrumentCoverageReport(
            calendar_latest=calendar_latest.strftime("%Y-%m-%d") if calendar_latest is not None else None,
            all_latest=all_latest.strftime("%Y-%m-%d") if all_latest is not None else None,
            universe=universe,
            universe_latest=universe_latest.strftime("%Y-%m-%d") if universe_latest is not None else None,
        )

    def ensure_instrument_coverage(self, universe: str = "csi300", *, refresh_on_mismatch: bool = True) -> InstrumentCoverageReport:
        report = self.get_instrument_coverage_report(universe=universe)
        if report.is_closed or not refresh_on_mismatch:
            return report

        log.warning(report.blocker_message())
        self._refresh_universe_instruments(universe=universe)
        return self.get_instrument_coverage_report(universe=universe)

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

        coverage_report = self.ensure_instrument_coverage("csi300", refresh_on_mismatch=True)

        # Verify the update
        qlib_latest = self.get_last_qlib_date()
        if qlib_latest:
            log.info(f"Qlib bin latest date after refresh: {qlib_latest.date()}")
        else:
            log.warning("Failed to get qlib latest date after refresh")

        if not coverage_report.is_closed:
            raise RuntimeError(coverage_report.blocker_message())

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

    def touch_qlib_mtime(self):
        """Touch the qlib roots after any successful write so freshness checks stay accurate."""
        for path in [self.qlib_dir, self.qlib_dir / "features", self.qlib_dir / "instruments"]:
            if path.exists():
                os.utime(path, None)

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
            # Resolve any qlib instrument registry file (csi300/csi500/csi800,
            # plus PIT universe registries such as csi800_pit_union).  The
            # canonical index names keep their legacy fallback to "all" when
            # the registry file is missing; a PIT registry is only resolved
            # when its instruments/<name>.txt actually exists.
            if low in ("all", "csi300", "csi500", "csi800") or (
                self.qlib_dir / "instruments" / f"{low}.txt"
            ).exists():
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

    @staticmethod
    def _normalize_field_list(fields):
        if fields is None:
            return []
        if isinstance(fields, tuple) and len(fields) == 2:
            return list(fields[0])
        if isinstance(fields, dict):
            inner = fields.get("feature") or fields.get("fields") or []
            if isinstance(inner, tuple) and len(inner) == 2:
                return list(inner[0])
            return list(inner)
        return list(fields)

    @staticmethod
    def _split_feature_fields(fields):
        requested = []
        native = []
        derived = []
        derived_candidates = {
            feature
            for group in list_feature_groups().values()
            for feature in group.get("features", [])
        }
        derived_candidates.update(["inventory_yoy", "ar_yoy"])

        for field in fields:
            if not isinstance(field, str):
                continue
            name = field.strip()
            if not name:
                continue
            requested.append(name)
            if name in derived_candidates:
                derived.append(name)
            else:
                native.append(name)
        return requested, native, derived

    @staticmethod
    def _semantic_support_fields():
        return [
            "$open",
            "$high",
            "$low",
            "$close",
            "$volume",
            "$amount",
            "$turnover_rate",
            "$paused",
            "$high_limit",
            "$low_limit",
            "$pe",
            "$pb",
            "$ps_ttm",
            "$total_mv",
            "$circ_mv",
            "$grossprofit_margin",
            "$debt_to_assets",
            "$current_ratio",
            "$net_income",
            "$revenue",
            "$total_assets",
            "$equity",
            "$op_cashflow",
            "$inventory",
            "$accounts_receiv",
            "$roe",
            "$margin_balance",
            "$margin_buy_amount",
            "$margin_repay_amount",
            "$margin_total_balance",
            "$lend_volume",
            "$lend_sell_volume",
            "$lend_repay_volume",
            "$industry",
        ]

    @staticmethod
    def _semantic_lookback_start(start_time, end_time):
        anchor = pd.Timestamp(start_time or end_time)
        return (
            anchor - pd.Timedelta(days=QlibAdapter._SEMANTIC_LOOKBACK_CALENDAR_DAYS)
        ).strftime("%Y-%m-%d")

    @staticmethod
    def _to_semantic_builder_frame(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.copy()
        if isinstance(out.index, pd.MultiIndex):
            out = out.reset_index()
        rename_map = {
            "instrument": "ts_code",
            "datetime": "trade_date",
            "$open": "open",
            "$high": "high",
            "$low": "low",
            "$close": "close",
            "$volume": "volume",
            "$amount": "amount",
            "$turnover_rate": "turnover_rate",
            "$paused": "paused",
            "$high_limit": "high_limit",
            "$low_limit": "low_limit",
            "$pe": "pe",
            "$pb": "pb",
            "$ps_ttm": "ps_ttm",
            "$total_mv": "total_mv",
            "$circ_mv": "circ_mv",
            "$grossprofit_margin": "grossprofit_margin",
            "$debt_to_assets": "debt_to_assets",
            "$current_ratio": "current_ratio",
            "$net_income": "net_income",
            "$revenue": "revenue",
            "$total_assets": "total_assets",
            "$equity": "equity",
            "$op_cashflow": "op_cashflow",
            "$inventory": "inventory",
            "$accounts_receiv": "accounts_receiv",
            "$roe": "roe",
            "$margin_balance": "margin_balance",
            "$margin_buy_amount": "margin_buy_amount",
            "$margin_repay_amount": "margin_repay_amount",
            "$margin_total_balance": "margin_total_balance",
            "$lend_volume": "lend_volume",
            "$lend_sell_volume": "lend_sell_volume",
            "$lend_repay_volume": "lend_repay_volume",
            "$industry": "industry",
        }
        out = out.rename(columns=rename_map)
        if "trade_date" in out.columns:
            out["trade_date"] = pd.to_datetime(out["trade_date"])
        return out

    @staticmethod
    def _semantic_feature_flags(derived_fields):
        flags = {key: False for key in RESEARCH_FEATURE_FLAGS}
        groups = list_feature_groups()
        requested = set(derived_fields)
        for group in groups.values():
            if requested.intersection(group.get("features", [])):
                flags[group["enabled_by"]] = True
        if requested.intersection({"stock_minus_industry_ret_3d", "stock_minus_industry_ret_5d"}):
            flags["enable_industry_context_features"] = True
            flags["enable_relative_strength_features"] = True
        if requested.intersection({"inventory_yoy", "ar_yoy"}):
            flags["enable_fundamental_context_features"] = True
        if any(f.startswith("industry_") or f.startswith("stock_minus_industry_") for f in requested):
            flags["enable_industry_momentum_features"] = True
            flags["enable_industry_context_features"] = True
        return flags

    def _build_semantic_features(
        self,
        base_df: pd.DataFrame,
        derived_fields,
        start_time=None,
        end_time=None,
        *,
        margin_lag_sessions: int = 0,
    ) -> pd.DataFrame:
        semantic_input = self._to_semantic_builder_frame(base_df)
        if semantic_input.empty:
            return pd.DataFrame()
        open_dates = None
        if margin_lag_sessions:
            calendar_path = self.qlib_dir / "calendars" / "day.txt"
            if calendar_path.is_file():
                calendar = pd.read_csv(calendar_path, header=None)
                open_dates = calendar.iloc[:, 0].dropna().astype(str).tolist()
        semantic_input = apply_margin_source_lag(
            semantic_input,
            lag_sessions=margin_lag_sessions,
            open_dates=open_dates,
        )

        try:
            flags = self._semantic_feature_flags(derived_fields)
            if self.shareholder_holder_path is not None:
                flags["shareholder_holder_path"] = str(
                    self.shareholder_holder_path
                )
            if self.shareholder_top10_path is not None:
                flags["shareholder_top10_path"] = str(
                    self.shareholder_top10_path
                )
            flags["income_source_mode"] = self.income_source_mode
            if self.income_sidecar_path is not None:
                flags.update({
                    "income_sidecar_path": str(self.income_sidecar_path),
                    "income_sidecar_sha256": self.income_sidecar_sha256,
                    "income_sidecar_manifest_path": str(
                        self.income_sidecar_manifest_path or ""
                    ),
                    "income_sidecar_manifest_sha256": (
                        self.income_sidecar_manifest_sha256
                    ),
                    "income_sidecar_required_start": (
                        str(start_time)[:10] if start_time is not None else None
                    ),
                    "income_sidecar_required_end": (
                        str(end_time)[:10] if end_time is not None else None
                    ),
                    "income_sidecar_required_history_start": (
                        self.income_sidecar_required_history_start
                    ),
                })
            feat = build_phase1_features(semantic_input, flags=flags)
        except KeyError as exc:
            log.warning(f"Semantic feature inputs missing; fallback to NaN for unsupported columns: {exc}")
            feat = semantic_input.copy()
        for col in derived_fields:
            if col not in feat.columns:
                feat[col] = np.nan

        keep_cols = ["trade_date", "ts_code"] + list(derived_fields)
        feat = feat[keep_cols].copy()
        if start_time is not None:
            feat = feat[feat["trade_date"] >= pd.Timestamp(start_time)]
        if end_time is not None:
            feat = feat[feat["trade_date"] <= pd.Timestamp(end_time)]
        if feat.empty:
            return pd.DataFrame(columns=derived_fields)

        feat = feat.set_index(["trade_date", "ts_code"]).sort_index()
        feat.index = feat.index.rename(["datetime", "instrument"])
        return feat[list(derived_fields)]

    def get_features(
        self,
        instruments,
        fields,
        start_time=None,
        end_time=None,
        freq="day",
        inst_processors=None,
        *,
        margin_lag_sessions: int = 0,
    ):
        inst = self.normalize_instruments(instruments)
        field_list = self._normalize_field_list(fields)
        requested_fields, native_fields, derived_fields = self._split_feature_fields(field_list)
        if not derived_fields:
            return DatasetD.dataset(
                inst,
                field_list,
                start_time=start_time,
                end_time=end_time,
                freq=freq,
                inst_processors=inst_processors or []
            )

        support_fields = [f for f in self._semantic_support_fields() if f not in native_fields]
        native_request = native_fields + support_fields
        base_start_time = self._semantic_lookback_start(start_time, end_time)
        native_df = DatasetD.dataset(
            inst,
            native_request,
            start_time=base_start_time,
            end_time=end_time,
            freq=freq,
            inst_processors=inst_processors or []
        )
        semantic_df = self._build_semantic_features(
            native_df,
            derived_fields,
            start_time=start_time,
            end_time=end_time,
            margin_lag_sessions=margin_lag_sessions,
        )

        native_current = native_df
        if start_time is not None or end_time is not None:
            if native_current is not None and not native_current.empty and isinstance(native_current.index, pd.MultiIndex):
                dt_index = pd.to_datetime(native_current.index.get_level_values("datetime"))
                mask = pd.Series(True, index=native_current.index)
                if start_time is not None:
                    mask &= dt_index >= pd.Timestamp(start_time)
                if end_time is not None:
                    mask &= dt_index <= pd.Timestamp(end_time)
                native_current = native_current[mask.to_numpy()]

        if native_current is None or native_current.empty:
            combined = semantic_df.copy()
        else:
            combined = native_current.copy()
            if semantic_df is not None and not semantic_df.empty:
                combined = combined.join(semantic_df, how="left")

        for field in requested_fields:
            if field not in combined.columns:
                combined[field] = np.nan
        return combined[requested_fields]

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

    def _prepare_csvs(self, since_date=None, *, until_date=None, selected_symbols=None, output_dir=None, require_pit_industry=False):
        """
        Prepare CSVs from Feather files.
        If ``since_date`` is provided, only include rows on/after that date.
        If ``until_date`` is provided, only include rows on/before that date.
        Returns path to ``csv_dir`` and count of files generated.
        """
        csv_dir = Path(output_dir) if output_dir is not None else self.qlib_dir.parent / "qlib_csv_tmp"
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

        files = list(self.raw_dir.glob("*.feather"))
        if selected_symbols:
            selected_normalized = {str(symbol).strip().upper() for symbol in selected_symbols if str(symbol).strip()}
            files = [f for f in files if f.stem.strip().upper() in selected_normalized]
        if not files:
            log.warning("No feather files found.")
            return csv_dir, 0

        store = StockDataStore()
        stock_df = store.get_stock_list()
        code_to_industry = {}
        if stock_df is not None and not stock_df.empty and {"ts_code", "industry"}.issubset(stock_df.columns):
            code_to_industry = stock_df.set_index("ts_code")["industry"].to_dict()
        historical_names: set[str] = set()
        for source_file in files:
            schema_frame = pd.read_feather(source_file)
            if "industry" not in schema_frame.columns:
                continue
            values = schema_frame["industry"]
            historical_names.update(
                value for value in values.dropna().astype(str).str.strip().tolist()
                if value and value != "nan"
            )
        industry_map = self._load_industry_map(stock_df, historical_names=historical_names)

        converted_count = 0
        for f in files:
            try:
                df = pd.read_feather(f)
                if df.empty:
                    continue
                pit_industry = (
                    df["industry"].astype("string").str.strip().copy()
                    if "industry" in df.columns else pd.Series(pd.NA, index=df.index, dtype="string")
                )
                
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
                
                for col in self._PERCENT_FINANCIAL_COLS:
                    if col not in df.columns:
                        continue
                    values = pd.to_numeric(df[col], errors="coerce")
                    mask = values.abs() > self._PERCENT_LIKE_THRESHOLD
                    if mask.any():
                        df.loc[mask, col] = values.loc[mask] / 100.0

                # Unit Conversion (Tushare -> Qlib Standard)
                # Tushare vol is in lots (100 shares), Qlib expects shares
                if 'volume' in df.columns:
                    df['volume'] = df['volume'] * 100
                
                # Tushare amount already converted from 千元 to 元 in collector;
                # adapter passes through without additional scaling.
                if 'amount' in df.columns:
                    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')

                # Derive VWAP explicitly for phase123/Alpha360 families.
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
                    names = pit_industry.reindex(df.index)
                    missing = names.isna() | names.eq("")
                    required_mask = pd.Series(True, index=df.index)
                    pit_dates = None
                    if "date" in df.columns:
                        pit_date_text = df["date"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
                        pit_dates = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
                        ymd = pit_date_text.str.fullmatch(r"\d{8}")
                        pit_dates.loc[ymd] = pd.to_datetime(
                            pit_date_text.loc[ymd], format="%Y%m%d", errors="coerce"
                        )
                        pit_dates.loc[~ymd] = pd.to_datetime(pit_date_text.loc[~ymd], errors="coerce")
                        required_mask = pit_dates >= pd.Timestamp(self._PIT_INDUSTRY_START)
                    elif require_pit_industry:
                        raise RuntimeError(f"PIT industry date identity missing for {symbol}")
                    if until_date is not None:
                        if pit_dates is None:
                            raise RuntimeError(f"PIT industry date identity missing for {symbol}")
                        required_mask &= pit_dates <= pd.Timestamp(until_date)
                    if require_pit_industry and (missing & required_mask).any():
                        raise RuntimeError(f"PIT industry coverage missing for {symbol}")
                    if missing.any():
                        names = names.where(~missing, code_to_industry.get(symbol))
                    unknown = sorted(set(names.dropna().tolist()) - set(industry_map))
                    if unknown:
                        raise RuntimeError(f"industry mapping missing values for {symbol}: {unknown[:5]}")
                    df["industry"] = names.map(industry_map).fillna(0).astype(int)

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
                
                # Bound CSV export to the requested date window.
                if since_date is not None:
                    # Include the latest qlib date itself so repaired raw rows on the same
                    # trading day can overwrite stale or partially converted values.
                    df = df[df['date'] >= since_date]
                if until_date is not None:
                    df = df[df['date'] <= until_date]
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
                if require_pit_industry:
                    raise
                if "adj_factor" not in str(e): # Ignore expected adj_factor missing
                    log.warning(f"Failed to convert {f.name}: {e}")

        return csv_dir, converted_count

    def _load_industry_map(self, stock_df: pd.DataFrame, *, historical_names: set[str] | None = None):
        meta_dir = cfg.get_path("meta")
        if meta_dir is None:
            return {}
        map_path = meta_dir / "industry_map.json"
        if map_path.exists():
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    industry_map = json.load(f)
            except Exception as exc:
                raise RuntimeError(f"industry mapping is unreadable: {map_path}") from exc
            if (
                not isinstance(industry_map, dict)
                or any(not isinstance(name, str) or not isinstance(value, int) or value <= 0 for name, value in industry_map.items())
                or len(set(industry_map.values())) != len(industry_map)
            ):
                raise RuntimeError(f"industry mapping is invalid: {map_path}")
        else:
            industry_map = {}
        industry_names = set(historical_names or set())
        if stock_df is not None and not stock_df.empty and "industry" in stock_df.columns:
            industry_names.update(
                value for value in stock_df["industry"].dropna().astype(str).str.strip().tolist()
                if value and value != "nan"
            )
        for name in sorted(industry_names - set(industry_map)):
            industry_map[name] = max([int(value) for value in industry_map.values()] or [0]) + 1
        map_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=map_path.parent,
            prefix=f".{map_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(industry_map, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, map_path)
        return industry_map

    def convert_incremental(self, since_date, *, max_workers: int | None = None):
        """Incremental update using dump_update, with read-back verification."""
        log.info(f"Starting incremental update (since {since_date})...")
        csv_dir, count = self._prepare_csvs(since_date)

        if count == 0:
            log.info("No new data found to update.")
            os.utime(self.qlib_dir, None)
            return

        log.info(f"Found {count} stocks with new data. Running dump_update...")
        self._run_dump_script(csv_dir, mode="dump_update", max_workers=max_workers)

        # Lightweight calendar verification: confirm dump_update advanced
        # the qlib calendar to include the target date.  This replaces the
        # more expensive (and never-triggering) read-back price comparison
        # that was removed.
        cal_path = self.qlib_dir / "calendars" / "day.txt"
        if cal_path.exists():
            try:
                all_dates = [l.strip() for l in cal_path.read_text().splitlines() if l.strip()]
                expected = str(since_date) if not hasattr(since_date, "strftime") else since_date.strftime("%Y-%m-%d")
                expected_short = expected[:10]
                if all_dates and all_dates[-1] < expected_short:
                    log.warning(
                        f"dump_update did not extend calendar to {expected_short}; "
                        f"latest calendar date is {all_dates[-1]}. "
                        "Data may be stale on expected latest date."
                    )
            except Exception as exc:
                log.warning(f"Calendar verification failed: {exc}")

    def convert_fix(self, since_date, *, max_workers: int | None = None):
        """Repair same-date changes without collapsing symbol history to a one-day slice."""
        log.info(f"Starting same-date repair update (since {since_date})...")
        if since_date is None:
            affected_symbols = []
        else:
            threshold = pd.Timestamp(since_date)
            affected_symbols = []
            for feather_path in self.raw_dir.glob("*.feather"):
                try:
                    frame = pd.read_feather(feather_path, columns=["trade_date"])
                except Exception as err:
                    log.warning(f"Failed to inspect {feather_path.name} for same-date repair: {err}")
                    continue
                if frame.empty or "trade_date" not in frame.columns:
                    continue
                dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
                if not dates.empty and dates.max() >= threshold:
                    affected_symbols.append(feather_path.stem)
            affected_symbols = sorted(set(affected_symbols))
        if not affected_symbols:
            log.info("No repaired symbols found to update.")
            self.touch_qlib_mtime()
            return

        # dump_fix rewrites the full per-symbol bin file, so export complete history
        # for the affected symbols instead of only the latest-date slice.
        csv_dir, count = self._prepare_csvs(selected_symbols=affected_symbols)

        if count == 0:
            log.info("No repaired data found to update.")
            self.touch_qlib_mtime()
            return

        log.info(f"Found {count} stocks with repaired data. Running dump_fix...")
        self._run_dump_script(csv_dir, mode="dump_fix", max_workers=max_workers)

    def convert_fix_symbols(
        self,
        symbols: list[str],
        *,
        refresh_universes: list[str] | None = None,
        max_workers: int | None = None,
        require_pit_industry: bool = False,
    ) -> dict:
        """Replace per-symbol qlib bins from canonical data using ``dump_fix``.

        Unlike ``convert_fix`` which scans canonical dir by date threshold,
        this operates on an explicit symbol list and is suitable for
        targeted per-symbol repair from qlib_sync or backfill callers.

        Returns a dict with ``status`` (``"success"`` | ``"skipped"``) and
        summary fields for downstream audit/artifact recording.
        """
        if not symbols:
            log.info("convert_fix_symbols: empty symbol list, no-op.")
            return {"status": "skipped", "reason": "empty_symbol_list"}

        csv_dir, count = self._prepare_csvs(
            selected_symbols=symbols, require_pit_industry=require_pit_industry
        )
        if count == 0:
            log.info("convert_fix_symbols: no CSV generated from selected symbols.")
            return {"status": "skipped", "reason": "no_csv_generated"}

        log.info(f"convert_fix_symbols: {count} symbols, running dump_fix...")
        self._run_dump_script(
            csv_dir,
            mode="dump_fix",
            refresh_universes=refresh_universes,
            max_workers=max_workers,
        )
        return {"status": "success", "symbols_count": len(symbols), "csv_count": count}

    def convert_all(self, *, output_qlib_dir=None, selected_symbols=None, until_date=None, csv_output_dir=None, refresh_universes=None):
        """Full update using dump_all"""
        log.info("Starting full Qlib data conversion...")
        original_qlib_dir = self.qlib_dir
        if output_qlib_dir is not None:
            self.qlib_dir = Path(output_qlib_dir).expanduser()
        csv_dir, count = self._prepare_csvs(since_date=None, until_date=until_date, selected_symbols=selected_symbols, output_dir=csv_output_dir)
        
        if count == 0:
            log.error("No CSV files generated for full dump.")
            self.qlib_dir = original_qlib_dir
            return

        log.info(f"Generated {count} CSV files. Running dump_all...")
        try:
            self._run_dump_script(csv_dir, mode="dump_all", refresh_universes=refresh_universes)
        finally:
            self.qlib_dir = original_qlib_dir

    def _run_dump_script(
        self,
        csv_dir,
        mode="dump_all",
        *,
        refresh_universes=None,
        cleanup_csv_dir=True,
        max_workers: int | None = None,
    ):
        """Helper to run the dump_bin.py script"""
        # Use cfg.project_root to find the script reliably
        dump_script = cfg.project_root / "scripts" / "dev" / "dump_bin.py"
        
        if not dump_script.exists():
             # Fallback: check if we are in development mode and script is in relative path
             # e.g. if running from project root
             fallback = Path("scripts/dev/dump_bin.py").resolve()
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
        if max_workers is not None:
            cmd.extend(["--max_workers", str(max_workers)])
            
        # log.info(f"Running command: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
            log.info(f"Qlib {mode} completed successfully.")
            if refresh_universes is None:
                refresh_universes = ["csi300", "csi800"]
            for universe in refresh_universes:
                self._refresh_universe_instruments(universe=universe)
            self.touch_qlib_mtime()
        except subprocess.CalledProcessError as e:
            log.error(f"Qlib dump failed: {e}")
            raise
        finally:
            if cleanup_csv_dir and csv_dir.exists():
                shutil.rmtree(csv_dir)
            self._clean_artifacts()


    def _refresh_universe_instruments(self, universe: str = "csi300"):
        """Refresh derived universe instrument files after qlib conversion.

        Supports csi300 and csi800. Fetches index constituents via Tushare,
        matches against all.txt, and writes the instrument file.

        Forces end_date to at least the latest calendar date in qlib,
        because the qlib dump_update process does not reliably bump
        end_dates for all symbols — without this force, the readiness
        check will under-count active instruments on new trading days.
        """
        supported = {"csi300", "csi800"}
        if universe not in supported:
            log.warning(f"Universe refresh is not implemented for {universe}")
            return

        try:
            codes = TushareCollector().get_universe(universe)
        except Exception as e:
            log.warning(f"Failed to fetch {universe} components: {e}")
            return

        if not codes:
            log.warning(f"Empty {universe} components, skipping instrument refresh.")
            return

        log.info(f"Fetched {len(codes)} {universe} components.")

        qlib_dir = cfg.get_path("qlib_bin")
        all_txt_path = qlib_dir / "instruments" / "all.txt"
        if not all_txt_path.exists():
            log.warning(f"all.txt not found at {all_txt_path}")
            return

        # Resolve the latest calendar date to use as the force-end_date
        cal_path = qlib_dir / "calendars" / "day.txt"
        calendar_latest = None
        if cal_path.exists():
            try:
                all_dates = [l.strip() for l in cal_path.read_text().splitlines() if l.strip()]
                if all_dates:
                    calendar_latest = all_dates[-1]
            except Exception:
                pass

        df_all = pd.read_csv(all_txt_path, sep="\t", names=["symbol", "start_date", "end_date"])
        code_set = set(codes)
        df_universe = df_all[df_all["symbol"].isin(code_set)]

        log.info(f"Matched {len(df_universe)} stocks in all.txt for {universe}")

        if df_universe.empty:
            log.warning(f"No matches found for {universe}! Check symbol format.")
            return

        # Force end_date to at least the latest calendar date.
        # dump_update only bumps end_dates for symbols it touched,
        # leaving the rest pointing to the previous day.  Since we
        # know the sync completed, all constituents are active on
        # the latest trading day.
        if calendar_latest:
            before = int((df_universe["end_date"] >= calendar_latest).sum())
            df_universe["end_date"] = df_universe["end_date"].clip(lower=calendar_latest)
            after = int((df_universe["end_date"] >= calendar_latest).sum())
            log.info(f"  end_date force: {before} → {after} symbols >= {calendar_latest}")

        out_path = qlib_dir / "instruments" / f"{universe}.txt"
        df_universe.to_csv(out_path, sep="\t", header=False, index=False)
        log.info(f"Written {universe} instrument file to {out_path}")

    def _clean_artifacts(self):
        """Clean up mlruns and Users directories often generated by Qlib/MLflow"""
        project_root = cfg.project_root
        
        dirs_to_remove = [
            project_root / "Users",
            project_root / "notebooks" / "mlruns",
            project_root / "notebooks" / "Users"
        ]

        # Keep the project mlflow root in place; Qlib may create the recorder lazily after init.
        (project_root / "mlruns").mkdir(parents=True, exist_ok=True)

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
