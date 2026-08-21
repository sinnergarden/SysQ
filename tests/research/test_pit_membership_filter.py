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

    def test_new_mode_field_wins_over_legacy_flag(self) -> None:
        # pit_filter_mode takes precedence over the legacy pit_membership bool.
        g = _gen(pit_membership=True, pit_filter_mode="ever_member_as_of")
        assert g._effective_pit_filter_mode() == "ever_member_as_of"
        out = g._apply_pit_membership(_make_frame())
        # ever-member keeps the gap rows too; only the never-member is dropped.
        assert len(out) == 6, f"expected 6 survivor rows, got {len(out)}"
        assert list(out["instrument"].unique()) == [_GAPPED]
        assert set(out["trade_date"]) == {
            "2007-06-01", "2008-06-02", "2012-06-01", "2020-06-01",
            "2025-01-02", "2026-06-01",
        }

    def test_ever_member_mode_keeps_gap_rows(self) -> None:
        g = _gen(pit_filter_mode="ever_member_as_of", pit_universe_artifact="csi800_pit_v1")
        out = g._apply_pit_membership(_make_frame())
        # gap rows (2012, 2020) survive: ever-member ignores effective_to.
        assert len(out) == 6
        assert {"2012-06-01", "2020-06-01"} <= set(out["trade_date"])

    def test_liquidity_exclusion_anti_join(self, tmp_path) -> None:
        # U3 semantics: after the span filter, (trade_date, instrument) rows in
        # the exclusion parquet are removed (suspension / liquidity floors).
        excl = pd.DataFrame({
            "trade_date": ["2025-01-02"],
            "instrument": [_GAPPED],
        })
        path = tmp_path / "exclusions.parquet"
        excl.to_parquet(path, index=False)

        g = _gen(pit_membership=True, liquidity_exclusion_path=str(path))
        out = g._apply_pit_membership(_make_frame())
        # 4 member_as_of survivors minus the excluded (2025-01-02, 000032.SZ).
        assert len(out) == 3, f"expected 3 rows after exclusion, got {len(out)}"
        assert set(out["trade_date"]) == {"2007-06-01", "2008-06-02", "2026-06-01"}

    def test_empty_mode_passes_frame_through(self) -> None:
        g = _gen()
        assert g._effective_pit_filter_mode() == ""
        out = g._apply_pit_membership(_make_frame())
        assert len(out) == len(_make_frame())
        assert out.columns.tolist() == ["trade_date", "instrument", "feat_x"]


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


class TestEverMembershipAsOf:
    """PitUniverseStore.ever_membership_as_of — U1 ever-member semantics."""

    def test_bare_dirname_resolves_and_monotonic(self) -> None:
        # bare dirname resolves under data/research/universes/ (new behavior)
        store = PitUniverseStore("csi800_pit_v1")
        assert store.artifact_dir.name == "csi800_pit_v1"
        early = set(store.ever_membership_as_of("2010-01-01"))
        late = set(store.ever_membership_as_of("2026-07-31"))
        assert early <= late  # ever-member only grows with as_of_date
        assert len(late) > len(early)

    def test_ever_member_includes_gap_period_stock(self) -> None:
        # 000032.SZ re-entered CSI800 in 2024; during 2010-2024 it is in the
        # ever-member set (its first span started 2007) but NOT member-as-of.
        store = PitUniverseStore("csi800_pit_v1")
        assert _GAPPED in store.ever_membership_as_of("2010-01-01")
        assert _GAPPED not in store.membership_as_of("2010-01-01")
        # ever-member is a superset of point-in-time membership at any date
        recent = store.membership_as_of("2026-07-31")
        assert set(recent) <= set(store.ever_membership_as_of("2026-07-31"))


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

    def test_expansion_forwards_pit_filter_mode_and_artifact(self) -> None:
        from qsys.research.matrix_job import expand_multi_label_generators

        expanded = expand_multi_label_generators([
            {
                "generator_id": "v3a_growth_180d_u2",
                "type": "multi_label_lightgbm",
                "params": {
                    "universe": "csi1800_pit_union",
                    "n_estimators": 300,
                    "feature_list_id": "v3a_plus_liquidity_financial_rc",
                    "pit_filter_mode": "member_as_of",
                    "pit_universe_artifact": "csi1800_pit_v1",
                    "liquidity_exclusion_path": "data/research/universes/csi_liquid_pit_v1/raw/liquidity_exclusions.parquet",
                    "labels": [{"label_id": "fwd_ret_180d_raw_pit_csi1800"}],
                },
            },
        ])
        assert len(expanded) == 1
        p = expanded[0]["params"]
        assert p["pit_filter_mode"] == "member_as_of"
        assert p["pit_universe_artifact"] == "csi1800_pit_v1"
        assert p["liquidity_exclusion_path"].endswith("liquidity_exclusions.parquet")
        assert p["label_id"] == "fwd_ret_180d_raw_pit_csi1800"

    def test_factory_passes_new_fields(self) -> None:
        from qsys.research.matrix_job import _create_generator_from_config

        gen = _create_generator_from_config({
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {
                "label_id": "fwd_ret_180d_raw_pit_csi1800",
                "universe": "csi1800_pit_union",
                "n_estimators": 300,
                "pit_filter_mode": "member_as_of",
                "pit_universe_artifact": "csi1800_pit_v1",
            },
        })
        assert gen.pit_filter_mode == "member_as_of"
        assert gen.pit_universe_artifact == "csi1800_pit_v1"
        assert gen.liquidity_exclusion_path == ""
        assert gen.universe == "csi1800_pit_union"

    def test_factory_defaults_new_fields(self) -> None:
        from qsys.research.matrix_job import _create_generator_from_config

        gen = _create_generator_from_config({
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {"label_id": "fwd_ret_5d_xsz_clip3", "universe": "csi800"},
        })
        assert gen.pit_filter_mode == ""
        assert gen.pit_universe_artifact == "csi800_pit_v1"
        assert gen.liquidity_exclusion_path == ""
