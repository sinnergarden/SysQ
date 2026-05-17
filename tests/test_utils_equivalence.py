"""Equivalence tests for utils/json_io.py.

Verifies that the canonical write_json/write_csv produce identical output
to the inline _write_json/_write_csv definitions they replaced.
"""
from __future__ import annotations

import json
import csv
import io
import tempfile
from pathlib import Path
from unittest import TestCase

from qsys.utils.json_io import write_json, atomic_write_json, write_csv, load_json


def inline_write_json(path: Path, payload: dict) -> Path:
    """Reference implementation matching the canonical contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def inline_write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> Path:
    """Reference implementation matching the canonical contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestWriteJsonEquivalence(TestCase):
    """write_json produces byte-identical output to inline implementation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_simple_dict(self):
        payload = {"a": 1, "b": "hello", "c": [1, 2, 3]}
        p1 = self.tmp / "canonical.json"
        p2 = self.tmp / "inline.json"
        write_json(p1, payload)
        inline_write_json(p2, payload)
        self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_nested_dict(self):
        payload = {"outer": {"inner": [1, 2, 3], "key": "val"}, "num": 42}
        p1 = self.tmp / "canonical.json"
        p2 = self.tmp / "inline.json"
        write_json(p1, payload)
        inline_write_json(p2, payload)
        self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_empty_dict(self):
        payload = {}
        p1 = self.tmp / "canonical.json"
        p2 = self.tmp / "inline.json"
        write_json(p1, payload)
        inline_write_json(p2, payload)
        self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_trailing_newline(self):
        """Output must end with newline (consumers may depend on it)."""
        p = self.tmp / "test.json"
        write_json(p, {"a": 1})
        content = p.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))


class TestWriteCsvEquivalence(TestCase):
    """write_csv produces identical output to inline implementation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_simple_rows(self):
        rows = [
            {"symbol": "000001.SZ", "score": 0.5, "rank": 1},
            {"symbol": "000002.SZ", "score": 0.3, "rank": 2},
        ]
        fieldnames = ["symbol", "score", "rank"]
        p1 = self.tmp / "canonical.csv"
        p2 = self.tmp / "inline.csv"
        write_csv(p1, rows, fieldnames)
        inline_write_csv(p2, rows, fieldnames)
        self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_empty_rows(self):
        rows: list[dict] = []
        fieldnames = ["a", "b"]
        p1 = self.tmp / "canonical.csv"
        p2 = self.tmp / "inline.csv"
        write_csv(p1, rows, fieldnames)
        inline_write_csv(p2, rows, fieldnames)
        self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_header_only(self):
        """Even with no rows, header must be written."""
        p = self.tmp / "header.csv"
        write_csv(p, [], ["col1", "col2"])
        content = p.read_text(encoding="utf-8")
        self.assertEqual(content.strip(), "col1,col2")


class TestAtomicWriteJson(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_atomic_write(self):
        payload = {"a": 1, "b": [2, 3]}
        p = self.tmp / "payload.json"
        result = atomic_write_json(p, payload)
        self.assertEqual(result, p)
        self.assertEqual(load_json(p), payload)

    def test_overwrite(self):
        p = self.tmp / "payload.json"
        atomic_write_json(p, {"first": 1})
        atomic_write_json(p, {"second": 2})
        self.assertEqual(load_json(p), {"second": 2})


class TestLoadJson(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_load_missing(self):
        self.assertEqual(load_json(self.tmp / "nope.json"), {})

    def test_load_valid(self):
        p = self.tmp / "data.json"
        write_json(p, {"key": "val"})
        self.assertEqual(load_json(p), {"key": "val"})
