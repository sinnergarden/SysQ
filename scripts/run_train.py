"""
Primary training entrypoint.

Purpose:
- train the selected model on a chosen universe/window
- optionally run a minimal post-train backtest
- emit structured training report

Typical usage:
- python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set price_volume_fundamental_core_v1

Key args:
- --model: currently qlib_lgbm
- --universe: csi300 / all
- --start / --end: training window
- --feature_set: semantic feature-set name, or a legacy alias like alpha158 / extended / margin_extended
- --run_backtest: run a minimal validation backtest after training
- --no_report: skip JSON run report
"""

import sys
import time
from pathlib import Path
import yaml

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import lightgbm as lgb
import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import assert_qlib_data_ready
from qsys.feature.registry import resolve_feature_selection
from qsys.feature.runtime import build_feature_panel
from qsys.model.zoo.qlib_native import build_identity_sanitize_contract
from qsys.reports.train import TrainingReport
from qsys.utils.logger import log


def _fit_semantic_all_features_model(
    *,
    model_name: str,
    model_config: dict,
    feature_set_name: str,
    universe: str,
    start: str,
    end: str,
) -> tuple[object, list[str], dict, dict]:
    panel, selection_info = build_feature_panel(
        feature_set=feature_set_name,
        universe=universe,
        start_date=start,
        end_date=end,
        include_close=True,
    )
    if panel.empty:
        raise ValueError(f'No feature panel built for feature_set={feature_set_name}')

    panel = panel.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    panel['label'] = panel.groupby('ts_code', sort=False)['close'].shift(-5) / panel.groupby('ts_code', sort=False)['close'].shift(-1) - 1

    feature_columns = [c for c in panel.columns if c not in {'trade_date', 'ts_code', 'close', 'label'}]
    train_mask = panel['label'].notna()
    X_train_raw = panel.loc[train_mask, feature_columns]
    y_train = panel.loc[train_mask, 'label']
    safe_X_train, sanitize_contract = build_identity_sanitize_contract(X_train_raw, fill_value=0.0)
    feature_name_map = dict(sanitize_contract['feature_name_map'])
    constant_cols = list(sanitize_contract['dropped_columns'])
    if safe_X_train.empty or safe_X_train.shape[1] == 0:
        raise ValueError(f'All features were dropped during sanitization for feature_set={feature_set_name}')

    model = lgb.LGBMRegressor(
        objective='regression',
        learning_rate=float(model_config['kwargs'].get('learning_rate', 0.05)),
        num_leaves=int(model_config['kwargs'].get('num_leaves', 64)),
        max_depth=int(model_config['kwargs'].get('max_depth', 8)),
        colsample_bytree=float(model_config['kwargs'].get('colsample_bytree', 0.8)),
        subsample=float(model_config['kwargs'].get('subsample', 0.8)),
        reg_alpha=float(model_config['kwargs'].get('lambda_l1', 0.0)),
        reg_lambda=float(model_config['kwargs'].get('lambda_l2', 0.0)),
        n_estimators=1000,
        n_jobs=int(model_config['kwargs'].get('num_threads', 8)),
    )
    model.fit(safe_X_train, y_train)

    train_pred = pd.Series(model.predict(safe_X_train), index=y_train.index)
    aligned = pd.DataFrame({'score': train_pred.values, 'label': y_train.values}, index=y_train.index).dropna()
    rank_ic = float(aligned['score'].corr(aligned['label'], method='spearman')) if not aligned.empty else float('nan')
    mse = float(((aligned['score'] - aligned['label']) ** 2).mean()) if not aligned.empty else float('nan')
    training_summary = {
        'train_start': start,
        'train_end': end,
        'sample_count': int(len(aligned)),
        'feature_count': int(len(feature_columns)),
        'feature_count_used': int(safe_X_train.shape[1]),
        'feature_count_constant_dropped': int(len(constant_cols)),
        'label_name': '(Ref($close, -5) / Ref($close, -1) - 1)',
        'mse': mse,
        'rank_ic': rank_ic,
        'score_mean': float(aligned['score'].mean()) if not aligned.empty else float('nan'),
        'label_mean': float(aligned['label'].mean()) if not aligned.empty else float('nan'),
    }
    return model, list(feature_name_map.keys()), training_summary, sanitize_contract


