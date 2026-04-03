"""
Rolling backtest for weekly retraining over a held-out evaluation window.

Default protocol:
- universe: csi300
- feature_set: semantic_all_features_v1
- rolling train window: 4 years
- eval window: 1 trading week (5 trading days)
- test_start: 2025-01-02
- equal-weight top_k strategy
- init cash: 500000 CNY
- transaction cost model inherited from MatchEngine:
  commission=0.03%, stamp_duty=0.10% on sells, min_commission=5 CNY, slippage=0.10%
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import click
import numpy as np
import pandas as pd
import yaml

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qlib.data import D

from qsys.analysis.tearsheet import PerformanceAnalyzer
from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.feature.registry import resolve_feature_selection
from qsys.feature.runtime import build_feature_panel
from qsys.model.zoo.qlib_native import QlibNativeModel
from qsys.reports.backtest import BacktestReport
from qsys.strategy.engine import BufferedTopKStrategy, StrategyEngine
from qsys.strategy.generator import load_model_artifact_metadata
from qsys.trader.account import Account
from qsys.utils.logger import log
from scripts.run_train import _fit_semantic_all_features_model


def _make_model_config() -> dict:
    return {
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
        },
    }


def _build_scores_for_window(model_instance: QlibNativeModel, feature_set: str, universe: str, start: str, end: str) -> pd.DataFrame:
    panel, _ = build_feature_panel(
        feature_set=feature_set,
        universe=universe,
        start_date=start,
        end_date=end,
        include_close=False,
    )
    if panel.empty:
        return pd.DataFrame()
    panel = panel.set_index(['trade_date', 'ts_code']).sort_index()
    panel.index = panel.index.rename(['datetime', 'instrument'])
    inference_df = panel[model_instance.feature_config]
    scores = model_instance.predict(inference_df)
    if isinstance(scores, pd.Series):
        return scores.to_frame('score')
    if isinstance(scores, pd.DataFrame):
        if 'score' in scores.columns:
            return scores[['score']]
        if scores.shape[1] == 1:
            out = scores.copy()
            out.columns = ['score']
            return out
    raise ValueError('Unexpected prediction output format')


def _compute_extended_metrics(daily_df: pd.DataFrame, trades_df: pd.DataFrame, init_cash: float) -> dict:
    if daily_df.empty:
        return {}
    df = daily_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['return'] = df['total_assets'].pct_change().fillna(0.0)
    df['equity_peak'] = df['total_assets'].cummax()
    df['drawdown'] = df['total_assets'] / df['equity_peak'] - 1.0

    total_days = len(df)
    total_return = df['total_assets'].iloc[-1] / init_cash - 1.0
    annual_factor = 252
    annual_return = (1.0 + total_return) ** (annual_factor / total_days) - 1.0 if total_days > 0 else 0.0
    daily_std = float(df['return'].std())
    annual_vol = daily_std * math.sqrt(annual_factor) if total_days > 1 else 0.0
    sharpe = float(df['return'].mean() / daily_std * math.sqrt(annual_factor)) if daily_std > 0 else 0.0
    max_drawdown = float(df['drawdown'].min())

    positive_days = int((df['return'] > 0).sum())
    negative_days = int((df['return'] < 0).sum())
    flat_days = int((df['return'] == 0).sum())
    daily_win_rate = positive_days / (positive_days + negative_days) if (positive_days + negative_days) else 0.0

    total_turnover = float(df['daily_turnover'].sum()) if 'daily_turnover' in df.columns else 0.0
    total_fee = float(df['daily_fee'].sum()) if 'daily_fee' in df.columns else 0.0
    avg_turnover = float(df['daily_turnover'].mean()) if 'daily_turnover' in df.columns else 0.0
    fee_rate = total_fee / total_turnover if total_turnover > 0 else 0.0

    filled_trades = trades_df[trades_df['status'] == 'filled'].copy() if not trades_df.empty else pd.DataFrame()
    trade_count = int(len(filled_trades))
    buy_count = int((filled_trades['side'] == 'buy').sum()) if trade_count else 0
    sell_count = int((filled_trades['side'] == 'sell').sum()) if trade_count else 0
    avg_trade_fee = float(filled_trades['fee'].mean()) if trade_count else 0.0
    avg_deal_price = float(filled_trades['deal_price'].mean()) if trade_count else 0.0
    avg_filled_amount = float(filled_trades['filled_amount'].mean()) if trade_count else 0.0

    monthly_returns = (
        df.set_index('date')['total_assets'].resample('M').last().pct_change().dropna()
        if total_days > 1 else pd.Series(dtype=float)
    )
    monthly_win_rate = float((monthly_returns > 0).mean()) if not monthly_returns.empty else 0.0

    return {
        'init_cash': float(init_cash),
        'final_assets': float(df['total_assets'].iloc[-1]),
        'total_return': float(total_return),
        'annual_return': float(annual_return),
        'annual_volatility': float(annual_vol),
        'sharpe': float(sharpe),
        'max_drawdown': float(max_drawdown),
        'trading_days': int(total_days),
        'positive_days': positive_days,
        'negative_days': negative_days,
        'flat_days': flat_days,
        'daily_win_rate': float(daily_win_rate),
        'monthly_win_rate': float(monthly_win_rate),
        'total_turnover': float(total_turnover),
        'avg_daily_turnover': float(avg_turnover),
        'total_fee': float(total_fee),
        'avg_fee_rate': float(fee_rate),
        'trade_count': trade_count,
        'buy_count': buy_count,
        'sell_count': sell_count,
        'avg_trade_fee': float(avg_trade_fee),
        'avg_deal_price': float(avg_deal_price),
        'avg_filled_amount': float(avg_filled_amount),
    }


def _window_starts(trade_dates: list[pd.Timestamp], step: int) -> list[pd.Timestamp]:
    starts = trade_dates[::step]
    if starts and starts[-1] != trade_dates[-1]:
        starts.append(trade_dates[-1])
    deduped = []
    seen = set()
    for dt in starts:
        if dt not in seen:
            deduped.append(dt)
            seen.add(dt)
    return deduped


@click.command()
@click.option('--model', default='qlib_lgbm', show_default=True)
@click.option('--feature_set', default='semantic_all_features_v1', show_default=True)
@click.option('--universe', default='csi300', show_default=True)
@click.option('--train_start', default='2020-01-01', show_default=True)
@click.option('--train_years', default=4, show_default=True, type=int, help='Fixed rolling training window in years')
@click.option('--test_start', default='2025-01-02', show_default=True)
@click.option('--test_end', default=None, help='Defaults to latest trading date in qlib calendar')
@click.option('--eval_days', default=5, show_default=True, type=int, help='Trading days per evaluation window')
@click.option('--top_k', default=10, show_default=True)
@click.option('--strategy_variant', default='baseline', type=click.Choice(['baseline', 'buffered']), show_default=True)
@click.option('--hold_k', default=15, show_default=True, type=int, help='Buffered strategy sell threshold rank')
@click.option('--init_cash', default=500000.0, show_default=True, type=float)
@click.option('--output_dir', default=None, help='Optional report directory')
def main(model, feature_set, universe, train_start, train_years, test_start, test_end, eval_days, top_k, strategy_variant, hold_k, init_cash, output_dir):
    if model != 'qlib_lgbm':
        raise click.ClickException('Only qlib_lgbm is supported currently')

    started_at = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()

    calendar = [pd.Timestamp(x) for x in D.calendar(start_time=test_start, end_time=test_end)]
    if not calendar:
        raise click.ClickException('No trading dates found for requested test window')
    actual_test_end = pd.Timestamp(test_end).strftime('%Y-%m-%d') if test_end else calendar[-1].strftime('%Y-%m-%d')
    trade_dates = [pd.Timestamp(x) for x in D.calendar(start_time=test_start, end_time=actual_test_end)]
    starts = _window_starts(trade_dates, eval_days)

    resolved_feature_set = FeatureLibrary.normalize_feature_set_name(feature_set)
    selection = resolve_feature_selection(feature_set=resolved_feature_set)
    model_config = _make_model_config()
    model_name = f'{model}_rolling_{resolved_feature_set}_{strategy_variant}'

    timestamp = pd.Timestamp.now(tz='Asia/Shanghai').strftime('%Y%m%d_%H%M%S')
    report_dir = Path(output_dir) if output_dir else project_root / 'reports' / 'rolling_backtest' / f'{timestamp}_{resolved_feature_set}'
    report_dir.mkdir(parents=True, exist_ok=True)

    config_payload = {
        'model': model,
        'model_name': model_name,
        'feature_set': resolved_feature_set,
        'feature_set_alias': feature_set,
        'universe': universe,
        'train_start_floor': train_start,
        'train_years': train_years,
        'test_start': test_start,
        'test_end': actual_test_end,
        'eval_days': eval_days,
        'top_k': top_k,
        'hold_k': hold_k,
        'init_cash': init_cash,
        'strategy': strategy_variant,
        'match_engine': {
            'commission': 0.0003,
            'stamp_duty_sell': 0.001,
            'min_commission': 5.0,
            'slippage': 0.001,
        },
        'selection': {
            'feature_ids': list(selection.feature_ids),
            'native_qlib_fields': list(selection.native_qlib_fields),
            'derived_columns': list(selection.derived_columns),
        },
    }
    with open(report_dir / 'config.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(config_payload, f, sort_keys=False, allow_unicode=True)

    account = Account(init_cash=init_cash)
    strategy = (
        BufferedTopKStrategy(top_k=top_k, hold_k=hold_k, method='equal_weight')
        if strategy_variant == 'buffered'
        else StrategyEngine(top_k=top_k, method='equal_weight')
    )
    all_daily = []
    all_trades = []
    window_rows = []
    last_model_path = None

    for idx, window_start in enumerate(starts, start=1):
        window_trade_dates = trade_dates[(idx - 1) * eval_days: idx * eval_days]
        if not window_trade_dates:
            continue
        window_end = window_trade_dates[-1]

        prev_dates = D.calendar(start_time=train_start, end_time=window_start - pd.Timedelta(days=1))
        if len(prev_dates) == 0:
            raise click.ClickException(f'No trading date before window start {window_start.date()}')
        train_end_ts = pd.Timestamp(prev_dates[-1])
        rolling_train_start_ts = max(pd.Timestamp(train_start), train_end_ts - pd.DateOffset(years=train_years) + pd.Timedelta(days=1))
        train_start_str = pd.Timestamp(rolling_train_start_ts).strftime('%Y-%m-%d')
        train_end = train_end_ts.strftime('%Y-%m-%d')
        window_start_str = window_start.strftime('%Y-%m-%d')
        window_end_str = window_end.strftime('%Y-%m-%d')

        log.info(
            f'[rolling] window={idx}/{len(starts)} train={train_start_str}~{train_end} test={window_start_str}~{window_end_str}'
        )

        model_instance = QlibNativeModel(name=model_name, model_config=model_config, feature_config=[])
        model_instance.params = {
            'feature_set_name': resolved_feature_set,
            'feature_set_alias': feature_set,
            'feature_ids': list(selection.feature_ids),
            'native_qlib_fields': list(selection.native_qlib_fields),
        }

        train_summary = {}
        if selection.derived_columns:
            trained_model, semantic_feature_columns, train_summary, sanitize_contract = _fit_semantic_all_features_model(
                model_name=model_name,
                model_config=model_config,
                feature_set_name=resolved_feature_set,
                universe=universe,
                start=train_start_str,
                end=train_end,
            )
            constant_cols = list(sanitize_contract.get('dropped_columns', []))
            model_instance.model = trained_model
            model_instance.feature_config = semantic_feature_columns
            model_instance.training_summary = train_summary
            model_instance.preprocess_params = {
                'method': 'identity',
                'fillna': float(sanitize_contract.get('fillna', 0.0)),
            }
            model_instance.params = {
                **model_instance.params,
                'uses_derived_features': True,
                'derived_feature_columns': list(selection.derived_columns),
                'feature_name_map': dict(sanitize_contract.get('feature_name_map', {})),
                'constant_feature_columns': constant_cols,
                'sanitized_feature_columns': list(sanitize_contract.get('sanitized_feature_columns', [])),
                'sanitize_feature_contract': sanitize_contract,
            }
        else:
            feature_config = FeatureLibrary.get_feature_fields_by_set(resolved_feature_set)
            model_instance.feature_config = feature_config
            model_instance.fit(universe, train_start_str, train_end)
            train_summary = model_instance.training_summary or {}

        model_dir = report_dir / 'models' / f'window_{idx:03d}_{window_start_str}'
        model_dir.mkdir(parents=True, exist_ok=True)
        model_instance.save(model_dir)
        last_model_path = model_dir

        scores = _build_scores_for_window(model_instance, resolved_feature_set, universe, window_start_str, window_end_str)
        if scores.empty:
            log.warning(f'[rolling] empty scores for window {idx}; skipping backtest slice')
            continue

        engine = BacktestEngine(
            model_path=str(model_dir),
            universe=universe,
            start_date=window_start_str,
            end_date=window_end_str,
            account=account,
            daily_predictions=scores,
            top_k=top_k,
            strategy=strategy,
        )
        daily_df = engine.run()
        trades_df = engine.last_trades.copy() if engine.last_trades is not None else pd.DataFrame()

        if not daily_df.empty:
            daily_df['window_id'] = idx
            daily_df['train_start'] = train_start_str
            daily_df['train_end'] = train_end
            daily_df['window_start'] = window_start_str
            daily_df['window_end'] = window_end_str
            all_daily.append(daily_df)

        if not trades_df.empty:
            trades_df['window_id'] = idx
            trades_df['train_start'] = train_start_str
            trades_df['train_end'] = train_end
            trades_df['window_start'] = window_start_str
            trades_df['window_end'] = window_end_str
            all_trades.append(trades_df)

        slice_metrics = _compute_extended_metrics(daily_df, trades_df, init_cash=float(daily_df['total_assets'].iloc[0]) if not daily_df.empty else init_cash)
        window_rows.append(
            {
                'window_id': idx,
                'train_start': train_start_str,
                'train_end': train_end,
                'window_start': window_start_str,
                'window_end': window_end_str,
                'score_rows': int(len(scores)),
                'trade_days': int(len(daily_df)),
                'trade_count': int((trades_df['status'] == 'filled').sum()) if not trades_df.empty else 0,
                'total_fee': float(trades_df.loc[trades_df['status'] == 'filled', 'fee'].sum()) if not trades_df.empty else 0.0,
                'window_return': float(slice_metrics.get('total_return', 0.0)),
                'window_sharpe': float(slice_metrics.get('sharpe', 0.0)),
                'window_max_drawdown': float(slice_metrics.get('max_drawdown', 0.0)),
                'train_sample_count': train_summary.get('sample_count'),
                'train_feature_count_used': train_summary.get('feature_count_used', train_summary.get('feature_count')),
                'train_rank_ic': train_summary.get('rank_ic'),
                'train_mse': train_summary.get('mse'),
            }
        )

    daily_all = pd.concat(all_daily, ignore_index=True) if all_daily else pd.DataFrame()
    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    window_df = pd.DataFrame(window_rows)

    if not daily_all.empty:
        daily_all = daily_all.sort_values('date').drop_duplicates(subset=['date'], keep='last').reset_index(drop=True)
    if not trades_all.empty:
        trades_all = trades_all.sort_values(['date', 'symbol', 'side']).reset_index(drop=True)

    daily_path = report_dir / 'rolling_daily.csv'
    trades_path = report_dir / 'rolling_trades.csv'
    windows_path = report_dir / 'rolling_windows.csv'
    if not daily_all.empty:
        daily_all.to_csv(daily_path, index=False)
    if not trades_all.empty:
        trades_all.to_csv(trades_path, index=False)
    if not window_df.empty:
        window_df.to_csv(windows_path, index=False)

    perf_summary = PerformanceAnalyzer.show(daily_all) if not daily_all.empty else {}
    extended_summary = _compute_extended_metrics(daily_all, trades_all, init_cash=init_cash) if not daily_all.empty else {}
    summary = {
        'runtime_seconds': time.time() - started_at,
        'report_dir': str(report_dir),
        'last_model_path': str(last_model_path) if last_model_path else None,
        'window_count': int(len(window_df)),
        'daily_rows': int(len(daily_all)),
        'trade_rows': int(len(trades_all)),
        'performance_analyzer': perf_summary,
        'extended_metrics': extended_summary,
    }
    with open(report_dir / 'summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    report = BacktestReport.generate(
        start_date=test_start,
        end_date=actual_test_end,
        model_info={
            'model_path': str(last_model_path) if last_model_path else None,
            'model_name': model_name,
            'feature_set': resolved_feature_set,
            'feature_set_alias': feature_set,
            'feature_id_count': len(selection.feature_ids),
        },
        metrics=extended_summary,
        top_k=top_k,
        universe=universe,
        duration_seconds=time.time() - started_at,
        notes=[
            f'train_years={train_years}',
            f'eval_days={eval_days}',
            f'strategy_variant={strategy_variant}',
            f'hold_k={hold_k}',
            'weighting=equal_weight',
            'transaction_costs=commission_0.03pct+stamp_duty_sell_0.10pct+min_5+slippage_0.10pct',
        ],
    )
    report.artifacts['daily'] = str(daily_path)
    report.artifacts['trades'] = str(trades_path)
    report.artifacts['windows'] = str(windows_path)
    report.artifacts['config'] = str(report_dir / 'config.yaml')
    report.artifacts['summary'] = str(report_dir / 'summary.json')
    report_path = BacktestReport.save(report, output_dir=str(report_dir))

    lines = [
        f"feature_set: {resolved_feature_set}",
        f"universe: {universe}",
        f"train_window_years: {train_years}",
        f"train_floor: {train_start}",
        f"test_window: {test_start} -> {actual_test_end}",
        f"eval_days: {eval_days}",
        f"strategy: {strategy_variant} | equal_weight top_{top_k}",
        f"hold_k: {hold_k}",
        f"init_cash: {init_cash:,.2f}",
        f"commission: 0.03% | stamp_duty_sell: 0.10% | min_commission: 5 | slippage: 0.10%",
        "",
        f"final_assets: {extended_summary.get('final_assets', 0.0):,.2f}",
        f"total_return: {extended_summary.get('total_return', 0.0):.2%}",
        f"annual_return: {extended_summary.get('annual_return', 0.0):.2%}",
        f"annual_volatility: {extended_summary.get('annual_volatility', 0.0):.2%}",
        f"sharpe: {extended_summary.get('sharpe', 0.0):.4f}",
        f"max_drawdown: {extended_summary.get('max_drawdown', 0.0):.2%}",
        f"daily_win_rate: {extended_summary.get('daily_win_rate', 0.0):.2%}",
        f"monthly_win_rate: {extended_summary.get('monthly_win_rate', 0.0):.2%}",
        f"trade_count: {extended_summary.get('trade_count', 0)}",
        f"buy_count: {extended_summary.get('buy_count', 0)} | sell_count: {extended_summary.get('sell_count', 0)}",
        f"total_turnover: {extended_summary.get('total_turnover', 0.0):,.2f}",
        f"avg_daily_turnover: {extended_summary.get('avg_daily_turnover', 0.0):,.2f}",
        f"total_fee: {extended_summary.get('total_fee', 0.0):,.2f}",
        f"avg_fee_rate: {extended_summary.get('avg_fee_rate', 0.0):.4%}",
        f"window_count: {len(window_df)}",
        f"report_json: {report_path}",
    ]
    (report_dir / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print('\n'.join(lines))


if __name__ == '__main__':
    main()
