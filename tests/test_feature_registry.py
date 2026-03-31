import unittest

from qsys.feature.registry import (
    get_feature_metadata,
    get_feature_metadata_by_id,
    list_feature_groups,
    list_feature_sets,
    list_features_for_group,
    list_standardization_candidates,
    load_feature_registry,
    load_feature_sets,
    resolve_feature_columns,
    resolve_feature_selection,
)


class TestFeatureRegistry(unittest.TestCase):
    def test_registry_loads(self):
        registry = load_feature_registry()
        self.assertIn("providers", registry)
        self.assertIn("feature_groups", registry)
        self.assertIn("features", registry)

        feature_sets = load_feature_sets()
        self.assertIn("feature_sets", feature_sets)

    def test_group_listing(self):
        groups = list_feature_groups()
        self.assertIn("microstructure", groups)
        self.assertIn("regime", groups)
        self.assertIn("event", groups)

        price_state_features = list_features_for_group("microstructure")
        self.assertIn("open_to_close_ret", price_state_features)

    def test_feature_metadata(self):
        meta = get_feature_metadata("tradability_score")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["group"], "tradability")
        self.assertTrue(meta["tabular_fit"])
        self.assertEqual(meta["source_type"], "custom_python")

        same_meta = get_feature_metadata_by_id(meta["id"])
        self.assertEqual(same_meta["name"], "tradability_score")

    def test_standardization_candidates(self):
        candidates = list_standardization_candidates()
        self.assertIn("open_to_close_ret", candidates)
        self.assertIn("amount_log", candidates)
        self.assertNotIn("market_breadth", candidates)

    def test_feature_set_resolution(self):
        selection = resolve_feature_selection(feature_set="mixed_provider_demo_v1")

        self.assertEqual(selection.feature_names, ["alpha158_kmid", "net_inflow_raw", "tradability_score"])
        self.assertIn("($close-$open)/$open", selection.native_qlib_fields)
        self.assertIn("$net_inflow", selection.native_qlib_fields)
        self.assertEqual(selection.derived_columns, ["tradability_score"])
        self.assertEqual(selection.required_groups, ["execution_state"])

    def test_feature_set_columns_payload(self):
        payload = resolve_feature_columns(feature_set="atomic_panel_plus_state_v1")
        self.assertIn("feature_ids", payload)
        self.assertIn("native_qlib_fields", payload)
        self.assertIn("derived_columns", payload)
        self.assertIn("tradability_score", payload["derived_columns"])

    def test_feature_set_catalog(self):
        feature_sets = list_feature_sets()
        self.assertIn("price_volume_fundamental_core_v1", feature_sets)
        self.assertIn("research_semantic_default_v1", feature_sets)


if __name__ == "__main__":
    unittest.main()
