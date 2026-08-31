#!/usr/bin/env python3
"""Signal Analytics CLI — UC-5.

Usage:
    python scripts/run_signal_analytics.py --experiment-id <id>
    python scripts/run_signal_analytics.py --diagnostics-config <path>
    python scripts/run_signal_analytics.py --feature-catalog-config <path>
    python scripts/run_signal_analytics.py --backtest-validation-config <path>
    python scripts/run_signal_analytics.py --signal-id <id> --signal-run-id <run> --label-id <id>
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def _resolve_manifest(experiment_id, research_root):
    mp = Path(research_root) / "experiments" / experiment_id / "signal_research_manifest.json"
    if not mp.exists(): print(f"Manifest not found: {mp}"); sys.exit(1)
    import json; m = json.loads(mp.read_text())
    refs = {}
    for sr in m.get("signal_runs", []):
        if sr.get("signal_id") and sr.get("signal_run_id"): refs[sr["signal_id"]] = sr["signal_run_id"]
    for csr in m.get("combined_signal_runs", []):
        if csr.get("signal_id") and csr.get("signal_run_id"): refs[csr["signal_id"]] = csr["signal_run_id"]
    lids = list(dict.fromkeys(er.get("label_id") for er in m.get("eval_refs", []) if er.get("label_id")))
    if not lids: lids = list(dict.fromkeys(l.get("label_id") for l in m.get("labels", []) if l.get("label_id")))
    return refs, lids

def main():
    p = argparse.ArgumentParser(description="Signal Analytics — UC-5")
    p.add_argument("--experiment-id", default=None)
    p.add_argument(
        "--diagnostics-config",
        default=None,
        help="Run PIT-filtered ResearchDiagnostics from an explicit YAML config",
    )
    p.add_argument(
        "--feature-catalog-config",
        default=None,
        help="Review the current complete feature universe from an explicit YAML config",
    )
    p.add_argument(
        "--alpha-map-config",
        default=None,
        help="Aggregate validated Stage-A artifacts into an Alpha Map",
    )
    p.add_argument(
        "--pit-benchmark-config",
        default=None,
        help="Build or validate a PIT-universe synthetic benchmark and its analytics",
    )
    p.add_argument(
        "--portfolio-analytics-config",
        default=None,
        help="Write or validate multiple benchmark views over a frozen backtest",
    )
    p.add_argument(
        "--backtest-validation-config",
        default=None,
        help="Independently validate a frozen complete-accounting backtest",
    )
    p.add_argument(
        "--stage-c-config",
        default=None,
        help="Freeze or independently validate a Stage-C assessment",
    )
    p.add_argument(
        "--validate-only",
        action="store_true",
        help="Independently validate the selected feature-catalog artifact",
    )
    p.add_argument("--signal-id", default=None); p.add_argument("--signal-run-id", default=None)
    p.add_argument("--label-id", default=None)
    p.add_argument("--start-date", default=None); p.add_argument("--end-date", default=None)
    p.add_argument("--min-count", type=int, default=5)
    p.add_argument("--output-dir", default=None); p.add_argument("--research-root", default="data/research")
    args = p.parse_args()
    selected_modes = sum(bool(value) for value in (
        args.experiment_id, args.diagnostics_config, args.feature_catalog_config,
        args.alpha_map_config, args.stage_c_config,
        args.pit_benchmark_config,
        args.portfolio_analytics_config,
        args.backtest_validation_config,
        args.signal_id,
    ))
    if selected_modes != 1:
        p.error(
            "select exactly one mode: --experiment-id, --diagnostics-config, "
            "--feature-catalog-config, --stage-c-config, --alpha-map-config, "
            "--pit-benchmark-config, --portfolio-analytics-config, "
            "--backtest-validation-config, or direct "
            "--signal-id/--signal-run-id/--label-id"
        )
    if args.validate_only and not (
        args.feature_catalog_config or args.diagnostics_config or args.stage_c_config
        or args.alpha_map_config or args.pit_benchmark_config
        or args.portfolio_analytics_config
        or args.backtest_validation_config
    ):
        p.error(
            "--validate-only requires --feature-catalog-config or "
            "--diagnostics-config, --stage-c-config, --alpha-map-config, or "
            "--pit-benchmark-config/--portfolio-analytics-config"
            "/--backtest-validation-config"
        )
    if args.backtest_validation_config:
        if any((args.signal_run_id, args.label_id)):
            p.error(
                "--backtest-validation-config cannot be combined with direct signal arguments"
            )
        from qsys.research.backtest_validation import (
            validate_complete_accounting_backtest,
        )

        result = validate_complete_accounting_backtest(
            args.backtest_validation_config
        )
        print(
            "Complete-accounting backtest validation: "
            f"{result['status']} {result['manifest_sha256']}"
        )
        print(json.dumps(result, sort_keys=True))
        return
    if args.portfolio_analytics_config:
        if any((args.signal_run_id, args.label_id)):
            p.error(
                "--portfolio-analytics-config cannot be combined with direct signal arguments"
            )
        from qsys.research.portfolio_analytics import run_portfolio_analytics_config

        result = run_portfolio_analytics_config(
            args.portfolio_analytics_config, validate_only=args.validate_only
        )
        for item in result["benchmarks"]:
            print(
                f"Portfolio analytics [{item['benchmark_id']}]: "
                f"{item['portfolio_analytics_identity_sha256']}"
            )
            print(f"Manifest: {item['manifest']}")
        return
    if args.pit_benchmark_config:
        if any((args.signal_run_id, args.label_id)):
            p.error("--pit-benchmark-config cannot be combined with direct signal arguments")
        from qsys.research.pit_universe_benchmark import (
            run_pit_universe_benchmark_config,
        )

        result = run_pit_universe_benchmark_config(
            args.pit_benchmark_config, validate_only=args.validate_only
        )
        print(f"PIT benchmark: {result['benchmark_identity_sha256']}")
        print(f"Manifest: {result['manifest']}")
        if result.get("portfolio_analytics"):
            print(
                "Portfolio analytics: "
                f"{result['portfolio_analytics']['portfolio_analytics_identity_sha256']}"
            )
        return
    if args.diagnostics_config and any(
        (args.signal_run_id, args.label_id)
    ):
        p.error("--diagnostics-config cannot be combined with direct signal arguments")
    if args.experiment_id and any((args.signal_run_id, args.label_id)):
        p.error("--experiment-id cannot be combined with direct signal arguments")
    if args.signal_id and not all(
        (args.signal_run_id, args.label_id)
    ):
        p.error(
            "direct analytics requires --signal-id, --signal-run-id, and --label-id"
        )
    if args.feature_catalog_config:
        if any((args.signal_run_id, args.label_id)):
            p.error("--feature-catalog-config cannot be combined with direct signal arguments")
        if args.validate_only:
            from qsys.research.feature_catalog_validation import validate_feature_catalog

            result = validate_feature_catalog(
                args.feature_catalog_config, root=args.research_root
            )
            print(f"Feature catalog validation: {result['catalog_identity_sha256']}")
            print(f"Validation: {result['validation']}")
        else:
            from qsys.analysis.feature_catalog import FeatureCatalog

            result = FeatureCatalog.from_config(
                args.feature_catalog_config, root=args.research_root
            ).run()
            print(f"Feature catalog: {result['catalog_identity_sha256']}")
            print(f"Manifest: {result['manifest']}")
        return
    if args.stage_c_config:
        if any((args.signal_run_id, args.label_id)):
            p.error("--stage-c-config cannot be combined with direct signal arguments")
        if args.validate_only:
            from qsys.research.stage_c_validation import validate_stage_c_assessment

            result = validate_stage_c_assessment(
                args.stage_c_config, root=args.research_root
            )
            print(f"Stage-C validation: {result['stage_c_identity_sha256']}")
            print(
                "Validation: "
                f"{Path(args.research_root) / 'stage_c_assessments' / result['assessment_id'] / 'validation.json'}"
            )
        else:
            from qsys.research.stage_c import StageCAssessment

            result = StageCAssessment.from_config(
                args.stage_c_config, root=args.research_root
            ).run()
            print(f"Stage-C assessment: {result['stage_c_identity_sha256']}")
            print(f"Manifest: {result['manifest']}")
        return
    if args.alpha_map_config:
        if any((args.signal_run_id, args.label_id)):
            p.error("--alpha-map-config cannot be combined with direct signal arguments")
        if args.validate_only:
            from qsys.research.alpha_map_validation import validate_alpha_map

            result = validate_alpha_map(
                args.alpha_map_config, root=args.research_root
            )
            print(f"Alpha Map validation: {result['alpha_map_identity_sha256']}")
            print(
                "Validation: "
                f"{Path(args.research_root) / 'alpha_maps' / result['alpha_map_id'] / 'validation.json'}"
            )
        else:
            from qsys.research.alpha_map import AlphaMap

            result = AlphaMap.from_config(
                args.alpha_map_config, root=args.research_root
            ).run()
            print(f"Alpha Map: {result['alpha_map_identity_sha256']}")
            print(f"Manifest: {result['manifest']}")
        return
    if args.diagnostics_config:
        if args.validate_only:
            from qsys.research.diagnostics_validation import (
                validate_research_diagnostics,
            )

            result = validate_research_diagnostics(
                args.diagnostics_config, root=args.research_root
            )
            print(f"Diagnostics validation: {result['diagnostics_identity_sha256']}")
            print(f"Validation: {result['validation']}")
        else:
            from qsys.analysis.research_diagnostics import ResearchDiagnostics

            result = ResearchDiagnostics.from_config(
                args.diagnostics_config, root=args.research_root
            ).run()
            print(f"Diagnostics: {result['diagnostics_identity_sha256']}")
            print(f"Manifest: {result['manifest']}")
        return
    from qsys.research.signal_analytics import SignalAnalytics
    with SignalAnalytics(root=args.research_root) as sa:
        if args.experiment_id:
            refs, lids = _resolve_manifest(args.experiment_id, args.research_root)
            print(f"Experiment: {args.experiment_id}")
            if lids: print(f"  Labels: {lids}")
            for sid, srid in refs.items():
                print(f"\n--- {sid} / {srid} vs {lids} ---")
                ic = sa.compute_ic_matrix(signal_ids=[sid], signal_run_ids={sid: srid}, label_ids=lids if lids else None, start_date=args.start_date, end_date=args.end_date, min_count=args.min_count)
                if ic is not None and not ic.empty: print(ic.to_string(index=False))
        elif args.signal_id and args.label_id:
            signal_run_ids = {args.signal_id: args.signal_run_id}
            print(f"\n--- IC: {args.signal_id} vs {args.label_id} ---")
            ic = sa.compute_ic_matrix(signal_ids=[args.signal_id], signal_run_ids=signal_run_ids, label_ids=[args.label_id], start_date=args.start_date, end_date=args.end_date, min_count=args.min_count)
            if ic is not None and not ic.empty: print(ic.to_string(index=False))
        if args.output_dir:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    main()
