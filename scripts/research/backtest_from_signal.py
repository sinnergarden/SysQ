#!/usr/bin/env python3
"""Backtest from a saved SignalRun (no model inference).

Usage::

    python scripts/research/backtest_from_signal.py \\
        --signal-id alpha_v1_score \\
        --signal-run-id smoke_20260518_20260525 \\
        --start-date 2026-05-18 \\
        --end-date 2026-05-25 \\
        --initial-capital 10000000 \\
        --top-n 20 \\
        --rebalance-freq weekly \\
        --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.strategy_runner import BacktestRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest from a saved SignalRun (no model inference)"
    )
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--signal-run-id", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--strategy-template-id", default="rank_weight_top20")
    parser.add_argument("--initial-capital", type=float, default=10_000_000.0)
    parser.add_argument("--score-column", default="score")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=None)
    parser.add_argument("--commission", type=float, default=0.0003)
    parser.add_argument("--stamp-duty", type=float, default=0.001)
    parser.add_argument("--min-commission", type=float, default=5.0)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--rebalance-freq", choices=["daily", "weekly"], default="weekly")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--artifact-mode", choices=["summary", "debug"], default="summary")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--accumulate", action="store_true",
                        help="Accumulate mode: never sell based on signal; only buy to fill top_n")
    parser.add_argument("--stop-loss", type=float, default=None,
                        help="Stop-loss threshold e.g. 0.07 = sell at -7%%")
    parser.add_argument("--trailing-stop", type=float, default=None,
                        help="Trailing stop e.g. 0.10 = sell if -10%% from peak")
    parser.add_argument("--use-adjusted-price", action=argparse.BooleanOptionalAction, default=True,
                        help="Multiply prices by factor for signal-consistent backtest (default True)")
    parser.add_argument("--signal-id-2", default=None,
                        help="Second signal ID for blending")
    parser.add_argument("--signal-run-id-2", default=None,
                        help="Second signal run ID for blending")
    parser.add_argument("--blend-weight", type=float, default=1.0,
                        help="Weight for primary signal (0.0-1.0). Secondary gets 1-w.")
    args = parser.parse_args()

    runner = BacktestRunner(artifact_mode=args.artifact_mode)
    kwargs = dict(
        signal_id=args.signal_id,
        signal_run_id=args.signal_run_id,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        score_column=args.score_column,
        top_n=args.top_n,
        commission=args.commission,
        stamp_duty=args.stamp_duty,
        min_commission=args.min_commission,
        slippage=args.slippage,
        rebalance_freq=args.rebalance_freq,
        strategy_template_id=args.strategy_template_id,
        output_dir=args.output_dir,
        artifact_mode=args.artifact_mode,
        overwrite=args.overwrite,
        stop_loss=args.stop_loss,
        trailing_stop=args.trailing_stop,
        use_adjusted_price=args.use_adjusted_price,
        signal_id_2=args.signal_id_2,
        signal_run_id_2=args.signal_run_id_2,
        blend_weight=args.blend_weight,
    )
    if args.accumulate:
        result = runner.run_accumulate(**kwargs)
    else:
        kwargs["max_weight"] = args.max_weight
        result = runner.run_from_signal_cache(**kwargs)

    print(json.dumps({
        "status": result.status,
        "backtest_id": result.backtest_id,
        "final_value": result.final_value,
        "total_return": result.total_return,
        "initial_capital": result.initial_capital,
        "trading_dates": len(result.daily_summary),
        "notes": result.notes,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
