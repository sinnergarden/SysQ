from abc import ABC, abstractmethod
import joblib
import pickle
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
            
        # Save Metadata
        meta = {
            "name": self.name,
            "params": self.params,
            "feature_config": self.feature_config,
            "preprocess_params": self.preprocess_params
        }
        with open(path / "meta.yaml", 'w') as f:
            yaml.dump(meta, f)
            
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
            with open(meta_path, 'r') as f:
                meta = yaml.safe_load(f)
                self.name = meta.get("name", self.name)
                self.params = meta.get("params", {})
                self.feature_config = meta.get("feature_config", [])
                self.preprocess_params = meta.get("preprocess_params", {})
        
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
        
        # 2. Preprocess (Simple Z-Score)
        if self.preprocess_params:
            mean = self.preprocess_params.get("mean")
            std = self.preprocess_params.get("std")
            if mean is not None and std is not None:
                feat_df = (feat_df - mean) / std
                
        # Fill NaNs
        feat_df = feat_df.fillna(0)
        
        return feat_df
