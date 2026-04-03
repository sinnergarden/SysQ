
import sys
import yaml
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from qsys.utils.logger import log
from qsys.config import cfg

# Default paths for production manifest
DEFAULT_MANIFEST_FILENAME = "production_manifest.yaml"


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
        feature_set_name = None
        model_name = Path(model_path).name
        
        model_dir = Path(model_path)
        meta_path = model_dir / "meta.yaml"
        
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = yaml.safe_load(f) or {}
                    model_name = meta.get("name", model_name)
                    params = meta.get("params") or {}
                    feature_set_name = params.get("feature_set_name") or params.get("feature_set_alias")
                    train_period = meta.get("train_period")
                    if train_period and len(train_period) >= 2:
                        train_end_str = str(train_period[1])
                        try:
                            train_end_date = datetime.strptime(train_end_str.split()[0], "%Y-%m-%d")
                        except ValueError:
                            train_end_date = None
                    if train_end_date is None:
                        training_summary = meta.get("training_summary") or {}
                        train_end_str = training_summary.get("train_end")
                        if train_end_str:
                            train_end_date = datetime.strptime(str(train_end_str).split()[0], "%Y-%m-%d")
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
                "--end", new_end,
            ]
            if feature_set_name:
                cmd.extend(["--feature_set", str(feature_set_name)])
            
            try:
                subprocess.check_call(cmd)

                data_root = cfg.get_path("root")
                if data_root is not None:
                    expected_model_path = data_root / "models" / model_name
                    if expected_model_path.exists():
                        log.info(f"Updated model available at: {expected_model_path}")
                        return str(expected_model_path)

                models_root = Path("data/models")
                if models_root.exists():
                    candidates = sorted([d for d in models_root.iterdir() if d.is_dir() and d.name == model_name])
                    if candidates:
                        new_model_path = candidates[-1]
                        log.info(f"New model trained at: {new_model_path}")
                        return str(new_model_path)

                if model_dir.exists() and model_dir.name == model_name:
                    log.info(f"Updated model available at: {model_dir}")
                    return str(model_dir)
                
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

    @staticmethod
    def resolve_production_model(manifest_path: str = None) -> str:
        """
        Resolve the production model path from the manifest.
        
        This is the preferred method for daily ops to get the model to use.
        It reads production_manifest.yaml to determine which model is approved.
        
        Args:
            manifest_path: Path to manifest file. If None, uses default location.
        
        Returns:
            Path to the production model directory.
            Falls back to find_latest_model() if manifest not found.
        """
        data_root = cfg.get_path("root")
        repo_root = data_root.parent if data_root is not None else Path.cwd()

        if manifest_path is None:
            # cfg.get_path("root") points to the data root.
            models_dir = data_root / "models"
            manifest_path = str(models_dir / DEFAULT_MANIFEST_FILENAME)
        
        manifest_file = Path(manifest_path)
        
        if manifest_file.exists():
            try:
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f)
                    model_path = manifest.get("model_path")
                    if model_path:
                        # Resolve relative paths relative to repo root
                        model_path_obj = Path(model_path)
                        if not model_path_obj.is_absolute():
                            model_path_obj = repo_root / model_path_obj
                        
                        if model_path_obj.exists():
                            log.info(f"Production model resolved from manifest: {model_path_obj}")
                            log.info(f"  Manifest version: {manifest.get('version', 'unknown')}")
                            log.info(f"  Status: {manifest.get('status', 'unknown')}")
                            return str(model_path_obj)
                        else:
                            log.warning(f"Model path in manifest does not exist: {model_path_obj}")
            except Exception as e:
                log.warning(f"Failed to read production manifest: {e}")
        
        # Fallback to latest model
        log.warning("Production manifest not found or invalid. Falling back to latest model.")
        latest = ModelScheduler.find_latest_model()
        if latest:
            return str(latest)
        
        raise FileNotFoundError("No production model found and no fallback available.")
