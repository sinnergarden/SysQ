
import sys
import yaml
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from qsys.utils.logger import log

class ModelScheduler:
    """
    Manages model retraining schedules and checks.
    """
    
    @staticmethod
    def check_and_retrain(model_path, current_date, retrain_freq_days=7, train_window_years=3):
        """
        Check if model needs retraining based on metadata and current date.
        If yes, triggers retraining and returns path to new model.
        If no, returns original model path.
        """
        needs_retrain = False
        train_end_date = None
        
        model_dir = Path(model_path)
        meta_path = model_dir / "meta.yaml"
        
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    # Use UnsafeLoader to handle python tags like !!python/tuple
                    meta = yaml.load(f, Loader=yaml.UnsafeLoader)
                    train_period = meta.get("train_period")
                    if train_period:
                        # train_period is likely a tuple/list of strings or dates
                        train_end_str = str(train_period[1])
                        # Try parsing date
                        try:
                            # Handle potential datetime format or just date
                            train_end_date = datetime.strptime(train_end_str.split()[0], "%Y-%m-%d")
                        except ValueError:
                            pass
            except Exception as e:
                log.warning(f"Failed to read model metadata: {e}")

        current_dt = datetime.strptime(current_date, "%Y-%m-%d")
        
        if train_end_date:
            age = (current_dt - train_end_date).days
            log.info(f"Current Model Age: {age} days (End Date: {train_end_date.strftime('%Y-%m-%d')})")
            if age > retrain_freq_days:
                log.info(f"Model is outdated (Threshold: {retrain_freq_days} days). Retraining...")
                needs_retrain = True
        else:
            log.warning("Could not determine model age from metadata. Skipping retrain check (assuming manual control).")
            
        if needs_retrain:
            # Calculate new training period
            # End date: yesterday (to avoid lookahead bias, or T-1)
            new_end_dt = current_dt - timedelta(days=1)
            new_start_dt = new_end_dt - timedelta(days=train_window_years*365)
            
            new_start = new_start_dt.strftime("%Y-%m-%d")
            new_end = new_end_dt.strftime("%Y-%m-%d")
            
            log.info(f"Retraining model from {new_start} to {new_end}...")
            
            # Run training script
            # Use sys.executable to ensure we use the same python environment
            cmd = [
                sys.executable, "scripts/run_train.py",
                "--model", "qlib_lgbm",
                "--start", new_start,
                "--end", new_end
            ]
            
            try:
                subprocess.check_call(cmd)
                
                # Find the new model
                # It should be in data/models/qlib_lgbm_{timestamp}
                models_root = Path("data/models")
                if models_root.exists():
                    candidates = sorted([d for d in models_root.iterdir() if d.is_dir() and "qlib_lgbm" in d.name])
                    if candidates:
                        new_model_path = candidates[-1]
                        log.info(f"New model trained at: {new_model_path}")
                        return str(new_model_path)
                
                log.error("Training finished but could not find new model directory.")
                return model_path # Fallback
                    
            except subprocess.CalledProcessError as e:
                log.error(f"Retraining failed: {e}")
                return model_path # Fallback
                
        return model_path

    @staticmethod
    def find_latest_model(models_dir="data/models", experiments_dir="data/experiments"):
        """Find the latest model directory."""
        candidates = []
        
        # Check data/models (Preferred for production/rolling models)
        models_root = Path(models_dir)
        if models_root.exists():
            candidates.extend([d for d in models_root.iterdir() if d.is_dir()])
            
        # Check data/experiments (For research models)
        exp_root = Path(experiments_dir)
        if exp_root.exists():
            candidates.extend([d for d in exp_root.iterdir() if d.is_dir()])
            
        if not candidates:
            return None
            
        # Sort by modification time (newest first)
        candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return candidates[0]
