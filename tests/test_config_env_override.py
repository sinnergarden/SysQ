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


if __name__ == "__main__":
    unittest.main()
