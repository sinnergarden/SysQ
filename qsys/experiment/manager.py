
import os
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime
from qsys.utils.logger import log

class ExperimentManager:
    """
    Manages experiment runs, configs, and results.
    Structure:
        data/experiments/
            exp_20231027_alpha158_lgbm/
                config.yaml
                model.bin
                pred.pkl
                metrics.json
            ...
        leaderboard.csv
    """
    def __init__(self, experiment_root="data/experiments"):
        self.root = Path(experiment_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.leaderboard_path = self.root / "leaderboard.csv"

    def create_run(self, name, config: dict, description=""):
        """
        Create a new experiment run folder.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{timestamp}_{name}"
        run_dir = self.root / run_name
        run_dir.mkdir()
        
        # Save Config
        config['description'] = description
        with open(run_dir / "config.yaml", 'w') as f:
            yaml.dump(config, f)
            
        log.info(f"Created experiment run: {run_name}")
        return RunContext(run_dir, run_name, self)

    def update_leaderboard(self, run_name, metrics: dict):
        """
        Append metrics to leaderboard.csv
        """
        record = {'run_name': run_name, 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        record.update(metrics)
        
        df_new = pd.DataFrame([record])
        
        if self.leaderboard_path.exists():
            df_old = pd.read_csv(self.leaderboard_path)
            df = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df = df_new
            
        df.to_csv(self.leaderboard_path, index=False)
        log.info(f"Leaderboard updated with {run_name}")

class RunContext:
    def __init__(self, run_dir, run_name, manager):
        self.dir = run_dir
        self.name = run_name
        self.manager = manager
        
    def log_metrics(self, metrics: dict):
        import json
        with open(self.dir / "metrics.json", 'w') as f:
            json.dump(metrics, f, indent=4)
        
        # Update Global Leaderboard
        self.manager.update_leaderboard(self.name, metrics)
        
    def save_model(self, model, filename="model.bin"):
        # Depends on model type, assume pickle or qlib model save
        import pickle
        with open(self.dir / filename, 'wb') as f:
            pickle.dump(model, f)
            
    def get_path(self, filename):
        return self.dir / filename
