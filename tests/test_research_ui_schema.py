import sys
import os
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qsys.research_ui import ResearchCockpitRepository


class TestResearchUiSchema(unittest.TestCase):
    def setUp(self):
        self.repo = ResearchCockpitRepository(project_root=os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

    def test_feature_registry_entries_are_semantic_and_stable(self):
        entries = self.repo.list_feature_registry()
        self.assertTrue(entries)
        first = entries[0].to_dict()
        self.assertIn('feature_id', first)
        self.assertIn('source_layer', first)
        self.assertIn('value_kind', first)
        self.assertEqual(first['source_layer'], 'semantic_derived')

    def test_daily_manifest_maps_to_stable_run_manifest(self):
        manifest = self.repo.build_daily_run_manifest('2026-04-06').to_dict()
        self.assertEqual(manifest['run_type'], 'daily_ops')
        self.assertEqual(manifest['execution_date'], '2026-04-06')
        self.assertIn('artifacts', manifest)
        self.assertTrue(any(item['artifact_id'] == 'signal_basket' for item in manifest['artifacts']))

    def test_decision_replay_uses_order_intents_contract(self):
        replay = self.repo.build_decision_replay(execution_date='2026-04-06', account_name='shadow').to_dict()
        self.assertEqual(replay['trade_date'], '2026-04-06')
        self.assertIn('final_orders', replay)
        self.assertIn('constraints', replay)
        self.assertIn('summary', replay)

    def test_case_bundle_tracks_run_and_price_mode(self):
        bundle = self.repo.build_case_bundle(
            execution_date='2026-04-06',
            instrument_id='000001.SZ',
            price_mode='fq',
        ).to_dict()
        self.assertEqual(bundle['price_mode'], 'fq')
        self.assertEqual(bundle['instrument_id'], '000001.SZ')
        self.assertIn('run_id', bundle)
        self.assertIn('feature_snapshot', bundle)


if __name__ == '__main__':
    unittest.main()
