import unittest

import pandas as pd

from qsys.model.zoo.qlib_native import QlibNativeModel, MissingPreprocessParamsError
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

    def test_predict_requires_non_identity_contract(self):
        model = self._make_model()
        model.preprocess_params = {"mean": 0.0, "std": 1.0}
        frame = pd.DataFrame({"f1": [1.0], "f2": [2.0]})

        with self.assertRaises(MissingPreprocessParamsError):
            model.predict(frame)

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
        }

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model"
            model.save(path)

            loaded = self._make_model()
            loaded.load(path)
            self.assertEqual(loaded.params["feature_set_name"], "price_volume_fundamental_core_v1")
            self.assertEqual(loaded.params["feature_set_alias"], "extended")
            self.assertEqual(loaded.params["feature_ids"], ["F0001", "F0002"])

            meta = load_model_artifact_metadata(path)
            self.assertEqual(meta["feature_set_name"], "price_volume_fundamental_core_v1")
            self.assertEqual(meta["feature_set_alias"], "extended")
            self.assertEqual(meta["feature_ids"], ["F0001", "F0002"])
            self.assertEqual(meta["native_qlib_fields"], ["$open", "$close"])


if __name__ == "__main__":
    unittest.main()
