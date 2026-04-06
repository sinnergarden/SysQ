from qsys.model.base import IModel
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.utils import init_instance_by_config
import pandas as pd
import numpy as np
from qsys.utils.logger import log


class MissingPreprocessParamsError(ValueError):
    pass


class QlibNativeModel(IModel):
    def __init__(self, name, model_config, feature_config, label_config=None):
        """
        Wrapper for Qlib's native models (e.g. LGBModel, XGBModel).
        model_config: dict, e.g. {'class': 'LGBModel', 'module_path': 'qlib.contrib.model.gbdt', 'kwargs': {...}}
        """
        super().__init__(name, model_config.get('kwargs', {}))
        self.model_config = model_config
        self.feature_config = self._normalize_fields(feature_config)
        self.label_config = self._normalize_label(label_config or ["(Ref($close, -5) / Ref($close, -1) - 1)"])
        # Note: Qlib default label is usually Ref($close, -2)/Ref($close, -1) - 1 (T+1 return)
        
    def _normalize_fields(self, feature_config):
        if feature_config is None:
            return []
        if isinstance(feature_config, tuple) and len(feature_config) == 2:
            return list(feature_config[0])
        if isinstance(feature_config, dict):
            fields = feature_config.get("feature") or feature_config.get("fields") or []
            if isinstance(fields, tuple) and len(fields) == 2:
                return list(fields[0])
            return list(fields)
        return list(feature_config)

    def _normalize_label(self, label_config):
        if label_config is None:
            return []
        if isinstance(label_config, tuple) and len(label_config) == 2:
            return list(label_config[0])
        if isinstance(label_config, dict):
            fields = label_config.get("label") or label_config.get("fields") or []
            if isinstance(fields, tuple) and len(fields) == 2:
                return list(fields[0])
            return list(fields)
        return list(label_config)

    def _normalize_processor_columns(self, cols):
        normalized = []
        for col in cols:
            if isinstance(col, tuple):
                if len(col) == 2 and col[0] == "feature":
                    normalized.append(col[1])
                else:
                    normalized.append("__".join(map(str, col)))
            else:
                normalized.append(col)
        return normalized

    def fit(self, universe, start_date, end_date):
        log.info(f"Training Qlib Native Model: {self.name}")
        
        # Ensure Qlib is initialized
        from qsys.data.adapter import QlibAdapter
        QlibAdapter().init_qlib()
        
        # 1. Prepare DataHandler Config
        # DataHandlerLP (phase123) uses QlibDataLoader by default.
        # We need to pass feature/label config via 'data_loader' argument, NOT top-level kwargs.
        
        feature_fields = self.feature_config
        label_fields = self.label_config
        dh_config = {
            "start_time": start_date,
            "end_time": end_date,
            "instruments": universe,
            "infer_processors": [
                {
                    "class": "RobustZScoreNorm", 
                    "kwargs": {
                        "fields_group": "feature", 
                        "clip_outlier": True,
                        "fit_start_time": start_date,
                        "fit_end_time": end_date
                    }
                },
                {
                    "class": "Fillna",
                    "kwargs": {
                        "fields_group": "feature",
                        "fill_value": 0,
                    },
                },
            ],
            "learn_processors": [
                {"class": "DropnaLabel"},
                {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}}
            ],
            # Pass loader configuration
            "data_loader": {
                "class": "QlibDataLoader",
                "kwargs": {
                    "config": {
                        "feature": (feature_fields, feature_fields),
                        "label": (label_fields, label_fields),
                    },
                },
            },
        }
        
        # 2. Initialize Dataset
        # This will load data from Qlib bin
        ds = DatasetH(
            handler={
                "class": "DataHandlerLP",
                "module_path": "qlib.data.dataset.handler",
                "kwargs": dh_config
            },
            segments={
                "train": (start_date, end_date)
            }
        )
        
        # 3. Initialize Model
        self.model = init_instance_by_config(self.model_config)
        
        # 4. Fit
        self.model.fit(ds)

        train_df = ds.prepare("train", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
        feature_frame = train_df["feature"] if isinstance(train_df.columns, pd.MultiIndex) else train_df[self.feature_config]
        label_frame = train_df["label"] if isinstance(train_df.columns, pd.MultiIndex) else train_df[self.label_config]
        label_series = label_frame.iloc[:, 0] if isinstance(label_frame, pd.DataFrame) else label_frame
        booster = self.model.model if hasattr(self.model, "model") else self.model
        raw_predictions = booster.predict(feature_frame.values)
        prediction_series = pd.Series(raw_predictions, index=feature_frame.index, name="score")
        aligned = pd.concat([prediction_series, pd.Series(label_series, name="label")], axis=1).dropna()
        rank_ic = float(aligned["score"].corr(aligned["label"], method="spearman")) if not aligned.empty else float("nan")
        mse = float(((aligned["score"] - aligned["label"]) ** 2).mean()) if not aligned.empty else float("nan")
        self.training_summary = {
            "train_start": start_date,
            "train_end": end_date,
            "sample_count": int(len(aligned)),
            "feature_count": int(len(self.feature_config)),
            "label_name": self.label_config[0] if self.label_config else None,
            "mse": mse,
            "rank_ic": rank_ic,
            "score_mean": float(aligned["score"].mean()) if not aligned.empty else float("nan"),
            "label_mean": float(aligned["label"].mean()) if not aligned.empty else float("nan"),
        }
        log.info(
            f"Training summary | samples={self.training_summary['sample_count']} "
            f"features={self.training_summary['feature_count']} "
            f"mse={self.training_summary['mse']:.6f} rank_ic={self.training_summary['rank_ic']:.6f}"
        )
        
        # 5. Extract preprocessing contract from fitted Qlib processors.
        self.preprocess_params = self._extract_preprocess_params(ds)

    def _extract_preprocess_params(self, ds):
        try:
            handler = ds.handler
            infer_processors = list(getattr(handler, "_infer_processors", []) or [])
            if not infer_processors and hasattr(handler, "infer_processors"):
                infer_processors = list(getattr(handler, "infer_processors", []) or [])

            robust_proc = None
            fillna_proc = None
            for proc in infer_processors:
                name = proc.__class__.__name__
                if name == "RobustZScoreNorm":
                    robust_proc = proc
                elif name == "Fillna":
                    fillna_proc = proc

            if robust_proc is None:
                raise MissingPreprocessParamsError("RobustZScoreNorm processor not found on fitted handler")

            center = getattr(robust_proc, "mean_train", None)
            scale = getattr(robust_proc, "std_train", None)
            cols_obj = getattr(robust_proc, "cols", None)
            if cols_obj is None:
                cols = list(self.feature_config)
            else:
                cols = self._normalize_processor_columns(list(cols_obj))
            if center is None or scale is None:
                raise MissingPreprocessParamsError("RobustZScoreNorm fitted stats missing mean_train/std_train")

            center_arr = np.asarray(center, dtype=float).reshape(-1)
            scale_arr = np.asarray(scale, dtype=float).reshape(-1)
            if len(center_arr) != len(cols) or len(scale_arr) != len(cols):
                raise MissingPreprocessParamsError("RobustZScoreNorm stats shape does not match feature columns")

            clip_outlier = bool(getattr(robust_proc, "clip_outlier", True))
            fill_value = float(getattr(fillna_proc, "fill_value", 0.0)) if fillna_proc is not None else 0.0
            params = {
                "method": "qlib_robust_zscore",
                "center": dict(zip(cols, center_arr.tolist())),
                "scale": dict(zip(cols, scale_arr.tolist())),
                "fillna": fill_value,
                "clip_outlier": clip_outlier,
            }
            log.info("Extracted fitted RobustZScoreNorm params from Qlib handler.")
            return params
        except Exception as e:
            raise MissingPreprocessParamsError(f"Failed to extract preprocessing contract: {e}") from e

    def _apply_preprocess(self, X: pd.DataFrame) -> pd.DataFrame:
        params = getattr(self, "preprocess_params", None) or {}
        method = params.get("method")
        if method == "identity":
            return X.astype(float).fillna(float(params.get("fillna", 0.0)))
        if method != "qlib_robust_zscore":
            raise MissingPreprocessParamsError("Missing or unsupported preprocessing contract for inference")

        center = pd.Series(params.get("center", {}), dtype=float).reindex(X.columns)
        scale = pd.Series(params.get("scale", {}), dtype=float).reindex(X.columns)
        if center.isna().any() or scale.isna().any():
            missing = sorted(set(X.columns[center.isna() | scale.isna()]))
            raise MissingPreprocessParamsError(f"Preprocessing params missing for columns: {missing}")

        scale = scale.replace(0, 1.0)
        X = X.astype(float)
        X = (X - center) / scale
        if params.get("clip_outlier", True):
            X = X.clip(-3, 3)
        X = X.fillna(float(params.get("fillna", 0.0)))
        return X

    def predict(self, df):
        """
        Pure Python Inference.
        """
        first_feat = self.feature_config[0] if self.feature_config else None

        if not first_feat or first_feat not in df.columns:
            raise ValueError("Feature columns missing. Provide precomputed features.")
        X = self._apply_preprocess(df[self.feature_config].copy())

        # 2. Predict
        # We need to access the underlying booster to predict on DataFrame/Numpy
        # Qlib's LGBModel stores booster in self.model (which is lightgbm.Booster)
        
        try:
            # Check for GBDT models (LGBM, XGB, CatBoost)
            if hasattr(self.model, 'model'):
                # LGBModel.model is the booster
                booster = self.model.model
                # Booster.predict expects numpy or dataframe
                return pd.Series(booster.predict(X.values), index=X.index)
            else:
                # Fallback: Try calling predict directly (might fail if it expects Dataset)
                # Some Qlib models might support numpy input
                return pd.Series(self.model.predict(X.values), index=X.index)
        except Exception as e:
            log.error(f"Prediction failed: {e}")
            raise e
