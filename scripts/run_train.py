"""
Primary training entrypoint.

Purpose:
- train the selected model on a chosen universe/window
- optionally run a minimal post-train backtest
- emit structured training report

Typical usage:
- python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set extended

Key args:
- --model: currently qlib_lgbm
- --universe: csi300 / all
- --start / --end: training window
- --feature_set: alpha158 | extended
- --run_backtest: run a minimal validation backtest after training
- --no_report: skip JSON run report
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import assert_qlib_data_ready
from qsys.reports.train import TrainingReport
from qsys.utils.logger import log


@click.command()
@click.option('--model', default='qlib_lgbm', help='Model name')
@click.option('--universe', default='csi300', help='Stock universe (e.g., csi300, all)')
@click.option('--start', default='2020-01-01', help='Start date')
@click.option('--end', default='2023-12-31', help='End date')
@click.option('--run_backtest', is_flag=True, help='Run minimal backtest after training')
@click.option('--backtest_start', default=None, help='Backtest start date; defaults to last 40 trading days window start')
@click.option('--backtest_end', default=None, help='Backtest end date; defaults to training end date')
@click.option('--feature_set', type=click.Choice(['alpha158', 'extended', 'margin_extended', 'phase1', 'phase12', 'phase123', 'semantic_all_features'], case_sensitive=False), default='extended', show_default=True, help='Feature set: alpha158 | extended | margin_extended | phase1 | phase12 | phase123 | semantic_all_features')
@click.option('--no_report', is_flag=True, help='Skip generating the structured report')
def main(model, universe, start, end, run_backtest, backtest_start, backtest_end, feature_set, no_report):
    start_time = time.time()
    blockers = []
    notes = []
    
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
    elif feature_set == 'margin_extended':
        feature_config = FeatureLibrary.get_alpha158_margin_extended_config()
    elif feature_set == 'phase1':
        feature_config = FeatureLibrary.get_research_phase1_config()
    elif feature_set == 'phase12':
        feature_config = FeatureLibrary.get_research_phase12_config()
    elif feature_set == 'phase123':
        feature_config = FeatureLibrary.get_research_phase123_config()
    elif feature_set == 'semantic_all_features':
        feature_config = FeatureLibrary.get_semantic_all_features_config()
    else:
        feature_config = FeatureLibrary.get_alpha158_config()

    if feature_set == 'alpha158':
        model_name = model
    elif feature_set == 'margin_extended':
        model_name = f"{model}_margin_extended"
    elif feature_set in {'phase1', 'phase12', 'phase123'}:
        model_name = f"{model}_{feature_set}"
    elif feature_set == 'semantic_all_features':
        model_name = f"{model}_semantic_all_features"
    else:
        model_name = f"{model}_extended"
    log.info(f"Using feature_set={feature_set} with {len(feature_config)} features")
    model_instance = QlibNativeModel(
        name=model_name,
        model_config=qlib_config,
        feature_config=feature_config,
    )

    health = assert_qlib_data_ready(end, model_instance.feature_config, universe=universe)
    log.info("\n" + health.to_markdown())

    # Data status for report
    data_status = {}
    try:
        adapter = QlibAdapter()
        adapter.init_qlib()
        status_report = adapter.get_data_status_report()
        data_status = {
            "raw_latest": status_report.get("raw_latest"),
            "qlib_latest": status_report.get("qlib_latest"),
            "aligned": status_report.get("aligned", False),
            "health_ok": True,
        }
    except Exception as e:
        log.warning(f"Could not get data status: {e}")
        data_status = {"health_ok": False}

    model_info = {
        "model_name": model_name,
        "feature_set": feature_set,
        "train_window": f"{start} to {end}",
        "universe": universe,
    }

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
            model_info["sample_count"] = training_summary.get("sample_count")

        notes.append(f"Feature count: {len(feature_config)}")
        
        backtest_info = None
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
        blockers.append(f"Training failed: {str(e)}")
        
        if not no_report:
            report = TrainingReport.generate(
                signal_date=end,
                data_status=data_status,
                model_info=model_info,
                training_metrics={},
                feature_count=len(feature_config),
                duration_seconds=time.time() - start_time,
                blockers=blockers,
                notes=notes,
            )
            report_path = TrainingReport.save(report)
            log.info(f"Training report (failure) saved to: {report_path}")
        
        raise e
    
    # Generate training report on success
    duration = time.time() - start_time
    
    if not no_report:
        report = TrainingReport.generate(
            signal_date=end,
            data_status=data_status,
            model_info=model_info,
            training_metrics=training_summary,
            feature_count=len(feature_config),
            sample_count=training_summary.get("sample_count", 0),
            duration_seconds=duration,
            backtest_info=backtest_info,
            blockers=blockers,
            notes=notes,
        )
        report.artifacts["training_summary"] = str(save_path / "training_summary.csv")
        report.artifacts["model_path"] = str(save_path)
        
        report_path = TrainingReport.save(report)
        log.info(f"Training report saved to: {report_path}")
        
        # Also print markdown summary
        print("\n" + "=" * 60)
        print(report.to_markdown())
        print("=" * 60)
    
    log.info(f"Training completed. Duration: {duration:.1f}s")


if __name__ == '__main__':
    main()
