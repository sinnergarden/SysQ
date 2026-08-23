"""Transactional, hash-bound checkpoints for rolling-window predictions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd

from qsys.research.rolling_window import RollingWindow


_SCHEMA_VERSION = "rolling_window_prediction_checkpoint_v1"


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window_payload(window: RollingWindow) -> dict[str, str]:
    return {
        "window_id": window.window_id,
        "train_start": window.train_start,
        "train_end": window.train_end,
        "predict_start": window.predict_start,
        "predict_end": window.predict_end,
    }


@dataclass(frozen=True)
class WindowCheckpointRef:
    window_id: str
    checkpoint_key: str
    row_count: int
    predictions_sha256: str
    manifest_sha256: str
    predictions_path: Path
    manifest_path: Path


class WindowPredictionCheckpointStore:
    """Persist and validate raw prediction frames one rolling window at a time.

    The JSON manifest is the commit marker and is replaced only after the
    parquet file is durable.  A lone parquet/temp file is therefore an
    incomplete checkpoint and fails closed instead of being reused.
    """

    def __init__(self, root: str | Path, base_identity: dict[str, Any]) -> None:
        self.root = Path(root)
        self.base_identity = {
            "schema_version": _SCHEMA_VERSION,
            **base_identity,
        }
        self.base_identity_sha256 = hashlib.sha256(
            _canonical_json(self.base_identity)
        ).hexdigest()

    def _identity(self, window: RollingWindow) -> dict[str, Any]:
        return {
            **self.base_identity,
            "base_identity_sha256": self.base_identity_sha256,
            "window": _window_payload(window),
        }

    def _key(self, window: RollingWindow) -> str:
        return hashlib.sha256(_canonical_json(self._identity(window))).hexdigest()

    def _paths(self, window: RollingWindow) -> tuple[Path, Path]:
        key = self._key(window)
        return self.root / f"{key}.parquet", self.root / f"{key}.manifest.json"

    def validate(self, window: RollingWindow) -> WindowCheckpointRef | None:
        predictions_path, manifest_path = self._paths(window)
        if not predictions_path.exists() and not manifest_path.exists():
            return None
        if not predictions_path.is_file() or not manifest_path.is_file():
            raise ValueError(
                f"Incomplete rolling-window checkpoint for {window.window_id}"
            )

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Invalid rolling-window checkpoint manifest for {window.window_id}"
            ) from exc

        expected_identity = self._identity(window)
        if manifest.get("identity") != expected_identity:
            raise ValueError(
                f"Rolling-window checkpoint identity mismatch for {window.window_id}"
            )
        actual_sha256 = _sha256_file(predictions_path)
        if manifest.get("predictions_sha256") != actual_sha256:
            raise ValueError(
                f"Rolling-window checkpoint hash mismatch for {window.window_id}"
            )

        columns = manifest.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError(
                f"Rolling-window checkpoint columns missing for {window.window_id}"
            )
        row_count = manifest.get("row_count")
        if not isinstance(row_count, int) or row_count < 0:
            raise ValueError(
                f"Rolling-window checkpoint row_count invalid for {window.window_id}"
            )

        return WindowCheckpointRef(
            window_id=window.window_id,
            checkpoint_key=self._key(window),
            row_count=row_count,
            predictions_sha256=actual_sha256,
            manifest_sha256=_sha256_file(manifest_path),
            predictions_path=predictions_path,
            manifest_path=manifest_path,
        )

    def save(
        self, window: RollingWindow, predictions: pd.DataFrame
    ) -> WindowCheckpointRef:
        existing = self.validate(window)
        if existing is not None:
            return existing

        self.root.mkdir(parents=True, exist_ok=True)
        predictions_path, manifest_path = self._paths(window)
        parquet_fd, parquet_name = tempfile.mkstemp(
            prefix=f".{window.window_id}-", suffix=".parquet.tmp", dir=self.root
        )
        os.close(parquet_fd)
        parquet_tmp = Path(parquet_name)
        manifest_fd, manifest_name = tempfile.mkstemp(
            prefix=f".{window.window_id}-", suffix=".manifest.tmp", dir=self.root
        )
        os.close(manifest_fd)
        manifest_tmp = Path(manifest_name)
        try:
            predictions.to_parquet(parquet_tmp, index=False)
            with parquet_tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            predictions_sha256 = _sha256_file(parquet_tmp)
            manifest = {
                "artifact_type": "rolling_window_prediction_checkpoint",
                "columns": list(predictions.columns),
                "identity": self._identity(window),
                "predictions_file": predictions_path.name,
                "predictions_sha256": predictions_sha256,
                "row_count": int(len(predictions)),
            }
            with manifest_tmp.open("wb") as handle:
                handle.write(json.dumps(
                    manifest,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(parquet_tmp, predictions_path)
            os.replace(manifest_tmp, manifest_path)
        finally:
            parquet_tmp.unlink(missing_ok=True)
            manifest_tmp.unlink(missing_ok=True)

        ref = self.validate(window)
        if ref is None:  # pragma: no cover - defensive invariant
            raise RuntimeError(f"Checkpoint commit failed for {window.window_id}")
        return ref

    def load(self, window: RollingWindow) -> pd.DataFrame:
        ref = self.validate(window)
        if ref is None:
            raise FileNotFoundError(
                f"Rolling-window checkpoint missing for {window.window_id}"
            )
        frame = pd.read_parquet(ref.predictions_path)
        manifest = json.loads(ref.manifest_path.read_text(encoding="utf-8"))
        if len(frame) != ref.row_count:
            raise ValueError(
                f"Rolling-window checkpoint row_count mismatch for {window.window_id}"
            )
        if list(frame.columns) != manifest["columns"]:
            raise ValueError(
                f"Rolling-window checkpoint schema mismatch for {window.window_id}"
            )
        return frame

    @staticmethod
    def checkpoint_set_sha256(refs: list[WindowCheckpointRef]) -> str:
        payload = [
            {
                "window_id": ref.window_id,
                "checkpoint_key": ref.checkpoint_key,
                "row_count": ref.row_count,
                "predictions_sha256": ref.predictions_sha256,
                "manifest_sha256": ref.manifest_sha256,
            }
            for ref in refs
        ]
        return hashlib.sha256(_canonical_json({"checkpoints": payload})).hexdigest()
