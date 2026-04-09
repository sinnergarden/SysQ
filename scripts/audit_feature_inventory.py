from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qsys.research_ui import ResearchCockpitRepository
OUT_DIR = PROJECT_ROOT / "scratch"
OUT_CSV = OUT_DIR / "feature_inventory_audit.csv"


def main() -> None:
    repo = ResearchCockpitRepository(project_root=PROJECT_ROOT)
    entries = repo.list_feature_registry()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "feature_name",
        "canonical_name",
        "source_layer",
        "group_name",
        "idea_family",
        "alias_of",
        "supports_snapshot",
        "description",
        "tags",
        "action",
        "notes",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "feature_name": entry.feature_name,
                    "canonical_name": entry.feature_name,
                    "source_layer": entry.source_layer,
                    "group_name": entry.group_name,
                    "idea_family": "",
                    "alias_of": "",
                    "supports_snapshot": entry.supports_snapshot,
                    "description": entry.description,
                    "tags": ",".join(entry.tags or []),
                    "action": "investigate",
                    "notes": "",
                }
            )
    print(f"wrote {len(entries)} rows to {OUT_CSV}")


if __name__ == "__main__":
    main()
