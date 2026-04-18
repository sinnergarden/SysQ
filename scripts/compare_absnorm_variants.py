import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from run_absnorm_comparison import main


if __name__ == "__main__":
    main()
