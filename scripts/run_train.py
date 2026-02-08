import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
from qsys.utils.logger import log
from qsys.config import cfg

@click.command()
@click.option('--model', default='qlib_lgbm', help='Model name')
@click.option('--universe', default='csi300', help='Stock universe (e.g., csi300, all)')
@click.option('--start', default='2020-01-01', help='Start date')
@click.option('--end', default='2023-12-31', help='End date')
def main(model, universe, start, end):
    log.info(f"Starting training for {model}")
    
    # 1. Resolve Universe (Placeholder)
    # Ideally load from a file or Qlib instrument
    if universe == 'csi300':
        # Placeholder: we should fetch CSI300 components
        # For now, pass 'csi300' if qlib supports it, or 'all'
        codes = 'csi300' 
    else:
        codes = 'all'

    # 2. Instantiate Model
    if model != 'qlib_lgbm':
        log.error(f"Unknown model: {model}")
        return
    from qsys.model.zoo.qlib_native import QlibNativeModel
    from qsys.feature.library import FeatureLibrary
    
    qlib_config = {
        'class': 'LGBModel',
        'module_path': 'qlib.contrib.model.gbdt',
        'kwargs': {
            'loss': 'mse',
            'colsample_bytree': 0.8879,
            'learning_rate': 0.0421,
            'subsample': 0.8789,
            'lambda_l1': 205.6999,
            'lambda_l2': 580.9768,
            'max_depth': 8,
            'num_leaves': 210,
            'num_threads': 20,
        }
    }
    
    model_instance = QlibNativeModel(
        name=model, 
        model_config=qlib_config,
        feature_config=FeatureLibrary.get_alpha158_config()
    )

    # 3. Fit
    try:
        model_instance.fit(codes, start, end)
        
        # 4. Save
        root_path = cfg.get_path("root")
        if root_path is None:
            raise ValueError("Root path not configured")
        save_path = root_path / "models" / model
        model_instance.save(save_path)
        
    except Exception as e:
        log.error(f"Training failed: {e}")
        raise e

if __name__ == '__main__':
    main()
