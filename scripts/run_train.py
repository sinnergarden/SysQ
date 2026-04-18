"""
Primary training entrypoint.

Purpose:
- train the selected model on a chosen universe/window
- optionally run a minimal post-train backtest
- emit structured training report

Typical usage:
- python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set extended
- python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --bundle_id bundle_semantic_demo

Key args:
- --model: currently qlib_lgbm
- --universe: csi300 / all
- --start / --end: training window
- --feature_set: legacy feature-set input path
- --bundle_id: manifest-driven bundle input path
- --run_backtest: run a minimal validation backtest after training
- --no_report: skip JSON run report
"""

import os
import sys
import time
from pathlib import Path
from typing import Any

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import assert_qlib_data_ready
from qsys.reports.train import TrainingReport
from qsys.reports.unified_schema import training_contract_payload, write_json
from qsys.research import (
    ManifestValidationError,
    get_mainline_spec_by_bundle_id,
    get_mainline_spec_by_feature_set,
    load_factor_registry,
    resolve_mainline_feature_config,
    resolve_mainline_object_name,
)
from qsys.utils.logger import log

SUPPORTED_FEATURE_SET_CHOICES = [
    'alpha158',
    'extended',
    'extended_absnorm',
    'margin_extended',
    'phase1',
    'phase12',
    'phase123',
    'phase123_absnorm',
    'semantic_all_features',
    'semantic_all_features_absnorm',
]


def _resolve_legacy_feature_config(feature_set: str) -> tuple[list[str], str]:
    from qsys.feature.library import FeatureLibrary

    if feature_set == 'extended':
        return FeatureLibrary.get_alpha158_extended_config(), 'extended'
    if feature_set == 'extended_absnorm':
        return FeatureLibrary.get_alpha158_extended_absnorm_config(), 'extended_absnorm'
    if feature_set == 'margin_extended':
        return FeatureLibrary.get_alpha158_margin_extended_config(), 'margin_extended'
    if feature_set == 'phase1':
        return FeatureLibrary.get_research_phase1_config(), 'phase1'
    if feature_set == 'phase12':
        return FeatureLibrary.get_research_phase12_config(), 'phase12'
    if feature_set == 'phase123':
        return FeatureLibrary.get_research_phase123_config(), 'phase123'
    if feature_set == 'phase123_absnorm':
        return FeatureLibrary.get_research_phase123_absnorm_config(), 'phase123_absnorm'
    if feature_set == 'semantic_all_features':
        return FeatureLibrary.get_semantic_all_features_config(), 'semantic_all_features'
    if feature_set == 'semantic_all_features_absnorm':
        return FeatureLibrary.get_semantic_all_features_absnorm_config(), 'semantic_all_features_absnorm'
    return FeatureLibrary.get_alpha158_config(), 'alpha158'


def _model_name_for_input(model: str, feature_set: str | None, input_mode: str, bundle_id: str | None) -> str:
    if input_mode == 'bundle_id':
        if not bundle_id:
            raise ValueError('bundle_id must be provided when input_mode=bundle_id')
        mainline_spec = get_mainline_spec_by_bundle_id(bundle_id)
        if mainline_spec is not None:
            return mainline_spec.model_name
        safe_bundle = bundle_id.replace('/', '_').replace('@', '_at_')
        return f"{model}_bundle_{safe_bundle}"
    if feature_set == 'alpha158':
        return model
    if feature_set == 'margin_extended':
        return f"{model}_margin_extended"
    if feature_set in {'extended_absnorm', 'phase1', 'phase12', 'phase123', 'phase123_absnorm'}:
        return f"{model}_{feature_set}"
    if feature_set == 'semantic_all_features':
        return f"{model}_semantic_all_features"
    if feature_set == 'semantic_all_features_absnorm':
        return f"{model}_semantic_all_features_absnorm"
    return f"{model}_extended"


