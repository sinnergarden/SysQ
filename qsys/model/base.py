from abc import ABC, abstractmethod
import joblib
import yaml
from pathlib import Path
from qsys.utils.logger import log
from qsys.feature.calculator import FeatureCalculator
import pandas as pd

class IModel(ABC):
    def __init__(self, name, params=None):
        self.name = name
        self.params = params or {}
        self.model = None
        self.feature_config = None # List of expressions
        self.preprocess_params = None # Mean/Std
        self.training_summary = None

    @abstractmethod
    def fit(self, universe, start_date, end_date) -> None:
        """
        Train using Qlib ecosystem.
        universe: list of codes
        """
        pass

    @abstractmethod
    def predict(self, df) -> pd.Series:
        """
        Predict using pure python engine.
        df: DataFrame with raw columns (open, close, ...)
        Returns: Series/DataFrame with scores
        """
        pass

    def save(self, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        # Save Model
        if self.model:
            joblib.dump(self.model, path / "model.pkl")

        training_summary = self.training_summary or {}
        train_period = None
        train_start = training_summary.get("train_start")
        train_end = training_summary.get("train_end")
        if train_start and train_end:
            train_period = [str(train_start), str(train_end)]

        # Save Metadata
        meta = {
            "name": self.name,
            "params": self.params,
            "feature_config": self.feature_config,
            "preprocess_params": self.preprocess_params,
            "training_summary": self.training_summary,
            "train_period": train_period,
            "model_config": getattr(self, "model_config", None),
            "label_config": getattr(self, "label_config", None),
        }
        with open(path / "meta.yaml", 'w', encoding='utf-8') as f:
            yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)

        log.info(f"Model saved to {path}")

    def load(self, path):
        path = Path(path)
        
        # Load Model
        model_path = path / "model.pkl"
        if model_path.exists():
            self.model = joblib.load(model_path)
            
        # Load Metadata
        meta_path = path / "meta.yaml"
        if meta_path.exists():
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = yaml.safe_load(f) or {}
                self.name = meta.get("name", self.name)
                self.params = meta.get("params", {})
                self.feature_config = meta.get("feature_config", [])
                self.preprocess_params = meta.get("preprocess_params", {})
                self.training_summary = meta.get("training_summary")
                if meta.get("model_config") is not None:
                    self.model_config = meta.get("model_config")
                if meta.get("label_config") is not None:
                    self.label_config = meta.get("label_config")
        
        log.info(f"Model loaded from {path}")

    def prepare_inference_features(self, df):
        """
        Standard pipeline:
        1. Calculate Features (via FeatureCalculator)
        2. Preprocess (Z-Score using saved params)
        """
        if not self.feature_config:
            raise ValueError("Feature config is empty")

        # 1. Calc Features
        feat_df = FeatureCalculator.calculate(df, self.feature_config)
        
        # 2. Preprocess
        if self.preprocess_params:
            method = self.preprocess_params.get("method")
            if method == "qlib_robust_zscore":
                center = pd.Series(self.preprocess_params.get("center", {}), dtype=float).reindex(feat_df.columns)
                scale = pd.Series(self.preprocess_params.get("scale", {}), dtype=float).reindex(feat_df.columns).replace(0, 1.0)
                if not center.isna().any() and not scale.isna().any():
                    feat_df = (feat_df.astype(float) - center) / scale
                    if self.preprocess_params.get("clip_outlier", True):
                        feat_df = feat_df.clip(-3, 3)
                feat_df = feat_df.fillna(self.preprocess_params.get("fillna", 0.0))
            else:
                mean = self.preprocess_params.get("mean")
                std = self.preprocess_params.get("std")
                if mean is not None and std is not None:
                    feat_df = (feat_df - mean) / std
        
        # Fill NaNs
        feat_df = feat_df.fillna(0)
        
        return feat_df
