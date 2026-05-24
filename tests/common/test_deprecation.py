"""Tests for qsys/common/deprecation.py — legacy entrypoint warnings."""

from __future__ import annotations

import io

from qsys.common.deprecation import print_legacy_entrypoint_warning


class TestPrintLegacyEntrypointWarning:
    def test_writes_to_stderr_by_default(self):
        """Warning should be written to stderr."""
        buf = io.StringIO()
        print_legacy_entrypoint_warning("old.py", "new.py --mode x", file=buf)
        output = buf.getvalue()
        assert "DEPRECATED" in output
        assert "old.py" in output
        assert "new.py --mode x" in output

    def test_writes_to_specified_stream(self):
        buf = io.StringIO()
        print_legacy_entrypoint_warning("a.sh", "b.sh", file=buf)
        output = buf.getvalue()
        assert "DEPRECATED" in output
        assert "a.sh" in output

    def test_contains_separator(self):
        buf = io.StringIO()
        print_legacy_entrypoint_warning("x", "y", file=buf)
        assert "=" * 40 in buf.getvalue()
