"""Generic file I/O helpers.

Business-neutral utilities only.  Strategy/model/feature-specific readers
belong in their respective modules or in ``qsys/ops/``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON from *path*.  Returns *default* on missing/invalid file."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str | Path, data: Any) -> Path:
    """Write *data* as pretty-printed JSON with trailing newline.  Returns *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_json_atomic(path: str | Path, data: Any) -> Path:
    """Atomically write JSON via tempfile + rename.  Prevents partial writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", delete=False, dir=str(path.parent), encoding="utf-8"
    ) as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)
    return path


def ensure_parent(path: str | Path) -> Path:
    """Create parent directories for *path* if needed.  Returns the *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def archive_dir(src: Path, dst_root: Path, prefix: str = "") -> Path:
    """Move *src* directory into *dst_root* with a timestamp suffix.

    Returns the archive destination path.
    """
    if not src.exists():
        raise FileNotFoundError(f"Cannot archive non-existent directory: {src}")
    dst_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = dst_root / f"{prefix}_{ts}" if prefix else dst_root / ts
    shutil.move(str(src), str(dst))
    return dst
