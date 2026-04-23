#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.model.zoo.qlib_native import QlibNativeModel
from qsys.research.ablation import (
    DEFAULT_BAD_FIELDS,
    build_add_back_feature_config,
    build_window_stability_summary,
    markdown_table,
    summarize_rolling_metrics,
    write_ablation_summary_csv,
    write_markdown,
    write_stability_summary_csv,
)
from qsys.research.mainline import MAINLINE_OBJECTS, resolve_mainline_feature_config
from qsys.research.rolling import build_rolling_windows, compute_window_metrics, snapshot_train_window
from qsys.research.spec import V1_IMPL1_FIXED_LABEL_HORIZON
from scripts.run_backtest import build_backtest_lineage, load_training_snapshot

QLIB_CONFIG = {
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


def _train_custom_model(model_name: str, feature_config: list[str], *, start: str, end: str, infer_date: str) -> Path:
    model = QlibNativeModel(name=model_name, model_config=QLIB_CONFIG, feature_config=feature_config)
    model.fit('csi300', start, end, infer_date=infer_date, label_horizon=5)
    root = cfg.get_path('root')
    if root is None:
        raise ValueError('Root path not configured')
    save_path = root / 'models' / model_name
    model.save(save_path)
    snapshot = {
        'input_mode': 'ablation_feature_list',
        'feature_set': None,
        'bundle_id': None,
        'mainline_object_name': model_name,
        'legacy_feature_set_alias': model_name,
        'factor_variants': [],
        'bundle_resolution_status': 'custom_ablation',
        'object_layer_status': 'custom_ablation',
        'label_spec': {'label_type': 'forward_return', 'label_horizon': V1_IMPL1_FIXED_LABEL_HORIZON},
        'split_spec': {'train_start': start, 'train_end': end, 'train_end_effective': end},
        'model_spec': {'model_name': model_name, 'model_type': 'qlib_lgbm', 'model_path': str(save_path)},
        'strategy_spec': {'strategy_type': 'rank_topk', 'top_k': 5, 'rebalance_freq': 'weekly'},
        'feature_config': feature_config,
    }
    (save_path / 'config_snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    return save_path


def _run_rolling(model_path: Path, object_name: str, *, start: str, end: str, out_dir: Path) -> dict:
    snapshot = load_training_snapshot(model_path)
    lineage = build_backtest_lineage(snapshot)
    train_start, train_end = snapshot_train_window(snapshot)
    windows = build_rolling_windows(start=start, end=end, train_start=train_start, train_end=train_end)
    rows = []
    for window in windows:
        engine = BacktestEngine(
            model_path=model_path,
            universe='csi300',
            start_date=window.test_start,
            end_date=window.test_end,
            top_k=5,
            strategy_type='rank_topk',
            label_horizon=V1_IMPL1_FIXED_LABEL_HORIZON,
            strategy_params={},
        )
        result = engine.run()
        spec = type('Spec', (), {
            'mainline_object_name': object_name,
            'bundle_id': object_name,
            'legacy_feature_set_alias': object_name,
        })()
        rows.append(compute_window_metrics(spec=spec, window=window, daily_result=result, signal_metrics=engine.last_signal_metrics or {}))
    metrics = pd.DataFrame(rows)
    obj_dir = out_dir / object_name
    obj_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(obj_dir / 'rolling_metrics.csv', index=False)
    summary = summarize_rolling_metrics(metrics)
    summary.update({'mainline_object_name': object_name, 'lineage': lineage})
    (obj_dir / 'rolling_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


@click.command(name='run_trimmed_ablation')
@click.option('--start', default='2025-01-02')
@click.option('--end', default='2026-03-20')
@click.option('--train_start', default='2020-01-01')
@click.option('--train_end', default='2026-03-13')
@click.option('--infer_date', default='2026-03-20')
@click.option('--output_dir', default='experiments/mainline_ablation')
def main(start: str, end: str, train_start: str, train_end: str, infer_date: str, output_dir: str) -> None:
    out_dir = (project_root / output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for baseline_name in ['feature_254_trimmed', 'feature_254_absnorm_trimmed']:
        metrics = pd.read_csv(project_root / 'experiments' / 'mainline_trimmed_compare' / baseline_name / 'rolling_metrics.csv')
        summary = summarize_rolling_metrics(metrics)
        rows.append({
            'experiment_name': baseline_name,
            'base_object_name': baseline_name,
            'added_back_field': None,
            'removed_field': None,
            **summary,
        })

    base_fields = resolve_mainline_feature_config('feature_254_trimmed')
    for field in DEFAULT_BAD_FIELDS:
        feature_config = build_add_back_feature_config(base_fields, field)
        model_name = f'qlib_lgbm_feature_254_trimmed_addback_{field}'
        model_path = _train_custom_model(model_name, feature_config, start=train_start, end=train_end, infer_date=infer_date)
        summary = _run_rolling(model_path, f'feature_254_trimmed_addback_{field}', start=start, end=end, out_dir=out_dir / 'rolling')
        rows.append({
            'experiment_name': f'feature_254_trimmed_addback_{field}',
            'base_object_name': 'feature_254_trimmed',
            'added_back_field': field,
            'removed_field': None,
            **{k: summary.get(k) for k in ['rolling_total_return_mean','rolling_rankic_mean','rolling_rankic_std','rolling_max_drawdown_worst','rolling_turnover_mean']},
        })

    ablation_df = pd.DataFrame(rows)
    ablation_csv = write_ablation_summary_csv(out_dir / 'ablation_summary.csv', rows)
    ablation_md = write_markdown(out_dir / 'ablation_summary.md', '# Trimmed ablation summary\n\n' + markdown_table(ablation_df))

    stability_rows = []
    for name, metrics_path in [
        ('feature_173', project_root / 'experiments' / 'mainline_trimmed_compare' / 'feature_173' / 'rolling_metrics.csv'),
        ('feature_254_trimmed', project_root / 'experiments' / 'mainline_trimmed_compare' / 'feature_254_trimmed' / 'rolling_metrics.csv'),
        ('feature_254_absnorm_trimmed', project_root / 'experiments' / 'mainline_trimmed_compare' / 'feature_254_absnorm_trimmed' / 'rolling_metrics.csv'),
    ]:
        stability_rows.append(build_window_stability_summary(name, pd.read_csv(metrics_path)))
    stability_csv = write_stability_summary_csv(out_dir / 'window_stability_summary.csv', stability_rows)
    stability_md = write_markdown(out_dir / 'window_stability_summary.md', '# Window stability summary\n\n' + markdown_table(pd.read_csv(out_dir / 'window_stability_summary.csv')))

    print(f'ablation_summary={ablation_csv}')
    print(f'ablation_markdown={ablation_md}')
    print(f'window_stability_summary={stability_csv}')
    print(f'window_stability_markdown={stability_md}')


if __name__ == '__main__':
    main()
