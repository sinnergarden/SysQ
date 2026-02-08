import sys
from pathlib import Path
import shutil

# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.config import cfg

def main():
    print("Rebuilding Qlib Bin...")
    adapter = QlibAdapter()
    
    # Clean existing features to force rebuild
    qlib_dir = Path(str(cfg.get_path("qlib_bin")))
    features_dir = qlib_dir / "features"
    if features_dir.exists():
        print(f"Removing {features_dir}...")
        shutil.rmtree(features_dir)
        
    print("Starting conversion...")
    adapter.convert_all()
    print("Done.")

if __name__ == "__main__":
    main()
