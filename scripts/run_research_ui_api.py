from __future__ import annotations

from pathlib import Path

import uvicorn

from qsys.research_ui.api import create_app


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    uvicorn.run(create_app(root), host="127.0.0.1", port=8000)
