import unittest

from qsys.feature.library import FeatureLibrary


class TestExtendedFeatureConfig(unittest.TestCase):
    def test_extended_config_adds_expected_fields(self):
        base = FeatureLibrary.get_alpha158_config()
        extended = FeatureLibrary.get_alpha158_extended_config()

        self.assertGreater(len(extended), len(base))
        for field in FeatureLibrary.EXTENDED_RAW_FIELDS:
            self.assertIn(field, extended)
        self.assertEqual(len(extended), len(set(extended)))

    def test_research_feature_sets_exist(self):
        phase1 = FeatureLibrary.get_research_phase1_config()
        phase12 = FeatureLibrary.get_research_phase12_config()
        phase123 = FeatureLibrary.get_research_phase123_config()
        semantic = FeatureLibrary.get_semantic_all_features_config()
        self.assertGreaterEqual(len(phase1), len(FeatureLibrary.get_alpha158_extended_config()))
        self.assertGreaterEqual(len(phase12), len(phase1))
        self.assertGreaterEqual(len(phase123), len(phase12))
        self.assertGreater(len(semantic), len(phase123))
        self.assertIn("ps_ttm", semantic)
        self.assertEqual(len(semantic), len(set(semantic)))

    def test_absnorm_feature_sets_add_variants_without_dropping_baseline(self):
        extended = FeatureLibrary.get_alpha158_extended_config()
        extended_absnorm = FeatureLibrary.get_alpha158_extended_absnorm_config()
        phase123 = FeatureLibrary.get_research_phase123_config()
        phase123_absnorm = FeatureLibrary.get_research_phase123_absnorm_config()
        semantic_absnorm = FeatureLibrary.get_semantic_all_features_absnorm_config()

        self.assertGreater(len(extended_absnorm), len(extended))
        self.assertGreater(len(phase123_absnorm), len(phase123))
        for field in extended:
            self.assertIn(field, extended_absnorm)
        for field in phase123:
            self.assertIn(field, phase123_absnorm)
        self.assertIn("$net_inflow/($circ_mv+1e-12)", extended_absnorm)
        self.assertIn("($net_inflow/(Abs($net_inflow)+1e-12))*Log(Abs($net_inflow)+1)", extended_absnorm)
        self.assertGreater(len(semantic_absnorm), len(FeatureLibrary.get_semantic_all_features_config()))
        self.assertIn("$net_inflow/($circ_mv+1e-12)", semantic_absnorm)
        self.assertEqual(len(extended_absnorm), len(set(extended_absnorm)))
        self.assertEqual(len(phase123_absnorm), len(set(phase123_absnorm)))
        self.assertEqual(len(semantic_absnorm), len(set(semantic_absnorm)))


if __name__ == '__main__':
    unittest.main()
