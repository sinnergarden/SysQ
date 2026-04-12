import unittest

import pandas as pd

from qsys.model.zoo.qlib_native import QlibNativeModel, MissingPreprocessParamsError


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

    def test_predict_supports_identity_contract(self):
        model = self._make_model()
        model.preprocess_params = {"method": "identity", "fillna": 0.0}
        frame = pd.DataFrame({"f1": [1.0, None], "f2": [2.0, 3.0]})

        out = model.predict(frame)

        self.assertEqual(out.tolist(), [3.0, 3.0])

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

    def test_apply_preprocess_sanitizes_nan_contract_values(self):
        model = self._make_model()
        model.preprocess_params = {
            "method": "qlib_robust_zscore",
            "center": {"f1": 10.0, "f2": float("nan")},
            "scale": {"f1": 2.0, "f2": float("nan")},
            "fillna": 0.0,
            "clip_outlier": True,
        }
        frame = pd.DataFrame({"f1": [12.0], "f2": [5.0]})

        out = model._apply_preprocess(frame)

        expected = pd.DataFrame({"f1": [1.0], "f2": [3.0]})
        pd.testing.assert_frame_equal(out.reset_index(drop=True), expected)

    def test_load_sanitizes_invalid_saved_contract_values(self):
        model = self._make_model()
        model.preprocess_params = {
            "method": "qlib_robust_zscore",
            "center": {"f1": 1.0, "f2": float("nan")},
            "scale": {"f1": 3.0, "f2": 0.0},
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
            self.assertEqual(loaded.preprocess_params["center"]["f2"], 0.0)
            self.assertEqual(loaded.preprocess_params["scale"]["f2"], 1.0)
            self.assertEqual(loaded.preprocess_params["invalid_columns"], ["f2"])


if __name__ == "__main__":
    unittest.main()
