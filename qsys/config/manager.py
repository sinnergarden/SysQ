import os
import yaml
from pathlib import Path

class ConfigManager:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        # Determine project root (assuming this file is in qsys/config/)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        config_path = self.project_root / "config" / "settings.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found at {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f) or {}

        self._init_directories()

    def _init_directories(self):
        data_root = self.project_root / self._config.get("data_root", "data")
        
        # Define required subdirectories
        self.dirs = {
            "root": data_root,
            "raw": data_root / "raw",
            "raw_daily": data_root / "raw" / "daily",
            "meta": data_root / "meta",
            "db": data_root, # db usually sits in root or specific db folder
            "qlib_bin": data_root / "qlib_bin",
            "feature": data_root / "feature",
            "clean": data_root / "clean"
        }

        # Create directories
        for path in self.dirs.values():
            path.mkdir(parents=True, exist_ok=True)

    def get(self, key, default=None):
        return self._config.get(key, default)

    @property
    def data_root(self):
        return self.dirs["root"]

    def get_path(self, key):
        return self.dirs.get(key)

# Global instance
cfg = ConfigManager()
