import sys
import os
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qsys.research_ui import ResearchCockpitRepository


class TestResearchUiSchema(unittest.TestCase):
    def setUp(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.repo = ResearchCockpitRepository(project_root=project_root)
        daily_root = os.path.join(project_root, 'daily')
        self.execution_date = sorted([name for name in os.listdir(daily_root) if os.path.isdir(os.path.join(daily_root, name))])[-1]

    def test_feature_registry_entries_are_semantic_and_stable(self):
        entries = self.repo.list_feature_registry()
        self.assertTrue(entries)
        registry = {item.feature_name: item.to_dict() for item in entries}
        self.assertIn('close', registry)
        self.assertIn('ret_1d', registry)
        self.assertIn('feature_id', registry['close'])
        self.assertIn('source_layer', registry['close'])
        self.assertIn('value_kind', registry['ret_1d'])
        self.assertIn(registry['close']['source_layer'], {'raw', 'qlib_native'})
        self.assertEqual(registry['ret_1d']['source_layer'], 'semantic_derived')

    def test_daily_manifest_maps_to_stable_run_manifest(self):
        manifest = self.repo.build_daily_run_manifest('2025-01-03').to_dict()
        self.assertEqual(manifest['run_type'], 'daily_ops')
        self.assertEqual(manifest['execution_date'], '2025-01-03')
        self.assertIn('artifacts', manifest)
        self.assertTrue(any(item['artifact_id'] == 'signal_basket' for item in manifest['artifacts']))

    def test_decision_replay_uses_order_intents_contract(self):
        replay = self.repo.build_decision_replay(execution_date=self.execution_date, account_name='shadow').to_dict()
        self.assertEqual(replay['trade_date'], self.execution_date)
        self.assertIn('final_orders', replay)
        self.assertIn('constraints', replay)
        self.assertIn('summary', replay)

    def test_case_bundle_tracks_run_and_price_mode(self):
        bundle = self.repo.build_case_bundle(
            execution_date='2025-01-03',
            instrument_id='600219.SH',
            price_mode='fq',
        ).to_dict()
        self.assertEqual(bundle['price_mode'], 'fq')
        self.assertEqual(bundle['instrument_id'], '600219.SH')
        self.assertIn('run_id', bundle)
        self.assertIn('feature_snapshot', bundle)
        self.assertIn('benchmark_bars', bundle)
        self.assertEqual(bundle['benchmark_label'], 'CSI300')

    def test_feature_snapshot_defaults_to_more_than_manual_subset(self):
        full_snapshot = self.repo.get_feature_snapshot(trade_date=self.execution_date, instrument_id='600219.SH')
        subset_snapshot = self.repo.get_feature_snapshot(
            trade_date=self.execution_date,
            instrument_id='600219.SH',
            feature_names=['close', 'ret_1d'],
        )
        self.assertIn('features', full_snapshot)
        self.assertIn('close', full_snapshot['features'])
        self.assertGreater(len(full_snapshot['features']), len(subset_snapshot['features']))


if __name__ == '__main__':
    unittest.main()
