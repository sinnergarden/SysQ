import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qsys.research_ui.api import create_app


class TestResearchUiApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cls.client = TestClient(create_app(project_root))

    def test_root_serves_ui_shell(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Research UI', response.text)

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
        self.assertGreater(payload['count'], 0)

    def test_backtest_runs_endpoint(self):
        response = self.client.get('/api/backtest-runs', params={'limit': 5})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertGreater(payload['count'], 0)
        self.assertIn('run_id', payload['items'][0])

        run_id = payload['items'][0]['run_id']
        metrics_response = self.client.get(f'/api/backtest-runs/{run_id}/metrics')
        self.assertEqual(metrics_response.status_code, 200)
        metrics_payload = metrics_response.json()
        self.assertEqual(metrics_payload['api_version'], 'v1')
        self.assertEqual(metrics_payload['data']['run_id'], run_id)
        self.assertIn('metrics', metrics_payload['data'])

        daily_response = self.client.get(f'/api/backtest-runs/{run_id}/daily')
        self.assertEqual(daily_response.status_code, 200)
        daily_payload = daily_response.json()
        self.assertEqual(daily_payload['api_version'], 'v1')
        self.assertEqual(daily_payload['run_id'], run_id)
        self.assertGreater(len(daily_payload['items']), 0)

        first_trade_date = daily_payload['items'][0]['trade_date']
        orders_response = self.client.get(f'/api/backtest-runs/{run_id}/orders', params={'trade_date': first_trade_date})
        self.assertEqual(orders_response.status_code, 200)
        orders_payload = orders_response.json()
        self.assertEqual(orders_payload['api_version'], 'v1')
        self.assertEqual(orders_payload['run_id'], run_id)
        self.assertEqual(orders_payload['trade_date'], first_trade_date)
        self.assertIn('items', orders_payload)

    def test_daily_run_endpoints(self):
        daily_response = self.client.get('/api/runs/daily/2026-04-06')
        self.assertEqual(daily_response.status_code, 200)
        daily_payload = daily_response.json()
        self.assertEqual(daily_payload['api_version'], 'v1')
        self.assertEqual(daily_payload['data']['run_id'], 'daily:2026-04-06')
        self.assertEqual(daily_payload['data']['execution_date'], '2026-04-06')

        response = self.client.get('/api/decision-replay', params={'execution_date': '2026-04-06', 'account_name': 'shadow'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['api_version'], 'v1')
        self.assertEqual(payload['data']['trade_date'], '2026-04-06')
        self.assertIn('summary', payload['data'])

        case_response = self.client.get('/api/cases/2026-04-06:000001.SZ:fq')
        self.assertEqual(case_response.status_code, 200)
        case_payload = case_response.json()
        self.assertEqual(case_payload['api_version'], 'v1')
        self.assertEqual(case_payload['data']['instrument_id'], '000001.SZ')
        self.assertEqual(case_payload['data']['price_mode'], 'fq')

    def test_missing_backtest_returns_clear_404(self):
        response = self.client.get('/api/backtest-runs/not-a-real-run/summary')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Unknown backtest run_id', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
