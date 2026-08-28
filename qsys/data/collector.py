import os
import hashlib
import tushare as ts
import pandas as pd
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Callable, Mapping, Optional
from qsys.config import cfg
from qsys.utils.logger import log
from qsys.data.storage import StockDataStore
from qsys.data._collector_utils import _normalize_date, _dedupe_list
from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FinancialAvailabilityError,
    merge_trade_frames,
    prepare_financial_frame,
    select_first_available_financial_rows,
)
from qsys.data._fetch_strategies import fetch_with_retry, fetch_by_stock_loop, fetch_by_date_loop
from qsys.data.source_audit import (
    canonical_symbol_files_sha256,
    checkpoint_requested_scope,
    history_scope_identity,
    normalized_response_metadata,
    redact_secrets,
    stable_scope_hash,
    utc_now,
)
import numpy as np


HISTORY_SCOPE_PROCESSING_CONTRACT = (
    "csi_history_bundle_v1:" + FINANCIAL_AVAILABILITY_CONTRACT
)


class _LocalResumeMiss(RuntimeError):
    """Exact shard was not reusable; caller must use the serial remote lane."""


HISTORY_FIELD_ENDPOINTS = {
    "open": "daily",
    "high": "daily",
    "low": "daily",
    "close": "daily",
    "volume": "daily",
    "amount": "daily",
    "factor": "adj_factor",
    "pe": "daily_basic",
    "pb": "daily_basic",
    "total_mv": "daily_basic",
    "turnover_rate": "daily_basic",
    "circ_mv": "daily_basic",
    "rzye": "margin",
    "rzmre": "margin",
    "rzche": "margin",
    "roe": "fina_indicator",
    "grossprofit_margin": "fina_indicator",
    "debt_to_assets": "fina_indicator",
    "n_cashflow_act": "cashflow",
    "n_income": "income",
    "revenue": "income",
    "oper_cost": "income",
    "total_assets": "balancesheet",
    "total_hldr_eqy_exc_min_int": "balancesheet",
    "ann_date": "income",
    "end_date": "income",
    "report_type": "income",
    "industry": "bak_basic",
}


