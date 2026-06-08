"""Shared pytest fixtures for SysQ tests.

Usage in new tests::

    def test_something(data_dir):
        # data_dir is a Path to a temp dir with cfg.dirs configured
        ...
"""
import tempfile
from pathlib import Path

import pytest

from qsys.config import cfg

_BASE_DIRS = {
    "root": ".",
    "raw": "raw",
    "raw_daily": "raw/daily",
    "canonical_dir": "canonical/daily",
    "meta": "meta",
    "db": ".",
    "qlib_bin": "qlib_bin",
    "feature": "feature",
    "clean": "clean",
}


@pytest.fixture
def data_dir():
    """Create a temporary directory tree with cfg.dirs populated.

    Restores the original *cfg.dirs* on teardown.
    """
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    original_dirs = cfg.dirs.copy()
    cfg.dirs = {key: root / val for key, val in _BASE_DIRS.items()}
    for path in cfg.dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    yield root
    cfg.dirs = original_dirs
    tmp.cleanup()
