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


if __name__ == '__main__':
    unittest.main()