def _resolve_variant_to_feature_fields(base_factor_id: str, transform_chain: list[str]) -> list[str]:
    normalized_chain = [step.strip().lower() for step in (transform_chain or []) if step and step.strip()]
    if not normalized_chain:
        raise ValueError(f"Variant for factor '{base_factor_id}' has empty transform_chain")

    mainline_feature_config = resolve_mainline_feature_config(base_factor_id)
    if mainline_feature_config is not None:
        return list(mainline_feature_config)

    if normalized_chain in (['identity'], ['raw']):
        return [base_factor_id]
    raise ValueError(
        f"Current training bundle compat layer supports only raw/identity variants; "
        f"got base_factor_id={base_factor_id}, transform_chain={transform_chain}"
    )


def resolve_training_input(
    feature_set: str | None,
    bundle_id: str | None,
    *,
    feature_set_explicit: bool = False,
) -> dict[str, Any]:
    if bundle_id and feature_set_explicit:
        raise ValueError('Provide only one of explicit feature_set or bundle_id')

    if bundle_id:
        try:
            registry = load_factor_registry()
        except ManifestValidationError as exc:
            raise ValueError(f"Failed to load factor manifests: {exc}") from exc

        bundle = registry.bundles.get(bundle_id or '')
        if bundle is None:
            raise ValueError(f"Unknown bundle_id: {bundle_id}")

        feature_config: list[str] = []
        for variant_id in bundle.factor_variant_ids:
            variant = registry.variants.get(variant_id)
            if variant is None:
                raise ValueError(f"Bundle '{bundle.bundle_id}' references unknown variant_id '{variant_id}'")
            for field in _resolve_variant_to_feature_fields(variant.base_factor_id, variant.transform_chain):
                if field not in feature_config:
                    feature_config.append(field)

        mainline_spec = get_mainline_spec_by_bundle_id(bundle.bundle_id)
        return {
            'input_mode': 'bundle_id',
            'feature_set': mainline_spec.legacy_feature_set_alias if mainline_spec else None,
            'bundle_id': bundle.bundle_id,
            'factor_variants': bundle.factor_variant_ids,
            'bundle_source': 'research/factors/bundles',
            'bundle_resolution_status': 'resolved_via_manifest_compat_layer',
            'object_layer_status': 'bundle_manifest_resolved_for_train_v1',
            'feature_config': feature_config,
            'mainline_object_name': mainline_spec.mainline_object_name if mainline_spec else resolve_mainline_object_name(bundle_id=bundle.bundle_id),
            'legacy_feature_set_alias': mainline_spec.legacy_feature_set_alias if mainline_spec else None,
        }

    resolved_feature_set = feature_set or 'extended'
    feature_config, resolved_feature_set = _resolve_legacy_feature_config(resolved_feature_set)
    mainline_spec = get_mainline_spec_by_feature_set(resolved_feature_set)
    return {
        'input_mode': 'feature_set',
        'feature_set': resolved_feature_set,
        'bundle_id': mainline_spec.bundle_id if mainline_spec else None,
        'factor_variants': [],
        'bundle_source': None,
        'bundle_resolution_status': 'not_applicable',
        'object_layer_status': 'legacy_feature_set_path',
        'feature_config': feature_config,
        'mainline_object_name': mainline_spec.mainline_object_name if mainline_spec else resolve_mainline_object_name(feature_set=resolved_feature_set),
        'legacy_feature_set_alias': resolved_feature_set,
    }


