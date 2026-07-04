import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.live.derived_rollup import rollup_daily_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="Roll daily evidence into append-friendly derived tables")
    parser.add_argument("--execution_date", required=True, help="Daily evidence package date (YYYY-MM-DD)")
    parser.add_argument("--daily_root", default=str(project_root / "daily"), help="Daily evidence root")
    parser.add_argument("--derived_root", default=str(project_root / "data" / "derived"), help="Derived table root")
    args = parser.parse_args()

    result = rollup_daily_artifacts(
        execution_date=args.execution_date,
        daily_root=args.daily_root,
        derived_root=args.derived_root,
    )
    payload = {
        "execution_date": result.execution_date,
        "derived_root": result.derived_root,
        "tables": {
            name: {
                "output_path": table.output_path,
                "added_rows": table.added_rows,
                "total_rows": table.total_rows,
                "source_files": table.source_files,
            }
            for name, table in result.tables.items()
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
