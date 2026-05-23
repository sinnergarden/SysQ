"""Tests for qsys/common/ — business-neutral utilities."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from qsys.common.io import archive_dir, ensure_parent, read_json, write_json, write_json_atomic
from qsys.common.time import now_iso, timestamp_for_filename


class TestReadJson(unittest.TestCase):
    """read_json — returns default on missing/invalid, parsed content on success."""

    def test_missing_file_returns_default(self):
        result = read_json("/tmp/nonexistent_file_for_test.json", default="fallback")
        self.assertEqual(result, "fallback")

    def test_missing_file_defaults_to_none(self):
        result = read_json("/tmp/nonexistent_file_for_test.json")
        self.assertIsNone(result)

    def test_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"a": 1, "b": [2, 3]}')
            f.flush()
            path = f.name
        try:
            result = read_json(path)
            self.assertEqual(result, {"a": 1, "b": [2, 3]})
        finally:
            os.unlink(path)

    def test_invalid_json_returns_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid")
            f.flush()
            path = f.name
        try:
            result = read_json(path, default={})
            self.assertEqual(result, {})
        finally:
            os.unlink(path)

    def test_pathlib_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"x": 1}')
            f.flush()
            path = Path(f.name)
        try:
            result = read_json(path)
            self.assertEqual(result, {"x": 1})
        finally:
            os.unlink(str(path))


class TestWriteJson(unittest.TestCase):
    """write_json — writes pretty-printed JSON with trailing newline."""

    def test_writes_pretty_json(self):
        data = {"z": 1, "a": 2}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            write_json(path, data)
            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            parsed = json.loads(raw)
            self.assertEqual(parsed, {"a": 2, "z": 1})  # sort_keys=True
        finally:
            os.unlink(str(path))

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b" / "test.json"
            write_json(nested, {"key": "val"})
            self.assertTrue(nested.exists())
            self.assertEqual(json.loads(nested.read_text()), {"key": "val"})

    def test_ensure_ascii_false_handles_cjk(self):
        data = {"name": "测试"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            write_json(path, data)
            raw = path.read_text(encoding="utf-8")
            self.assertIn("测试", raw)
        finally:
            os.unlink(str(path))

    def test_returns_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            result = write_json(path, {"x": 1})
            self.assertEqual(result, path)
        finally:
            os.unlink(str(path))


class TestWriteJsonAtomic(unittest.TestCase):
    """write_json_atomic — tempfile + rename prevents partial writes."""

    def test_atomic_write(self):
        data = {"atomic": True, "list": [1, 2, 3]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            write_json_atomic(path, data)
            parsed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parsed, data)
        finally:
            os.unlink(str(path))

    def test_atomic_write_creates_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "x" / "y" / "atomic.json"
            write_json_atomic(nested, {"ok": True})
            self.assertTrue(nested.exists())

    def test_atomic_write_handles_cjk(self):
        data = {"msg": "原子写入"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            write_json_atomic(path, data)
            raw = path.read_text(encoding="utf-8")
            self.assertIn("原子写入", raw)
        finally:
            os.unlink(str(path))


class TestEnsureParent(unittest.TestCase):
    """ensure_parent — creates parent directories, returns path."""

    def test_creates_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "deep" / "dir" / "file.txt"
            result = ensure_parent(nested)
            self.assertTrue(nested.parent.exists())
            self.assertEqual(result, nested)

    def test_noop_when_parent_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "existing.txt"
            result = ensure_parent(f)
            self.assertEqual(result, f)


class TestArchiveDir(unittest.TestCase):
    """archive_dir — moves src into dst_root with timestamp."""

    def test_archives_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sourcedir"
            src.mkdir()
            (src / "file.txt").write_text("hello")
            dst_root = Path(tmp) / "archive"
            result = archive_dir(src, dst_root, prefix="test")
            self.assertFalse(src.exists())
            self.assertTrue(result.exists())
            self.assertTrue((result / "file.txt").exists())
            self.assertIn("test_", result.name)

    def test_raises_on_missing_src(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "nonexistent"
            dst_root = Path(tmp) / "archive"
            with self.assertRaises(FileNotFoundError):
                archive_dir(src, dst_root)


class TestTimeUtils(unittest.TestCase):
    """time.py — ISO and filename-safe timestamps."""

    def test_now_iso_format(self):
        ts = now_iso()
        # ISO-8601: 2026-05-23T12:34:56.789...
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_timestamp_for_filename_format(self):
        ts = timestamp_for_filename()
        self.assertRegex(ts, r"^\d{8}_\d{6}$")
        self.assertEqual(len(ts), 15)  # YYYYMMDD_HHMMSS


class TestGitUtils(unittest.TestCase):
    """git.py — commit hash lookup."""

    def test_git_commit_returns_string(self):
        from qsys.common.git import git_commit

        result = git_commit(short=True)
        self.assertIsInstance(result, str)
        # Either "unknown" or a short hash (7+ chars)
        if result != "unknown":
            self.assertRegex(result, r"^[a-f0-9]{7,}$")

    def test_git_commit_full_returns_string(self):
        from qsys.common.git import git_commit_full

        result = git_commit_full()
        self.assertIsInstance(result, str)
        if result != "unknown":
            self.assertRegex(result, r"^[a-f0-9]{40}$")


if __name__ == "__main__":
    unittest.main()
