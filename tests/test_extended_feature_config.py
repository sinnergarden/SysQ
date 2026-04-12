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


if __name__ == '__main__':
    unittest.main()
