import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.backtest import BacktestEngine

if __name__ == "__main__":
    import sys
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")
    model_dir = root_path / "models" / "qlib_lgbm"
    
    # Run slightly longer backtest to get meaningful stats
    engine = BacktestEngine(model_dir, universe='csi300', start_date='2022-01-01', end_date='2022-03-01')
    res = engine.run()
    # Save to experiments folder
    save_path = root_path / "experiments" / "backtest_result.csv"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(save_path)
