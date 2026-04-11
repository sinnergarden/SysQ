from __future__ import annotations

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import uvicorn

from qsys.research_ui.api import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Research UI API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    uvicorn.run(create_app(ROOT), host=args.host, port=args.port)
