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
import hashlib
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.strategy_runner import BacktestRunner  # noqa: E402


def _default_blend_ids(
    signal_id: str,
    signal_run_id: str,
    signal_id_2: str,
    signal_run_id_2: str,
    blend_weight: float,
    *,
    primary_sha256: str | None = None,
    secondary_sha256: str | None = None,
    primary_manifest_sha256: str | None = None,
    secondary_manifest_sha256: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, str]:
    """Return stable identifiers for a materialized two-signal blend."""
    raw = json.dumps(
        {
            "primary": [signal_id, signal_run_id],
            "secondary": [signal_id_2, signal_run_id_2],
            "primary_weight": blend_weight,
            "secondary_weight": 1.0 - blend_weight,
            "primary_sha256": primary_sha256,
            "secondary_sha256": secondary_sha256,
            "primary_manifest_sha256": primary_manifest_sha256,
            "secondary_manifest_sha256": secondary_manifest_sha256,
            "start_date": start_date,
            "end_date": end_date,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{signal_id}__cached_blend", f"blend__{digest}"


def _materialize_blend(args: argparse.Namespace) -> tuple[str, str, Path]:
    """Persist the requested cache-to-cache blend as its own SignalRun."""
    from qsys.research.paths import ResearchPaths
    from qsys.research.signal_combine import CombineInput, CombineSpec, combine_signals
    from qsys.signal.store import SignalStore

    store = SignalStore(args.research_root)
    primary_identity = store.validate_backtest_source(
        args.signal_id, args.signal_run_id
    )
    secondary_identity = store.validate_backtest_source(
        args.signal_id_2, args.signal_run_id_2
    )
    start_date = getattr(args, "start_date", None)
    end_date = getattr(args, "end_date", None)
    output_signal_id, output_signal_run_id = _default_blend_ids(
        args.signal_id,
        args.signal_run_id,
        args.signal_id_2,
        args.signal_run_id_2,
        args.blend_weight,
        primary_sha256=primary_identity["predictions_sha256"],
        secondary_sha256=secondary_identity["predictions_sha256"],
        primary_manifest_sha256=primary_identity["manifest_sha256"],
        secondary_manifest_sha256=secondary_identity["manifest_sha256"],
        start_date=start_date,
        end_date=end_date,
    )
    output_signal_id = args.blend_output_signal_id or output_signal_id
    output_signal_run_id = args.blend_output_signal_run_id or output_signal_run_id
    paths = ResearchPaths(args.research_root)
    spec = CombineSpec(
        combine_id=f"cached_blend_{args.blend_weight:g}_{1.0 - args.blend_weight:g}",
        combine_type="linear_blend",
        inputs=[
            CombineInput(
                source_signal_id=args.signal_id,
                source_signal_run_id=args.signal_run_id,
                weight=args.blend_weight,
            ),
            CombineInput(
                source_signal_id=args.signal_id_2,
                source_signal_run_id=args.signal_run_id_2,
                weight=1.0 - args.blend_weight,
            ),
        ],
    )
    combine_signals(
        spec,
        output_signal_id=output_signal_id,
        output_signal_run_id=output_signal_run_id,
        signal_store=store,
        research_paths=paths,
        overwrite=args.overwrite,
        required_start_date=start_date,
        required_end_date=end_date,
    )
    manifest_path = (
        paths.signal_dir(output_signal_id, output_signal_run_id)
        / "combination_manifest.json"
    )
    return output_signal_id, output_signal_run_id, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest from a saved SignalRun (no model inference)"
    )
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--signal-run-id", required=True)
    parser.add_argument("--research-root", default="data/research")
    parser.add_argument(
        "--pit-universe-artifact",
        default=None,
        help=(
            "Bare PIT universe artifact name under research-root/universes; "
            "filters signal candidates on their execution trade date"
        ),
    )
    parser.add_argument(
        "--corporate-action-artifact", default=None,
        help="Bare corporate-action artifact name; required for accounting baseline",
    )
    parser.add_argument(
        "--canonical-data-root", default=None,
        help="Root of immutable canonical daily raw-price files",
    )
    parser.add_argument(
        "--max-participation-rate", type=float, default=None,
        help="ADV participation cap; baseline uses 0.10 with reject gate",
    )
    parser.add_argument(
        "--liquidity-gate-mode", choices=["warning", "reject"], default="warning",
    )
    parser.add_argument("--adv-window", type=int, default=20)
    parser.add_argument("--adv-min-periods", type=int, default=5)
    parser.add_argument(
        "--require-complete-accounting", action="store_true",
        help="Require canonical data + corporate-action artifact + 10% reject gate",
    )
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
    parser.add_argument(
        "--rebalance-freq",
        default="weekly",
        help="'weekly' (ISO-week), 'daily', or '<n>d' (refresh every n trading days)",
    )
    parser.add_argument(
        "--rebalance-offset", type=int, default=0,
        help="Trading-day phase offset of the '<n>d' cadence grid (0 = first day "
             "rebalances; 20 with 60d puts rebalances on trading days 20, 80, ...).",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--artifact-mode", choices=["summary", "debug"], default="summary")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--portfolio-analytics",
        action="store_true",
        help="Write hash-bound portfolio analytics after a complete backtest",
    )
    parser.add_argument("--benchmark-id", default=None)
    parser.add_argument("--benchmark-csv", type=Path, default=None)
    parser.add_argument(
        "--holdout-start",
        default=None,
        help="First untouched holdout date; required for portfolio analytics",
    )
    parser.add_argument("--accumulate", action="store_true",
                        help="Accumulate mode: never sell based on signal; only buy to fill top_n")
    parser.add_argument("--stop-loss", type=float, default=None,
                        help="Stop-loss threshold as a fraction; e.g. 0.07")
    parser.add_argument("--trailing-stop", type=float, default=None,
                        help="Trailing-stop drawdown as a fraction; e.g. 0.10")
    parser.add_argument(
        "--use-adjusted-price",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Legacy synthetic-price mode. It is rejected because adjusted "
            "prices cannot be used for A-share lot sizing/execution."
        ),
    )
    parser.add_argument("--signal-id-2", default=None,
                        help="Second signal ID for blending")
    parser.add_argument("--signal-run-id-2", default=None,
                        help="Second signal run ID for blending")
    parser.add_argument("--blend-weight", type=float, default=1.0,
                        help="Weight for primary signal (0.0-1.0). Secondary gets 1-w.")
    parser.add_argument(
        "--holding-policy",
        choices=["target_rebalance", "posterior_confirmed"],
        default="target_rebalance",
    )
    parser.add_argument("--score-delta-lookback", type=int, default=20)
    parser.add_argument("--score-delta-quantile", type=float, default=0.10)
    parser.add_argument("--score-delta-history-days", type=int, default=504)
    parser.add_argument("--score-delta-min-observations", type=int, default=500)
    parser.add_argument("--posterior-stop-loss", type=float, default=0.09)
    parser.add_argument("--winner-activation-return", type=float, default=0.20)
    parser.add_argument("--winner-trailing-stop", type=float, default=0.125)
    parser.add_argument("--stale-after-days", type=int, default=20)
    parser.add_argument("--stale-max-return", type=float, default=0.03)
    parser.add_argument("--replacement-rank-gap", type=int, default=20)
    parser.add_argument(
        "--rank-exit",
        action="store_true",
        help="posterior_confirmed: on rebalance, sell any held name that has "
             "dropped out of the current top_n (pure score-refresh baseline; "
             "all four exit rules should be disabled to isolate it).",
    )
    parser.add_argument(
        "--rank-exit-hold-top", type=int, default=None,
        help="posterior_confirmed + --rank-exit: rank-hysteresis band.  Keep a "
             "held name while its current rank is <= this value (wider than "
             "top_n); exit only when rank > band.  None = plain top_n dropout.",
    )
    parser.add_argument(
        "--materialize-blend",
        action="store_true",
        help=(
            "Persist the two input SignalRuns as a combined SignalRun before "
            "backtesting it"
        ),
    )
    parser.add_argument("--blend-output-signal-id", default=None)
    parser.add_argument("--blend-output-signal-run-id", default=None)
    parser.add_argument("--maxdd-signal-id", default=None,
                        help="Signal ID for maxdd binary probability (risk filter)")
    parser.add_argument("--maxdd-signal-run-id", default=None,
                        help="Signal run ID for maxdd binary probability")
    parser.add_argument("--maxdd-threshold", type=float, default=None,
                        help="MaxDD calibrated prob threshold: skip candidates with prob >= this value")
    parser.add_argument("--maxdd-percentile", type=float, default=None,
                        help="MaxDD risk percentile: skip the highest-risk candidates (0-1; e.g. 0.80 keeps the lower 80th percentile)")
    parser.add_argument(
        "--exposure-gate-mode",
        choices=["none", "market_risk", "model_health", "either"],
        default="none",
        help="Exposure gate: on gated dates, scale target weights to "
             "--exposure-gate-scale of equity.",
    )
    parser.add_argument(
        "--exposure-gate-scale", type=float, default=0.5,
        help="Target exposure multiplier when the gate is active (e.g. 0.5).",
    )
    parser.add_argument(
        "--exposure-gate-schedule", type=Path, default=None,
        help="JSON file mapping trade_date (YYYY-MM-DD) -> bool (gate active). "
             "Precomputed point-in-time schedule; data, not config.",
    )
    args = parser.parse_args()

    if (args.signal_id_2 is None) != (args.signal_run_id_2 is None):
        parser.error("--signal-id-2 and --signal-run-id-2 must be provided together")
    if not 0.0 <= args.blend_weight <= 1.0:
        parser.error("--blend-weight must be within [0, 1]")
    if args.materialize_blend and args.signal_id_2 is None:
        parser.error("--materialize-blend requires the second signal id and run id")
    if args.signal_id_2 is not None and not args.materialize_blend:
        parser.error(
            "two-signal backtests require --materialize-blend so coverage and "
            "source hashes are pinned"
        )
    if (args.blend_output_signal_id or args.blend_output_signal_run_id) and not args.materialize_blend:
        parser.error("blend output identifiers require --materialize-blend")
    if args.accumulate and args.require_complete_accounting:
        parser.error(
            "--require-complete-accounting is not supported with --accumulate"
        )
    if not args.accumulate and args.require_complete_accounting:
        if args.corporate_action_artifact is None or args.canonical_data_root is None:
            parser.error(
                "--require-complete-accounting requires corporate-action artifact "
                "and canonical data root"
            )
        if args.max_participation_rate is None:
            args.max_participation_rate = 0.10
        if args.liquidity_gate_mode != "reject":
            parser.error(
                "complete accounting requires --liquidity-gate-mode reject"
            )
    if args.portfolio_analytics:
        if args.accumulate or not args.require_complete_accounting:
            parser.error(
                "--portfolio-analytics requires a non-accumulate complete-accounting backtest"
            )
        if not all((args.benchmark_id, args.benchmark_csv, args.holdout_start)):
            parser.error(
                "--portfolio-analytics requires --benchmark-id, --benchmark-csv, "
                "and --holdout-start"
            )

    exposure_gate_schedule: dict[str, bool] | None = None
    if args.exposure_gate_schedule is not None:
        raw = json.loads(args.exposure_gate_schedule.read_text())
        exposure_gate_schedule = {str(d): bool(v) for d, v in raw.items()}
        if not exposure_gate_schedule:
            parser.error("--exposure-gate-schedule must be a non-empty JSON object")

    blend_manifest: Path | None = None
    if args.materialize_blend:
        materialized_signal_id, materialized_run_id, blend_manifest = _materialize_blend(args)
        args.signal_id = materialized_signal_id
        args.signal_run_id = materialized_run_id
        args.signal_id_2 = None
        args.signal_run_id_2 = None
        args.blend_weight = 1.0

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
        rebalance_offset=args.rebalance_offset,
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
        research_root=args.research_root,
        holding_policy=args.holding_policy,
        score_delta_lookback=args.score_delta_lookback,
        score_delta_quantile=args.score_delta_quantile,
        score_delta_history_days=args.score_delta_history_days,
        score_delta_min_observations=args.score_delta_min_observations,
        posterior_stop_loss=args.posterior_stop_loss,
        winner_activation_return=args.winner_activation_return,
        winner_trailing_stop=args.winner_trailing_stop,
        stale_after_days=args.stale_after_days,
        stale_max_return=args.stale_max_return,
        replacement_rank_gap=args.replacement_rank_gap,
        rank_exit=args.rank_exit,
        rank_exit_hold_top=args.rank_exit_hold_top,
        maxdd_signal_id=args.maxdd_signal_id,
        maxdd_signal_run_id=args.maxdd_signal_run_id,
        maxdd_threshold=args.maxdd_threshold,
        maxdd_percentile=args.maxdd_percentile,
        exposure_gate_mode=args.exposure_gate_mode,
        exposure_gate_scale=args.exposure_gate_scale,
        exposure_gate_schedule=exposure_gate_schedule,
        pit_universe_artifact=args.pit_universe_artifact,
        corporate_action_artifact=args.corporate_action_artifact,
        canonical_data_root=args.canonical_data_root,
        max_participation_rate=args.max_participation_rate,
        liquidity_gate_mode=args.liquidity_gate_mode,
        adv_window=args.adv_window,
        adv_min_periods=args.adv_min_periods,
        require_complete_accounting=args.require_complete_accounting,
    )
    if args.accumulate:
        if args.pit_universe_artifact is not None:
            parser.error("--pit-universe-artifact is not supported with --accumulate")
        kwargs.pop("pit_universe_artifact", None)
        kwargs.pop("corporate_action_artifact", None)
        kwargs.pop("canonical_data_root", None)
        kwargs.pop("max_participation_rate", None)
        kwargs.pop("liquidity_gate_mode", None)
        kwargs.pop("adv_window", None)
        kwargs.pop("adv_min_periods", None)
        kwargs.pop("require_complete_accounting", None)
        result = runner.run_accumulate(**kwargs)
    else:
        kwargs.pop("maxdd_signal_id", None)
        kwargs.pop("maxdd_signal_run_id", None)
        kwargs.pop("maxdd_threshold", None)
        kwargs.pop("maxdd_percentile", None)
        kwargs["max_weight"] = args.max_weight
        result = runner.run_from_signal_cache(**kwargs)

    portfolio_analytics = None
    if args.portfolio_analytics:
        from qsys.research.portfolio_analytics import write_portfolio_analytics

        portfolio_analytics = write_portfolio_analytics(
            backtest_dir=Path(result.artifacts["manifest"]).parent,
            research_root=args.research_root,
            benchmark_id=args.benchmark_id,
            benchmark_csv=args.benchmark_csv,
            holdout_start=args.holdout_start,
        )

    print(json.dumps({
        "status": result.status,
        "backtest_id": result.backtest_id,
        "final_value": result.final_value,
        "total_return": result.total_return,
        "initial_capital": result.initial_capital,
        "trading_dates": len(result.daily_summary),
        "notes": result.notes,
        "combined_signal_manifest": str(blend_manifest) if blend_manifest else None,
        "portfolio_analytics": portfolio_analytics,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
