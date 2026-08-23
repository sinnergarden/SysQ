import os
import tempfile
import unittest
from pathlib import Path

from qsys.config.manager import ConfigManager


class TestConfigEnvOverride(unittest.TestCase):
    def test_qsys_qlib_bin_env_override_applies(self):
        previous = os.environ.get("QSYS_QLIB_BIN")
        previous_instance = ConfigManager._instance
        previous_config = ConfigManager._config
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                override = Path(tmpdir) / "candidate_qlib"
                os.environ["QSYS_QLIB_BIN"] = str(override)
                ConfigManager._instance = None
                ConfigManager._config = None
                manager = ConfigManager()
                self.assertEqual(manager.get_path("qlib_bin"), override)
                self.assertTrue(override.exists())
        finally:
            if previous is None:
                os.environ.pop("QSYS_QLIB_BIN", None)
            else:
                os.environ["QSYS_QLIB_BIN"] = previous
            ConfigManager._instance = previous_instance
            ConfigManager._config = previous_config

    def test_settings_and_data_root_env_overrides_apply(self):
        previous_settings = os.environ.get("QSYS_SETTINGS_FILE")
        previous_data_root = os.environ.get("QSYS_DATA_ROOT")
        previous_instance = ConfigManager._instance
        previous_config = ConfigManager._config
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                settings = root / "runtime-settings.yaml"
                settings.write_text(
                    "data_root: ignored-data-root\n"
                    "canonical_dir: data/canonical/daily\n",
                    encoding="utf-8",
                )
                data_root = root / "data"
                os.environ["QSYS_SETTINGS_FILE"] = str(settings)
                os.environ["QSYS_DATA_ROOT"] = str(data_root)
                ConfigManager._instance = None
                ConfigManager._config = None

                manager = ConfigManager()

                self.assertEqual(manager.get_path("root"), data_root)
                self.assertEqual(
                    manager.get_path("canonical_dir"),
                    data_root / "canonical" / "daily",
                )
        finally:
            if previous_settings is None:
                os.environ.pop("QSYS_SETTINGS_FILE", None)
            else:
                os.environ["QSYS_SETTINGS_FILE"] = previous_settings
            if previous_data_root is None:
                os.environ.pop("QSYS_DATA_ROOT", None)
            else:
                os.environ["QSYS_DATA_ROOT"] = previous_data_root
            ConfigManager._instance = previous_instance
            ConfigManager._config = previous_config


if __name__ == "__main__":
    unittest.main()
