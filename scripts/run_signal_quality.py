import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.live.signal_monitoring import (
    build_signal_quality_blockers,
    collect_signal_quality_snapshot,
    write_signal_quality_outputs,
)
from qsys.utils.logger import log


def _parse_horizons(raw: str) -> tuple[int, ...]:
    values = []
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return tuple(values or [1, 2, 3])


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh signal basket quality monitoring artifacts")
    parser.add_argument("--date", required=True, help="As-of date to evaluate signal baskets (YYYY-MM-DD)")
    parser.add_argument("--signal_dir", default="data", help="Directory containing signal_basket_<signal_date>.csv files")
    parser.add_argument("--output_dir", default="data/signal_quality", help="Directory to write signal quality outputs")
    parser.add_argument("--horizons", default="1,2,3", help="Comma-separated horizons to summarize, e.g. 1,2,3,5")
    parser.add_argument("--recent_window", type=int, default=5, help="Recent vintage window for win-rate summary")
    parser.add_argument(
        "--require_ready",
        action="store_true",
        help="Exit non-zero when required horizons have failed or partial signal-quality data",
    )
    args = parser.parse_args()

    QlibAdapter().init_qlib()
    horizons = _parse_horizons(args.horizons)
    snapshot = collect_signal_quality_snapshot(
        as_of_date=args.date,
        signal_dir=args.signal_dir,
        horizons=horizons,
        recent_window=args.recent_window,
    )
    written = write_signal_quality_outputs(snapshot, output_dir=args.output_dir, as_of_date=args.date)

    print(json.dumps(snapshot.summary, indent=2, ensure_ascii=False))
    for name, path in written.items():
        log.info(f"Wrote {name}: {path}")

    if args.require_ready:
        blockers = build_signal_quality_blockers(snapshot.summary, required_horizons=horizons)
        if blockers:
            for blocker in blockers:
                log.error(blocker)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
