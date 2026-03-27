import tushare as ts
import pandas as pd
import time
import json
from datetime import datetime, timedelta
from typing import Optional
from qsys.config import cfg
from qsys.utils.logger import log
from qsys.data.storage import StockDataStore
import numpy as np

class TushareCollector:
    def __init__(self):
        token = cfg.get("tushare_token")
        if not token:
            raise ValueError("Tushare token not found in settings")
        self.pro = ts.pro_api(token)
        self.store = StockDataStore()
        self.max_retries = 3
        self.batch_size = 50

        self._tushare_cfg = cfg.get_tushare_feature_config()
        collector_cfg = self._tushare_cfg.get("collector", {})
        self._collector_cfg = collector_cfg
        self._collector_interfaces = collector_cfg.get("interfaces", {})
        self._financial_interfaces = collector_cfg.get(
            "financial_interfaces",
            ["income", "balancesheet", "cashflow", "fina_indicator"],
        )
        self.financial_cols = collector_cfg.get(
            "financial_cols",
            [
                "net_income",
                "revenue",
                "oper_cost",
                "total_assets",
                "equity",
                "total_cur_assets",
                "total_cur_liab",
                "roe",
                "op_cashflow",
                "q_dt_profit",
                "q_gr_yoy",
                "roe_ttm",
                "grossprofit_margin",
                "debt_to_assets",
                "current_ratio",
            ],
        )
        self.moneyflow_fields = collector_cfg.get(
            "moneyflow_fields",
            [
                "buy_sm_amount",
                "buy_md_amount",
                "buy_lg_amount",
                "buy_elg_amount",
                "sell_sm_amount",
                "sell_md_amount",
                "sell_lg_amount",
                "sell_elg_amount",
                "net_mf_amount",
            ],
        )
        self._moneyflow_derived = collector_cfg.get("derived_fields", {}).get(
            "moneyflow",
            ["big_inflow", "net_inflow"],
        )
        self._expected_extra_cols = collector_cfg.get(
            "expected_extra_cols",
            ["paused"],
        )
        self._numeric_extra_cols = collector_cfg.get(
            "numeric_extra_cols",
            ["paused"],
        )
        self._non_numeric_cols = collector_cfg.get(
            "non_numeric_cols",
            ["trade_status"],
        )
        # Margin financing (两融) - first batch
        self.margin_cols = collector_cfg.get(
            "margin_cols",
            [
                "margin_balance", "margin_buy_amount", "margin_repay_amount",
                "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
            ],
        )
        self._margin_interfaces = collector_cfg.get("interfaces", {}).get("margin", {})
        
        self._non_negative_cols = collector_cfg.get(
            "non_negative_cols",
            [
                "open", "high", "low", "close", "vol", "amount",
                "turnover_rate", "total_share", "float_share",
                "free_share", "total_mv", "circ_mv", "adj_factor", "up_limit", "down_limit",
                # Margin financing (两融) - first batch
                "margin_balance", "margin_buy_amount", "margin_repay_amount",
                "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
            ],
        )

    def _normalize_date(self, date_str):
        if date_str is None:
            return None
        date_str = str(date_str)
        if "-" in date_str:
            return date_str.replace("-", "")
        return date_str

    def _get_interface_config(self, name):
        return self._collector_interfaces.get(name, {})

    def _get_interface_fields(self, name):
        cfg_item = self._get_interface_config(name)
        fields = cfg_item.get("fields", [])
        if isinstance(fields, list):
            return ",".join(fields)
        return fields

    def _get_interface_field_list(self, name):
        cfg_item = self._get_interface_config(name)
        fields = cfg_item.get("fields", [])
        if isinstance(fields, str):
            return [f.strip() for f in fields.split(",") if f.strip()]
        return list(fields)

    def _dedupe_list(self, items):
        seen = set()
        result = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    def _get_interface_feature_fields(self, name):
        fields = self._get_interface_field_list(name)
        return [f for f in fields if f not in {"ts_code", "trade_date", "ann_date", "end_date"}]

    def _get_all_interface_fields(self):
        fields = []
        seen = set()
        for name in self._collector_interfaces:
            if name in self._financial_interfaces:
                continue
            for f in self._get_interface_field_list(name):
                if f in {"ts_code", "trade_date", "ann_date", "end_date"}:
                    continue
                if f not in seen:
                    seen.add(f)
                    fields.append(f)
        return fields

    def _get_expected_columns(self):
        cols = self._get_all_interface_fields()
        cols += self._expected_extra_cols + self._moneyflow_derived + self.financial_cols
        return self._dedupe_list(cols)

    def _get_numeric_columns(self):
        expected = self._get_expected_columns()
        non_numeric = set(self._non_numeric_cols or [])
        numeric = [c for c in expected if c not in non_numeric]
        for col in self._numeric_extra_cols:
            if col not in numeric:
                numeric.append(col)
        return numeric

    def _get_interface_api(self, name):
        cfg_item = self._get_interface_config(name)
        api_name = cfg_item.get("interface", name)
        return getattr(self.pro, api_name)

    def _get_interface_rename(self, name):
        cfg_item = self._get_interface_config(name)
        rename = cfg_item.get("rename", {})
        return rename if isinstance(rename, dict) else {}

    def _fetch_by_date_range(self, interface_name, ts_codes, start_date, end_date):
        api = self._get_interface_api(interface_name)
        fields = self._get_interface_fields(interface_name)
        
        # Strategy (Tushare Best Practices):
        # 1. Full Market (ts_codes is None): Use Date Loop. 
        #    Tushare recommends looping by trade_date for getting all history.
        # 2. Subset/Universe (ts_codes provided):
        #    a. daily/adj_factor/moneyflow: Use Batch Range (ts_code=list, start/end).
        #       Efficiency: 50 stocks * 1 call vs 50 calls (if date loop).
        #    b. daily_basic/stk_limit: Use Stock Loop (ts_code=single, start/end).
        #       These interfaces limit rows strictly or don't support multi-code range well.
        
        if not ts_codes:
            # Full market fetch -> Loop by Date
            return self._fetch_by_date_loop(api, fields, start_date, end_date)
            
        if interface_name in ["daily_basic", "stk_limit", "margin"]:
            # margin interface doesn't support ts_code list well, needs stock loop like daily_basic/stk_limit
            if isinstance(ts_codes, str):
                code_list = ts_codes.split(",")
            else:
                code_list = ts_codes
            # Use Stock Loop (Range Fetch) - MUCH faster for subset of stocks
            return self._fetch_by_stock_loop(api, fields, start_date, end_date, code_list)

        # Default: Daily/Adj/Moneyflow with ts_codes -> Use Batch Range
        code_str = ",".join(ts_codes) if ts_codes else None
        df = self._fetch_with_retry(
            api,
            ts_code=code_str,
            start_date=start_date,
            end_date=end_date,
            fields=fields,
        )
        
        # Fallback
        if df is None or df.empty:
            return pd.DataFrame()
            
        if ts_codes and "ts_code" in df.columns:
            df = df[df["ts_code"].isin(ts_codes)]
        return df

    def _fetch_by_stock_loop(self, api, fields, start_date, end_date, code_list):
        """
        Iterate over stocks and fetch range for each.
        """
        dfs = []
        # Tushare limit is ~4000-5000 rows.
        # If range is large (e.g. > 10 years), we might need to chunk time.
        # But for typical updates (incremental), range is small.
        # For full history (10+ years), let's chunk by 5 years to be safe.
        
        start_dt = datetime.strptime(str(start_date), "%Y%m%d")
        end_dt = datetime.strptime(str(end_date), "%Y%m%d")
        
        chunks = []
        curr = start_dt
        while curr <= end_dt:
            # 5 years chunk
            chunk_end = datetime(min(curr.year + 4, end_dt.year), 12, 31)
            if chunk_end > end_dt:
                chunk_end = end_dt
            chunks.append((curr.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
            curr = chunk_end + timedelta(days=1)
            
        for i, code in enumerate(code_list):
            if i > 0 and i % 5 == 0:
                time.sleep(0.1)
                
            for c_start, c_end in chunks:
                df = self._fetch_with_retry(
                    api,
                    ts_code=code,
                    start_date=c_start,
                    end_date=c_end,
                    fields=fields,
                )
                if df is not None and not df.empty:
                    dfs.append(df)
                    
        if not dfs:
            return pd.DataFrame()
            
        # Filter empty or all-NA frames
        dfs = [d for d in dfs if not d.empty and not d.isna().all().all()]
        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, ignore_index=True)

    def _fetch_by_date_loop(self, api, fields, start_date, end_date, ts_codes=None):
        # Get calendar from Tushare directly to be safe
        try:
            cal = self.pro.trade_cal(start_date=start_date, end_date=end_date, is_open='1')
        except Exception as e:
            log.error(f"Failed to fetch calendar: {e}")
            return pd.DataFrame()

        if cal is None or cal.empty:
            return pd.DataFrame()
        
        dates = cal['cal_date'].tolist()
        dfs = []
        
        for date in dates:
            # Fetch snapshot for the day
            df = self._fetch_with_retry(api, trade_date=date, fields=fields)
            if df is not None and not df.empty:
                if ts_codes and "ts_code" in df.columns:
                    df = df[df["ts_code"].isin(ts_codes)]
                dfs.append(df)
                
        if not dfs:
            return pd.DataFrame()
            
        # Filter empty or all-NA frames
        dfs = [d for d in dfs if not d.empty and not d.isna().all().all()]
        if not dfs:
            return pd.DataFrame()
            
        return pd.concat(dfs, ignore_index=True)

    def _prepare_financial_frame(self, df: pd.DataFrame, value_cols):
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        if "ann_date" not in df.columns:
            # 如果没有公告日期，无法进行 PIT 对齐，必须丢弃
            log.warning("Financial dataframe missing 'ann_date' column. Dropping.")
            return pd.DataFrame()
            
        if "end_date" not in df.columns:
            df["end_date"] = np.nan

        # 清理日期字段
        df["ann_date"] = df["ann_date"].replace("", np.nan)
        df["end_date"] = df["end_date"].replace("", np.nan)
        
        # 移除没有公告日期的行 (严格 PIT 要求：不知道何时发布的数据不可用)
        # 绝对不能 ffill ann_date，那是造假/未来函数
        original_len = len(df)
        df = df[df["ann_date"].notna()]
        if len(df) < original_len:
            # log.debug(f"Dropped {original_len - len(df)} rows due to missing ann_date")
            pass

        df["_ann_dt"] = pd.to_datetime(df["ann_date"], errors="coerce")
        df["_end_dt"] = pd.to_datetime(df["end_date"], errors="coerce")
        
        # 排序方便后续处理，但不要去重（保留修正记录）
        df = df.sort_values(["ts_code", "_ann_dt", "_end_dt"])
        
        cols = ["ts_code", "ann_date", "end_date"] + list(value_cols)
        cols = [c for c in cols if c in df.columns]
        return df[cols]

    def _get_quarter_periods(self, start_date, end_date):
        start_dt = datetime.strptime(str(start_date), "%Y%m%d")
        end_dt = datetime.strptime(str(end_date), "%Y%m%d")
        periods = []
        
        current = start_dt
        while current <= end_dt:
            year = current.year
            md = current.strftime("%m%d")
            if md <= "0331":
                q_end = datetime(year, 3, 31)
            elif md <= "0630":
                q_end = datetime(year, 6, 30)
            elif md <= "0930":
                q_end = datetime(year, 9, 30)
            else:
                q_end = datetime(year, 12, 31)
            
            if q_end < start_dt:
                 q_end = datetime(year + 1, 3, 31)
            
            if q_end > end_dt:
                break
            
            # 避免重复（虽然逻辑上应该是递增的）
            p_str = q_end.strftime("%Y%m%d")
            if not periods or periods[-1] != p_str:
                periods.append(p_str)
            
            current = q_end + timedelta(days=1)
            
        return periods

    def _fetch_financials(self, start_date, end_date, ts_code=None):
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)
        if start_date is None or end_date is None:
            return pd.DataFrame()
            
        # Optimization: Fetch by date range directly (Single Stock)
        # Note: Tushare financial interfaces require ts_code for range fetch usually.
        # If ts_code is None, we can't fetch range efficiently without looping dates (which is slow).
        # So we assume ts_code is provided and is a SINGLE code.
        
        if not ts_code:
            return pd.DataFrame()

        income_dfs = []
        balance_dfs = []
        cashflow_dfs = []
        fina_dfs = []
        
        # 1. Income
        df = self._fetch_with_retry(
            self._get_interface_api("income"),
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=self._get_interface_fields("income"),
        )
        if df is not None and not df.empty:
            income_dfs.append(df)
            
        # 2. Balancesheet
        df = self._fetch_with_retry(
            self._get_interface_api("balancesheet"),
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=self._get_interface_fields("balancesheet"),
        )
        if df is not None and not df.empty:
            balance_dfs.append(df)
            
        # 3. Cashflow
        df = self._fetch_with_retry(
            self._get_interface_api("cashflow"),
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=self._get_interface_fields("cashflow"),
        )
        if df is not None and not df.empty:
            cashflow_dfs.append(df)
            
        # 4. Fina Indicator
        df = self._fetch_with_retry(
            self._get_interface_api("fina_indicator"),
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=self._get_interface_fields("fina_indicator"),
        )
        if df is not None and not df.empty:
            fina_dfs.append(df)

        income = pd.concat(income_dfs, ignore_index=True) if income_dfs else pd.DataFrame()
        balancesheet = pd.concat(balance_dfs, ignore_index=True) if balance_dfs else pd.DataFrame()
        cashflow = pd.concat(cashflow_dfs, ignore_index=True) if cashflow_dfs else pd.DataFrame()
        fina_indicator = pd.concat(fina_dfs, ignore_index=True) if fina_dfs else pd.DataFrame()

        if income.empty and balancesheet.empty and cashflow.empty and fina_indicator.empty:
            return pd.DataFrame()
            
        income = income.rename(columns={"n_income": "net_income"})
        balancesheet = balancesheet.rename(columns={"total_hldr_eqy_exc_min_int": "equity"})
        cashflow = cashflow.rename(columns={"n_cashflow_act": "op_cashflow"})
        
        income = self._prepare_financial_frame(income, ["net_income", "revenue", "oper_cost"])
        balancesheet = self._prepare_financial_frame(
            balancesheet,
            ["total_assets", "equity", "total_cur_assets", "total_cur_liab"],
        )
        cashflow = self._prepare_financial_frame(cashflow, ["op_cashflow"])
        
        if not fina_indicator.empty:
            rename_map = self._get_interface_rename("fina_indicator")
            if rename_map:
                fina_indicator = fina_indicator.rename(columns=rename_map)
            if "ann_date" in fina_indicator.columns:
                fina_indicator = fina_indicator[fina_indicator["ann_date"].notna()]
            # 注意：这里不能按照 end_date 去重，必须保留历史上的每一次修正记录，才能做到真正的 Point-in-Time
            # if "end_date" in fina_indicator.columns:
            #     fina_indicator = fina_indicator.sort_values("ann_date").drop_duplicates(
            #         subset=["ts_code", "end_date"], keep="last"
            #     )
                
        fina_indicator = self._prepare_financial_frame(
            fina_indicator,
            [
                "roe",
                "roe_waa",
                "grossprofit_margin",
                "debt_to_assets",
                "current_ratio",
                "q_dt_profit",
                "dt_netprofit_yoy",
                "q_gr_yoy",
                "profit_to_gr",
                "net_profit_margin",
            ],
        )
        
        frames = [f for f in [income, balancesheet, cashflow, fina_indicator] if not f.empty]
        if not frames:
            return pd.DataFrame()
        merged = frames[0]
        for frame in frames[1:]:
            # 必须同时匹配 ts_code, ann_date, end_date，确保是同一份财报的数据
            merge_keys = ["ts_code", "ann_date"]
            if "end_date" in merged.columns and "end_date" in frame.columns:
                merge_keys.append("end_date")
            merged = pd.merge(merged, frame, on=merge_keys, how="outer")
        merged["ann_date"] = merged["ann_date"].astype(str)

        for col in ["net_income", "equity", "total_assets", "revenue", "oper_cost", "total_cur_assets", "total_cur_liab"]:
            if col in merged.columns:
                merged[col] = pd.to_numeric(merged[col], errors="coerce")

        if "roe" not in merged.columns:
            merged["roe"] = np.nan
        roe_missing = merged["roe"].isna()
        if roe_missing.any() and {"net_income", "equity"}.issubset(merged.columns):
            denom = merged["equity"].replace(0, np.nan)
            merged.loc[roe_missing, "roe"] = merged.loc[roe_missing, "net_income"] / denom[roe_missing]

        if "grossprofit_margin" not in merged.columns:
            merged["grossprofit_margin"] = np.nan
        gpm_missing = merged["grossprofit_margin"].isna()
        if gpm_missing.any() and {"revenue", "oper_cost"}.issubset(merged.columns):
            revenue = merged["revenue"].replace(0, np.nan)
            gross_profit = merged["revenue"] - merged["oper_cost"]
            merged.loc[gpm_missing, "grossprofit_margin"] = gross_profit[gpm_missing] / revenue[gpm_missing]

        if "debt_to_assets" not in merged.columns:
            merged["debt_to_assets"] = np.nan
        dta_missing = merged["debt_to_assets"].isna()
        if dta_missing.any() and {"total_assets", "equity"}.issubset(merged.columns):
            assets = merged["total_assets"].replace(0, np.nan)
            liabilities = merged["total_assets"] - merged["equity"]
            merged.loc[dta_missing, "debt_to_assets"] = liabilities[dta_missing] / assets[dta_missing]

        if "current_ratio" not in merged.columns:
            merged["current_ratio"] = np.nan
        cr_missing = merged["current_ratio"].isna()
        if cr_missing.any() and {"total_cur_assets", "total_cur_liab"}.issubset(merged.columns):
            denom = merged["total_cur_liab"].replace(0, np.nan)
            merged.loc[cr_missing, "current_ratio"] = merged.loc[cr_missing, "total_cur_assets"] / denom[cr_missing]

        return merged


    def _merge_financials(self, daily_df, fin_df):
        if daily_df is None or daily_df.empty:
            return daily_df
        if fin_df is None or fin_df.empty:
            for col in self.financial_cols:
                if col not in daily_df.columns:
                    daily_df[col] = np.nan
            return daily_df
        daily_df = daily_df.copy()
        fin_df = fin_df.copy()
        daily_df["_orig_idx"] = np.arange(len(daily_df))
        daily_df["trade_date"] = daily_df["trade_date"].astype(str)
        fin_df["ann_date"] = fin_df["ann_date"].astype(str)
        daily_df["trade_date_dt"] = pd.to_datetime(daily_df["trade_date"], errors="coerce")
        fin_df["ann_date_dt"] = pd.to_datetime(fin_df["ann_date"], errors="coerce")
        valid_left = daily_df[daily_df["trade_date_dt"].notna()].copy()
        invalid_left = daily_df[daily_df["trade_date_dt"].isna()].copy()
        valid_left["ts_code"] = valid_left["ts_code"].astype(str)
        fin_df = fin_df[fin_df["ann_date_dt"].notna()].copy()
        fin_df["ts_code"] = fin_df["ts_code"].astype(str)
        merged_chunks = []
        for code, left_grp in valid_left.groupby("ts_code"):
            left_sorted = left_grp.sort_values("trade_date_dt")
            right_grp = fin_df[fin_df["ts_code"] == code].copy()
            if right_grp.empty:
                for col in self.financial_cols:
                    if col not in left_sorted.columns:
                        left_sorted[col] = np.nan
                merged_chunks.append(left_sorted)
                continue
            # PIT 核心逻辑：
            # 1. 必须按照 ann_date 排序，确保 merge_asof 找到的是 trade_date 之前(或当天)发布的记录
            # 2. 如果同一天发布了多个报告（例如修正公告，或延期的Q1和正常的Q2同天发），
            #    通常我们认为最新的报告期(end_date)更有价值，或者修正后的值（通常修正值在后，但Tushare数据不保证顺序）
            #    这里增加按照 end_date 排序，确保同 ann_date 下，最新的报告期排在后面，被 merge_asof 选中。
            sort_cols = ["ann_date_dt"]
            if "end_date" in right_grp.columns:
                right_grp["_end_dt"] = pd.to_datetime(right_grp["end_date"], errors="coerce")
                sort_cols.append("_end_dt")
            
            right_sorted = right_grp.sort_values(sort_cols).drop(columns=["ts_code", "_end_dt"], errors="ignore")
            
            merged_chunk = pd.merge_asof(
                left_sorted,
                right_sorted,
                left_on="trade_date_dt",
                right_on="ann_date_dt",
                direction="backward",
            )
            merged_chunks.append(merged_chunk)
        merged_valid = pd.concat(merged_chunks, ignore_index=True) if merged_chunks else valid_left
        if not invalid_left.empty:
            for col in self.financial_cols:
                if col not in invalid_left.columns:
                    invalid_left[col] = np.nan
            merged = pd.concat([merged_valid, invalid_left], ignore_index=True)
        else:
            merged = merged_valid
        merged = merged.drop(columns=["trade_date_dt", "ann_date_dt", "ann_date"], errors="ignore")
        for col in ["net_income", "revenue", "oper_cost", "total_assets", "equity", "total_cur_assets", "total_cur_liab", "roe", "grossprofit_margin", "debt_to_assets", "current_ratio"]:
            if col in merged.columns:
                merged[col] = pd.to_numeric(merged[col], errors="coerce")
        if {"net_income", "equity"}.issubset(merged.columns):
            merged["roe"] = merged.get("roe").combine_first(merged["net_income"] / merged["equity"].replace(0, np.nan)) if "roe" in merged.columns else merged["net_income"] / merged["equity"].replace(0, np.nan)
        if {"revenue", "oper_cost"}.issubset(merged.columns):
            derived = (merged["revenue"] - merged["oper_cost"]) / merged["revenue"].replace(0, np.nan)
            merged["grossprofit_margin"] = merged.get("grossprofit_margin").combine_first(derived) if "grossprofit_margin" in merged.columns else derived
        if {"total_assets", "equity"}.issubset(merged.columns):
            derived = (merged["total_assets"] - merged["equity"]) / merged["total_assets"].replace(0, np.nan)
            merged["debt_to_assets"] = merged.get("debt_to_assets").combine_first(derived) if "debt_to_assets" in merged.columns else derived
        if {"total_cur_assets", "total_cur_liab"}.issubset(merged.columns):
            derived = merged["total_cur_assets"] / merged["total_cur_liab"].replace(0, np.nan)
            merged["current_ratio"] = merged.get("current_ratio").combine_first(derived) if "current_ratio" in merged.columns else derived
        for col in self.financial_cols:
            if col not in merged.columns:
                merged[col] = np.nan
        merged = merged.sort_values("_orig_idx").drop(columns=["_orig_idx"], errors="ignore")
        return merged
    
    def _validate_and_clean(self, df: pd.DataFrame, code: str, ignore_columns=None) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        ignore_columns = set(ignore_columns or [])
        df = df.copy()
        df['trade_date'] = df['trade_date'].astype(str)
        if "paused" not in df.columns and "vol" in df.columns:
            df["paused"] = (pd.to_numeric(df["vol"], errors="coerce").fillna(0) <= 0).astype(int)
        expected_columns = self._get_expected_columns()
        missing_columns = [c for c in expected_columns if c not in df.columns and c not in ignore_columns]
        if missing_columns:
            log.warning(f"{code} missing columns: {missing_columns}")
        for col in expected_columns:
            if col not in df.columns:
                df[col] = np.nan
        numeric_cols = self._get_numeric_columns()
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        if "paused" in df.columns:
            df["paused"] = pd.to_numeric(df["paused"], errors="coerce").fillna(0).astype(int)
        cal = self.store.get_calendar()
        if cal is not None and not cal.empty:
            open_dates = set(cal[cal['is_open'] == 1]['cal_date'].astype(str).tolist())
            before = len(df)
            df = df[df['trade_date'].isin(open_dates)]
            after = len(df)
            if before != after:
                log.info(f"{code} removed {before - after} non-open days")
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
        df = df.sort_values('trade_date').reset_index(drop=True)
        non_negative_cols = self._non_negative_cols
        for col in non_negative_cols:
            if col in df.columns:
                bad = df[col] < 0
                bad_count = int(bad.sum())
                if bad_count > 0:
                    log.warning(f"{code} {col} has {bad_count} negative values")
                    df.loc[bad, col] = np.nan
        
        # Sanity Check for Outliers
        if 'pe' in df.columns:
            neg_pe = (df['pe'] < 0).sum()
            if neg_pe > 0:
                log.debug(f"{code} has {neg_pe} rows with negative PE")
        
        if 'pb' in df.columns:
            huge_pb = (df['pb'] > 10000).sum()
            if huge_pb > 0:
                log.warning(f"{code} has {huge_pb} rows with PB > 10000")

        if {'open','high','low','close'}.issubset(df.columns):
            o = df['open']
            h = df['high']
            l = df['low']
            c = df['close']
            bad = (o < 0) | (h < 0) | (l < 0) | (c < 0) | (h < o.clip(lower=c)) | (l > o.clip(upper=c))
            bad_count = int(bad.sum())
            if bad_count > 0:
                log.warning(f"{code} dropping {bad_count} rows due to price sanity checks")
                df = df[~bad]
        if 'close' in df.columns:
            pct = df['close'].pct_change().abs()
            extreme = int((pct > 0.25).sum())
            if extreme > 0:
                log.warning(f"{code} found {extreme} extreme moves >25%")
        present_cols = [c for c in numeric_cols if c in df.columns and c not in ignore_columns]
        if present_cols:
            miss_ratio = df[present_cols].isna().mean()
            # Relax threshold to 0.95 (allow 5% data presence) or make it stricter
            high_missing = miss_ratio[miss_ratio > 0.95] 
            
            # Special handling for banking/insurance sectors which legitimately lack some fields
            # e.g., Banks don't have standard 'revenue' or 'grossprofit_margin' in some contexts
            # 600000.SH (Pudong Bank), 601318.SH (Ping An)
            
            if not high_missing.empty:
                items = [f"{k}={v:.2%}" for k, v in high_missing.sort_values(ascending=False).items()]
                log.warning(f"{code} high missing ratio: {items}")
        if 'adj_factor' in df.columns:
            df['adj_factor'] = df['adj_factor'].fillna(1.0)
        if 'circ_mv' in df.columns:
            if "circ_mv" not in ignore_columns and df['circ_mv'].isna().all():
                log.warning(f"{code} circ_mv all missing")
            df['circ_mv'] = df['circ_mv'].fillna(0.0)
        return df

    def _fetch_with_retry(self, api_func, **kwargs):
        for i in range(self.max_retries):
            try:
                return api_func(**kwargs)
            except Exception as e:
                log.warning(f"API call failed (attempt {i+1}/{self.max_retries}): {e}")
                time.sleep(1 * (i + 1))
        raise Exception("Max retries exceeded")

    def update_daily(self, date: str):
        """
        Update all stocks for a specific date.
        date format: YYYYMMDD
        """
        cal = self.store.get_calendar()
        latest_open_date = None
        if cal is not None and not cal.empty and 'is_open' in cal.columns and 'cal_date' in cal.columns:
            cal_open = cal[cal['is_open'] == 1]
            cal_open = cal_open[cal_open['cal_date'] <= date]
            if not cal_open.empty:
                latest_open_date = cal_open['cal_date'].max()
        if latest_open_date is None:
            log.warning("Trading calendar not available, skipping calendar checks")
        else:
            if latest_open_date != date:
                log.info(f"Using latest open date {latest_open_date} instead of {date}")
                date = latest_open_date

        local_latest = self.store.get_global_latest_date()
        if local_latest is not None and local_latest >= date:
            log.info(f"Local data already up to date at {local_latest}, skipping Tushare fetch")
            return

        log.info(f"Fetching daily data for {date}")
        
        try:
            df_daily = self._fetch_with_retry(
                self._get_interface_api("daily"),
                trade_date=date,
                fields=self._get_interface_fields("daily"),
            )

            df_basic = self._fetch_with_retry(
                self._get_interface_api("daily_basic"),
                trade_date=date,
                fields=self._get_interface_fields("daily_basic"),
            )
            if df_basic is None or df_basic.empty:
                log.warning(f"{date} daily_basic empty")

            df_adj = self._fetch_with_retry(
                self._get_interface_api("adj_factor"),
                trade_date=date,
                fields=self._get_interface_fields("adj_factor"),
            )

            df_limit = self._fetch_with_retry(
                self._get_interface_api("stk_limit"),
                trade_date=date,
                fields=self._get_interface_fields("stk_limit"),
            )
            if df_limit is None or df_limit.empty:
                log.warning(f"{date} stk_limit empty")
            df_moneyflow = self._fetch_with_retry(
                self._get_interface_api("moneyflow"),
                trade_date=date,
                fields=self._get_interface_fields("moneyflow"),
            )
            if df_moneyflow is None or df_moneyflow.empty:
                log.warning(f"{date} moneyflow empty")

            if df_daily.empty:
                log.warning(f"No daily data for {date}")
                return

            # Merge
            if "amount" in df_daily.columns:
                df_daily["amount"] = pd.to_numeric(df_daily["amount"], errors="coerce") * 1000
            if not df_basic.empty:
                df_daily = pd.merge(df_daily, df_basic, on=["ts_code", "trade_date"], how="left")
            
            if not df_adj.empty:
                df_daily = pd.merge(df_daily, df_adj, on=["ts_code", "trade_date"], how="left")
                
            if not df_limit.empty:
                df_daily = pd.merge(df_daily, df_limit, on=["ts_code", "trade_date"], how="left")
            if df_moneyflow is not None and not df_moneyflow.empty:
                df_moneyflow = df_moneyflow.copy()
                df_moneyflow["buy_elg_amount"] = pd.to_numeric(df_moneyflow["buy_elg_amount"], errors="coerce")
                df_moneyflow["sell_elg_amount"] = pd.to_numeric(df_moneyflow["sell_elg_amount"], errors="coerce")
                df_moneyflow["net_mf_amount"] = pd.to_numeric(df_moneyflow["net_mf_amount"], errors="coerce")
                df_moneyflow["big_inflow"] = df_moneyflow["buy_elg_amount"] - df_moneyflow["sell_elg_amount"]
                df_moneyflow["net_inflow"] = df_moneyflow["net_mf_amount"]
                keep_cols = ["ts_code", "trade_date"] + self.moneyflow_fields + self._moneyflow_derived
                keep_cols = [c for c in keep_cols if c in df_moneyflow.columns]
                df_moneyflow = df_moneyflow[keep_cols]
                df_daily = pd.merge(df_daily, df_moneyflow, on=["ts_code", "trade_date"], how="left")

            # Margin financing (两融) - fetch and merge
            margin_df = self._fetch_with_retry(
                self._get_interface_api("margin"),
                trade_date=date,
                fields=self._get_interface_fields("margin"),
            )
            if margin_df is not None and not margin_df.empty:
                # Rename columns according to config
                rename_map = self._get_interface_rename("margin")
                if rename_map:
                    margin_df = margin_df.rename(columns=rename_map)
                # Keep only needed columns
                keep_cols = ["ts_code", "trade_date"] + self.margin_cols
                keep_cols = [c for c in keep_cols if c in margin_df.columns]
                margin_df = margin_df[keep_cols]
                df_daily = pd.merge(df_daily, margin_df, on=["ts_code", "trade_date"], how="left")
            else:
                log.warning(f"{date} margin data empty")

            fin_df = self._fetch_financials(date, date)
            if fin_df is None or fin_df.empty:
                log.warning(f"{date} financials empty")
            df_daily = self._merge_financials(df_daily, fin_df)

            # Fill missing adj_factor with 1.0 (new listings might miss it?)
            if 'adj_factor' in df_daily.columns:
                df_daily['adj_factor'] = df_daily['adj_factor'].fillna(1.0)
            
            # Save by code
            ignore_columns = []
            if df_basic is None or df_basic.empty:
                ignore_columns += self._get_interface_feature_fields("daily_basic")
            if df_limit is None or df_limit.empty:
                ignore_columns += self._get_interface_feature_fields("stk_limit")
            if df_moneyflow is None or df_moneyflow.empty:
                ignore_columns += self.moneyflow_fields + self._moneyflow_derived
            if margin_df is None or margin_df.empty:
                ignore_columns += self.margin_cols
            if fin_df is None or fin_df.empty:
                ignore_columns += self.financial_cols
            df_daily = self._validate_and_clean(df_daily, "ALL", ignore_columns=ignore_columns)
            codes = df_daily['ts_code'].unique()
            log.info(f"Saving data for {len(codes)} stocks...")
            
            count = 0
            for code in codes:
                row = df_daily[df_daily['ts_code'] == code].copy()
                existing_df = self.store.load_daily(code)
                for col in self.financial_cols:
                    if col not in row.columns:
                        row[col] = np.nan
                if existing_df is not None and not existing_df.empty:
                    last_row = existing_df.iloc[-1]
                    for col in self.financial_cols:
                        if col in row.columns and row[col].isna().all():
                            row.loc[:, col] = last_row.get(col)
                self.store.save_daily(row, code, existing_df=existing_df)
                count += 1
                if count % 500 == 0:
                    log.info(f"Saved {count}/{len(codes)}")
            
            log.info(f"Daily update for {date} completed.")
            
        except Exception as e:
            log.error(f"Update daily failed: {e}")
            raise

    def update_stock_list(self):
        df = self._fetch_with_retry(self.pro.stock_basic, exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,market,list_date')
        df_industry = self._fetch_with_retry(self.pro.stock_basic, exchange='', list_status='L', fields='ts_code,industry')
        self.store.save_meta_stocks(df)
        self._save_industry_map(df_industry)
        log.info(f"Updated stock list: {len(df)} stocks")

    def _save_industry_map(self, df: pd.DataFrame):
        if df is None or df.empty or "industry" not in df.columns:
            return
        industry_series = df["industry"].dropna().astype(str)
        industry_names = sorted(set([v for v in industry_series.tolist() if v and v != "nan"]))
        industry_map = {name: idx + 1 for idx, name in enumerate(industry_names)}
        meta_dir = cfg.get_path("meta")
        if meta_dir is None:
            return
        map_path = meta_dir / "industry_map.json"
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(industry_map, f, ensure_ascii=False)

    def update_calendar(self):
        # Fetch a wide range
        df = self._fetch_with_retry(self.pro.trade_cal, exchange='', start_date='20000101', end_date='20301231')
        self.store.save_meta_calendar(df)
        log.info("Updated trading calendar")

    # === Dragon-Tiger List (龙虎榜) Batch 1 Integration ===

    def get_top_inst(self, trade_date: str) -> Optional[pd.DataFrame]:
        """
        Fetch institution seat data (机构席位).
        Batch 1: Best for first integration - daily level, few fields, good for PIT.
        """
        if "top_inst" not in self._collector_interfaces:
            log.warning("top_inst not configured in interfaces")
            return None
        
        try:
            df = self._fetch_with_retry(
                self.pro.top_inst,
                trade_date=trade_date,
                fields=self._get_interface_fields("top_inst"),
            )
            if df is not None and not df.empty:
                # Convert numeric fields
                for col in ["buy", "sell", "net_buy"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                log.info(f"Fetched top_inst: {len(df)} records for {trade_date}")
            return df
        except Exception as e:
            log.error(f"Failed to fetch top_inst for {trade_date}: {e}")
            return None

    def get_top_list(self, trade_date: str) -> Optional[pd.DataFrame]:
        """
        Fetch dragon-tiger list (龙虎榜列表).
        Core龙虎榜数据: 股票当日是否上榜.
        """
        if "top_list" not in self._collector_interfaces:
            log.warning("top_list not configured in interfaces")
            return None
        
        try:
            df = self._fetch_with_retry(
                self.pro.top_list,
                trade_date=trade_date,
                fields=self._get_interface_fields("top_list"),
            )
            if df is not None and not df.empty:
                # Convert numeric fields
                for col in ["close", "pct_chg", "turnover_rate", "amount", 
                            "buyer_sum", "seller_sum", "net_amount"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                log.info(f"Fetched top_list: {len(df)} records for {trade_date}")
            return df
        except Exception as e:
            log.error(f"Failed to fetch top_list for {trade_date}: {e}")
            return None

    def get_daily(self, trade_date: str) -> Optional[pd.DataFrame]:
        """
        Fetch daily K-line data.
        """
        try:
            df = self._fetch_with_retry(
                self.pro.daily,
                trade_date=trade_date,
                fields=self._get_interface_fields("daily"),
            )
            if df is not None and not df.empty:
                log.info(f"Fetched daily: {len(df)} records for {trade_date}")
            return df
        except Exception as e:
            log.error(f"Failed to fetch daily for {trade_date}: {e}")
            return None

    def get_index_daily(self, index_code="000300.SH", start_date=None, end_date=None):
        df = self._fetch_with_retry(
            self.pro.index_daily,
            ts_code=index_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close",
        )
        return df

    def get_index_weights(self, index_code='000300.SH', trade_date=None):
        """Fetch index components"""
        if trade_date is None:
            # Use latest available date? Or today?
            # Tushare index_weight might need a specific date or start/end.
            # Let's try fetching for last month to be safe if trade_date not provided.
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
            end_date = datetime.now().strftime('%Y%m%d')
        else:
            start_date = trade_date
            end_date = trade_date

        df = self._fetch_with_retry(self.pro.index_weight, index_code=index_code, start_date=start_date, end_date=end_date)
        return df

    def get_universe(self, universe):
        if universe is None:
            return []
        if isinstance(universe, (list, tuple, set)):
            return list(universe)
        key = str(universe).strip()
        key_lower = key.lower()
        if key_lower == "debug":
            stock_df = self.store.get_stock_list()
            if stock_df is None or stock_df.empty:
                return []
            return stock_df["ts_code"].head(30).tolist()
        if key_lower == "test":
            stock_df = self.store.get_stock_list()
            if stock_df is None or stock_df.empty:
                return []
            return stock_df["ts_code"].head(50).tolist()
        if key_lower in {"csi300", "csi500"}:
            index_code = "000300.SH" if key_lower == "csi300" else "000905.SH"
            df = self.get_index_weights(index_code)
            if df is None or df.empty:
                return []
            codes = df["con_code"].dropna().unique().tolist()
            if len(codes) < 200:
                try:
                    df_member = self._fetch_with_retry(self.pro.index_member, index_code=index_code)
                except Exception:
                    df_member = pd.DataFrame()
                if df_member is not None and not df_member.empty and "con_code" in df_member.columns:
                    codes = df_member["con_code"].dropna().unique().tolist()
            return codes
        if key_lower == "all":
            stock_df = self.store.get_stock_list()
            if stock_df is None or stock_df.empty:
                return []
            return stock_df["ts_code"].tolist()
        if "," in key:
            return [c.strip() for c in key.split(",") if c.strip()]
        return [key]

    def update_universe_history(self, universe='csi300', start_date='20100101', end_date=None, incremental=True, include_basic=True, include_limit=True, include_adj=True, batch_size=50, include_moneyflow=True):
        start_ts = time.time()
        start_date = self._normalize_date(start_date)
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        end_date = self._normalize_date(end_date)
        log.info(f"Fetching universe {universe} from {start_date} to {end_date}...")
        codes = self.get_universe(universe)
        if not codes:
            log.warning("No codes found for universe.")
            return
        log.info(f"Found {len(codes)} stocks in universe {universe}. Starting update...")
        batch_size = batch_size or self.batch_size
        code_batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
        total_batches = len(code_batches)
        for i, batch_codes in enumerate(code_batches):
            batch_no = i + 1
            batch_str = ",".join(batch_codes)
            batch_start_ts = time.time()
            log.info(f"Processing batch {batch_no}/{total_batches} ({len(batch_codes)} stocks)...")
            self._update_batch_by_year(
                batch_codes,
                batch_str,
                start_date,
                end_date,
                include_basic=include_basic,
                include_limit=include_limit,
                include_adj=include_adj,
                include_moneyflow=include_moneyflow,
            )
            batch_elapsed = time.time() - batch_start_ts
            avg_elapsed = (time.time() - start_ts) / batch_no
            eta_seconds = max(int(avg_elapsed * (total_batches - batch_no)), 0)
            log.info(
                f"Finished batch {batch_no}/{total_batches} in {batch_elapsed:.1f}s | ETA ~{eta_seconds}s"
            )
        total_elapsed = time.time() - start_ts
        log.info(f"Universe {universe} update completed in {total_elapsed:.1f}s.")

    # def _fetch_financials_batch(self, code_str, start_date, end_date, span_years=5):
    #     start_date = self._normalize_date(start_date)
    #     end_date = self._normalize_date(end_date)
    #     if start_date is None or end_date is None:
    #         return pd.DataFrame()
    #     start_dt = datetime.strptime(start_date, '%Y%m%d')
    #     end_dt = datetime.strptime(end_date, '%Y%m%d')
    #     frames = []
    #     current = start_dt
    #     while current <= end_dt:
    #         chunk_end = datetime(min(current.year + span_years - 1, end_dt.year), 12, 31)
    #         if chunk_end > end_dt:
    #             chunk_end = end_dt
    #         df = self._fetch_financials(current.strftime('%Y%m%d'), chunk_end.strftime('%Y%m%d'), ts_code=code_str)
    #         if df is not None and not df.empty:
    #             frames.append(df)
    #         current = chunk_end + timedelta(days=1)
    #     if not frames:
    #         return pd.DataFrame()
    #     merged = pd.concat(frames, ignore_index=True)
    #     merged = merged.drop_duplicates(subset=['ts_code', 'ann_date'], keep='last')
    #     return merged

    def _fetch_financials_batch(self, code_str, start_date, end_date):
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)
        if start_date is None or end_date is None or not code_str:
            return pd.DataFrame()
        
        codes = code_str.split(",") if isinstance(code_str, str) else code_str
        frames = []

        # Optimization: Loop over stocks, fetch full range for each.
        # This avoids loop-by-period which is inefficient for small batches,
        # and works around Tushare's inability to batch-fetch financials by multiple codes.
        
        for i, code in enumerate(codes):
            # Rate limiting protection: Tushare has QPS limits (e.g. 200/min)
            # Fetching 4 tables per stock * 50 stocks = 200 requests instantly
            # We add a small sleep to avoid hitting the limit too hard
            if i > 0:
                time.sleep(0.3)
                
            df = self._fetch_financials(start_date, end_date, ts_code=code)
            if df is not None and not df.empty:
                frames.append(df)
            
        # Filter out empty or all-NA DataFrames before concat
        frames = [f for f in frames if not f.empty and not f.isna().all().all()]
        
        if not frames:
            return pd.DataFrame()
            
        merged = pd.concat(frames, ignore_index=True)
        subset_cols = ["ts_code", "ann_date"]
        if "end_date" in merged.columns:
            subset_cols.append("end_date")
        merged = merged.drop_duplicates(subset=subset_cols, keep="last")
        return merged


    def _update_batch_by_year(self, code_list, code_str, start_date, end_date, include_basic=True, include_limit=True, include_adj=True, include_moneyflow=True, include_margin=True):
        """
        [Optimization] 
        1. Fetch Financials, DailyBasic, StkLimit for the FULL period (per stock loop or batch period).
        2. Loop by Quarter for Daily/Adj/Moneyflow (batch fetch).
        3. Merge and Save.
        """
        # 1. Financials (Outside Loop)
        fin_df_all = self._fetch_financials_batch(code_str, start_date, end_date)
        
        # 2. Daily Basic (Outside Loop) -> Using the new Optimized Fetch (Stock Loop)
        df_basic_all = pd.DataFrame()
        if include_basic:
            # This will use _fetch_by_stock_loop which is FAST for subset of stocks
            df_basic_all = self._fetch_by_date_range("daily_basic", code_list, start_date, end_date)
            if df_basic_all is not None and not df_basic_all.empty:
                df_basic_all["trade_date"] = df_basic_all["trade_date"].astype(str)
        
        # 3. Limit (Outside Loop)
        df_limit_all = pd.DataFrame()
        if include_limit:
             df_limit_all = self._fetch_by_date_range("stk_limit", code_list, start_date, end_date)
             if df_limit_all is not None and not df_limit_all.empty:
                df_limit_all["trade_date"] = df_limit_all["trade_date"].astype(str)

        # Get List Date Map
        stock_df = self.store.get_stock_list()
        list_date_map = {}
        if stock_df is not None and not stock_df.empty and "ts_code" in stock_df.columns and "list_date" in stock_df.columns:
            list_date_series = stock_df.set_index("ts_code")["list_date"].astype(str)
            list_date_map = list_date_series.to_dict()

        # Loop Control
        curr_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')

        while curr_dt <= end_dt:
            # Chunking: 3 Months (Quarterly)
            year = curr_dt.year
            md = curr_dt.strftime("%m%d")
            if md <= "0331":
                q_end = datetime(year, 3, 31)
            elif md <= "0630":
                q_end = datetime(year, 6, 30)
            elif md <= "0930":
                q_end = datetime(year, 9, 30)
            else:
                 q_end = datetime(year, 12, 31)
            
            chunk_end_dt = min(q_end, end_dt)
            chunk_start = curr_dt.strftime('%Y%m%d')
            chunk_end = chunk_end_dt.strftime('%Y%m%d')

            # Filter valid codes (Listed before chunk end)
            if list_date_map:
                valid_codes = []
                for code in code_list:
                    l_date = list_date_map.get(code)
                    if not l_date or l_date == "nan" or l_date <= chunk_end:
                        valid_codes.append(code)
                if not valid_codes:
                    curr_dt = chunk_end_dt + timedelta(days=1)
                    continue
                valid_code_str = ",".join(valid_codes)
            else:
                valid_codes = code_list
                valid_code_str = code_str

            try:
                # 1. Daily (Batch)
                df_daily = self._fetch_with_retry(
                    self._get_interface_api("daily"),
                    ts_code=valid_code_str,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    fields=self._get_interface_fields("daily"),
                )
                
                if df_daily is None or df_daily.empty:
                    curr_dt = chunk_end_dt + timedelta(days=1)
                    continue

                # 2. Adj (Batch)
                df_adj = pd.DataFrame()
                if include_adj:
                    df_adj = self._fetch_with_retry(
                        self._get_interface_api("adj_factor"),
                        ts_code=valid_code_str,
                        start_date=chunk_start,
                        end_date=chunk_end,
                        fields=self._get_interface_fields("adj_factor"),
                    )

                # 3. MoneyFlow (Batch)
                df_moneyflow = pd.DataFrame()
                if include_moneyflow:
                    df_moneyflow = self._fetch_with_retry(
                        self._get_interface_api("moneyflow"),
                        ts_code=valid_code_str,
                        start_date=chunk_start,
                        end_date=chunk_end,
                        fields=self._get_interface_fields("moneyflow"),
                    )

                # 3.5 Margin (Batch by date range)
                df_margin = pd.DataFrame()
                if include_margin:
                    df_margin = self._fetch_by_date_range("margin", valid_codes, chunk_start, chunk_end)
                    if df_margin is not None and not df_margin.empty and "trade_date" in df_margin.columns:
                        df_margin["trade_date"] = df_margin["trade_date"].astype(str)

                # 4. Filter Basic/Limit from All
                df_basic = pd.DataFrame()
                if not df_basic_all.empty:
                    mask = (df_basic_all["trade_date"] >= chunk_start) & (df_basic_all["trade_date"] <= chunk_end) & (df_basic_all["ts_code"].isin(valid_codes))
                    df_basic = df_basic_all[mask].copy()

                df_limit = pd.DataFrame()
                if not df_limit_all.empty:
                    mask = (df_limit_all["trade_date"] >= chunk_start) & (df_limit_all["trade_date"] <= chunk_end) & (df_limit_all["ts_code"].isin(valid_codes))
                    df_limit = df_limit_all[mask].copy()

                # === Merge Logic ===
                if "amount" in df_daily.columns:
                    df_daily["amount"] = pd.to_numeric(df_daily["amount"], errors="coerce") * 1000

                if not df_basic.empty:
                    df_daily = pd.merge(df_daily, df_basic, on=['ts_code', 'trade_date'], how='left')
                if not df_adj.empty:
                    df_daily = pd.merge(df_daily, df_adj, on=['ts_code', 'trade_date'], how='left')
                if not df_limit.empty:
                    df_daily = pd.merge(df_daily, df_limit, on=['ts_code', 'trade_date'], how='left')
                
                if include_moneyflow and not df_moneyflow.empty:
                    cols_to_numeric = ["buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
                    for c in cols_to_numeric:
                        if c in df_moneyflow.columns:
                            df_moneyflow[c] = pd.to_numeric(df_moneyflow[c], errors="coerce")
                    
                    df_moneyflow["big_inflow"] = df_moneyflow["buy_elg_amount"] - df_moneyflow["sell_elg_amount"]
                    df_moneyflow["net_inflow"] = df_moneyflow["net_mf_amount"]
                    
                    keep_cols = ["ts_code", "trade_date"] + self.moneyflow_fields + self._moneyflow_derived
                    keep_cols = [c for c in keep_cols if c in df_moneyflow.columns]
                    df_daily = pd.merge(df_daily, df_moneyflow[keep_cols], on=['ts_code', 'trade_date'], how='left')

                if include_margin and df_margin is not None and not df_margin.empty:
                    rename_map = self._get_interface_rename("margin")
                    if rename_map:
                        df_margin = df_margin.rename(columns=rename_map)
                    keep_cols = ["ts_code", "trade_date"] + self.margin_cols
                    keep_cols = [c for c in keep_cols if c in df_margin.columns]
                    df_margin = df_margin[keep_cols]
                    merge_keys = {"ts_code", "trade_date"}
                    if not merge_keys.issubset(df_margin.columns):
                        log.warning(
                            f"Skip margin merge for {chunk_start}-{chunk_end}: missing keys {sorted(merge_keys - set(df_margin.columns))}"
                        )
                        df_margin = pd.DataFrame()
                    else:
                        df_daily = pd.merge(df_daily, df_margin, on=['ts_code', 'trade_date'], how='left')

                # Merge Financials
                if fin_df_all is not None and not fin_df_all.empty:
                    df_daily = self._merge_financials(df_daily, fin_df_all)

                # Ignore columns for validation
                ignore_columns = []
                if not include_moneyflow:
                    ignore_columns += self.moneyflow_fields + self._moneyflow_derived
                if include_basic and df_basic.empty:
                    ignore_columns += self._get_interface_feature_fields("daily_basic")
                if include_limit and df_limit.empty:
                    ignore_columns += self._get_interface_feature_fields("stk_limit")
                if include_moneyflow and df_moneyflow.empty:
                    ignore_columns += self.moneyflow_fields + self._moneyflow_derived
                if include_margin and (df_margin is None or df_margin.empty):
                    ignore_columns += self.margin_cols
                if fin_df_all is None or fin_df_all.empty:
                    ignore_columns += self.financial_cols

                # Save
                self._save_batch_results(df_daily, valid_codes, ignore_columns=ignore_columns)

            except Exception as e:
                log.error(f"Failed batch chunk {chunk_start}-{chunk_end}: {e}")

            # Next chunk
            curr_dt = chunk_end_dt + timedelta(days=1)

    def _save_batch_results(self, df_big, code_list, ignore_columns=None):
        if df_big is None or df_big.empty:
            return
        grouped = df_big.groupby('ts_code')
        for code in code_list:
            if code not in grouped.groups:
                continue
            df_part = grouped.get_group(code).copy()
            df_part = self._validate_and_clean(df_part, code, ignore_columns=ignore_columns)
            existing_df = self.store.load_daily(code)
            if existing_df is not None and not existing_df.empty:
                df_part = pd.concat([existing_df, df_part], ignore_index=True)
                df_part = df_part.drop_duplicates(subset=['trade_date'], keep='last')
                df_part = df_part.sort_values('trade_date').reset_index(drop=True)
            self.store.save_daily(df_part, code, existing_df=None)

    def update_history(self, code: str, start_date='20100101', end_date=None, incremental=True, include_basic=True, include_limit=True, include_adj=True, include_moneyflow=True, include_margin=True):
        """
        Fetch history for a single stock.
        """
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        basic_fields = self._get_interface_feature_fields("daily_basic")
        limit_fields = self._get_interface_feature_fields("stk_limit")
            
        existing_df = None
        if incremental:
            existing_df = self.store.load_daily(code)
            if existing_df is not None and not existing_df.empty:
                existing_dates = existing_df['trade_date'].astype(str)
                max_date = existing_dates.max()
                next_start = (datetime.strptime(max_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
                if next_start > end_date:
                    log.info(f"{code} already up to date at {max_date}, skipping")
                    return
                if next_start > start_date:
                    start_date = next_start
                    log.info(f"{code} incremental start adjusted to {start_date}")

        current_start = start_date
        ignore_columns = set()
        if not include_moneyflow:
            ignore_columns.update(self.moneyflow_fields + self._moneyflow_derived)
        if not include_margin:
            ignore_columns.update(self.margin_cols)
        chunks = []
        while current_start <= end_date:
            current_end_dt = min(datetime.strptime(current_start, '%Y%m%d').replace(year=int(current_start[:4])+1), datetime.strptime(end_date, '%Y%m%d'))
            current_end = current_end_dt.strftime('%Y%m%d')
            
            # log.debug(f"Fetching chunk {current_start} - {current_end}")
            
            try:
                df_daily = self._fetch_with_retry(
                    self._get_interface_api("daily"),
                    ts_code=code,
                    start_date=current_start,
                    end_date=current_end,
                    fields=self._get_interface_fields("daily"),
                )
                df_adj = self._fetch_with_retry(
                    self._get_interface_api("adj_factor"),
                    ts_code=code,
                    start_date=current_start,
                    end_date=current_end,
                    fields=self._get_interface_fields("adj_factor"),
                ) if include_adj else pd.DataFrame()
                df_basic = self._fetch_by_date_range(
                    "daily_basic",
                    [code],
                    current_start,
                    current_end,
                ) if include_basic else pd.DataFrame()
                df_limit = self._fetch_by_date_range(
                    "stk_limit",
                    [code],
                    current_start,
                    current_end,
                ) if include_limit else pd.DataFrame()
                df_moneyflow = self._fetch_with_retry(
                    self._get_interface_api("moneyflow"),
                    ts_code=code,
                    start_date=current_start,
                    end_date=current_end,
                    fields=self._get_interface_fields("moneyflow"),
                ) if include_moneyflow else pd.DataFrame()
                df_margin = self._fetch_by_date_range(
                    "margin",
                    [code],
                    current_start,
                    current_end,
                ) if include_margin else pd.DataFrame()

                if not df_daily.empty:
                    if include_basic and (df_basic is None or df_basic.empty):
                        log.warning(f"{code} {current_start}-{current_end} daily_basic empty")
                        ignore_columns.update(basic_fields)
                    elif include_basic:
                        missing_basic = [f for f in basic_fields if f not in df_basic.columns]
                        if missing_basic:
                            log.warning(f"{code} {current_start}-{current_end} daily_basic missing fields: {missing_basic}")
                            ignore_columns.update(missing_basic)
                    if include_limit and (df_limit is None or df_limit.empty):
                        log.warning(f"{code} {current_start}-{current_end} stk_limit empty")
                        ignore_columns.update(limit_fields)
                    if include_moneyflow and (df_moneyflow is None or df_moneyflow.empty):
                        log.warning(f"{code} {current_start}-{current_end} moneyflow empty")
                        ignore_columns.update(self.moneyflow_fields + self._moneyflow_derived)
                    if include_margin and (df_margin is None or df_margin.empty):
                        log.warning(f"{code} {current_start}-{current_end} margin empty")
                        ignore_columns.update(self.margin_cols)
                    # Merge
                    if "amount" in df_daily.columns:
                        df_daily["amount"] = pd.to_numeric(df_daily["amount"], errors="coerce") * 1000
                    if not df_basic.empty:
                        df_daily = pd.merge(df_daily, df_basic, on=['ts_code', 'trade_date'], how='left')
                    if not df_adj.empty:
                        df_daily = pd.merge(df_daily, df_adj, on=['ts_code', 'trade_date'], how='left')
                    if not df_limit.empty:
                        df_daily = pd.merge(df_daily, df_limit, on=['ts_code', 'trade_date'], how='left')
                    if include_moneyflow and df_moneyflow is not None and not df_moneyflow.empty:
                        df_moneyflow = df_moneyflow.copy()
                        df_moneyflow["buy_elg_amount"] = pd.to_numeric(df_moneyflow["buy_elg_amount"], errors="coerce")
                        df_moneyflow["sell_elg_amount"] = pd.to_numeric(df_moneyflow["sell_elg_amount"], errors="coerce")
                        df_moneyflow["net_mf_amount"] = pd.to_numeric(df_moneyflow["net_mf_amount"], errors="coerce")
                        df_moneyflow["big_inflow"] = df_moneyflow["buy_elg_amount"] - df_moneyflow["sell_elg_amount"]
                        df_moneyflow["net_inflow"] = df_moneyflow["net_mf_amount"]
                        keep_cols = ["ts_code", "trade_date"] + self.moneyflow_fields + self._moneyflow_derived
                        keep_cols = [c for c in keep_cols if c in df_moneyflow.columns]
                        df_moneyflow = df_moneyflow[keep_cols]
                        df_daily = pd.merge(df_daily, df_moneyflow, on=['ts_code', 'trade_date'], how='left')
                    if include_margin and df_margin is not None and not df_margin.empty:
                        rename_map = self._get_interface_rename("margin")
                        if rename_map:
                            df_margin = df_margin.rename(columns=rename_map)
                        keep_cols = ["ts_code", "trade_date"] + self.margin_cols
                        keep_cols = [c for c in keep_cols if c in df_margin.columns]
                        df_margin = df_margin[keep_cols]
                        merge_keys = {"ts_code", "trade_date"}
                        if not merge_keys.issubset(df_margin.columns):
                            log.warning(
                                f"{code} {current_start}-{current_end} skip margin merge: missing keys {sorted(merge_keys - set(df_margin.columns))}"
                            )
                            ignore_columns.update(self.margin_cols)
                        else:
                            df_daily = pd.merge(df_daily, df_margin, on=['ts_code', 'trade_date'], how='left')
                    fin_df = self._fetch_financials(current_start, current_end, ts_code=code)
                    if fin_df is None or fin_df.empty:
                        log.warning(f"{code} {current_start}-{current_end} financials empty")
                        ignore_columns.update(self.financial_cols)
                    df_daily = self._merge_financials(df_daily, fin_df)

                    chunks.append(df_daily)
            except Exception as e:
                log.error(f"Failed chunk {current_start}-{current_end}: {e}")

            # Next chunk
            current_start = (current_end_dt + timedelta(days=1)).strftime('%Y%m%d')
        
        if chunks:
            merged = pd.concat(chunks, ignore_index=True)
            merged = self._validate_and_clean(merged, code, ignore_columns=sorted(ignore_columns))
            self.store.save_daily(merged, code, existing_df=existing_df)
