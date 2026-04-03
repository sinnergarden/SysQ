import unittest

import pandas as pd

from qsys.model.zoo.qlib_native import QlibNativeModel, MissingPreprocessParamsError, build_identity_sanitize_contract
from qsys.strategy.generator import load_model_artifact_metadata


class DummyBooster:
    def predict(self, values):
        return values.sum(axis=1)


class DummyWrappedModel:
    def __init__(self):
        self.model = DummyBooster()


class TestPreprocessingConsistency(unittest.TestCase):
    def _make_model(self):
        model = QlibNativeModel(
            name="test-model",
            model_config={"kwargs": {}},
            feature_config=["f1", "f2"],
        )
        model.model = DummyWrappedModel()
        return model

    def test_apply_preprocess_matches_saved_contract(self):
        model = self._make_model()
        model.preprocess_params = {
            "method": "qlib_robust_zscore",
            "center": {"f1": 10.0, "f2": 100.0},
            "scale": {"f1": 2.0, "f2": 10.0},
            "fillna": 0.0,
            "clip_outlier": True,
        }
        frame = pd.DataFrame({"f1": [12.0, None], "f2": [130.0, 80.0]})

        out = model._apply_preprocess(frame)

        expected = pd.DataFrame({"f1": [1.0, 0.0], "f2": [3.0, -2.0]})
        pd.testing.assert_frame_equal(out.reset_index(drop=True), expected)

    def test_predict_rejects_unsupported_preprocess_contract(self):
        model = self._make_model()
        model.preprocess_params = {"method": "unsupported", "mean": 0.0, "std": 1.0}
        frame = pd.DataFrame({"f1": [1.0], "f2": [2.0]})

        with self.assertRaises(MissingPreprocessParamsError):
            model.predict(frame)

    def test_identity_preprocess_contract_is_allowed(self):
        model = self._make_model()
        model.preprocess_params = {"method": "identity", "fillna": 0.0}
        frame = pd.DataFrame({"f1": [1.0], "f2": [2.0]})

        pred = model.predict(frame)
        self.assertEqual(float(pred.iloc[0]), 3.0)

    def test_identity_contract_sanitizes_semantic_feature_frame_for_inference(self):
        model = QlibNativeModel(
            name="semantic-model",
            model_config={"kwargs": {}},
            feature_config=["flag", "text_num", "ratio"],
        )
        model.model = DummyWrappedModel()
        _, sanitize_contract = build_identity_sanitize_contract(
            pd.DataFrame({
                "flag": [True, False],
                "text_num": ["1.5", "2.0"],
                "ratio": [0.25, -0.5],
                "constant": [7, 7],
            }),
            fill_value=0.0,
        )
        model.params = {
            "sanitize_feature_contract": sanitize_contract,
            "feature_name_map": sanitize_contract["feature_name_map"],
            "constant_feature_columns": sanitize_contract["dropped_columns"],
        }
        model.preprocess_params = {"method": "identity", "fillna": 0.0}
        frame = pd.DataFrame({
            "flag": [True, None],
            "text_num": ["3.5", "bad"],
            "ratio": [float("inf"), float("nan")],
            "constant": [7, 7],
        })

        out = model._apply_preprocess(frame.reindex(columns=model.feature_config))
        expected = pd.DataFrame({
            "f_0001": [1.0, 0.0],
            "f_0002": [3.5, 0.0],
            "f_0003": [0.0, 0.0],
        })
        pd.testing.assert_frame_equal(out.reset_index(drop=True), expected)

        pred = model.predict(frame)
        self.assertEqual(pred.round(4).tolist(), [4.5, 0.0])

    def test_save_and_load_preserve_preprocess_contract(self):
        model = self._make_model()
        model.preprocess_params = {
            "method": "qlib_robust_zscore",
            "center": {"f1": 1.0, "f2": 2.0},
            "scale": {"f1": 3.0, "f2": 4.0},
            "fillna": 0.0,
            "clip_outlier": True,
        }

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model"
            model.save(path)

            loaded = self._make_model()
            loaded.load(path)
            self.assertEqual(loaded.preprocess_params, model.preprocess_params)

    def test_save_and_load_preserve_feature_set_metadata(self):
        model = self._make_model()
        model.params = {
            "feature_set_name": "price_volume_fundamental_core_v1",
            "feature_set_alias": "extended",
            "feature_ids": ["F0001", "F0002"],
            "native_qlib_fields": ["$open", "$close"],
            "sanitize_feature_contract": {
                "raw_feature_columns": ["f1", "f2"],
                "feature_name_map": {"f1": "f_0001", "f2": "f_0002"},
                "sanitized_feature_columns": ["f_0001", "f_0002"],
                "dropped_columns": ["constant"],
                "fillna": 0.0,
                "method": "identity",
            },
        }
        model.training_summary = {"train_start": "2024-01-01", "train_end": "2024-12-31"}

        import tempfile
        from pathlib import Path
        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model"
            model.save(path)

            loaded = self._make_model()
            loaded.load(path)
            self.assertEqual(loaded.params["feature_set_name"], "price_volume_fundamental_core_v1")
            self.assertEqual(loaded.params["feature_set_alias"], "extended")
            self.assertEqual(loaded.params["feature_ids"], ["F0001", "F0002"])
            self.assertEqual(loaded.params["sanitize_feature_contract"]["dropped_columns"], ["constant"])

            meta = load_model_artifact_metadata(path)
            self.assertEqual(meta["feature_set_name"], "price_volume_fundamental_core_v1")
            self.assertEqual(meta["feature_set_alias"], "extended")
            self.assertEqual(meta["feature_ids"], ["F0001", "F0002"])
            self.assertEqual(meta["native_qlib_fields"], ["$open", "$close"])

            with open(path / "meta.yaml", "r", encoding="utf-8") as handle:
                saved_meta = yaml.safe_load(handle)
            self.assertEqual(saved_meta["train_period"], ["2024-01-01", "2024-12-31"])
            self.assertIn("model_config", saved_meta)
            self.assertIn("label_config", saved_meta)


if __name__ == "__main__":
    unittest.main()
