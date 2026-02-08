from qsys.model.base import IModel
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.utils import init_instance_by_config
import pandas as pd
import numpy as np
from qsys.utils.logger import log

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

    def fit(self, universe, start_date, end_date):
        log.info(f"Training Qlib Native Model: {self.name}")
        
        # Ensure Qlib is initialized
        from qsys.data.adapter import QlibAdapter
        QlibAdapter().init_qlib()
        
        # 1. Prepare DataHandler Config
        # DataHandlerLP (Alpha158) uses QlibDataLoader by default.
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
                }
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
        
        # 5. Extract Preprocess Params (RobustZScoreNorm)
        # We need these to replicate preprocessing during inference (Pure Python)
        try:
            handler = ds.handler
            
            # Debug: Inspect handler properties
            # log.info(f"Handler keys: {handler.__dict__.keys()}")
            
            proc = None
            
            # Strategy 1: Look into _infer_processors (Qlib standard)
            infer_processors = getattr(handler, "_infer_processors", [])
            if infer_processors:
                for p in infer_processors:
                    if p.__class__.__name__ == 'RobustZScoreNorm':
                        proc = p
                        break
            
            # Strategy 2: Look into data_loader (if configured there) - Unlikely for ZScoreNorm
            
            # Strategy 3: Check fetch_kwargs (sometimes Qlib stores it here?)
            
            if proc and hasattr(proc, 'mean') and hasattr(proc, 'std'):
                self.preprocess_params = {
                    "mean": proc.mean.to_dict() if hasattr(proc.mean, 'to_dict') else proc.mean,
                    "std": proc.std.to_dict() if hasattr(proc.std, 'to_dict') else proc.std
                }
                log.info("Extracted preprocessing parameters from Qlib handler.")
            else:
                log.warning("Could not extract mean/std from processor. Using Identity preprocessing.")
                # Fallback to Identity to avoid crash
                self.preprocess_params = {"mean": 0.0, "std": 1.0}
                
        except Exception as e:
            log.warning(f"Failed to extract preprocess params: {e}")
            self.preprocess_params = {"mean": 0.0, "std": 1.0}

    def predict(self, df):
        """
        Pure Python Inference.
        """
        # 1. Feature Calc & Preprocess (using extracted params)
        # If df already contains feature columns (from D.features), we just preprocess.
        # If df contains raw columns (open, close), we calculate first.
        
        # Check if first feature name exists in columns
        first_feat = self.feature_config[0] if self.feature_config else None
        
        if not first_feat or first_feat not in df.columns:
            raise ValueError("Feature columns missing. Provide precomputed features.")
        X = df[self.feature_config]
            
        # Apply RobustZScoreNorm manually
        if hasattr(self, 'preprocess_params'):
            mean = self.preprocess_params.get('mean')
            std = self.preprocess_params.get('std')
            
            if mean is None or std is None:
                mean = 0.0
                std = 1.0

            # Convert to float to avoid object type issues
            X = X.astype(float)
            
            # If mean/std are scalars (Identity fallback)
            if isinstance(mean, (int, float)) and isinstance(std, (int, float)):
                X = (X - mean) / std
            else:
                try:
                    mean_series = pd.Series(mean).reindex(X.columns).fillna(0.0)
                    std_series = pd.Series(std).reindex(X.columns).fillna(1.0).replace(0, 1.0)
                    X = (X - mean_series) / std_series
                except Exception as e:
                     log.warning(f"Preprocessing failed during predict: {e}")
        
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
