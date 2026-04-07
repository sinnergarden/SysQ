from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml
from qlib.contrib.data.handler import phase123DL
from qlib.contrib.eva.alpha import calc_ic
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.dataset.loader import QlibDataLoader

from qsys.data.adapter import QlibAdapter
from qsys.utils.logger import log

class FeatureLibrary:
    """
    Predefined feature sets.
    Research-only feature sets should not implicitly change production manifest behavior.
    """

    # Extended raw fields - A-share fundamentals and margin financing
    EXTENDED_RAW_FIELDS = [
        # Fundamentals
        "$pe",
        "$pb",
        "$total_mv",
        "$circ_mv",
        "$net_inflow",
        "$big_inflow",
        "$roe",
        "$grossprofit_margin",
        "$debt_to_assets",
        "$current_ratio",
        "$net_income",
        "$revenue",
        "$total_assets",
        "$equity",
        "$op_cashflow",
    ]
    
    # Margin financing (两融) fields - need to match adapter rename_map
    MARGIN_FIELDS = [
        "$margin_balance",    # 融资余额
        "$margin_buy_amount", # 融资买入额
        "$margin_repay_amount", # 融资偿还额
        "$margin_total_balance", # 融资融券余额
        "$lend_volume",       # 融券余量
        "$lend_sell_volume",  # 融券卖出量
        "$lend_repay_volume", # 融券偿还量
    ]
    
    @staticmethod
    def get_alpha158_config():
        config = phase123DL.get_feature_config()
        if isinstance(config, tuple) and len(config) == 2:
            return list(config[0])
        if isinstance(config, dict):
            feature = config.get("feature") or config.get("fields")
            if isinstance(feature, tuple) and len(feature) == 2:
                return list(feature[0])
            if isinstance(feature, list):
                return feature
        if isinstance(config, list):
            return config
        return list(config)

    @classmethod
    def get_alpha158_extended_config(cls):
        """Extended config: alpha158 + fundamentals (no margin yet)"""
        base = cls.get_alpha158_config()
        merged = list(base)
        for field in cls.EXTENDED_RAW_FIELDS:
            if field not in merged:
                merged.append(field)
        return merged
    
    @classmethod
    def get_alpha158_margin_extended_config(cls):
        """Margin-extended config: alpha158 + fundamentals + margin financing"""
        base = cls.get_alpha158_config()
        merged = list(base)
        for field in cls.EXTENDED_RAW_FIELDS:
            if field not in merged:
                merged.append(field)
        for field in cls.MARGIN_FIELDS:
            if field not in merged:
                merged.append(field)
        return merged

    @classmethod
    def get_research_phase1_config(cls):
        """Research config placeholder: current minimum uses extended raw feature base."""
        return cls.get_alpha158_extended_config()

    @classmethod
    def get_research_phase12_config(cls):
        """Research config placeholder: current minimum uses extended raw feature base until custom qlib build is wired."""
        return cls.get_alpha158_extended_config()

    @classmethod
    def get_research_phase123_config(cls):
        """Research config placeholder: current minimum uses margin_extended raw feature base until custom qlib build is wired."""
        return cls.get_alpha158_margin_extended_config()

    @classmethod
    def get_semantic_all_features_config(cls, meta_path: str | Path | None = None):
        """Load the full semantic feature set from the canonical model metadata."""
        if meta_path is None:
            meta_path = Path(__file__).resolve().parents[2] / "data" / "models" / "qlib_lgbm_semantic_all_features" / "meta.yaml"
        meta_path = Path(meta_path)
        if not meta_path.exists():
            raise FileNotFoundError(f"semantic_all_features meta not found: {meta_path}")
        with open(meta_path, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        feature_config = payload.get("feature_config") or []
        if not isinstance(feature_config, list) or not feature_config:
            raise ValueError(f"Invalid semantic_all_features feature_config in {meta_path}")
        return list(feature_config)

class phase123(DataHandlerLP):
    def __init__(
        self,
        instruments="csi300",
        start_time=None,
        end_time=None,
        infer_processors=None,
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        **kwargs
    ):
        if infer_processors is None:
            infer_processors = []
        if learn_processors is None:
            learn_processors = []
            
        # Extract label from kwargs if present, as parent class doesn't handle it
        label = kwargs.pop('label', None)
        self.custom_label = label

        data_loader = kwargs.pop('data_loader', None)
        if data_loader is None:
            config = {'feature': self.get_feature_config()}
            label_config = self.get_label_config()
            if label_config[0]:
                config['label'] = label_config
            data_loader = QlibDataLoader(config=cast(Any, config))

        try:
            super().__init__(
                instruments=instruments,
                start_time=start_time,
                end_time=end_time,
                data_loader=data_loader,
                infer_processors=infer_processors,
                learn_processors=learn_processors,
                process_type=process_type,
                **kwargs
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise e

    def get_feature_config(self):
        # Using the static method from FeatureLibrary to keep definitions in one place
        fields = FeatureLibrary.get_alpha158_config()
        return fields, fields

    def get_label_config(self):
        label = getattr(self, 'custom_label', None)
        if label:
            return label, [f"LABEL{i}" for i in range(len(label))]
        return [], []

class FeatureResearch:
    @staticmethod
    def rank_features_by_ic(instruments, start_time, end_time, label, feature_fields=None, topk=50, normalize=True):
        adapter = QlibAdapter()
        adapter.check_and_update()
        adapter.init_qlib()
        normalized_instruments = adapter.normalize_instruments(instruments)
        if feature_fields is None:
            feature_fields = FeatureLibrary.get_alpha158_config()
        label_fields = label if isinstance(label, list) else [label]
        label_names = [f"LABEL{i}" for i in range(len(label_fields))]
        def is_simple_field(fields):
            return all(isinstance(f, str) and "$" not in f and "(" not in f and ")" not in f for f in fields)
        def apply_cs_zscore(frame, cols):
            if not isinstance(frame.index, pd.MultiIndex):
                return frame
            if "datetime" not in frame.index.names:
                return frame
            date_level = "datetime"
            for col in cols:
                if col not in frame.columns:
                    continue
                grouped = frame[col].groupby(level=date_level)
                mean = grouped.transform("mean")
                std = grouped.transform(lambda x: x.std(ddof=0))
                std = std.replace(0, 1.0)
                frame[col] = (frame[col] - mean) / std
            return frame
        if is_simple_field(feature_fields) and is_simple_field(label_fields):
            all_fields = list(dict.fromkeys(feature_fields + label_fields))
            df = adapter.get_features(
                instruments=normalized_instruments,
                fields=all_fields,
                start_time=start_time,
                end_time=end_time
            )
            if df is None or df.empty:
                raise ValueError("D.features returned empty data")
            df = FeatureResearch._normalize_qlib_index(df)
            if normalize:
                df = df.copy()
                df = apply_cs_zscore(df, feature_fields + label_fields)
            label_col = label_fields[0]
        else:
            dh_config = {
                "start_time": start_time,
                "end_time": end_time,
                "instruments": normalized_instruments,
                "data_loader": {
                    "class": "QlibDataLoader",
                    "kwargs": {
                        "config": {
                            "feature": (feature_fields, feature_fields),
                            "label": (label_fields, label_names),
                        }
                    }
                }
            }
            if normalize:
                dh_config["infer_processors"] = [
                    {
                        "class": "RobustZScoreNorm",
                        "kwargs": {
                            "fields_group": "feature",
                            "clip_outlier": True,
                            "fit_start_time": start_time,
                            "fit_end_time": end_time
                        }
                    }
                ]
                dh_config["learn_processors"] = [
                    {"class": "DropnaLabel"},
                    {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}}
                ]
            ds = DatasetH(
                handler={
                    "class": "DataHandlerLP",
                    "module_path": "qlib.data.dataset.handler",
                    "kwargs": dh_config
                },
                segments={
                    "train": (start_time, end_time)
                }
            )
            try:
                df = ds.prepare("train", col_set="all")
            except KeyError:
                df_feat = ds.prepare("train", col_set="feature")
                df_label = ds.prepare("train", col_set="label")
                if isinstance(df_feat, list):
                    df_feat = pd.concat(df_feat, axis=0)
                if isinstance(df_label, list):
                    df_label = pd.concat(df_label, axis=0)
                df = pd.concat([df_feat, df_label], axis=1)
            if isinstance(df, list):
                df = pd.concat(df, axis=0)
            if df is None or df.empty:
                raise ValueError("D.features returned empty data")
            df = FeatureResearch._normalize_qlib_index(df)
            label_col = label_names[0]
        if label_col not in df.columns:
            raise ValueError("Label column not found in data")
        results = []
        label_series = df[label_col]
        for feat in feature_fields:
            if feat not in df.columns:
                continue
            pair = pd.concat([df[feat], label_series], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
            if len(pair) < 2:
                continue
            if isinstance(pair.index, pd.MultiIndex) and "datetime" in pair.index.names:
                date_level = "datetime"
                feat_std = pair[feat].groupby(level=date_level).transform(lambda x: x.std(ddof=0))
                label_std = pair[label_col].groupby(level=date_level).transform(lambda x: x.std(ddof=0))
                valid_mask = (feat_std > 0) & (label_std > 0)
                pair = pair[valid_mask]
                if len(pair) < 2:
                    continue
            ic, ric = calc_ic(pair[feat], pair[label_col])
            ic = ic.dropna()
            ric = ric.dropna()
            if ic.empty or ric.empty:
                continue
            ic_mean = ic.mean()
            ric_mean = ric.mean()
            ic_std = ic.std(ddof=0)
            ric_std = ric.std(ddof=0)
            results.append({
                "feature": feat,
                "ic_mean": ic_mean,
                "ic_std": ic_std,
                "icir": ic_mean / ic_std if ic_std != 0 else 0,
                "ric_mean": ric_mean,
                "ric_std": ric_std,
                "ricir": ric_mean / ric_std if ric_std != 0 else 0,
                "sample_count": len(pair)
            })
        if not results:
            return pd.DataFrame()
        report = pd.DataFrame(results)
        report = report.sort_values("ricir", ascending=False)
        if topk:
            report = report.head(topk)
        return report

    @staticmethod
    def _normalize_qlib_index(df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.index, pd.MultiIndex):
            return df
        names = list(df.index.names)
        if set(names) == {"trade_date", "ts_code"}:
            df = df.rename_axis(index={"trade_date": "datetime", "ts_code": "instrument"})
            names = list(df.index.names)
        if set(names) == {"datetime", "instrument"} and names != ["datetime", "instrument"]:
            df = df.swaplevel("instrument", "datetime").sort_index()
        return df