def _supplier_request_sha256(
    kwargs: Mapping[str, object], *, request_variant: str | None = None,
) -> str:
    """Hash the exact secret-safe supplier query without persisting it."""

    if not isinstance(kwargs, Mapping) or not kwargs:
        raise ValueError("supplier request kwargs cannot be empty")
    if any(not isinstance(key, str) or not key.strip() for key in kwargs):
        raise ValueError("supplier request kwargs contain an invalid key")
    try:
        # Validate the original values before redaction so types such as sets,
        # callables, and timestamps are rejected instead of being coerced or
        # silently omitted from the request identity.
        json.dumps(
            dict(kwargs),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("supplier request kwargs are not canonically serializable") from exc
    payload = {
        "kwargs": redact_secrets(dict(kwargs)),
        "request_variant": request_variant,
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("supplier request kwargs are not canonically serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TushareCollector:
    def __init__(self):
        token = os.environ.get("TUSHARE_TOKEN") or cfg.get("tushare_token")
        if not token:
            raise ValueError("Tushare token not found: set TUSHARE_TOKEN env or tushare_token in settings.yaml")
        self.pro = ts.pro_api(token)
        self.store = StockDataStore()
        self.max_retries = 3
        self.batch_size = 50

        self._tushare_cfg = cfg.get_tushare_feature_config()
        collector_cfg = self._tushare_cfg.get("collector", {})
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
        self._signed_numeric_cols = {"pct_chg", "net_buy", "net_amount"}
        self._percent_financial_cols = {
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
        self._percent_like_threshold = 3.0
        self._sparse_event_cols = {
            'exalter', 'buy', 'sell', 'net_buy', 'name', 'buyer_sum', 'seller_sum', 'net_amount', 'reason'
        }
        self._financial_sparse_by_industry = {
            '银行': {'grossprofit_margin', 'current_ratio', 'oper_cost', 'total_cur_assets', 'total_cur_liab'},
            '保险': {'grossprofit_margin', 'current_ratio', 'oper_cost', 'total_cur_assets', 'total_cur_liab'},
            '证券': {'grossprofit_margin', 'current_ratio', 'oper_cost', 'total_cur_assets', 'total_cur_liab'},
            '多元金融': {'grossprofit_margin', 'current_ratio', 'oper_cost', 'total_cur_assets', 'total_cur_liab'},
        }
        self._industry_cache = None

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

    def _get_interface_feature_fields(self, name):
        fields = self._get_interface_field_list(name)
        return [f for f in fields if f not in {"ts_code", "trade_date", "ann_date", "end_date"}]

    def _get_all_interface_fields(self):
        fields = []
        seen = set()
        for name in self._collector_interfaces:
            if name in self._financial_interfaces:
                continue
            rename_map = self._get_interface_rename(name)
            for f in self._get_interface_field_list(name):
                if f in {"ts_code", "trade_date", "ann_date", "end_date"}:
                    continue
                mapped = rename_map.get(f, f)
                if mapped not in seen:
                    seen.add(mapped)
                    fields.append(mapped)
        return fields

    def _get_expected_columns(self):
        cols = self._get_all_interface_fields()
        cols += self._expected_extra_cols + self._moneyflow_derived + self.financial_cols
        return _dedupe_list(cols)

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

    def _get_stock_industry(self, code: str) -> str | None:
        if self._industry_cache is None:
            try:
                basic = self.store.load_meta_stock_basic()
                if basic is not None and not basic.empty and {'ts_code', 'industry'}.issubset(basic.columns):
                    self._industry_cache = dict(zip(basic['ts_code'], basic['industry']))
                else:
                    self._industry_cache = {}
            except Exception:
                self._industry_cache = {}
        return self._industry_cache.get(code)

    def _get_interface_rename(self, name):
        cfg_item = self._get_interface_config(name)
        rename = cfg_item.get("rename", {})
        return rename if isinstance(rename, dict) else {}

    def _merge_trade_frames(self, left: pd.DataFrame, right: pd.DataFrame, *, keys: list[str]) -> pd.DataFrame:
        return merge_trade_frames(left, right, keys=keys)

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
        return fetch_by_stock_loop(
            api, fields, start_date, end_date, code_list,
            fetch_fn=lambda api_func, **kw: fetch_with_retry(api_func, self.max_retries, log.warning, **kw),
        )

    def _fetch_by_date_loop(self, api, fields, start_date, end_date, ts_codes=None):
        return fetch_by_date_loop(
            api, fields, start_date, end_date,
            fetch_fn=lambda api_func, **kw: fetch_with_retry(api_func, self.max_retries, log.warning, **kw),
            ts_codes=ts_codes,
            trade_cal_fn=self.pro.trade_cal,
        )

    def _normalize_percent_financial_columns(self, df: pd.DataFrame, columns=None) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()
        target_cols = set(columns or self._percent_financial_cols)
        threshold = float(getattr(self, "_percent_like_threshold", 3.0))
        for col in target_cols:
            if col not in df.columns:
                continue
            values = pd.to_numeric(df[col], errors="coerce")
            mask = values.abs() > threshold
            if mask.any():
                df.loc[mask, col] = values.loc[mask] / 100.0
        return df

    def _prepare_financial_frame(self, df: pd.DataFrame, value_cols):
        return prepare_financial_frame(df, value_cols)

    def _financial_response_error(
        self, raw: pd.DataFrame, *, endpoint_name: str, ts_code: str,
        start_date: str, end_date: str, availability_cutoff: str,
        exact_ann_date: str | None,
    ) -> Mapping[str, object] | None:
        """Validate one raw financial shard against its exact PIT request."""

        if raw is not None and not raw.empty:
            if "ts_code" not in raw.columns:
                return {"reason": "missing_financial_fields", "fields": ["ts_code"]}
            symbols = raw["ts_code"].astype(str).str.strip()
            if not symbols.eq(str(ts_code)).all():
                return {
                    "reason": "financial_response_symbol_mismatch",
                    "expected": str(ts_code),
                    "values": sorted(symbols.unique().tolist())[:10],
                }
            axis_column = (
                "ann_date"
                if exact_ann_date is not None or endpoint_name != "fina_indicator"
                else "end_date"
            )
            if axis_column not in raw.columns:
                return {
                    "reason": "missing_financial_fields",
                    "fields": [axis_column],
                }
            axis_values = (
                raw[axis_column].astype(str).str.strip()
                .str.replace("-", "", regex=False).str.slice(0, 8)
            )
            axis_start = exact_ann_date or start_date
            axis_end = exact_ann_date or end_date
            in_scope = (
                axis_values.str.fullmatch(r"\d{8}", na=False)
                & axis_values.ge(axis_start)
                & axis_values.le(axis_end)
            )
            if not in_scope.all():
                return {
                    "reason": "financial_response_query_axis_mismatch",
                    "axis": axis_column,
                    "expected_start": axis_start,
                    "expected_end": axis_end,
                    "values": sorted(axis_values.loc[~in_scope].unique().tolist())[:10],
                }
        try:
            select_first_available_financial_rows(
                raw,
                endpoint=endpoint_name,
                availability_cutoff=availability_cutoff,
            )
        except FinancialAvailabilityError as exc:
            return exc.details
        return None

    def _fetch_financials(
        self,
        start_date,
        end_date,
        ts_code=None,
        *,
        run_id: str | None = None,
        audit_store=None,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str = "ad_hoc",
        universe: str = "ad_hoc",
        availability_cutoff: str | None = None,
        exact_ann_date: str | None = None,
        local_reuse_only: bool = False,
        prepared_reuse: Mapping[str, Mapping[str, object]] | None = None,
    ):
        start_date = _normalize_date(start_date)
        end_date = _normalize_date(end_date)
        if start_date is None or end_date is None:
            return pd.DataFrame()
            
        # Optimization: Fetch by date range directly (Single Stock)
        # Note: Tushare financial interfaces require ts_code for range fetch usually.
        # If ts_code is None, we can't fetch range efficiently without looping dates (which is slow).
        # So we assume ts_code is provided and is a SINGLE code.
        
        if not ts_code:
            return pd.DataFrame()

        availability_cutoff = _normalize_date(availability_cutoff or end_date)
        exact_ann_date = _normalize_date(exact_ann_date)

        requested_scope = {
            "date_start": start_date,
            "date_end": end_date,
            "availability_cutoff": availability_cutoff,
            "symbol_count": 1,
            "symbols_sha256": stable_scope_hash([ts_code]),
        }
        if start_date != end_date:
            requested_scope["symbols"] = [ts_code]

        def fetch_statement(endpoint_name: str) -> pd.DataFrame:
            fields = tuple(self._get_interface_field_list(endpoint_name))
            endpoint_scope = {
                **requested_scope,
                "query_axis": (
                    "exact_announcement_date_query_axis"
                    if exact_ann_date is not None
                    else (
                        "report_period_query_axis"
                        if endpoint_name == "fina_indicator"
                        else "announcement_date_query_axis"
                    )
                ),
            }

            def validate_response(raw: pd.DataFrame):
                return self._financial_response_error(
                    raw,
                    endpoint_name=endpoint_name,
                    ts_code=str(ts_code),
                    start_date=start_date,
                    end_date=end_date,
                    availability_cutoff=availability_cutoff,
                    exact_ann_date=exact_ann_date,
                )

            identity_columns = (
                (
                    "ts_code", "end_date", "report_type", "comp_type", "end_type",
                    "ann_date", "f_ann_date", "update_flag",
                )
                if endpoint_name != "fina_indicator"
                else ("ts_code", "end_date", "ann_date", "update_flag")
            )
            supplier_query = (
                {"ann_date": exact_ann_date}
                if exact_ann_date is not None
                else {"start_date": start_date, "end_date": end_date}
            )
            frame, receipt_id = self._fetch_daily_endpoint_with_receipt(
                endpoint_name,
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=endpoint_scope,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                request_variant=FINANCIAL_AVAILABILITY_CONTRACT,
                identity_columns=identity_columns,
                evidence_fields=(),
                response_validator=validate_response,
                legacy_financial_contract=(
                    FINANCIAL_AVAILABILITY_CONTRACT
                    if exact_ann_date is None else None
                ),
                local_reuse_only=local_reuse_only,
                prepared_reuse=(prepared_reuse or {}).get(endpoint_name),
                ts_code=ts_code,
                **supplier_query,
                fields=self._get_interface_fields(endpoint_name),
            )
            if audit_store is not None and run_id is not None and receipt_id is not None:
                audit_store.record_field_receipt_links(
                    run_id=run_id, receipt_id=receipt_id, fields=fields,
                )
                if endpoint_name == "income":
                    audit_store.record_field_receipt_links(
                        run_id=run_id,
                        receipt_id=receipt_id,
                        dataset="income_sidecar",
                        fields=(
                            "ann_date", "f_ann_date", "end_date", "report_type",
                            "comp_type", "end_type", "update_flag", "n_income",
                            "revenue", "oper_cost",
                        ),
                    )
            semantic_error = validate_response(frame)
            if semantic_error is not None:
                raise RuntimeError(
                    "financial response failed requested-scope/PIT validation: "
                    f"{semantic_error}"
                )
            selected, projection = select_first_available_financial_rows(
                frame,
                endpoint=endpoint_name,
                availability_cutoff=availability_cutoff,
            )
            if audit_store is not None and run_id is not None:
                audit_store.append_event(
                    run_id,
                    "financial_availability_projection",
                    {
                        "endpoint": endpoint_name,
                        "receipt_id": receipt_id,
                        "ts_code": ts_code,
                        **projection,
                    },
                )
            return selected

        income_dfs = []
        balance_dfs = []
        cashflow_dfs = []
        fina_dfs = []
        
        # 1. Income
        df = fetch_statement("income")
        if df is not None and not df.empty:
            missing = {"ts_code", "availability_date"} - set(df.columns)
            if missing:
                raise RuntimeError(f"income response missing fields: {sorted(missing)}")
            income_dfs.append(df)
            
        # 2. Balancesheet
        df = fetch_statement("balancesheet")
        if df is not None and not df.empty:
            missing = {"ts_code", "availability_date"} - set(df.columns)
            if missing:
                raise RuntimeError(f"balancesheet response missing fields: {sorted(missing)}")
            balance_dfs.append(df)
            
        # 3. Cashflow
        df = fetch_statement("cashflow")
        if df is not None and not df.empty:
            missing = {"ts_code", "availability_date"} - set(df.columns)
            if missing:
                raise RuntimeError(f"cashflow response missing fields: {sorted(missing)}")
            cashflow_dfs.append(df)
            
        # 4. Fina Indicator
        df = fetch_statement("fina_indicator")
        if df is not None and not df.empty:
            missing = {"ts_code", "availability_date"} - set(df.columns)
            if missing:
                raise RuntimeError(f"fina_indicator response missing fields: {sorted(missing)}")
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
        fina_indicator = self._normalize_percent_financial_columns(fina_indicator)

        # Each endpoint is an independent publication stream.  Collapse
        # same-day reports to the latest report period (the legacy canonical
        # choice), then carry each endpoint's fields across the union of
        # availability events so a later income publication does not erase an
        # already-visible balance sheet, or vice versa.
        def endpoint_events(frame: pd.DataFrame, endpoint_name: str) -> pd.DataFrame:
            if frame.empty:
                return frame
            ordered = frame.sort_values(
                ["ts_code", "availability_date", "end_date"], kind="mergesort",
            )
            same_day = ordered.drop_duplicates(
                ["ts_code", "availability_date"], keep="last",
            )
            retained = []
            for _, group in same_day.groupby("ts_code", sort=False):
                report_period = pd.to_datetime(group["end_date"], errors="coerce")
                latest_before = report_period.cummax().shift(1)
                retained.append(
                    group.loc[latest_before.isna() | report_period.gt(latest_before)]
                )
            result = pd.concat(retained, ignore_index=True)
            return result.rename(columns={"end_date": f"_{endpoint_name}_end_date"})

        income = endpoint_events(income, "income")
        balancesheet = endpoint_events(balancesheet, "balancesheet")
        cashflow = endpoint_events(cashflow, "cashflow")
        fina_indicator = endpoint_events(fina_indicator, "fina_indicator")
        
        frames = [f for f in [income, balancesheet, cashflow, fina_indicator] if not f.empty]
        if not frames:
            return pd.DataFrame()
        union = pd.concat(
            [frame[["ts_code", "availability_date"]] for frame in frames],
            ignore_index=True,
        ).drop_duplicates()
        union["availability_date"] = union["availability_date"].astype(str)
        union = union.sort_values(
            ["ts_code", "availability_date"], kind="mergesort",
        ).reset_index(drop=True)

        # Carry the most recent row from each endpoint independently.  An
        # endpoint's new row (including its NaNs) replaces its prior row; only
        # the absence of an endpoint event on another endpoint's publication
        # date carries the previous endpoint snapshot forward.
        merged = union
        for frame in frames:
            carried = []
            frame = frame.copy()
            frame["availability_date"] = frame["availability_date"].astype(str)
            for code, left in union.groupby("ts_code", sort=False):
                left = left.copy()
                left["_availability_ord"] = pd.to_numeric(
                    left["availability_date"], errors="raise",
                )
                right = frame.loc[frame["ts_code"].eq(code)].copy()
                if right.empty:
                    continue
                right["_availability_ord"] = pd.to_numeric(
                    right["availability_date"], errors="raise",
                )
                right = right.drop(columns=["availability_date"])
                projection = pd.merge_asof(
                    left.sort_values("_availability_ord", kind="mergesort"),
                    right.sort_values("_availability_ord", kind="mergesort"),
                    on="_availability_ord",
                    by="ts_code",
                    direction="backward",
                ).drop(columns=["_availability_ord"])
                carried.append(projection)
            if carried:
                endpoint_projection = pd.concat(carried, ignore_index=True)
                merged = pd.merge(
                    merged,
                    endpoint_projection,
                    on=["ts_code", "availability_date"],
                    how="left",
                )
        endpoint_period_columns = [
            column for column in merged.columns if column.endswith("_end_date")
        ]
        if endpoint_period_columns:
            merged["_financial_period_end"] = merged[endpoint_period_columns].apply(
                lambda row: max(
                    (str(value) for value in row if pd.notna(value)),
                    default=pd.NA,
                ),
                axis=1,
            )
            merged = merged.drop(columns=endpoint_period_columns)
        if exact_ann_date is None:
            merged = merged.drop(columns=["_financial_period_end"], errors="ignore")

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


    def _discover_financial_announcement_codes(
        self,
        target_date: str,
        requested_codes: set[str],
        *,
        run_id: str | None = None,
        audit_store=None,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str = "ad_hoc",
        universe: str = "ad_hoc",
    ) -> dict[str, set[str]]:
        """Map disclosure candidates to exact original announcement dates.

        Tushare's ordinary income/balancesheet/cashflow/fina_indicator APIs
        require ``ts_code``.  ``disclosure_date`` is the non-VIP, market-wide
        discovery endpoint: ``actual_date`` is the primary publication-date
        signal, while the ``ann_date`` union catches revisions represented by
        the endpoint's alternate date predicate.  The two are intentionally
        only candidate discovery: disclosure_date's ``ann_date`` is not
        treated as financial visibility.  When ``actual_date`` is the target,
        its original ``ann_date`` is retained so a later final-announcement
        date can find the exact supplier row without a historical lookback.
        """
        target_date = _normalize_date(target_date)
        requested_codes = {str(code) for code in requested_codes if str(code).strip()}
        if target_date is None or not requested_codes:
            return {}

        cfg_item = self._get_interface_config("disclosure_date")
        fields = cfg_item.get("fields") if isinstance(cfg_item, dict) else None
        if isinstance(fields, list):
            fields = ",".join(fields)
        fields = fields or "ts_code,ann_date,end_date,pre_date,actual_date"
        candidates: dict[str, set[str]] = {}
        for date_field in ("actual_date", "ann_date"):
            frame, _ = self._fetch_daily_endpoint_with_receipt(
                "disclosure_date",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope={
                    "date_start": target_date,
                    "date_end": target_date,
                    "symbol_count": len(requested_codes),
                    "symbols_sha256": stable_scope_hash(requested_codes),
                },
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                request_variant=date_field,
                identity_columns=("ts_code", date_field),
                evidence_fields=tuple(self._get_interface_field_list("disclosure_date")),
                **{date_field: target_date, "fields": fields},
            )
            if frame is None or frame.empty:
                continue
            required = {"ts_code", date_field, "ann_date"}
            missing = required - set(frame.columns)
            if missing:
                raise RuntimeError(
                    f"disclosure_date {date_field} response missing fields: "
                    f"{sorted(missing)}"
                )
            dates = (
                frame[date_field]
                .astype(str)
                .str.strip()
                .str.replace("-", "", regex=False)
                .str.slice(0, 8)
            )
            matching = frame.loc[dates == target_date].copy()
            for row in matching.itertuples(index=False):
                code = str(getattr(row, "ts_code"))
                if code not in requested_codes:
                    continue
                original_ann_date = _normalize_date(getattr(row, "ann_date"))
                if original_ann_date is None:
                    raise RuntimeError(
                        "disclosure_date matched row has invalid ann_date: "
                        f"ts_code={code}, date_field={date_field}"
                    )
                candidates.setdefault(code, set()).add(original_ann_date)
        return candidates

    def _fetch_financials_for_daily(
        self,
        target_date: str,
        requested_codes: set[str],
        *,
        run_id: str | None = None,
        audit_store=None,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str = "ad_hoc",
        universe: str = "ad_hoc",
    ):
        """Fetch requested reports published after close on the target date.

        The canonical row is dated on publication day; the baseline's
        strict-before feature visibility contract makes it consumable only by
        a later trade date.  All four suppliers are queried on the exact
        ``ann_date`` axis, including ``fina_indicator`` whose historical
        start/end parameters otherwise mean report periods.
        """
        target_date = _normalize_date(target_date)
        requested_codes = {str(code) for code in requested_codes if str(code).strip()}
        discovery_kwargs = (
            {
                "run_id": run_id,
                "audit_store": audit_store,
                "resume_proof": resume_proof,
                "scope_key": scope_key,
                "universe": universe,
            }
            if run_id is not None or audit_store is not None or resume_proof is not None
            else {}
        )
        candidates = self._discover_financial_announcement_codes(
            target_date, requested_codes, **discovery_kwargs
        )
        if isinstance(candidates, set):
            # Compatibility for local callers/tests that stub the old helper.
            candidates = {code: {target_date} for code in candidates}
        frames = []
        query_index = 0
        for code in sorted(candidates):
            for announcement_date in sorted(candidates[code]):
                if query_index > 0:
                    time.sleep(0.3)
                query_index += 1
                statement_kwargs = (
                    {
                        "run_id": run_id,
                        "audit_store": audit_store,
                        "resume_proof": resume_proof,
                        "scope_key": scope_key,
                        "universe": universe,
                    }
                    if run_id is not None or audit_store is not None or resume_proof is not None
                    else {}
                )
                frame = self._fetch_financials(
                    announcement_date,
                    announcement_date,
                    ts_code=code,
                    availability_cutoff=target_date,
                    exact_ann_date=announcement_date,
                    **statement_kwargs,
                )
                if frame is None or frame.empty:
                    continue
                required = {"ts_code", "availability_date"}
                missing = required - set(frame.columns)
                if missing:
                    raise RuntimeError(
                        f"financial response for {code} missing fields: {sorted(missing)}"
                    )
                dates = (
                    frame["availability_date"].astype(str).str.strip()
                    .str.replace("-", "", regex=False).str.slice(0, 8)
                )
                frame = frame.loc[
                    dates.eq(target_date)
                    & frame["ts_code"].astype(str).isin(requested_codes)
                ].copy()
                if not frame.empty:
                    frames.append(frame)
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames, ignore_index=True)
        sort_columns = ["ts_code", "availability_date"]
        if "_financial_period_end" in merged.columns:
            sort_columns.append("_financial_period_end")
        merged = merged.sort_values(sort_columns, kind="mergesort")
        coalesce_columns = [
            column for column in merged.columns
            if column not in {"ts_code", "availability_date", "_financial_period_end"}
        ]
        if coalesce_columns:
            merged[coalesce_columns] = merged.groupby(
                ["ts_code", "availability_date"], sort=False,
            )[coalesce_columns].ffill()
        return merged.drop_duplicates(
            subset=["ts_code", "availability_date"], keep="last",
        ).reset_index(drop=True)


    def _merge_financials(self, daily_df, fin_df):
        if daily_df is None or daily_df.empty:
            return daily_df
        if fin_df is None or fin_df.empty:
            for col in self.financial_cols:
                if col not in daily_df.columns:
                    daily_df[col] = np.nan
            return daily_df
        daily_df = daily_df.copy()
        fin_df = self._normalize_percent_financial_columns(fin_df.copy())
        if "availability_date" not in fin_df.columns:
            raise RuntimeError(
                "financial projection missing audited availability_date"
            )
        daily_df["_orig_idx"] = np.arange(len(daily_df))
        daily_df["trade_date"] = daily_df["trade_date"].astype(str)
        fin_df["availability_date"] = fin_df["availability_date"].astype(str)
        daily_df["trade_date_dt"] = pd.to_datetime(daily_df["trade_date"], errors="coerce")
        fin_df["availability_date_dt"] = pd.to_datetime(
            fin_df["availability_date"], errors="coerce"
        )
        valid_left = daily_df[daily_df["trade_date_dt"].notna()].copy()
        invalid_left = daily_df[daily_df["trade_date_dt"].isna()].copy()
        valid_left["ts_code"] = valid_left["ts_code"].astype(str)
        fin_df = fin_df[fin_df["availability_date_dt"].notna()].copy()
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
            # The selector has already proven publication-time visibility.  At a
            # shared availability date, consume the latest eligible report period.
            sort_cols = ["availability_date_dt"]
            if "end_date" in right_grp.columns:
                right_grp["_end_dt"] = pd.to_datetime(right_grp["end_date"], errors="coerce")
                sort_cols.append("_end_dt")
            
            right_sorted = right_grp.sort_values(sort_cols).drop(columns=["ts_code", "_end_dt"], errors="ignore")
            
            merged_chunk = pd.merge_asof(
                left_sorted,
                right_sorted,
                left_on="trade_date_dt",
                right_on="availability_date_dt",
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
        merged = merged.drop(
            columns=[
                "trade_date_dt", "availability_date_dt", "availability_date",
                "_financial_period_end",
            ],
            errors="ignore",
        )
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

        # Repair common merge artifacts before validation.
        repair_map = {
            'close': ['close_x', 'close_y'],
            'high_limit': ['up_limit'],
            'low_limit': ['down_limit'],
            'volume': ['vol'],
        }
        for target, sources in repair_map.items():
            if target not in df.columns:
                df[target] = np.nan
            base = pd.to_numeric(df[target], errors='coerce')
            for src in sources:
                if src in df.columns:
                    base = base.combine_first(pd.to_numeric(df[src], errors='coerce'))
            df[target] = base

        if "paused" not in df.columns and "vol" in df.columns:
            df["paused"] = (pd.to_numeric(df["vol"], errors="coerce").fillna(0) <= 0).astype(int)
        expected_columns = self._get_expected_columns()
        missing_columns = [c for c in expected_columns if c not in df.columns and c not in ignore_columns]
        missing_columns = [c for c in missing_columns if c not in self._sparse_event_cols]
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
        df = df.drop_duplicates(subset=['ts_code', 'trade_date'] if 'ts_code' in df.columns else ['trade_date'], keep='last')
        df = df.sort_values('trade_date').reset_index(drop=True)
        non_negative_cols = self._non_negative_cols
        for col in non_negative_cols:
            if col in self._signed_numeric_cols:
                continue
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
            # ``update_daily`` validates one cross-sectional frame containing
            # many symbols.  A plain pct_change() compares adjacent symbols
            # after sorting by date and reports thousands of fictitious price
            # moves.  Price continuity is meaningful only within a symbol.
            if 'ts_code' in df.columns:
                pct = df.groupby('ts_code', sort=False)['close'].pct_change().abs()
            else:
                pct = df['close'].pct_change().abs()
            extreme = int((pct > 0.25).sum())
            if extreme > 0:
                log.warning(f"{code} found {extreme} extreme moves >25%")
        present_cols = [c for c in numeric_cols if c in df.columns and c not in ignore_columns]
        if present_cols:
            miss_ratio = df[present_cols].isna().mean()
            high_missing = miss_ratio[miss_ratio > 0.95]

            industry = self._get_stock_industry(code)
            allowed_sparse = set(self._sparse_event_cols)
            allowed_sparse.update({'roe_ttm'})
            if industry in self._financial_sparse_by_industry:
                allowed_sparse.update(self._financial_sparse_by_industry[industry])

            if not high_missing.empty:
                high_missing = high_missing[~high_missing.index.isin(allowed_sparse)]
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
        return fetch_with_retry(api_func, self.max_retries, log.warning, **kwargs)

    def _fetch_daily_endpoint_with_receipt(
        self,
        endpoint_name: str,
        *,
        run_id: str | None,
        audit_store,
        requested_scope: dict,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str = "ad_hoc",
        universe: str = "ad_hoc",
        request_variant: str | None = None,
        identity_columns: tuple[str, ...] = ("ts_code", "trade_date"),
        evidence_fields: tuple[str, ...] = (),
        required_column_groups: tuple[tuple[str, ...], ...] = (),
        response_validator: (
            Callable[[pd.DataFrame], Mapping[str, object] | None] | None
        ) = None,
        legacy_financial_contract: str | None = None,
        required_endpoint: bool = True,
        local_reuse_only: bool = False,
        prepared_reuse: Mapping[str, object] | None = None,
        **kwargs,
    ) -> tuple[pd.DataFrame, str | None]:
        """Fetch one endpoint and append a normalized, secret-safe receipt."""

        request_sha256 = _supplier_request_sha256(
            kwargs, request_variant=request_variant
        )
        requested_scope = checkpoint_requested_scope(
            requested_scope,
            source="tushare",
            endpoint=endpoint_name,
            contract_version="1",
            scope_key=scope_key,
            universe=universe,
            request_variant=request_variant,
            request_sha256=request_sha256,
        )
        if resume_proof is not None:
            if audit_store is None or run_id is None:
                raise ValueError("resume proof requires run_id and audit_store")
            if prepared_reuse is None:
                reused = audit_store.reuse_fetch_shard(
                    run_id=run_id,
                    resume_proof=resume_proof,
                    source="tushare",
                    endpoint=endpoint_name,
                    contract_version="1",
                    requested_scope=requested_scope,
                )
            elif prepared_reuse.get("kind") == "exact":
                reused = audit_store.commit_prepared_fetch_shard_reuse(
                    run_id=run_id, prepared=prepared_reuse["prepared"],
                )
            elif prepared_reuse.get("kind") == "legacy":
                reused = audit_store.commit_prepared_legacy_financial_shard(
                    run_id=run_id, prepared=prepared_reuse["prepared"],
                )
            else:
                raise ValueError("prepared financial reuse kind is invalid")
            if reused is not None:
                return reused["frame"], str(reused["receipt_id"])
            if legacy_financial_contract is not None:
                legacy = audit_store.reproject_legacy_financial_shard(
                    run_id=run_id,
                    resume_proof=resume_proof,
                    source="tushare",
                    endpoint=endpoint_name,
                    contract_version="1",
                    requested_scope=requested_scope,
                    legacy_request_sha256=_supplier_request_sha256(kwargs),
                    response_validator=response_validator or (lambda _frame: None),
                    contract_name=legacy_financial_contract,
                )
                if legacy["status"] == "compatible":
                    return legacy["frame"], str(legacy["receipt_id"])
                if legacy["status"] == "incompatible":
                    log.warning(
                        f"Legacy {endpoint_name} shard is not reusable "
                        f"({legacy.get('reason')}); fetching only this financial shard"
                    )
            if local_reuse_only:
                raise _LocalResumeMiss(
                    f"no exact reusable local shard for {endpoint_name}"
                )

        api = self._get_interface_api(endpoint_name)
        attempt_count = 0

        def counted_api(**call_kwargs):
            nonlocal attempt_count
            attempt_count += 1
            return api(**call_kwargs)

        try:
            frame = self._fetch_with_retry(counted_api, **kwargs)
        except Exception as exc:
            receipt_id = None
            if audit_store is not None and run_id is not None:
                empty_meta = normalized_response_metadata(pd.DataFrame())
                receipt_id = audit_store.record_fetch(
                    run_id=run_id,
                    source="tushare",
                    endpoint=endpoint_name,
                    status="failure",
                    requested_scope=requested_scope,
                    returned_rows=0,
                    attempt_count=max(1, attempt_count),
                    published_at=None,
                    error=str(exc),
                    **empty_meta,
                )
                if evidence_fields:
                    audit_store.record_field_receipt_links(
                        run_id=run_id, receipt_id=receipt_id, fields=evidence_fields
                    )
            if required_endpoint:
                raise
            return pd.DataFrame(), receipt_id

        frame = frame if frame is not None else pd.DataFrame()
        status = "empty" if frame.empty else "success"
        required_keys = set(identity_columns)
        if not frame.empty and not required_keys.issubset(frame.columns):
            status = "partial"
        if not frame.empty and any(
            not set(group).intersection(frame.columns)
            for group in required_column_groups
        ):
            status = "partial"
        validation_error = None
        if response_validator is not None:
            try:
                validation_result = response_validator(frame)
            except Exception as exc:
                validation_result = {
                    "reason": "validator_exception",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            if validation_result is not None:
                status = "partial"
                validation_error = {
                    "kind": "response_validation_failed",
                    "details": dict(validation_result),
                }
        receipt_id = None
        if audit_store is not None and run_id is not None:
            receipt_id = audit_store.record_fetch(
                run_id=run_id,
                source="tushare",
                endpoint=endpoint_name,
                status=status,
                requested_scope=requested_scope,
                returned_rows=len(frame),
                attempt_count=max(1, attempt_count),
                # Tushare does not expose a trustworthy publication timestamp
                # for these responses.  Do not infer one from trade_date.
                published_at=None,
                observed_at=utc_now(),
                payload_frame=frame if status in {"success", "partial"} else None,
                error=validation_error,
                **normalized_response_metadata(frame),
            )
            if evidence_fields:
                audit_store.record_field_receipt_links(
                    run_id=run_id, receipt_id=receipt_id, fields=evidence_fields
                )
        return frame, receipt_id

    def update_daily(
        self,
        date: str,
        *,
        codes: Optional[list[str]] = None,
        include_financial: bool = True,
        force: bool = False,
        run_id: str | None = None,
        audit_store=None,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str | None = None,
        universe: str | None = None,
    ) -> dict:
        """
        Update stocks for one specific date using trade-date batch APIs.

        ``codes`` only limits what is written; the source requests remain
        market-wide because the trade-date APIs are the efficient and stable
        Tushare shape.  ``include_financial`` discovers disclosure candidates
        by date, then fetches only candidate reports whose report ``ann_date``
        is ``date``; this lets the existing PIT merge carry disclosures
        forward without pulling historical reports.  ``force`` is used by a
        targeted repair when the global watermark is already at ``date``.

        date format: YYYYMMDD
        """
        date = _normalize_date(date)
        if date is None:
            raise ValueError("daily update requires a target trade date")
        if resume_proof is not None and (not scope_key or not universe):
            raise ValueError("resume requires explicit scope_key and universe")
        effective_universe = str(universe or scope_key or "ad_hoc")
        effective_scope_key = str(scope_key or effective_universe)
        requested_codes = set(str(code) for code in (codes or []) if str(code).strip())
        canonical_mutations: list[dict] = []
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
        if local_latest is not None and local_latest >= date and not force and not requested_codes:
            log.info(f"Local data already up to date at {local_latest}, skipping Tushare fetch")
            return {"status": "noop", "fetch_receipt_id": None, "mutations": []}

        log.info(f"Fetching daily data for {date}")
        
        try:
            request_scope = {
                "date_start": date,
                "date_end": date,
                "symbol_count": len(requested_codes),
                "symbols_sha256": stable_scope_hash(requested_codes),
            }
            df_daily, daily_receipt_id = self._fetch_daily_endpoint_with_receipt(
                "daily",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                evidence_fields=("open", "high", "low", "close", "volume"),
                required_column_groups=(("open",), ("high",), ("low",), ("close",), ("vol", "volume")),
                trade_date=date,
                fields=self._get_interface_fields("daily"),
            )

            df_basic, _ = self._fetch_daily_endpoint_with_receipt(
                "daily_basic",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                required_endpoint=False,
                trade_date=date,
                fields=self._get_interface_fields("daily_basic"),
            )
            if df_basic is None or df_basic.empty:
                log.warning(f"{date} daily_basic empty")

            df_adj, _ = self._fetch_daily_endpoint_with_receipt(
                "adj_factor",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                evidence_fields=("factor",),
                required_column_groups=(("adj_factor", "factor"),),
                trade_date=date,
                fields=self._get_interface_fields("adj_factor"),
            )

            def _returned_symbols_for_target(frame: pd.DataFrame) -> set[str]:
                if frame is None or frame.empty or not {"ts_code", "trade_date"}.issubset(frame.columns):
                    return set()
                dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
                return set(frame.loc[dates == date, "ts_code"].dropna().astype(str))

            required_endpoint_missing_symbols = {
                "daily": sorted(requested_codes - _returned_symbols_for_target(df_daily)),
                "adj_factor": sorted(requested_codes - _returned_symbols_for_target(df_adj)),
            }

            def _missing_required_values(
                frame: pd.DataFrame, field_sources: dict[str, tuple[str, ...]]
            ) -> dict[str, list[str]]:
                result = {field: [] for field in field_sources}
                if not requested_codes:
                    return result
                if frame is None or frame.empty or not {"ts_code", "trade_date"}.issubset(frame.columns):
                    return {field: sorted(requested_codes) for field in field_sources}
                dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
                target_rows = frame.loc[
                    dates == date
                ].copy()
                target_rows["ts_code"] = target_rows["ts_code"].astype(str)
                for field, source_columns in field_sources.items():
                    available = pd.Series(False, index=target_rows.index)
                    for column in source_columns:
                        if column in target_rows.columns:
                            available |= pd.to_numeric(
                                target_rows[column], errors="coerce"
                            ).notna()
                    symbols_with_value = set(
                        target_rows.loc[available, "ts_code"].astype(str)
                    )
                    result[field] = sorted(requested_codes - symbols_with_value)
                return result

            required_field_missing_symbols = {
                "daily": _missing_required_values(
                    df_daily,
                    {
                        "open": ("open",),
                        "high": ("high",),
                        "low": ("low",),
                        "close": ("close",),
                        "volume": ("volume", "vol"),
                    },
                ),
                "adj_factor": _missing_required_values(
                    df_adj, {"factor": ("factor", "adj_factor")}
                ),
            }

            df_limit, _ = self._fetch_daily_endpoint_with_receipt(
                "stk_limit",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                required_endpoint=False,
                trade_date=date,
                fields=self._get_interface_fields("stk_limit"),
            )
            if df_limit is None or df_limit.empty:
                log.warning(f"{date} stk_limit empty")
            df_moneyflow, _ = self._fetch_daily_endpoint_with_receipt(
                "moneyflow",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                required_endpoint=False,
                trade_date=date,
                fields=self._get_interface_fields("moneyflow"),
            )
            if df_moneyflow is None or df_moneyflow.empty:
                log.warning(f"{date} moneyflow empty")

            if df_daily.empty:
                log.warning(f"No daily data for {date}")
                return {
                    "status": "empty",
                    "fetch_receipt_id": daily_receipt_id,
                    "mutations": [],
                    "required_endpoint_missing_symbols": required_endpoint_missing_symbols,
                    "required_field_missing_symbols": required_field_missing_symbols,
                }

            # A trade-date endpoint should already be exact, but enforce the
            # boundary before any merge/write so an unexpected wider response
            # cannot mutate another date during a daily repair.
            if "trade_date" not in df_daily.columns or "ts_code" not in df_daily.columns:
                raise RuntimeError("daily response missing ts_code/trade_date")
            daily_dates = df_daily["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
            df_daily = df_daily.loc[daily_dates == date].copy()
            if requested_codes:
                df_daily = df_daily[df_daily["ts_code"].astype(str).isin(requested_codes)].copy()
            if df_daily.empty:
                log.warning(f"No requested stocks have daily data for {date}")
                return {
                    "status": "success",
                    "fetch_receipt_id": daily_receipt_id,
                    "mutations": [],
                    "reason": "source_response_has_no_requested_symbols",
                    "required_endpoint_missing_symbols": required_endpoint_missing_symbols,
                    "required_field_missing_symbols": required_field_missing_symbols,
                }
            if not requested_codes:
                # ``update_daily(date)`` is the existing full-market API;
                # use the exact symbols returned for this session as the
                # financial candidate allow-list.
                requested_codes = set(df_daily["ts_code"].astype(str))

            # Merge
            if "amount" in df_daily.columns:
                df_daily["amount"] = pd.to_numeric(df_daily["amount"], errors="coerce") * 1000
            if not df_basic.empty:
                df_daily = self._merge_trade_frames(df_daily, df_basic, keys=["ts_code", "trade_date"])
            
            if not df_adj.empty:
                df_daily = self._merge_trade_frames(df_daily, df_adj, keys=["ts_code", "trade_date"])
                
            if not df_limit.empty:
                df_daily = self._merge_trade_frames(df_daily, df_limit, keys=["ts_code", "trade_date"])
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
                df_daily = self._merge_trade_frames(df_daily, df_moneyflow, keys=["ts_code", "trade_date"])

            # Margin financing (两融) - fetch and merge
            margin_df, _ = self._fetch_daily_endpoint_with_receipt(
                "margin",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=request_scope,
                resume_proof=resume_proof,
                scope_key=effective_scope_key,
                universe=effective_universe,
                required_endpoint=False,
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

            # Financial statements are sparse events.  Discover candidates
            # market-wide, then call each ordinary financial endpoint with its
            # required ts_code and retain only report ann_date == target.
            fin_df = pd.DataFrame()
            if include_financial:
                fin_df = self._fetch_financials_for_daily(
                    date,
                    requested_codes,
                    run_id=run_id,
                    audit_store=audit_store,
                    resume_proof=resume_proof,
                    scope_key=effective_scope_key,
                    universe=effective_universe,
                )
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
            df_daily = self._validate_and_clean(df_daily, "ALL", ignore_columns=ignore_columns)
            bundle_receipt_id = daily_receipt_id
            if audit_store is not None and run_id is not None:
                bundle_receipt_id = audit_store.record_fetch(
                    run_id=run_id,
                    source="tushare",
                    endpoint="daily_bundle",
                    status="empty" if df_daily.empty else "success",
                    requested_scope=request_scope,
                    returned_rows=len(df_daily),
                    attempt_count=1,
                    payload_kind="derived",
                    published_at=None,
                    observed_at=utc_now(),
                    **normalized_response_metadata(df_daily),
                )
            codes = df_daily['ts_code'].unique()
            log.info(f"Saving data for {len(codes)} stocks...")
            
            count = 0
            for code in codes:
                row = df_daily[df_daily['ts_code'] == code].copy()
                if "trade_date" not in row.columns:
                    raise RuntimeError(f"daily row for {code} missing trade_date")
                existing_df = self.store.load_daily(code)
                previous = None
                if existing_df is not None and not existing_df.empty:
                    existing_dates = existing_df["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
                    previous_rows = existing_df.loc[existing_dates < date]
                    if not previous_rows.empty:
                        previous = previous_rows.sort_values("trade_date").iloc[-1]
                for col in self.financial_cols:
                    if col not in row.columns:
                        row[col] = np.nan
                    if previous is not None and row[col].isna().any():
                        row.loc[row[col].isna(), col] = previous.get(col)
                mutations = self.store.save_daily(row, code, existing_df=existing_df) or []
                for mutation in mutations:
                    mutation["endpoint"] = "daily_bundle"
                    mutation["fetch_receipt_id"] = bundle_receipt_id
                # Feather commit happens inside save_daily.  Evidence is
                # appended only afterwards; SQLite failure propagates and the
                # terminal gate stays closed even though canonical recovery
                # may be required.
                if audit_store is not None and run_id is not None:
                    audit_store.record_mutations(run_id=run_id, mutations=mutations)
                canonical_mutations.extend(mutations)
                count += 1
                if count % 500 == 0:
                    log.info(f"Saved {count}/{len(codes)}")
            
            log.info(f"Daily update for {date} completed.")
            return {
                "status": "success",
                "fetch_receipt_id": daily_receipt_id,
                "mutations": canonical_mutations,
                "required_endpoint_missing_symbols": required_endpoint_missing_symbols,
                "required_field_missing_symbols": required_field_missing_symbols,
            }
            
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
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        return df

    def get_index_weights(self, index_code='000300.SH', trade_date=None):
        """Fetch index components with robust snapshot search.

        Tushare index_weight is updated monthly (last trading day of the month).
        The search window must be wide enough to catch the latest snapshot.
        Falls back to progressively wider windows if empty.
        """
        if trade_date is not None:
            df = self._fetch_with_retry(
                self.pro.index_weight,
                index_code=index_code,
                start_date=trade_date,
                end_date=trade_date,
            )
            return df

        # Widening windows: 45d → 90d → 180d → 365d
        windows = [45, 90, 180, 365]
        now = datetime.now()
        for window in windows:
            start_date = (now - timedelta(days=window)).strftime('%Y%m%d')
            end_date = now.strftime('%Y%m%d')
            df = self._fetch_with_retry(
                self.pro.index_weight,
                index_code=index_code,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                return df

        # Last resort: no date filter at all (Tushare returns up to 7000 rows)
        return self._fetch_with_retry(self.pro.index_weight, index_code=index_code)

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
        if key_lower in {"csi300", "csi500", "csi800"}:
            index_code_map = {
                "csi300": "000300.SH",
                "csi500": "000905.SH",
                "csi800": "000906.SH",
            }
            index_code = index_code_map[key_lower]
            df = self.get_index_weights(index_code)
            if df is None or df.empty:
                return []
            codes = df["con_code"].dropna().unique().tolist()
            min_expected = {"csi300": 200, "csi500": 300, "csi800": 500}[key_lower]
            if len(codes) < min_expected:
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

    def _fetch_history_stock_endpoint(
        self,
        endpoint: str,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        run_id: str | None,
        audit_store,
        resume_proof: Mapping[str, object] | None,
        scope_key: str,
        universe: str,
        evidence_fields: tuple[str, ...] = (),
        required_endpoint: bool = True,
    ) -> pd.DataFrame:
        """Fetch one per-symbol range endpoint as durable resumable shards."""

        frames: list[pd.DataFrame] = []
        for code in codes:
            requested_scope = {
                "date_start": start_date,
                "date_end": end_date,
                "symbol_count": 1,
                "symbols": [code],
                "symbols_sha256": stable_scope_hash([code]),
            }
            frame, _ = self._fetch_daily_endpoint_with_receipt(
                endpoint,
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=requested_scope,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                evidence_fields=evidence_fields,
                required_endpoint=required_endpoint,
                ts_code=code,
                start_date=start_date,
                end_date=end_date,
                fields=self._get_interface_fields(endpoint),
            )
            if frame is not None and not frame.empty:
                frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def update_universe_history(
        self, universe='csi300', start_date='20100101', end_date=None,
        incremental=True, include_basic=True, include_limit=True,
        include_adj=True, batch_size=50, include_moneyflow=True,
        include_margin=True, *, run_id: str | None = None, audit_store=None,
        resume_proof: Mapping[str, object] | None = None,
        scope_key: str | None = None, evidence_universe: str | None = None,
        local_max_workers: int = 1,
    ):
        start_ts = time.time()
        start_date = _normalize_date(start_date)
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        end_date = _normalize_date(end_date)
        log.info(f"Fetching universe {universe} from {start_date} to {end_date}...")
        codes = sorted({str(code) for code in self.get_universe(universe)})
        if not codes:
            log.warning("No codes found for universe.")
            return {"status": "failed", "reason": "empty_universe", "mutation_count": 0}
        audited = run_id is not None or audit_store is not None or resume_proof is not None
        if audited and (run_id is None or audit_store is None or not scope_key or not evidence_universe):
            raise ValueError("audited history requires run_id, audit_store, scope_key, and evidence_universe")
        log.info(f"Found {len(codes)} stocks in universe {universe}. Starting update...")
        batch_size = batch_size or self.batch_size
        code_batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
        total_batches = len(code_batches)
        mutation_count = 0
        completed_scope_ids: list[str] = []
        inherited_scope_ids: list[str] = []
        completed_scopes = (
            audit_store.resumable_history_scopes(resume_proof)
            if audited and resume_proof is not None
            else {}
        )
        for i, batch_codes in enumerate(code_batches):
            batch_no = i + 1
            batch_str = ",".join(batch_codes)
            batch_start_ts = time.time()
            scope_identity = history_scope_identity(
                source="tushare",
                scope_key=str(scope_key or "ad_hoc"),
                universe=str(evidence_universe or "ad_hoc"),
                range_start=start_date,
                range_end=end_date,
                symbols=batch_codes,
                processing_contract=HISTORY_SCOPE_PROCESSING_CONTRACT,
            )
            checkpoint = completed_scopes.get(str(scope_identity["scope_id"]))
            if checkpoint is not None:
                canonical_hash = canonical_symbol_files_sha256(
                    self.store.canonical_dir,
                    batch_codes,
                    max_workers=local_max_workers,
                )
                if canonical_hash == checkpoint["canonical_scope_sha256"]:
                    audit_store.record_history_scope_inherited(
                        run_id=run_id, checkpoint=checkpoint,
                    )
                    inherited_scope_ids.append(str(scope_identity["scope_id"]))
                    log.info(
                        "Inherited completed batch %s/%s (%s stocks) from %s",
                        batch_no, total_batches, len(batch_codes),
                        checkpoint["source_run_id"],
                    )
                    continue
                log.warning(
                    "History checkpoint canonical hash mismatch for batch %s/%s; "
                    "replaying only this scope",
                    batch_no, total_batches,
                )
            log.info(f"Processing batch {batch_no}/{total_batches} ({len(batch_codes)} stocks)...")
            before_receipts = set(audit_store.fetch_receipt_ids(run_id)) if audited else set()
            batch_result = self._update_batch_by_year(
                batch_codes,
                batch_str,
                start_date,
                end_date,
                include_basic=include_basic,
                include_limit=include_limit,
                include_adj=include_adj,
                include_moneyflow=include_moneyflow,
                include_margin=include_margin,
                run_id=run_id,
                audit_store=audit_store,
                resume_proof=resume_proof,
                scope_key=str(scope_key or "ad_hoc"),
                evidence_universe=str(evidence_universe or "ad_hoc"),
                local_max_workers=local_max_workers,
            )
            mutation_count += int(batch_result.get("mutation_count", 0))
            if audited:
                all_receipts = audit_store.fetch_receipt_ids(run_id)
                scope_receipts = [item for item in all_receipts if item not in before_receipts]
                canonical_hash = canonical_symbol_files_sha256(
                    self.store.canonical_dir,
                    batch_codes,
                    max_workers=local_max_workers,
                )
                audit_store.record_history_scope_completed(
                    run_id=run_id,
                    identity=scope_identity,
                    canonical_scope_sha256=canonical_hash,
                    receipt_ids=scope_receipts,
                )
                completed_scope_ids.append(str(scope_identity["scope_id"]))
            batch_elapsed = time.time() - batch_start_ts
            avg_elapsed = (time.time() - start_ts) / batch_no
            eta_seconds = max(int(avg_elapsed * (total_batches - batch_no)), 0)
            log.info(
                f"Finished batch {batch_no}/{total_batches} in {batch_elapsed:.1f}s | ETA ~{eta_seconds}s"
            )
        total_elapsed = time.time() - start_ts
        log.info(f"Universe {universe} update completed in {total_elapsed:.1f}s.")
        return {
            "status": "success",
            "mutation_count": mutation_count,
            "evidence_field_endpoints": dict(HISTORY_FIELD_ENDPOINTS) if audited else {},
            "range_start": start_date,
            "range_end": end_date,
            "symbol_count": len(codes),
            "symbols_sha256": stable_scope_hash(codes),
            "history_scope_coverage": {
                "status": (
                    "success"
                    if not audited
                    or len(completed_scope_ids) + len(inherited_scope_ids) == total_batches
                    else "failed"
                ),
                "expected_scope_count": total_batches,
                "completed_scope_ids": completed_scope_ids,
                "inherited_scope_ids": inherited_scope_ids,
            },
        }

    def _prepare_financial_exact_reuse(
        self, code: str, start_date: str, end_date: str, *, run_id: str,
        audit_store, resume_proof: Mapping[str, object], scope_key: str,
        universe: str,
    ) -> dict[str, Mapping[str, object]]:
        """Worker lane: validate exact immutable financial shards, no writes."""

        start_date = _normalize_date(start_date)
        end_date = _normalize_date(end_date)
        requested_scope = {
            "date_start": start_date,
            "date_end": end_date,
            "availability_cutoff": end_date,
            "symbol_count": 1,
            "symbols_sha256": stable_scope_hash([code]),
        }
        if start_date != end_date:
            requested_scope["symbols"] = [code]
        prepared: dict[str, Mapping[str, object]] = {}
        for endpoint_name in ("income", "balancesheet", "cashflow", "fina_indicator"):
            endpoint_scope = {
                **requested_scope,
                "query_axis": (
                    "report_period_query_axis"
                    if endpoint_name == "fina_indicator"
                    else "announcement_date_query_axis"
                ),
            }
            supplier_query = {
                "ts_code": code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": self._get_interface_fields(endpoint_name),
            }
            request_sha256 = _supplier_request_sha256(
                supplier_query,
                request_variant=FINANCIAL_AVAILABILITY_CONTRACT,
            )
            exact_scope = checkpoint_requested_scope(
                endpoint_scope,
                source="tushare",
                endpoint=endpoint_name,
                contract_version="1",
                scope_key=scope_key,
                universe=universe,
                request_variant=FINANCIAL_AVAILABILITY_CONTRACT,
                request_sha256=request_sha256,
            )
            item = audit_store.prepare_fetch_shard_reuse(
                run_id=run_id,
                resume_proof=resume_proof,
                source="tushare",
                endpoint=endpoint_name,
                contract_version="1",
                requested_scope=exact_scope,
            )
            if item is not None:
                prepared[endpoint_name] = {
                    "kind": "exact", "prepared": item,
                }
                continue
            legacy = audit_store.prepare_legacy_financial_shard(
                run_id=run_id,
                resume_proof=resume_proof,
                source="tushare",
                endpoint=endpoint_name,
                contract_version="1",
                requested_scope=exact_scope,
                legacy_request_sha256=_supplier_request_sha256(supplier_query),
                response_validator=lambda raw, name=endpoint_name: (
                    self._financial_response_error(
                        raw,
                        endpoint_name=name,
                        ts_code=code,
                        start_date=start_date,
                        end_date=end_date,
                        availability_cutoff=end_date,
                        exact_ann_date=None,
                    )
                ),
                contract_name=FINANCIAL_AVAILABILITY_CONTRACT,
            )
            if legacy["status"] == "compatible":
                prepared[endpoint_name] = {
                    "kind": "legacy", "prepared": legacy,
                }
                continue
            if legacy["status"] == "incompatible":
                raise _LocalResumeMiss(
                    f"legacy {endpoint_name} shard requires supplier repair"
                )
            raise _LocalResumeMiss(
                f"no reusable local shard for {endpoint_name}"
            )
        return prepared

    def _fetch_financials_batch(
        self, code_str, start_date, end_date, *, run_id=None, audit_store=None,
        resume_proof=None, scope_key="ad_hoc", universe="ad_hoc",
        local_max_workers=1,
    ):
        start_date = _normalize_date(start_date)
        end_date = _normalize_date(end_date)
        if start_date is None or end_date is None or not code_str:
            return pd.DataFrame()

        codes = code_str.split(",") if isinstance(code_str, str) else code_str
        frames = []
        workers = max(1, min(int(local_max_workers), 8, len(codes)))
        local_plans: dict[str, Mapping[str, Mapping[str, object]]] = {}

        if resume_proof is not None and audit_store is not None and workers > 1:
            # Validate the chain once on the writer thread.  Workers only read
            # immutable payloads and prepare exact reuse objects.
            audit_store._validated_resume_chain(resume_proof)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = {
                    executor.submit(
                        self._prepare_financial_exact_reuse,
                        code, start_date, end_date,
                        run_id=run_id,
                        audit_store=audit_store,
                        resume_proof=resume_proof,
                        scope_key=scope_key,
                        universe=universe,
                    ): code
                    for code in codes
                }
                for future in as_completed(pending):
                    code = pending[future]
                    try:
                        local_plans[code] = future.result()
                    except _LocalResumeMiss:
                        pass

        # Commit/reproject/fetch each code in its original order.  Exact local
        # plans avoid supplier calls; missing or legacy shards use the existing
        # single remote/reprojection lane.
        remote_count = 0
        for code in codes:
            plan = local_plans.get(code)
            if plan is None:
                if remote_count > 0:
                    time.sleep(0.3)
                remote_count += 1
            frame = self._fetch_financials(
                start_date,
                end_date,
                ts_code=code,
                run_id=run_id,
                audit_store=audit_store,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                prepared_reuse=plan,
            )
            if frame is not None and not frame.empty:
                frames.append(frame)

        frames = [frame for frame in frames if not frame.empty and not frame.isna().all().all()]
        if not frames:
            return pd.DataFrame()

        merged = pd.concat(frames, ignore_index=True)
        subset_cols = ["ts_code", "availability_date"]
        if "end_date" in merged.columns:
            subset_cols.append("end_date")
        return merged.drop_duplicates(subset=subset_cols, keep="last")


    def _update_batch_by_year(
        self, code_list, code_str, start_date, end_date, include_basic=True,
        include_limit=True, include_adj=True, include_moneyflow=True,
        include_margin=True, *, run_id=None, audit_store=None,
        resume_proof=None, scope_key="ad_hoc", evidence_universe="ad_hoc",
        local_max_workers=1,
    ):
        """
        [Optimization] 
        1. Fetch Financials, DailyBasic, StkLimit for the FULL period (per stock loop or batch period).
        2. Loop by Quarter for Daily/Adj/Moneyflow (batch fetch).
        3. Merge and Save.
        """
        # 1. Financials (Outside Loop)
        audited = run_id is not None and audit_store is not None
        fin_df_all = self._fetch_financials_batch(
            code_str, start_date, end_date, run_id=run_id,
            audit_store=audit_store, resume_proof=resume_proof,
            scope_key=scope_key, universe=evidence_universe,
            local_max_workers=local_max_workers,
        )
        
        # 2. Daily Basic (Outside Loop) -> Using the new Optimized Fetch (Stock Loop)
        df_basic_all = pd.DataFrame()
        if include_basic:
            # This will use _fetch_by_stock_loop which is FAST for subset of stocks
            df_basic_all = self._fetch_history_stock_endpoint(
                "daily_basic", code_list, start_date, end_date,
                run_id=run_id, audit_store=audit_store,
                resume_proof=resume_proof, scope_key=scope_key,
                universe=evidence_universe,
                evidence_fields=("pe", "pb", "total_mv", "turnover_rate", "circ_mv"),
            ) if audited else self._fetch_by_date_range(
                "daily_basic", code_list, start_date, end_date
            )
            if df_basic_all is not None and not df_basic_all.empty:
                df_basic_all["trade_date"] = df_basic_all["trade_date"].astype(str)
        
        # 3. Limit (Outside Loop)
        df_limit_all = pd.DataFrame()
        if include_limit:
             df_limit_all = self._fetch_history_stock_endpoint(
                 "stk_limit", code_list, start_date, end_date,
                 run_id=run_id, audit_store=audit_store,
                 resume_proof=resume_proof, scope_key=scope_key,
                 universe=evidence_universe, required_endpoint=False,
             ) if audited else self._fetch_by_date_range(
                 "stk_limit", code_list, start_date, end_date
             )
             if df_limit_all is not None and not df_limit_all.empty:
                df_limit_all["trade_date"] = df_limit_all["trade_date"].astype(str)

        # 4. Margin (Outside Loop) — fetch once for the full period instead of per chunk
        df_margin_all = pd.DataFrame()
        if include_margin:
            df_margin_all = self._fetch_history_stock_endpoint(
                "margin", code_list, start_date, end_date,
                run_id=run_id, audit_store=audit_store,
                resume_proof=resume_proof, scope_key=scope_key,
                universe=evidence_universe,
                evidence_fields=("rzye", "rzmre", "rzche"),
                required_endpoint=False,
            ) if audited else self._fetch_by_date_range(
                "margin", code_list, start_date, end_date
            )
            if df_margin_all is not None and not df_margin_all.empty and "trade_date" in df_margin_all.columns:
                df_margin_all["trade_date"] = df_margin_all["trade_date"].astype(str)

        # Get List Date Map
        stock_df = self.store.get_stock_list()
        list_date_map = {}
        if stock_df is not None and not stock_df.empty and "ts_code" in stock_df.columns and "list_date" in stock_df.columns:
            list_date_series = stock_df.set_index("ts_code")["list_date"].astype(str)
            list_date_map = list_date_series.to_dict()

        # Loop Control
        curr_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        mutation_count = 0
        history_frames: list[pd.DataFrame] = []
        history_ignore_columns: list[set[str]] = []

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
            if audited:
                # The request scope must also evidence pre-listing emptiness;
                # supplier rows stay empty until the instrument exists.
                valid_codes = list(code_list)
                valid_code_str = code_str
            elif list_date_map:
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
                requested_scope = {
                    "date_start": chunk_start,
                    "date_end": chunk_end,
                    "symbol_count": len(valid_codes),
                    "symbols": sorted(valid_codes),
                    "symbols_sha256": stable_scope_hash(valid_codes),
                }
                # 1. Daily (Batch)
                if audited:
                    df_daily, _ = self._fetch_daily_endpoint_with_receipt(
                        "daily", run_id=run_id, audit_store=audit_store,
                        requested_scope=requested_scope,
                        resume_proof=resume_proof, scope_key=scope_key,
                        universe=evidence_universe,
                        evidence_fields=("open", "high", "low", "close", "volume", "amount"),
                        required_column_groups=(
                            ("open",), ("high",), ("low",), ("close",),
                            ("vol", "volume"),
                        ),
                        ts_code=valid_code_str, start_date=chunk_start,
                        end_date=chunk_end,
                        fields=self._get_interface_fields("daily"),
                    )
                else:
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
                    if audited:
                        df_adj, _ = self._fetch_daily_endpoint_with_receipt(
                            "adj_factor", run_id=run_id,
                            audit_store=audit_store,
                            requested_scope=requested_scope,
                            resume_proof=resume_proof, scope_key=scope_key,
                            universe=evidence_universe,
                            evidence_fields=("factor",),
                            required_column_groups=(("adj_factor", "factor"),),
                            ts_code=valid_code_str, start_date=chunk_start,
                            end_date=chunk_end,
                            fields=self._get_interface_fields("adj_factor"),
                        )
                    else:
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
                    if audited:
                        df_moneyflow, _ = self._fetch_daily_endpoint_with_receipt(
                            "moneyflow", run_id=run_id,
                            audit_store=audit_store,
                            requested_scope=requested_scope,
                            resume_proof=resume_proof, scope_key=scope_key,
                            universe=evidence_universe,
                            required_endpoint=False,
                            ts_code=valid_code_str, start_date=chunk_start,
                            end_date=chunk_end,
                            fields=self._get_interface_fields("moneyflow"),
                        )
                    else:
                        df_moneyflow = self._fetch_with_retry(
                            self._get_interface_api("moneyflow"),
                            ts_code=valid_code_str,
                            start_date=chunk_start,
                            end_date=chunk_end,
                            fields=self._get_interface_fields("moneyflow"),
                        )

                # 3.5 Margin — subset from pre-fetched full-period data
                df_margin = pd.DataFrame()
                if include_margin and df_margin_all is not None and not df_margin_all.empty:
                    required_margin_keys = {"ts_code", "trade_date"}
                    if required_margin_keys.issubset(df_margin_all.columns):
                        df_margin = df_margin_all[
                            df_margin_all["trade_date"].between(chunk_start, chunk_end)
                            & df_margin_all["ts_code"].isin(valid_codes)
                        ].copy()
                    else:
                        log.warning(
                            "Skip margin subset for %s-%s: missing keys %s",
                            chunk_start,
                            chunk_end,
                            sorted(required_margin_keys - set(df_margin_all.columns)),
                        )

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
                    df_daily = self._merge_trade_frames(df_daily, df_basic, keys=['ts_code', 'trade_date'])
                if not df_adj.empty:
                    df_daily = self._merge_trade_frames(df_daily, df_adj, keys=['ts_code', 'trade_date'])
                if not df_limit.empty:
                    df_daily = self._merge_trade_frames(df_daily, df_limit, keys=['ts_code', 'trade_date'])
                
                if include_moneyflow and not df_moneyflow.empty:
                    cols_to_numeric = ["buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
                    for c in cols_to_numeric:
                        if c in df_moneyflow.columns:
                            df_moneyflow[c] = pd.to_numeric(df_moneyflow[c], errors="coerce")
                    
                    df_moneyflow["big_inflow"] = df_moneyflow["buy_elg_amount"] - df_moneyflow["sell_elg_amount"]
                    df_moneyflow["net_inflow"] = df_moneyflow["net_mf_amount"]
                    
                    keep_cols = ["ts_code", "trade_date"] + self.moneyflow_fields + self._moneyflow_derived
                    keep_cols = [c for c in keep_cols if c in df_moneyflow.columns]
                    df_daily = self._merge_trade_frames(df_daily, df_moneyflow[keep_cols], keys=['ts_code', 'trade_date'])

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

                # Keep quarterly source receipts, but coalesce the derived
                # frames before touching canonical storage.  The former path
                # rewrote each symbol's complete feather once per quarter
                # (roughly 51 times for a full-history repair).  The final
                # frame is equivalent because chunks do not overlap and the
                # same validation, deduplication, sort, and financial forward
                # fill still run in _save_batch_results.
                history_frames.append(df_daily)
                history_ignore_columns.append(set(ignore_columns))

            except Exception as e:
                log.error(f"Failed batch chunk {chunk_start}-{chunk_end}: {e}")
                if audited:
                    raise

            # Next chunk
            curr_dt = chunk_end_dt + timedelta(days=1)

        if history_frames:
            df_history = pd.concat(history_frames, ignore_index=True)
            # A field is globally ignorable only when every non-empty chunk
            # lacked its endpoint.  This keeps validation conservative when
            # an optional endpoint is present for only part of the history.
            ignore_columns = (
                sorted(set.intersection(*history_ignore_columns))
                if history_ignore_columns
                else []
            )
            bundle_receipt_id = None
            if audited:
                history_scope = {
                    "date_start": start_date,
                    "date_end": end_date,
                    "symbol_count": len(code_list),
                    "symbols": sorted(code_list),
                    "symbols_sha256": stable_scope_hash(code_list),
                }
                bundle_receipt_id = audit_store.record_fetch(
                    run_id=run_id,
                    source="tushare",
                    endpoint="daily_bundle",
                    status="success",
                    requested_scope=history_scope,
                    returned_rows=len(df_history),
                    attempt_count=1,
                    payload_kind="derived",
                    published_at=None,
                    observed_at=utc_now(),
                    **normalized_response_metadata(df_history),
                )
            log.info(
                f"Saving coalesced history batch ({len(df_history)} rows, "
                f"{len(history_frames)} chunks, {len(code_list)} stocks)..."
            )
            batch_mutations = self._save_batch_results(
                df_history, code_list, ignore_columns=ignore_columns,
                run_id=run_id, audit_store=audit_store,
                bundle_receipt_id=bundle_receipt_id,
                fill_financial_without_existing=len(history_frames) > 1,
            )
            mutation_count += len(batch_mutations)

        return {"status": "success", "mutation_count": mutation_count}

    def _save_batch_results(
        self, df_big, code_list, ignore_columns=None, *, run_id=None,
        audit_store=None, bundle_receipt_id=None,
        fill_financial_without_existing=False,
    ):
        if df_big is None or df_big.empty:
            return []
        mutations: list[dict] = []
        grouped = df_big.groupby('ts_code')
        financial_like_cols = [
            *self.financial_cols,
            'ann_date',
            'end_date',
        ]
        for code in code_list:
            if code not in grouped.groups:
                continue
            df_part = grouped.get_group(code).copy()
            df_part = self._validate_and_clean(df_part, code, ignore_columns=ignore_columns)
            existing_df = self.store.load_daily(code)
            has_existing = existing_df is not None and not existing_df.empty
            if has_existing:
                df_part = pd.concat([existing_df, df_part], ignore_index=True)
                df_part = df_part.drop_duplicates(subset=['trade_date'], keep='last')
                df_part = df_part.sort_values('trade_date').reset_index(drop=True)
            if has_existing or fill_financial_without_existing:
                for col in financial_like_cols:
                    if col in df_part.columns:
                        df_part[col] = df_part[col].ffill()
            saved = self.store.save_daily(df_part, code, existing_df=None) or []
            saved = [
                mutation
                for mutation in saved
                if mutation.get("mutation_type") != "noop"
            ]
            for mutation in saved:
                mutation["endpoint"] = "daily_bundle"
                mutation["fetch_receipt_id"] = bundle_receipt_id
            if audit_store is not None and run_id is not None:
                audit_store.record_mutations(run_id=run_id, mutations=saved)
            mutations.extend(saved)
        return mutations

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
                        df_daily = self._merge_trade_frames(df_daily, df_basic, keys=['ts_code', 'trade_date'])
                    if not df_adj.empty:
                        df_daily = self._merge_trade_frames(df_daily, df_adj, keys=['ts_code', 'trade_date'])
                    if not df_limit.empty:
                        df_daily = self._merge_trade_frames(df_daily, df_limit, keys=['ts_code', 'trade_date'])
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
                        df_daily = self._merge_trade_frames(df_daily, df_moneyflow, keys=['ts_code', 'trade_date'])
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
