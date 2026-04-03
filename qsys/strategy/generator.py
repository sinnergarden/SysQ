import pandas as pd
import yaml
from pathlib import Path
from typing import Any

from qsys.utils.logger import log
from qsys.feature.runtime import build_feature_panel
from qsys.model.zoo.qlib_native import QlibNativeModel


def load_model_artifact_metadata(model_path: str | Path) -> dict:
    model_dir = Path(model_path)
    meta_path = model_dir / "meta.yaml"
    metadata = {
        "model_path": str(model_dir),
        "model_name": model_dir.name,
        "feature_set_name": None,
        "feature_set_alias": None,
        "feature_ids": [],
        "native_qlib_fields": [],
    }
    if not meta_path.exists():
        return metadata

    with open(meta_path, 'r') as f:
        meta = yaml.safe_load(f) or {}

    params = meta.get("params") or {}
    metadata.update(
        {
            "model_name": meta.get("name", metadata["model_name"]),
            "feature_set_name": params.get("feature_set_name"),
            "feature_set_alias": params.get("feature_set_alias"),
            "feature_ids": list(params.get("feature_ids", []) or []),
            "native_qlib_fields": list(params.get("native_qlib_fields", []) or []),
        }
    )
    return metadata

class SignalGenerator:
    def __init__(self, model_path):
        """
        Signal Generator: Loads model artifacts and provides stateless prediction.
        """
        self.model_path = Path(model_path)
        self.meta = load_model_artifact_metadata(self.model_path)
        self.feature_set_name = self.meta.get("feature_set_name")
        self.feature_set_alias = self.meta.get("feature_set_alias")
        self.model = self._load_model()

    def _load_model(self):
        meta_path = self.model_path / "meta.yaml"
        if not meta_path.exists():
            raise FileNotFoundError(f"Meta file not found at {meta_path}")

        with open(meta_path, 'r') as f:
            meta = yaml.safe_load(f)
            
        model_name = meta.get("name", "qlib_lgbm")
        feature_config = meta.get("feature_config", [])
        dummy_model_conf = {'class': 'LGBModel', 'module_path': '', 'kwargs': {}}
        model = QlibNativeModel(name=model_name, model_config=dummy_model_conf, feature_config=feature_config)
            
        model.load(self.model_path)
        return model

    def predict(self, inference_data):
        """
        Stateless Prediction.
        inference_data: pd.DataFrame (InferenceDataView output)
        Returns: pd.Series (Index=Code, Value=Score)
        """
        log.info(f"Generating signals for {len(inference_data)} symbols...")
        scores = self.model.predict(inference_data)
        return scores

    def load_feature_frame(self, *, universe: str, start_date: str, end_date: str) -> pd.DataFrame:
        if self.feature_set_name:
            panel, _ = build_feature_panel(
                feature_set=str(self.feature_set_name),
                universe=universe,
                start_date=start_date,
                end_date=end_date,
            )
            if panel.empty:
                return panel
            panel = panel.set_index(["trade_date", "ts_code"]).sort_index()
            panel.index = panel.index.rename(["datetime", "instrument"])
            return panel.reindex(columns=self.model.feature_config)

        from qsys.data.adapter import QlibAdapter
        from qlib.data import D

        instruments = D.instruments(universe)
        features = QlibAdapter().get_features(
            instruments,
            self.model.feature_config,
            start_time=start_date,
            end_time=end_date,
        )
        return features.reindex(columns=self.model.feature_config) if hasattr(features, 'reindex') else features
