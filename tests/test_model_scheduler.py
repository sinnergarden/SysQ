import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from qsys.config import cfg
from qsys.live.scheduler import ModelScheduler


class TestModelScheduler(unittest.TestCase):
    def test_check_and_retrain_returns_expected_models_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_root = root / "data"
            old_model_path = root / "experiments" / "stale_model"
            expected_model_path = data_root / "models" / "qlib_lgbm_extended"
            old_model_path.mkdir(parents=True)
            expected_model_path.parent.mkdir(parents=True)
            with open(old_model_path / "meta.yaml", "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    {
                        "name": "qlib_lgbm_extended",
                        "params": {"feature_set_name": "price_volume_fundamental_core_v1"},
                        "training_summary": {"train_end": "2024-01-01"},
                    },
                    handle,
                    sort_keys=False,
                )

            def fake_retrain(cmd):
                expected_model_path.mkdir(parents=True, exist_ok=True)
                with open(expected_model_path / "meta.yaml", "w", encoding="utf-8") as handle:
                    yaml.safe_dump({"name": "qlib_lgbm_extended"}, handle)

            with patch.object(cfg, "get_path", return_value=data_root), patch(
                "qsys.live.scheduler.subprocess.check_call",
                side_effect=fake_retrain,
            ):
                resolved = ModelScheduler.check_and_retrain(
                    str(old_model_path),
                    current_date="2024-01-20",
                    retrain_freq_days=7,
                )

            self.assertEqual(resolved, str(expected_model_path))


if __name__ == "__main__":
    unittest.main()