def build_training_snapshot(
    *,
    input_payload: dict[str, Any],
    model_name: str,
    model_type: str,
    universe: str,
    start: str,
    end: str,
    infer_date: str,
    label_horizon: int,
    training_summary: dict[str, Any],
    mlflow_root: str | None,
) -> dict[str, Any]:
    return {
        'model_name': model_name,
        'model_type': model_type,
        'input_mode': input_payload['input_mode'],
        'feature_set': input_payload.get('feature_set'),
        'bundle_id': input_payload.get('bundle_id'),
        'mainline_object_name': input_payload.get('mainline_object_name'),
        'legacy_feature_set_alias': input_payload.get('legacy_feature_set_alias'),
        'factor_variants': list(input_payload.get('factor_variants') or []),
        'bundle_source': input_payload.get('bundle_source'),
        'bundle_resolution_status': input_payload.get('bundle_resolution_status'),
        'object_layer_status': input_payload.get('object_layer_status'),
        'universe': universe,
        'train_start': start,
        'train_end': end,
        'infer_date': infer_date,
        'label_spec': {
            'label_type': 'forward_return',
            'label_horizon': label_horizon,
        },
        'split_spec': {
            'train_start': start,
            'train_end_requested': end,
            'train_end_effective': training_summary.get('train_end_effective') or training_summary.get('train_end'),
            'infer_date': infer_date,
            'universe': universe,
        },
        'model_spec': {
            'model_type': model_type,
            'model_name': model_name,
            'training_mode': training_summary.get('training_mode'),
            'mlflow_root': mlflow_root,
        },
        'strategy_spec': {},
    }


