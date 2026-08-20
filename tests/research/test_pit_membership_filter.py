"""PIT membership row-filter + param-threading tests (Stage 9B full-PIT retrain).

Covers the three behaviors that would silently produce a NON-PIT retrain if
they regressed:

1. ``_apply_pit_membership`` drops gap-period and never-member rows, reading
   the gapped artifact spans (not a collapsed min/max registry).
2. ``pit_membership`` is part of the window-cache identity, so a PIT-filtered
   run can never reuse a non-PIT window cache under the same universe id.
3. ``expand_multi_label_generators`` / ``_create_generator_from_config`` thread
   ``pit_membership`` end-to-end and reject unknown params loudly.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator
from qsys.research.pit_universe import PitUniverseStore

# 000032.SZ has two disjoint membership spans in the artifact:
#   2007-01-31 -> 2009-12-31  and  2024-06-28 -> 2026-07-31
# Rows between the spans (2010-01-01 .. 2024-06-27) are NOT membership.
_GAPPED = "000032.SZ"
_NEVER_MEMBER = "999999.SZ"


def _make_frame() -> pd.DataFrame:
    """Synthetic feature frame with in-span, gap, and never-member rows."""
    return pd.DataFrame({
        "trade_date": [
            "2007-06-01", "2008-06-02",   # span 1: member
            "2012-06-01", "2020-06-01",   # gap:    NOT member
            "2025-01-02", "2026-06-01",   # span 2: member again
            "2025-01-02",                  # never a member
        ],
        "instrument": [_GAPPED] * 6 + [_NEVER_MEMBER],
        "feat_x": [1.0] * 7,
    })


def _gen(**overrides) -> LightGBMSingleLabelGenerator:
    return LightGBMSingleLabelGenerator(**overrides)


class TestApplyPitMembership:
    def test_gap_rows_and_nonmembers_are_dropped(self) -> None:
        g = _gen(pit_membership=True)
        assert g._pit_store is None  # lazy-loaded on first use

        out = g._apply_pit_membership(_make_frame())
        assert len(out) == 4, f"expected 4 survivor rows, got {len(out)}"
        assert list(out["instrument"].unique()) == [_GAPPED]
        assert set(out["trade_date"]) == {
            "2007-06-01", "2008-06-02", "2025-01-02", "2026-06-01",
        }
        # feature columns survive untouched
        assert set(out.columns) == {"trade_date", "instrument", "feat_x"}

    def test_filter_is_idempotent(self) -> None:
        g = _gen(pit_membership=True)
        once = g._apply_pit_membership(_make_frame())
        twice = g._apply_pit_membership(once)
        pd.testing.assert_frame_equal(once.reset_index(drop=True), twice.reset_index(drop=True))

    def test_pit_store_memoized(self) -> None:
        g = _gen(pit_membership=True)
        g._apply_pit_membership(_make_frame())
        store_after_first = g._pit_store
        assert isinstance(store_after_first, PitUniverseStore)
        # Second call reuses the same store instance (artifact loaded once).
        g._apply_pit_membership(_make_frame())
        assert g._pit_store is store_after_first


class TestCacheIdentityBindsPitMembership:
    def test_pit_run_gets_distinct_window_key(self) -> None:
        g_true = _gen(pit_membership=True)
        g_false = _gen(pit_membership=False)
        ident_true = g_true._cache_identity("2018-01-01", "2018-06-01", ["feat_x"])
        ident_false = g_false._cache_identity("2018-01-01", "2018-06-01", ["feat_x"])
        assert ident_true["pit_membership"] is True
        assert ident_false["pit_membership"] is False
        assert g_true._window_key("2018-01-01", "2018-06-01", ["feat_x"]) != g_false._window_key(
            "2018-01-01", "2018-06-01", ["feat_x"]
        )

    def test_non_pit_identity_unaffected(self) -> None:
        g = _gen(pit_membership=False)
        ident = g._cache_identity("2018-01-01", "2018-06-01", ["feat_x"])
        assert ident["pit_membership"] is False


class TestParamThreading:
    def test_expansion_forwards_pit_membership(self) -> None:
        from qsys.research.matrix_job import expand_multi_label_generators

        expanded = expand_multi_label_generators([
            {
                "generator_id": "v3a_growth_financial_180d_pit",
                "type": "multi_label_lightgbm",
                "params": {
                    "universe": "csi800_pit_union",
                    "n_estimators": 300,
                    "feature_list_id": "v3a_plus_liquidity_financial_rc",
                    "pit_membership": True,
                    "labels": [{"label_id": "fwd_ret_180d_raw_pit"}],
                },
            },
        ])
        assert len(expanded) == 1
        p = expanded[0]["params"]
        assert p["universe"] == "csi800_pit_union"
        assert p["pit_membership"] is True
        assert p["feature_list_id"] == "v3a_plus_liquidity_financial_rc"
        assert p["label_id"] == "fwd_ret_180d_raw_pit"

    def test_factory_passes_pit_membership(self) -> None:
        from qsys.research.matrix_job import _create_generator_from_config

        gen = _create_generator_from_config({
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {
                "label_id": "fwd_ret_180d_raw_pit",
                "universe": "csi800_pit_union",
                "n_estimators": 100,
                "pit_membership": True,
            },
        })
        assert gen.pit_membership is True
        assert gen.universe == "csi800_pit_union"

    def test_factory_defaults_pit_membership_false(self) -> None:
        from qsys.research.matrix_job import _create_generator_from_config

        gen = _create_generator_from_config({
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {"label_id": "fwd_ret_5d_xsz_clip3", "universe": "csi800"},
        })
        assert gen.pit_membership is False

    def test_factory_rejects_unknown_params(self) -> None:
        from qsys.research.matrix_job import _create_generator_from_config

        with pytest.raises(ValueError, match="unknown keys"):
            _create_generator_from_config({
                "generator_id": "g",
                "type": "single_label_lightgbm",
                "params": {
                    "label_id": "fwd_ret_5d_xsz_clip3",
                    "universe": "csi800",
                    "typo_param": 42,  # would have been silently dropped
                },
            })
