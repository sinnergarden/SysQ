#!/usr/bin/env python3
"""Checkpoint 2: Superset and combo validation."""
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml

REPO = Path(__file__).resolve().parents[2]
FEAT_DIR = REPO / "configs" / "features"
COMBO_DIR = FEAT_DIR / "retest_60d_combinations"

print("=== Superset & Combo Check ===")

# 1. Superset
superset_file = FEAT_DIR / "retest_60d_all_candidate_features.yaml"
assert superset_file.exists(), "Superset YAML not found!"
sup_data = yaml.safe_load(superset_file.read_text())
sup_feats = set(sup_data["features"])
print(f"Superset: {sup_data['feature_set_id']}")
print(f"  Feature count: {len(sup_feats)}")

# Resolve via resolver CLI
import subprocess
result = subprocess.run(
    ["python", str(REPO / "scripts/dev/resolve_feature_set.py"),
     "--feature-set", str(superset_file),
     "--output-dir", "/tmp/superset_check"],
    capture_output=True, text=True, timeout=60,
)
print(f"  Resolve exit code: {result.returncode}")
for line in result.stdout.strip().split("\n"):
    if any(kw in line for kw in ["Resolved", "Raw", "Derived", "Required", "Unresolved", "Warnings"]):
        print(f"    {line.strip()}")

# 2. Combo YAMLs
print(f"\n=== {len(list(COMBO_DIR.glob('*.yaml')))} Combos ===")
all_feats_set = set()
combo_files = sorted(COMBO_DIR.glob("*.yaml"))
errors = []
for cf in combo_files:
    cd = yaml.safe_load(cf.read_text())
    cfeats = set(cd.get("features", []))
    cname = cd["feature_set_id"]
    missing = cfeats - sup_feats
    status = "✅" if not missing else "❌"
    print(f"  {status} {cf.stem:45s} {len(cfeats):4d} feats  (superset subset={not missing})")
    if missing:
        errors.append(f"  {cf.name}: {len(missing)} not in superset: {sorted(missing)[:5]}...")
    all_feats_set |= cfeats

if errors:
    print(f"\n❌ ERRORS ({len(errors)} combos have features outside superset):")
    for e in errors[:5]:
        print(e)
else:
    print(f"\n✅ All combos are subsets of superset ({len(sup_feats)} features)")

# 3. Transform registry check
print(f"\n=== Transform Registry Check ===")
from qsys.feature.transform_registry import is_registered, get_transform

import json
manifest_path = Path("/tmp/superset_check") / f"{sup_data['feature_set_id']}.json"
if manifest_path.exists():
    m = json.loads(manifest_path.read_text())
    print(f"Required transforms ({len(m.get('required_transforms', []))}):")
    all_ok = True
    for t in m.get("required_transforms", []):
        ok = is_registered(t)
        tspec = get_transform(t)
        out_count = len(tspec.output_features) if tspec else 0
        print(f"  {'✅' if ok else '❌'} {t} ({out_count} output features)")
        if not ok:
            all_ok = False
    if all_ok:
        print(f"  ✅ All transforms registered")
    else:
        errors.append("transforms not registered")

print(f"\nCheck complete. Errors: {len(errors)}")
for e in errors:
    print(f"  ❌ {e}")