@click.command()
@click.option('--model', default='qlib_lgbm', help='Model name')
@click.option('--universe', default='csi300', help='Stock universe (e.g., csi300, all)')
@click.option('--start', default='2020-01-01', help='Start date')
@click.option('--end', default='2023-12-31', help='End date')
@click.option('--run_backtest', is_flag=True, help='Run minimal backtest after training')
@click.option('--backtest_start', default=None, help='Backtest start date; defaults to last 40 trading days window start')
@click.option('--backtest_end', default=None, help='Backtest end date; defaults to training end date')
@click.option('--feature_set', type=click.Choice(SUPPORTED_FEATURE_SET_CHOICES, case_sensitive=False), default='extended', show_default=True, help='Legacy feature-set input path')
@click.option('--bundle_id', default=None, help='Bundle manifest id used as the training input object')
@click.option('--infer_date', default=None, help='Inference/signal date used for label maturity checks; defaults to --end')
@click.option('--label_horizon', default=5, type=int, show_default=True, help='Trading-day horizon used by label maturity cutoff')
@click.option('--mlflow_root', default=None, help='Optional MLflow tracking root for this training run; defaults to the project root behavior')
@click.option('--no_report', is_flag=True, help='Skip generating the structured report')
@click.pass_context
def main(ctx, model, universe, start, end, run_backtest, backtest_start, backtest_end, feature_set, bundle_id, infer_date, label_horizon, mlflow_root, no_report):
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

    feature_set_explicit = ctx.get_parameter_source('feature_set') == click.core.ParameterSource.COMMANDLINE
    try:
        input_payload = resolve_training_input(
            feature_set=feature_set,
            bundle_id=bundle_id,
            feature_set_explicit=feature_set_explicit,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    feature_config = input_payload['feature_config']
    resolved_feature_set = input_payload.get('feature_set')
    model_name = _model_name_for_input(model, resolved_feature_set, input_payload['input_mode'], input_payload.get('bundle_id'))
    input_label = input_payload.get('bundle_id') or resolved_feature_set or input_payload.get('mainline_object_name')
    log.info(f"Using {input_payload['input_mode']}={input_label} with {len(feature_config)} features")
    model_instance = QlibNativeModel(
        name=model_name,
        model_config=qlib_config,
        feature_config=feature_config,
    )

    health = assert_qlib_data_ready(end, model_instance.feature_config, universe=universe)
    log.info("\n" + health.to_markdown())

    data_status = {}
    try:
        adapter = QlibAdapter()
        adapter.init_qlib()
        status_report = adapter.get_data_status_report()
        data_status = {
            'raw_latest': status_report.get('raw_latest'),
            'qlib_latest': status_report.get('qlib_latest'),
            'aligned': status_report.get('aligned', False),
            'health_ok': True,
        }
    except Exception as e:
        log.warning(f"Could not get data status: {e}")
        data_status = {'health_ok': False}

    model_info = {
        'model_name': model_name,
        'feature_set': resolved_feature_set,
        'bundle_id': input_payload.get('bundle_id'),
        'mainline_object_name': input_payload.get('mainline_object_name'),
        'legacy_feature_set_alias': input_payload.get('legacy_feature_set_alias'),
        'input_mode': input_payload['input_mode'],
        'train_window': f"{start} to {end}",
        'universe': universe,
    }

    try:
        if mlflow_root:
            mlflow_root_path = Path(mlflow_root).expanduser()
            if not mlflow_root_path.is_absolute():
                mlflow_root_path = (project_root / mlflow_root_path).resolve()
            mlflow_root_path.mkdir(parents=True, exist_ok=True)
            os.environ['MLFLOW_TRACKING_URI'] = mlflow_root_path.as_uri()
            notes.append(f"MLflow root: {mlflow_root_path}")
        else:
            (project_root / 'mlruns').mkdir(parents=True, exist_ok=True)
            mlflow_root_path = None
        model_instance.fit(codes, start, end, infer_date=infer_date or end, label_horizon=label_horizon)

        root_path = cfg.get_path('root')
        if root_path is None:
            raise ValueError('Root path not configured')
        save_path = root_path / 'models' / model_name

        training_summary = dict(model_instance.training_summary or {})
        training_contract = training_contract_payload(
            training_mode=training_summary.get('training_mode'),
            train_end_requested=training_summary.get('train_end_requested'),
            train_end_effective=training_summary.get('train_end_effective'),
            infer_date=training_summary.get('infer_date'),
            last_train_sample_date=training_summary.get('last_train_sample_date'),
            max_label_date_used=training_summary.get('max_label_date_used'),
            is_label_mature_at_infer_time=training_summary.get('is_label_mature_at_infer_time'),
            mlflow_root=str(mlflow_root_path) if mlflow_root_path else None,
        )
        training_summary.update({k: v for k, v in training_contract.items() if v is not None})
        model_instance.training_summary = training_summary
        model_instance.save(save_path)
        if training_summary:
            summary_path = save_path / 'training_summary.csv'
            pd.DataFrame([training_summary]).to_csv(summary_path, index=False)
            write_json(save_path / 'training_summary.json', training_summary)
            log.info(f"Training summary saved to {summary_path}")
            log.info(
                f"Training metrics | mse={training_summary.get('mse')} "
                f"rank_ic={training_summary.get('rank_ic')} samples={training_summary.get('sample_count')}"
            )
            model_info['sample_count'] = training_summary.get('sample_count')
            model_info.update(training_contract)

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

    duration = time.time() - start_time

    if not no_report:
        report = TrainingReport.generate(
            signal_date=end,
            data_status=data_status,
            model_info=model_info,
            training_metrics=training_summary,
            feature_count=len(feature_config),
            sample_count=training_summary.get('sample_count', 0),
            duration_seconds=duration,
            backtest_info=backtest_info,
            blockers=blockers,
            notes=notes,
        )
        report.artifacts['training_summary'] = str(save_path / 'training_summary.json')
        report.artifacts['training_summary_csv'] = str(save_path / 'training_summary.csv')
        snapshot_payload = build_training_snapshot(
            input_payload=input_payload,
            model_name=model_name,
            model_type=model,
            universe=universe,
            start=start,
            end=end,
            infer_date=infer_date or end,
            label_horizon=label_horizon,
            training_summary=training_summary,
            mlflow_root=str(mlflow_root_path) if mlflow_root_path else None,
        )
        report.artifacts['config_snapshot'] = write_json(save_path / 'config_snapshot.json', snapshot_payload)
        report.artifacts['model_path'] = str(save_path)

        report_path = TrainingReport.save(report)
        log.info(f"Training report saved to: {report_path}")

        print("\n" + "=" * 60)
        print(report.to_markdown())


if __name__ == '__main__':
    main()
