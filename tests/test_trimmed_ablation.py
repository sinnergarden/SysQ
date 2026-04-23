from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.research.ablation import build_add_back_feature_config, build_window_stability_summary, summarize_rolling_metrics
from scripts.run_trimmed_ablation import main as trimmed_ablation_main


def test_single_field_add_back_logic_is_stable() -> None:
    base = ['a', 'b']
    assert build_add_back_feature_config(base, 'c') == ['a', 'b', 'c']
    assert build_add_back_feature_config(base, 'a') == ['a', 'b']


def test_ablation_artifact_contract_summary_fields() -> None:
    df = pd.DataFrame([
        {'total_return': 0.1, 'RankIC': 0.02, 'max_drawdown': -0.1, 'turnover': 0.8},
        {'total_return': -0.02, 'RankIC': -0.01, 'max_drawdown': -0.12, 'turnover': 0.9},
    ])
    summary = summarize_rolling_metrics(df)
    assert set(summary.keys()) == {
        'rolling_window_count',
        'rolling_total_return_mean',
        'rolling_rankic_mean',
        'rolling_rankic_std',
        'rolling_max_drawdown_worst',
        'rolling_turnover_mean',
    }


def test_window_stability_summary_fields() -> None:
    df = pd.DataFrame([
        {'window_id': 'w1', 'test_start': '2025-01-01', 'test_end': '2025-02-01', 'total_return': 0.1, 'RankIC': 0.02},
        {'window_id': 'w2', 'test_start': '2025-02-02', 'test_end': '2025-03-01', 'total_return': -0.02, 'RankIC': -0.01},
        {'window_id': 'w3', 'test_start': '2025-03-02', 'test_end': '2025-04-01', 'total_return': 0.03, 'RankIC': 0.01},
    ])
    summary = build_window_stability_summary('feature_254_trimmed', df)
    assert summary['mainline_object_name'] == 'feature_254_trimmed'
    assert 'best_3_windows' in summary and len(summary['best_3_windows']) == 3
    assert 'worst_3_windows' in summary and len(summary['worst_3_windows']) == 3


def test_trimmed_ablation_script_writes_contract_files(tmp_path: Path) -> None:
    base_root = tmp_path / 'experiments' / 'mainline_trimmed_compare'
    for name in ['feature_173', 'feature_254_trimmed', 'feature_254_absnorm_trimmed']:
        obj = base_root / name
        obj.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {'window_id': 'w1', 'test_start': '2025-01-01', 'test_end': '2025-02-01', 'total_return': 0.1, 'RankIC': 0.02, 'max_drawdown': -0.1, 'turnover': 0.8},
            {'window_id': 'w2', 'test_start': '2025-02-02', 'test_end': '2025-03-01', 'total_return': -0.02, 'RankIC': -0.01, 'max_drawdown': -0.12, 'turnover': 0.9},
        ]).to_csv(obj / 'rolling_metrics.csv', index=False)

    def _fake_train(model_name, feature_config, *, start, end, infer_date):
        model_dir = tmp_path / 'data' / 'models' / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'config_snapshot.json').write_text(json.dumps({'split_spec': {'train_start': start, 'train_end_effective': end}}), encoding='utf-8')
        return model_dir

    def _fake_run(model_path, object_name, *, start, end, out_dir):
        obj = out_dir / object_name
        obj.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {'window_id': 'w1', 'test_start': start, 'test_end': end, 'total_return': 0.01, 'RankIC': 0.01, 'max_drawdown': -0.1, 'turnover': 0.8}
        ]).to_csv(obj / 'rolling_metrics.csv', index=False)
        return {
            'rolling_total_return_mean': 0.01,
            'rolling_rankic_mean': 0.01,
            'rolling_rankic_std': 0.0,
            'rolling_max_drawdown_worst': -0.1,
            'rolling_turnover_mean': 0.8,
        }

    runner = CliRunner()
    with patch('scripts.run_trimmed_ablation.project_root', tmp_path), \
         patch('scripts.run_trimmed_ablation.resolve_mainline_feature_config', return_value=['x', 'y']), \
         patch('scripts.run_trimmed_ablation._train_custom_model', side_effect=_fake_train), \
         patch('scripts.run_trimmed_ablation._run_rolling', side_effect=_fake_run):
        result = runner.invoke(trimmed_ablation_main, ['--output_dir', 'experiments/mainline_ablation'])
    assert result.exit_code == 0, result.output
    out = tmp_path / 'experiments' / 'mainline_ablation'
    assert (out / 'ablation_summary.csv').exists()
    assert (out / 'ablation_summary.md').exists()
    assert (out / 'window_stability_summary.csv').exists()
    assert (out / 'window_stability_summary.md').exists()
