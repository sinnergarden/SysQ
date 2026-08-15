import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qsys.research_ui.api import create_app
from qsys.research_ui.assembler import ResearchCockpitRepository


class TestResearchUiApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cls.project_root = project_root
        cls.client = TestClient(create_app(project_root))
        daily_root = os.path.join(project_root, 'daily')
        available_dates = sorted([name for name in os.listdir(daily_root) if os.path.isdir(os.path.join(daily_root, name))])
        preferred_date = '2025-01-03'
        cls.execution_date = preferred_date if preferred_date in available_dates else available_dates[-1]
        cls.case_instrument = '600219.SH'

    def test_root_serves_ui_shell(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Research UI', response.text)
        self.assertIn('Quant Research Cockpit', response.text)
        self.assertIn('cdn.plot.ly/plotly', response.text)
        self.assertIn('backtest-equity-chart', response.text)
        self.assertIn('context-trade-date', response.text)
        self.assertIn('Decision Replay', response.text)

    def test_instruments_endpoint(self):
        response = self.client.get('/api/instruments', params={'q': '平安', 'limit': 10})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['meta']['resource'], 'instrument_list')
        self.assertIn('items', payload)

    def test_search_endpoint(self):
        response = self.client.get('/api/search', params={'q': '平安', 'limit': 10})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['meta']['query'], '平安')
        self.assertIn('items', payload)

    def test_feature_registry_endpoint(self):
        response = self.client.get('/api/feature-registry')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertGreaterEqual(payload['count'], 254)
        names = {item['feature_name']: item for item in payload['items']}
        self.assertIn('close', names)
        self.assertIn('ret_1d', names)
        self.assertNotIn('alpha158_beta10', names)

    def test_backtest_runs_endpoint(self):
        response = self.client.get('/api/backtest-runs', params={'limit': 5})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertGreaterEqual(payload['count'], 1)
        feature_sets = {item['feature_set'] for item in payload['items']}
        self.assertTrue(
            all(feature_sets),
            'every backtest run must carry a feature_set',
        )
        self.assertIn('run_id', payload['items'][0])
        self.assertIn('display_label', payload['items'][0])
        self.assertIn('parameter_summary', payload['items'][0])

        run_id = payload['items'][0]['run_id']
        metrics_response = self.client.get(f'/api/backtest-runs/{run_id}/metrics')
        self.assertEqual(metrics_response.status_code, 200)
        metrics_payload = metrics_response.json()
        self.assertEqual(metrics_payload['api_version'], 'v1')
        self.assertEqual(metrics_payload['data']['run_id'], run_id)
        self.assertIn('metrics', metrics_payload['data'])
        self.assertIn('parameter_summary', metrics_payload['data'])

        daily_response = self.client.get(f'/api/backtest-runs/{run_id}/daily')
        self.assertEqual(daily_response.status_code, 200)
        daily_payload = daily_response.json()
        self.assertEqual(daily_payload['api_version'], 'v1')
        self.assertEqual(daily_payload['run_id'], run_id)
        self.assertGreater(len(daily_payload['items']), 0)
        self.assertIn('benchmark_equity', daily_payload['items'][0])

        first_trade_date = daily_payload['items'][0]['trade_date']
        orders_response = self.client.get(f'/api/backtest-runs/{run_id}/orders', params={'trade_date': first_trade_date})
        self.assertEqual(orders_response.status_code, 200)
        orders_payload = orders_response.json()
        self.assertEqual(orders_payload['api_version'], 'v1')
        self.assertEqual(orders_payload['run_id'], run_id)
        self.assertEqual(orders_payload['trade_date'], first_trade_date)
        self.assertIn('items', orders_payload)

    def test_feature_snapshot_defaults_to_full_available_set(self):
        full_response = self.client.get('/api/feature-snapshot', params={'instrument_id': self.case_instrument, 'trade_date': self.execution_date})
        self.assertEqual(full_response.status_code, 200)
        full_payload = full_response.json()
        self.assertEqual(full_payload['api_version'], 'v1')
        self.assertIn('features', full_payload['data'])
        self.assertGreaterEqual(len(full_payload['data']['features']), 254)

        subset_response = self.client.get('/api/feature-snapshot', params={
            'instrument_id': self.case_instrument,
            'trade_date': self.execution_date,
            'feature_names': ['close', 'ret_1d'],
        })
        self.assertEqual(subset_response.status_code, 200)
        subset_payload = subset_response.json()
        self.assertGreater(len(full_payload['data']['features']), len(subset_payload['data']['features']))
        self.assertIn('close', full_payload['data']['features'])

    def test_daily_run_endpoints(self):
        daily_response = self.client.get(f'/api/runs/daily/{self.execution_date}')
        self.assertEqual(daily_response.status_code, 200)
        daily_payload = daily_response.json()
        self.assertEqual(daily_payload['api_version'], 'v1')
        self.assertEqual(daily_payload['data']['run_id'], f'daily:{self.execution_date}')
        self.assertEqual(daily_payload['data']['execution_date'], self.execution_date)

        response = self.client.get('/api/decision-replay', params={'execution_date': self.execution_date, 'account_name': 'shadow'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['data']['trade_date'], self.execution_date)
        self.assertIn('summary', payload['data'])

        case_response = self.client.get(f'/api/cases/{self.execution_date}:{self.case_instrument}:fq')
        self.assertEqual(case_response.status_code, 200)
        case_payload = case_response.json()
        self.assertEqual(case_payload['api_version'], 'v1')
        self.assertEqual(case_payload['data']['instrument_id'], self.case_instrument)
        self.assertEqual(case_payload['data']['price_mode'], 'fq')
        self.assertIn('benchmark_bars', case_payload['data'])
        self.assertEqual(case_payload['data']['benchmark_label'], 'CSI300')

    def test_missing_backtest_returns_clear_404(self):
        response = self.client.get('/api/backtest-runs/not-a-real-run/summary')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Unknown backtest run_id', response.json()['detail'])

    def test_positions_endpoint_contract(self):
        # Routes re-scan per request (fresh repo), so patch the class method:
        # every ResearchCockpitRepository instance serves the mocked holdings.
        sample = [{
            'instrument': '600000.SH',
            'qty': 100,
            'avg_cost': 10.1,
            'last_close': 12.0,
            'market_value': 1200.0,
            'unrealized_pnl': 190.0,
            'realized_pnl': 0.0,
            'first_trade_date': '2021-01-04',
            'last_trade_date': '2021-01-04',
        }]
        with patch.object(ResearchCockpitRepository, 'get_backtest_positions', return_value=sample) as mocked:
            response = self.client.get('/api/backtest-runs/some-run/positions', params={'trade_date': '2021-01-04'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['meta']['resource'], 'backtest_positions')
        self.assertEqual(payload['run_id'], 'some-run')
        self.assertEqual(payload['trade_date'], '2021-01-04')
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['items'][0]['instrument'], '600000.SH')
        self.assertEqual(payload['items'][0]['market_value'], 1200.0)
        mocked.assert_called_once()
        call_kwargs = mocked.call_args.kwargs
        self.assertEqual(call_kwargs.get('trade_date'), '2021-01-04')

    def test_positions_endpoint_404_when_run_unknown(self):
        with patch.object(ResearchCockpitRepository, 'get_backtest_positions', side_effect=FileNotFoundError('Unknown backtest run_id: canonical__nope__nope')):
            response = self.client.get('/api/backtest-runs/canonical__nope__nope/positions')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Unknown backtest run_id', response.json()['detail'])

    def test_behavior_episodes_endpoint_contract(self):
        sample = {
            "episodes": [{"symbol": "600000.SH", "exit_reason": "hard_stop", "realized_return": 0.1}],
            "summary": {"total_episodes": 1, "closed_episodes": 1, "open_episodes": 0},
        }
        with patch.object(ResearchCockpitRepository, 'get_behavior_episodes', return_value=sample) as mocked:
            response = self.client.get('/api/backtest-runs/some-run/behavior/episodes')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['meta']['resource'], 'behavior_episodes')
        self.assertEqual(payload['run_id'], 'some-run')
        self.assertEqual(payload['data']['summary']['total_episodes'], 1)
        self.assertEqual(payload['data']['episodes'][0]['symbol'], '600000.SH')
        mocked.assert_called_once()

    def test_behavior_episodes_endpoint_404_when_run_unknown(self):
        with patch.object(ResearchCockpitRepository, 'get_behavior_episodes', side_effect=FileNotFoundError('Unknown backtest run_id: canonical__nope__nope')):
            response = self.client.get('/api/backtest-runs/canonical__nope__nope/behavior/episodes')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Unknown backtest run_id', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
