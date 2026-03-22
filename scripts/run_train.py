import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.config import cfg
from qsys.data.health import inspect_qlib_data_health
from qsys.utils.logger import log


@click.command()
@click.option('--model', default='qlib_lgbm', help='Model name')
@click.option('--universe', default='csi300', help='Stock universe (e.g., csi300, all)')
@click.option('--start', default='2020-01-01', help='Start date')
@click.option('--end', default='2023-12-31', help='End date')
@click.option('--run_backtest', is_flag=True, help='Run minimal backtest after training')
@click.option('--backtest_start', default=None, help='Backtest start date; defaults to last 40 trading days window start')
@click.option('--backtest_end', default=None, help='Backtest end date; defaults to training end date')
@click.option('--feature_set', type=click.Choice(['alpha158', 'extended'], case_sensitive=False), default='extended', show_default=True, help='Feature set to train with')
def main(model, universe, start, end, run_backtest, backtest_start, backtest_end, feature_set):
    log.info(f"Starting training for {model}")

    if universe == 'csi300':
        codes = 'csi300'
    else:
        codes = 'all'

    if model != 'qlib_lgbm':
        log.error(f"Unknown model: {model}")
        return

    from qsys.feature.library import FeatureLibrary
    from qsys.model.zoo.qlib_native import QlibNativeModel

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

    if feature_set == 'extended':
        feature_config = FeatureLibrary.get_alpha158_extended_config()
    else:
        feature_config = FeatureLibrary.get_alpha158_config()

    model_name = model if feature_set == 'alpha158' else f"{model}_extended"
    log.info(f"Using feature_set={feature_set} with {len(feature_config)} features")
    model_instance = QlibNativeModel(
        name=model_name,
        model_config=qlib_config,
        feature_config=feature_config,
    )

    health = inspect_qlib_data_health(end, model_instance.feature_config, universe=universe)
    log.info("\n" + health.to_markdown())
    if not health.ok:
        raise ValueError(f"Data health check failed before training: {health.issues}")

    try:
        model_instance.fit(codes, start, end)

        root_path = cfg.get_path("root")
        if root_path is None:
            raise ValueError("Root path not configured")
        save_path = root_path / "models" / model_name
        model_instance.save(save_path)

        training_summary = model_instance.training_summary or {}
        if training_summary:
            summary_path = save_path / "training_summary.csv"
            pd.DataFrame([training_summary]).to_csv(summary_path, index=False)
            log.info(f"Training summary saved to {summary_path}")
            log.info(
                f"Training metrics | mse={training_summary.get('mse')} "
                f"rank_ic={training_summary.get('rank_ic')} samples={training_summary.get('sample_count')}"
            )

        if run_backtest:
            import subprocess

            bt_end = backtest_end or end
            bt_start = backtest_start or (pd.Timestamp(bt_end) - pd.Timedelta(days=60)).strftime('%Y-%m-%d')
            backtest_script = project_root / 'scripts' / 'run_backtest.py'
            cmd = [
                sys.executable,
                str(backtest_script),
                '--model_path',
                str(save_path),
                '--universe',
                universe,
                '--start',
                bt_start,
                '--end',
                bt_end,
                '--top_k',
                '30',
            ]
            log.info(f"Running post-train backtest in isolated process: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)

    except Exception as e:
        log.error(f"Training failed: {e}")
        raise e


if __name__ == '__main__':
    main()
