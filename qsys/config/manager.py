import os
import yaml
from pathlib import Path

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
        config_path = self.project_root / "config" / "settings.yaml"
        example_path = self.project_root / "config" / "settings.example.yaml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found at {config_path}. Copy {example_path} to settings.yaml and fill in your local secrets."
            )

        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f) or {}

        self._init_directories()

    def _init_directories(self):
        config = self._config or {}
        data_root = self.project_root / config.get("data_root", "data")
        
        # Define required subdirectories
        self.dirs = {
            "root": data_root,
            "raw": data_root / "raw",
            "raw_daily": data_root / "raw" / "daily",
            "meta": data_root / "meta",
            "db": data_root, # db usually sits in root or specific db folder
            "qlib_bin": data_root / "qlib_bin",
            "feature": data_root / "feature",
            "clean": data_root / "clean"
        }

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
        return self.dirs.get(key)

    def get_tushare_feature_config(self):
        config = self._config or {}
        value = config.get("tushare_feature_config")
        if isinstance(value, dict):
            return value
        return {
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
                        "fields": "ts_code,ann_date,end_date,n_income,revenue,oper_cost",
                    },
                    "balancesheet": {
                        "interface": "balancesheet",
                        "fields": "ts_code,ann_date,end_date,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab",
                    },
                    "cashflow": {
                        "interface": "cashflow",
                        "fields": "ts_code,ann_date,end_date,n_cashflow_act",
                    },
                    "fina_indicator": {
                        "interface": "fina_indicator",
                        "fields": "ts_code,ann_date,end_date,roe,roe_waa,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy",
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
                ]
            }
        }

# Global instance
cfg = ConfigManager()
