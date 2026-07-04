
import sys
import yaml
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from qsys.live.ops_paths import DEFAULT_EXPERIMENTS_ROOT
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
        
        model_dir = Path(model_path)
        meta_path = model_dir / "meta.yaml"
        
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta = yaml.safe_load(f) or {}
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
            # Extract feature set name from model directory name
            # e.g. "data/models/qlib_lgbm_semantic_all_features" -> "semantic_all_features"
            # e.g. "data/models/qlib_lgbm_extended" -> "extended"
            # e.g. "data/models/qlib_lgbm" -> "alpha158" (bare model name)
            model_dir_name = Path(model_path).name
            model_prefix = "qlib_lgbm_"
            if model_dir_name.startswith(model_prefix):
                feature_set = model_dir_name[len(model_prefix):]
            elif model_dir_name == "qlib_lgbm":
                feature_set = "alpha158"
            else:
                feature_set = "extended"

            log.info(f"Extracted feature_set='{feature_set}' from model path '{model_path}'")

            # Calculate new training period
            # End date: yesterday (to avoid lookahead bias, or T-1)
            new_end_dt = current_dt - timedelta(days=1)
            new_start_dt = new_end_dt - timedelta(days=train_window_years*365)

            new_start = new_start_dt.strftime("%Y-%m-%d")
            new_end = new_end_dt.strftime("%Y-%m-%d")

            log.info(f"Retraining model from {new_start} to {new_end}...")

            # Run training script preserving the original feature set
            cmd = [
                sys.executable, "scripts/run_train.py",
                "--model", "qlib_lgbm",
                "--start", new_start,
                "--end", new_end,
                "--feature_set", feature_set,
            ]

            try:
                subprocess.check_call(cmd)
                # run_train.py saves to data/models/{model_name} which equals
                # the current model_path directory — model is updated in-place.
                log.info(f"Retrained model at: {model_path}")
                return model_path

            except subprocess.CalledProcessError as e:
                log.error(f"Retraining failed: {e}")
                return model_path # Fallback
                
        return model_path

    @staticmethod
    def find_latest_model(models_dir="data/models", experiments_dir=str(DEFAULT_EXPERIMENTS_ROOT)):
        """Find the latest model directory via explicit pointer, NOT mtime.

        .. deprecated::
            Use ``resolve_model_for_strategy()`` in production code.
            This method is kept as a stub for backward compatibility only.
            It resolves via the unified model resolver, not by mtime sorting.
        """
        from qsys.ops.model_resolver import resolve_model_for_strategy  # noqa: PLC0415

        project_root = Path(models_dir).parent
        try:
            resolved = resolve_model_for_strategy(
                project_root=project_root,
                strategy_id="alpha_v1",
                mode="shadow",
            )
            return resolved.model_path
        except (FileNotFoundError, ValueError):
            return None

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

        Raises
        ------
        FileNotFoundError
            If no valid production manifest exists.  Never falls back to
            mtime sorting or symlink discovery.
        """
        data_root = cfg.get_path("root")
        repo_root = data_root.parent if data_root is not None else Path.cwd()

        if manifest_path is None:
            models_dir = data_root / "models"
            manifest_path = str(models_dir / DEFAULT_MANIFEST_FILENAME)

        manifest_file = Path(manifest_path)

        if manifest_file.exists():
            try:
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f)
                    model_path = manifest.get("model_path")
                    if model_path:
                        model_path_obj = Path(model_path)
                        if not model_path_obj.is_absolute():
                            model_path_obj = repo_root / model_path_obj

                        if model_path_obj.exists():
                            log.info(f"Production model resolved from manifest: {model_path_obj}")
                            log.info(f"  Manifest version: {manifest.get('version', 'unknown')}")
                            log.info(f"  Status: {manifest.get('status', 'unknown')}")
                            return str(model_path_obj)
                        else:
                            raise FileNotFoundError(
                                f"Model path in production manifest does not exist: {model_path_obj}. "
                                "Run approval workflow or update manifest."
                            )
            except FileNotFoundError:
                raise
            except Exception as e:
                raise FileNotFoundError(
                    f"Failed to read production manifest at {manifest_path}: {e}. "
                    "Run approval workflow first."
                )

        raise FileNotFoundError(
            f"No production manifest found at {manifest_path}. "
            "Run approval workflow first."
        )
