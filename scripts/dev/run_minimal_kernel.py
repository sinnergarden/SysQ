from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from qsys.core.runner import main


if __name__ == "__main__":
    raise SystemExit(main())