@click.command()
@click.option('--model', default='qlib_lgbm', help='Model name')
@click.option('--universe', default='csi300', help='Stock universe (e.g., csi300, all)')
@click.option('--start', default='2020-01-01', help='Start date')
@click.option('--end', default='2023-12-31', help='End date')
@click.option('--run_backtest', is_flag=True, help='Run minimal backtest after training')
@click.option('--backtest_start', default=None, help='Backtest start date; defaults to last 40 trading days window start')
@click.option('--backtest_end', default=None, help='Backtest end date; defaults to training end date')
@click.option('--feature_set', default='price_volume_fundamental_core_v1', show_default=True, help='Feature set name or legacy alias. Recommended: semantic_all_features_v1 | price_volume_expression_core_v1 | price_volume_fundamental_core_v1 | price_volume_fundamental_event_core_v1')
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

    resolved_feature_set = FeatureLibrary.normalize_feature_set_name(feature_set)
    selection = resolve_feature_selection(feature_set=resolved_feature_set)
    feature_config = FeatureLibrary.get_feature_fields_by_set(resolved_feature_set)
    uses_derived_features = bool(selection.derived_columns)

    if resolved_feature_set == 'semantic_all_features_v1':
        model_name = f"{model}_semantic_all_features"
    elif resolved_feature_set == 'price_volume_expression_core_v1':
        model_name = model
    elif resolved_feature_set == 'price_volume_fundamental_event_core_v1':
        model_name = f"{model}_margin_extended"
    elif resolved_feature_set in {'legacy_phase1_raw_v1', 'legacy_phase12_raw_v1', 'legacy_phase123_raw_v1'}:
        model_name = f"{model}_{feature_set}"
    else:
        model_name = f"{model}_extended"
    log.info(
        f"Using feature_set={feature_set} resolved_feature_set={resolved_feature_set} "
        f"with {len(feature_config)} native features"
    )
    model_instance = QlibNativeModel(
        name=model_name,
        model_config=qlib_config,
        feature_config=feature_config,
    )
    model_instance.params = {
        **model_instance.params,
        "feature_set_name": resolved_feature_set,
        "feature_set_alias": feature_set,
        "feature_ids": list(selection.feature_ids),
        "native_qlib_fields": list(selection.native_qlib_fields),
    }

    health = assert_qlib_data_ready(end, selection.native_qlib_fields, universe=universe)
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
        "feature_set": resolved_feature_set,
        "feature_set_alias": feature_set,
        "feature_id_count": len(selection.feature_ids),
        "train_window": f"{start} to {end}",
        "universe": universe,
    }

    try:
        training_summary = {}
        sanitize_contract = {}
        constant_cols: list[str] = []
        if uses_derived_features:
            trained_model, semantic_feature_columns, training_summary, sanitize_contract = _fit_semantic_all_features_model(
                model_name=model_name,
                model_config=qlib_config,
                feature_set_name=resolved_feature_set,
                universe=universe,
                start=start,
                end=end,
            )
            model_instance.model = trained_model
            model_instance.feature_config = semantic_feature_columns
            model_instance.training_summary = training_summary
            constant_cols = list(sanitize_contract.get('dropped_columns', []))
            model_instance.preprocess_params = {
                'method': 'identity',
                'fillna': float(sanitize_contract.get('fillna', 0.0)),
            }
            model_instance.params = {
                **model_instance.params,
                'derived_feature_columns': list(selection.derived_columns),
                'uses_derived_features': True,
                'feature_name_map': dict(sanitize_contract.get('feature_name_map', {})),
                'constant_feature_columns': constant_cols,
                'sanitized_feature_columns': list(sanitize_contract.get('sanitized_feature_columns', [])),
                'sanitize_feature_contract': sanitize_contract,
            }
        else:
            model_instance.fit(codes, start, end)
            training_summary = model_instance.training_summary or {}

        root_path = cfg.get_path("root")
        if root_path is None:
            raise ValueError("Root path not configured")
        save_path = root_path / "models" / model_name
        model_instance.save(save_path)
        feature_selection_path = save_path / "feature_selection.yaml"

        with open(feature_selection_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                {
                    "feature_set_name": resolved_feature_set,
                    "feature_set_alias": feature_set,
                    "feature_ids": selection.feature_ids,
                    "native_qlib_fields": selection.native_qlib_fields,
                    "feature_names": selection.feature_names,
                    "derived_columns": selection.derived_columns,
                },
                handle,
                sort_keys=False,
                allow_unicode=True,
            )

        training_summary = model_instance.training_summary or training_summary or {}
        if training_summary:
            summary_path = save_path / "training_summary.csv"
            pd.DataFrame([training_summary]).to_csv(summary_path, index=False)
            log.info(f"Training summary saved to {summary_path}")
            log.info(
                f"Training metrics | mse={training_summary.get('mse')} "
                f"rank_ic={training_summary.get('rank_ic')} samples={training_summary.get('sample_count')}"
            )
            model_info["sample_count"] = training_summary.get("sample_count")
            if uses_derived_features:
                model_info["derived_feature_count"] = len(selection.derived_columns)
                model_info["feature_count_constant_dropped"] = len(constant_cols)

        notes.append(f"Feature count: {len(feature_config)}")
        if uses_derived_features:
            notes.append(f"Derived feature count: {len(selection.derived_columns)}")
        
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
        report.artifacts["feature_selection"] = str(save_path / "feature_selection.yaml")
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
