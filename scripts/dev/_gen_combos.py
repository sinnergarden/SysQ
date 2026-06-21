#!/usr/bin/env python3
"""Generate combo FeatureSet YAMLs based on existing YAML files + group overrides."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml

REPO = Path(__file__).resolve().parents[2]
FEATURES_DIR = REPO / "configs" / "features"

# Load existing YAMLs
def load_yaml(name):
    d = yaml.safe_load((FEATURES_DIR / f"{name}.yaml").read_text())
    return list(d.get("features", []) or [])

# Base v3a_full features from existing YAML
v3a_feats = load_yaml("value_growth_multibagger_v3a_features")
v3a_set = set(v3a_feats)

# v3a_without_margin: remove $margin_* and derived margin features
margin_feats = {
    "margin_eligible", "margin_balance_to_float_mv", "margin_balance_chg_20d",
    "margin_balance_chg_60d", "margin_buy_intensity_20d", "margin_repay_to_buy_20d",
    "margin_crowding_score", "margin_trend_confirm_score", "margin_overheat_risk_score",
}

# v3a_without_shareholder: remove holder_* features
shareholder_feats = {
    "holder_num_chg_qoq", "holder_num_chg_2q", "avg_shares_per_holder_chg_qoq",
    "top10_holder_ratio_chg_qoq", "holder_concentration_score", "holder_squeeze_score",
    "holder_price_confirm_score", "holder_num_stale_days", "top10_holder_stale_days",
    "top10_holder_ratio",
}

# v2 base (v3a minus margin minus shareholder)
v2_feats = [f for f in v3a_feats if f not in margin_feats and f not in shareholder_feats]
v2_set = set(v2_feats)

# Other group features (from FEATURE_GROUPS derived names)
from qsys.feature.registry import FEATURE_GROUPS
micro_feats = list(FEATURE_GROUPS["microstructure"]["features"])
liquid_feats = list(FEATURE_GROUPS["liquidity"]["features"])
tradable_feats = list(FEATURE_GROUPS["tradability"]["features"])
ind_mom_feats = list(FEATURE_GROUPS["industry_momentum"]["features"])
v3b_pv_feats = list(FEATURE_GROUPS["v3b_price_volume"]["features"])
v3b_interact_feats = list(FEATURE_GROUPS["v3b_interaction"]["features"])

def make_yaml(name, features_list, desc=""):
    return {
        "feature_set_id": f"retest_60d_{name}",
        "description": desc or f"Delayed 60d retest: {name}",
        "features": features_list,
    }

COMBO_DIR = REPO / "configs" / "features" / "retest_60d_combinations"
COMBO_DIR.mkdir(parents=True, exist_ok=True)

combos = {
    "v3a_full": v3a_feats,
    "v3a_without_margin": sorted(v3a_set - margin_feats),
    "v3a_without_shareholder": sorted(v3a_set - shareholder_feats),
    "v3a_margin_only": sorted(v2_set | margin_feats),
    "v3a_shareholder_only": sorted(v2_set | shareholder_feats),
    "v3a_plus_microstructure": sorted(v3a_set | set(micro_feats)),
    "v3a_plus_liquidity": sorted(v3a_set | set(liquid_feats)),
    "v3a_plus_tradability": sorted(v3a_set | set(tradable_feats)),
    "v3a_plus_industry_momentum": sorted(v3a_set | set(ind_mom_feats)),
    "v3a_plus_v3b_price_volume": sorted(v3a_set | set(v3b_pv_feats)),
    "v3a_plus_v3b_interaction": sorted(v3a_set | set(v3b_interact_feats)),
}

# Write combos
for cname, cfeats in combos.items():
    out_path = COMBO_DIR / f"{cname}.yaml"
    with open(out_path, "w") as f:
        yaml.dump(make_yaml(cname, cfeats), f, default_flow_style=False, sort_keys=False)
    print(f"✅ {cname}: {len(cfeats)} features")

# Validate against superset
superset = yaml.safe_load((FEATURES_DIR / "retest_60d_all_candidate_features.yaml").read_text())
superset_set = set(superset["features"])

all_ok = True
for cname, cfeats in combos.items():
    missing = set(cfeats) - superset_set
    if missing:
        print(f"❌ {cname}: {len(missing)} not in superset: {missing}")
        all_ok = False
if all_ok:
    print(f"\n✅ All {len(combos)} combos are subsets of superset ({len(superset_set)} features)")
