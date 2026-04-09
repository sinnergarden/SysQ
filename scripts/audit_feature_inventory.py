from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qsys.research_ui import ResearchCockpitRepository

OUT_DIR = PROJECT_ROOT / "scratch"
OUT_CSV = OUT_DIR / "feature_inventory_audit.csv"
SAMPLE_TRADE_DATE = "2025-02-13"
SAMPLE_INSTRUMENT = "600219.SH"
SAMPLE_UNIVERSE = "csi300"


def main() -> None:
    repo = ResearchCockpitRepository(project_root=PROJECT_ROOT)
    entries = repo.list_feature_registry()
    feature_names = [entry.feature_name for entry in entries]

    snapshot = repo.get_feature_snapshot(trade_date=SAMPLE_TRADE_DATE, instrument_id=SAMPLE_INSTRUMENT)
    snapshot_features = snapshot.get("features") or {}
    snapshot_non_null = {name for name, value in snapshot_features.items() if value is not None}

    health = repo.build_feature_health_summary(
        trade_date=SAMPLE_TRADE_DATE,
        feature_names=feature_names,
        universe=SAMPLE_UNIVERSE,
    )
    health_map = {item.feature_name: item for item in health.features}

    usage_counter: Counter[str] = Counter()
    for entry in entries:
        for tag in entry.tags or []:
            if tag.startswith("feature_set:") or tag.startswith("model:") or tag.startswith("selection:"):
                usage_counter[entry.feature_name] += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "feature_name",
        "canonical_name",
        "source_layer",
        "group_name",
        "idea_family",
        "alias_of",
        "supports_snapshot",
        "snapshot_has_value",
        "snapshot_value",
        "health_coverage",
        "health_status",
        "used_in_models",
        "online_ready",
        "description",
        "tags",
        "action",
        "notes",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            health_item = health_map.get(entry.feature_name)
            coverage = ""
            status = ""
            if health_item is not None:
                coverage = round(float(health_item.coverage_ratio), 6)
                status = health_item.status
            snapshot_value = snapshot_features.get(entry.feature_name)
            snapshot_has_value = entry.feature_name in snapshot_non_null
            online_ready = bool(snapshot_has_value and (coverage == "" or float(coverage) > 0))
            writer.writerow(
                {
                    "feature_name": entry.feature_name,
                    "canonical_name": entry.feature_name,
                    "source_layer": entry.source_layer,
                    "group_name": entry.group_name,
                    "idea_family": "",
                    "alias_of": "",
                    "supports_snapshot": entry.supports_snapshot,
                    "snapshot_has_value": snapshot_has_value,
                    "snapshot_value": snapshot_value,
                    "health_coverage": coverage,
                    "health_status": status,
                    "used_in_models": usage_counter.get(entry.feature_name, 0),
                    "online_ready": online_ready,
                    "description": entry.description,
                    "tags": ",".join(entry.tags or []),
                    "action": "investigate",
                    "notes": "",
                }
            )

    print(f"wrote {len(entries)} rows to {OUT_CSV}")


if __name__ == "__main__":
    main()
