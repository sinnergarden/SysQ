"""DNN Kronos — shared-bottom multi-task model for alpha_v1 features.

Architecture::

    508 features (254 raw + 254 cs_zscore)
              │
         Linear(128) → ReLU
              │
         Linear(32) → ReLU   ← shared bottom
           ╱       ╲
    Linear(1)    Linear(1)   ← task towers
      score_5d    score_20d

Loss: MSE(score_5d, label_5d_zscore) + MSE(score_20d, label_20d_zscore)

Usage::

    model = DnnMultitask()
    model.fit(X_train, y_5d, y_20d)
    pred_5d, pred_20d = model.predict(X)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


class SharedBottomDNN(nn.Module):
    """Shared-bottom multi-task DNN."""

    def __init__(self, input_dim: int = 508, bottom_dim: int = 128, shared_dim: int = 32):
        super().__init__()
        self.bottom = nn.Sequential(
            nn.Linear(input_dim, bottom_dim),
            nn.ReLU(),
            nn.Linear(bottom_dim, shared_dim),
            nn.ReLU(),
        )
        self.tower_5d = nn.Linear(shared_dim, 1)
        self.tower_20d = nn.Linear(shared_dim, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        shared = self.bottom(x)
        return self.tower_5d(shared).squeeze(-1), self.tower_20d(shared).squeeze(-1)


@dataclass
class DnnMultitask:
    """High-level wrapper around SharedBottomDNN.

    Parameters
    ----------
    input_dim: int, default 508 (254 raw + 254 cs_zscore)
    epochs: int, default 50
    batch_size: int, default 1024
    lr: float, default 1e-3
    device: str, auto-detected if None
    """

    input_dim: int = 508
    epochs: int = 50
    batch_size: int = 1024
    lr: float = 1e-3
    device: str | None = None

    _model: nn.Module | None = None
    _feature_names: list[str] | None = None

    def __post_init__(self):
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def fit(
        self,
        X: np.ndarray,
        y_5d: np.ndarray,
        y_20d: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> None:
        """Train the shared-bottom DNN.

        Parameters
        ----------
        X: shape (N, 508) — [raw_254 | zscore_254]
        y_5d: shape (N,) — forward_return_5d, zscore'd per-date
        y_20d: shape (N,) — forward_return_20d, zscore'd per-date
        feature_names: optional list of column names for metadata
        """
        self._feature_names = feature_names

        model = SharedBottomDNN(self.input_dim).to(self.device)
        optimizer = torch.optim.Adam(model.parameters(), lr=self.lr)

        X_t = torch.tensor(X, dtype=torch.float32, device=self.device)
        y5_t = torch.tensor(y_5d, dtype=torch.float32, device=self.device)
        y20_t = torch.tensor(y_20d, dtype=torch.float32, device=self.device)

        dataset = torch.utils.data.TensorDataset(X_t, y5_t, y20_t)
        loader = torch.utils.data.DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        model.train()
        for epoch in range(1, self.epochs + 1):
            total_loss = 0.0
            for bx, b5, b20 in loader:
                pred5, pred20 = model(bx)
                loss = nn.functional.mse_loss(pred5, b5) + nn.functional.mse_loss(pred20, b20)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            if epoch % 10 == 0 or epoch == self.epochs:
                print(f"  Epoch {epoch:3d}/{self.epochs}  loss={total_loss:.4f}")

        self._model = model

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (pred_5d, pred_20d), each shape (N,)."""
        if self._model is None:
            raise RuntimeError("call fit() before predict()")
        self._model.eval()
        X_t = torch.tensor(X, dtype=torch.float32, device=self.device)
        with torch.no_grad():
            p5, p20 = self._model(X_t)
        return p5.cpu().numpy(), p20.cpu().numpy()

    def save(self, path: Path) -> None:
        """Save model weights + metadata."""
        path.mkdir(parents=True, exist_ok=True)
        if self._model is not None:
            torch.save(self._model.state_dict(), path / "model.pt")
        meta = {
            "input_dim": self.input_dim,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "lr": self.lr,
            "feature_names": self._feature_names,
        }
        import json
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"  Model saved to {path}")

    def load(self, path: Path) -> None:
        """Load model weights from saved checkpoint."""
        import json
        meta = json.loads((path / "meta.json").read_text())
        self.input_dim = meta.get("input_dim", self.input_dim)
        self.epochs = meta.get("epochs", self.epochs)
        self.batch_size = meta.get("batch_size", self.batch_size)
        self.lr = meta.get("lr", self.lr)
        self._feature_names = meta.get("feature_names")

        model = SharedBottomDNN(self.input_dim).to(self.device)
        model.load_state_dict(torch.load(path / "model.pt", map_location=self.device, weights_only=True))
        self._model = model
        print(f"  Model loaded from {path}")
