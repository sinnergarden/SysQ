import os
import yaml
from pathlib import Path


_TUSHARE_FINANCIAL_EVIDENCE_FIELDS = {
    "income": ("f_ann_date", "report_type", "comp_type", "end_type", "update_flag"),
    "balancesheet": ("f_ann_date", "report_type", "comp_type", "end_type", "update_flag"),
    "cashflow": ("f_ann_date", "report_type", "comp_type", "end_type", "update_flag"),
    # Tushare fina_indicator does not expose statement report metadata.  Its
    # only supplier-supported revision field is update_flag.
    "fina_indicator": ("update_flag",),
}


def _resolve_env_placeholders(value):
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, str) and value.startswith("ENV:"):
        return os.environ.get(value[4:], "")
    return value

class ConfigManager:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        # Determine project root (assuming this file is in qsys/config/)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        settings_override = os.environ.get("QSYS_SETTINGS_FILE", "").strip()
        config_path = (
            Path(settings_override).expanduser()
            if settings_override
            else self.project_root / "config" / "settings.yaml"
        )
        example_path = self.project_root / "config" / "settings.example.yaml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found at {config_path}. Copy {example_path} to settings.yaml and fill in your local secrets."
            )

        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = _resolve_env_placeholders(yaml.safe_load(f) or {})

        self._init_directories()

    def _init_directories(self):
        config = self._config or {}
        data_root_override = os.environ.get("QSYS_DATA_ROOT", "").strip()
        if data_root_override:
            data_root = Path(data_root_override).expanduser()
            if not data_root.is_absolute():
                data_root = (self.project_root / data_root).resolve()
        else:
            data_root = self.project_root / config.get("data_root", "data")

        # canonical_dir from settings.yaml takes priority over hardcoded default
        config_canonical = config.get("canonical_dir")
        if config_canonical:
            canonical_path = Path(config_canonical)
            if not canonical_path.is_absolute():
                canonical_path = (data_root.parent / canonical_path).resolve()
        else:
            canonical_path = data_root / "canonical" / "daily"

        self.dirs = {
            "root": data_root,
            "raw": data_root / "raw",
            "raw_daily": data_root / "raw" / "daily",
            "canonical_dir": canonical_path,
            "meta": data_root / "meta",
            "db": data_root,
            "qlib_bin": data_root / "qlib_bin",
            "feature": data_root / "feature",
            "clean": data_root / "clean",
        }

        # Deprecation: warn when code accesses "raw_daily" instead of "canonical_dir"
        self._deprecated_path_keys = {"raw_daily": "canonical_dir"}

        qlib_override = os.environ.get("QSYS_QLIB_BIN", "").strip()
        if qlib_override:
            override_path = Path(qlib_override).expanduser()
            if not override_path.is_absolute():
                override_path = (self.project_root / override_path).resolve()
            self.dirs["qlib_bin"] = override_path

        # Create directories
        for path in self.dirs.values():
            path.mkdir(parents=True, exist_ok=True)

    def get(self, key, default=None):
        config = self._config or {}
        return config.get(key, default)

    @property
    def data_root(self):
        return self.dirs["root"]

    def get_path(self, key):
        if key in getattr(self, "_deprecated_path_keys", {}):
            import logging
            replacement = self._deprecated_path_keys[key]
            logging.warning(
                "cfg.get_path(%r) is deprecated. Use cfg.get_path(%r) instead. "
                "This fallback will be removed in a future version.",
                key, replacement,
            )
        return self.dirs.get(key)

    def get_tushare_feature_config(self):
        config = self._config or {}
        value = config.get("tushare_feature_config")
        if isinstance(value, dict):
            self._verify_tushare_config(value)
            return self._with_financial_evidence_fields(value)
        value = {
            "collector": {
                "expected_extra_cols": ["paused"],
                "numeric_extra_cols": ["paused"],
                "non_numeric_cols": ["trade_status"],
                "non_negative_cols": [
                    "open", "high", "low", "close", "vol", "amount",
                    "turnover_rate", "total_share", "float_share", "free_share",
                    "total_mv", "circ_mv", "adj_factor", "up_limit", "down_limit",
                ],
                "financial_cols": [
                    "net_income", "revenue", "oper_cost", "total_assets", "equity",
                    "total_cur_assets", "total_cur_liab", "roe", "op_cashflow",
                    "q_dt_profit", "q_gr_yoy", "roe_ttm", "grossprofit_margin",
                    "debt_to_assets", "current_ratio",
                ],
                "moneyflow_fields": [
                    "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
                    "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount",
                    "net_mf_amount",
                ],
                "derived_fields": {
                    "moneyflow": ["big_inflow", "net_inflow"]
                },
                "interfaces": {
                    "margin": {
                        "interface": "margin_detail",
                        "fields": "ts_code,trade_date,rzye,rzmre,rzche,rzrqye,rqyl,rqmcl,rqchl",
                        "rename": {
                            "rzye": "margin_balance",
                            "rzmre": "margin_buy_amount",
                            "rzche": "margin_repay_amount",
                            "rzrqye": "margin_total_balance",
                            "rqyl": "lend_volume",
                            "rqmcl": "lend_sell_volume",
                            "rqchl": "lend_repay_volume",
                        }
                    },
                    "income": {
                        "interface": "income",
                        "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_income,revenue,oper_cost",
                    },
                    "balancesheet": {
                        "interface": "balancesheet",
                        "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab",
                    },
                    "cashflow": {
                        "interface": "cashflow",
                        "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_cashflow_act",
                    },
                    "fina_indicator": {
                        "interface": "fina_indicator",
                        "fields": "ts_code,ann_date,end_date,update_flag,roe,roe_waa,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy",
                        "rename": {
                            "q_dtprofit": "q_dt_profit",
                        },
                    }
                },
                "margin_cols": [
                    "margin_balance", "margin_buy_amount", "margin_repay_amount",
                    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume"
                ]
            },
            "adapter": {
                "rename_map": {
                    "trade_date": "date",
                    "adj_factor": "factor",
                    "vol": "volume",
                    "up_limit": "high_limit",
                    "down_limit": "low_limit",
                    # Margin financing (两融) - already renamed in collector, but ensure consistency
                    "margin_balance": "margin_balance",
                    "margin_buy_amount": "margin_buy_amount",
                    "margin_repay_amount": "margin_repay_amount",
                    "margin_total_balance": "margin_total_balance",
                    "lend_volume": "lend_volume",
                    "lend_sell_volume": "lend_sell_volume",
                    "lend_repay_volume": "lend_repay_volume",
                },
                "qlib_fields": [
                    "open", "high", "low", "close", "volume", "amount", "factor",
                    "vwap", "paused", "high_limit", "low_limit",
                    "turnover_rate", "pe", "pb", "total_mv", "circ_mv",
                    "net_inflow", "big_inflow",
                    "net_income", "revenue", "total_assets", "equity", "roe", "op_cashflow",
                    "q_dt_profit", "q_gr_yoy", "roe_ttm", "grossprofit_margin",
                    "debt_to_assets", "current_ratio",
                    # Margin financing (两融) - first batch
                    "margin_balance", "margin_buy_amount", "margin_repay_amount",
                    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
                    "industry",
                ]
            }
        }
        return self._with_financial_evidence_fields(value)

    @staticmethod
    def _with_financial_evidence_fields(value: dict) -> dict:
        """Add supplier-supported revision fields to configured raw requests.

        Local ``settings.yaml`` files predate the raw-evidence contract and may
        still contain the narrow financial field lists.  Enrich those lists at
        the configuration boundary so request identity changes without making
        the evidence-only metadata part of canonical feature configuration.
        """
        interfaces = value.get("collector", {}).get("interfaces", {})
        if not isinstance(interfaces, dict):
            return value
        for endpoint, evidence_fields in _TUSHARE_FINANCIAL_EVIDENCE_FIELDS.items():
            item = interfaces.get(endpoint)
            if not isinstance(item, dict):
                continue
            fields = item.get("fields")
            if isinstance(fields, str):
                field_list = [field.strip() for field in fields.split(",") if field.strip()]
                for field in evidence_fields:
                    if field not in field_list:
                        field_list.append(field)
                item["fields"] = ",".join(field_list)
            elif isinstance(fields, list):
                for field in evidence_fields:
                    if field not in fields:
                        fields.append(field)
        return value

    @staticmethod
    def _verify_tushare_config(value: dict) -> None:
        """Warn if YAML tushare_feature_config is missing expected collector keys."""
        COLLECTOR_EXPECTED = {
            "expected_extra_cols", "numeric_extra_cols", "non_numeric_cols",
            "non_negative_cols", "interfaces", "margin_cols",
            "financial_cols", "moneyflow_fields", "derived_fields",
        }
        collector = value.get("collector", {})
        missing = COLLECTOR_EXPECTED - set(collector.keys())
        if missing:
            import logging
            logging.warning(
                "settings.yaml tushare_feature_config.collector missing keys: %s "
                "(falling back to per-call defaults in collector.py)",
                sorted(missing),
            )

# Global instance
cfg = ConfigManager()
