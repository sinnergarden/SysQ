#!/usr/bin/env python3
"""Create candidate artifacts and promote to shadow trading (UC-10).

Usage::

    # Create a candidate from backtest results
    python scripts/promote_candidate.py create \\
        --candidate-id cand_alpha_v1_cls_zscore_202606 \\
        --signal-id alpha_v1_existing_score \\
        --signal-run-id rolling__alpha_v1_rolling_full_2024_2026__... \\
        --strategy-config-id alpha_v1_config_v1 \\
        --strategy-template-id rank_weight_top20 \\
        --strategy-run-id rank_weight_top20__alpha_v1... \\
        --backtest-id bt_2024-01-01_2026-05-22_4143cfd3 \\
        --backtest-path data/research/backtests/<strategy_run_id>/bt_... \\
        --created-by agent \\
        --notes "Baseline existing-score candidate"

    # Promote to shadow
    python scripts/promote_candidate.py promote \\
        --candidate-id cand_alpha_v1_cls_zscore_202606 \\
        --target shadow --promoted-by agent

    # One-step create + promote
    python scripts/promote_candidate.py create \\
        --candidate-id cand_alpha_v1_cls_zscore_202606 \\
        --signal-id ... --signal-run-id ... \\
        --strategy-config-id ... --strategy-template-id ... \\
        --strategy-run-id ... --backtest-id ... --backtest-path ... \\
        --promote-to shadow --promoted-by agent
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qsys.research.candidate import (
    build_candidate_payload,
    load_backtest_evidence,
    promote_candidate_to_shadow,
    validate_candidate,
    write_candidate,
)

RESEARCH_ROOT_DEFAULT = "data/research"


# ── Parser ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="UC-10 Candidate Promotion — create and promote candidates",
    )
    parser.add_argument(
        "--research-root", default=RESEARCH_ROOT_DEFAULT,
        help=f"Research root directory (default: {RESEARCH_ROOT_DEFAULT})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── create ──────────────────────────────────────────────────────────
    create_p = sub.add_parser("create", help="Create a candidate from backtest results")
    create_p.add_argument("--candidate-id", required=True)
    create_p.add_argument("--signal-id", required=True)
    create_p.add_argument("--signal-run-id", required=True)
    create_p.add_argument("--strategy-config-id", required=True)
    create_p.add_argument("--strategy-id", required=True)
    create_p.add_argument(
        "--strategy-config-path",
        default=None,
        help="Runtime strategy YAML (default: configs/strategies/<strategy-id>.yaml)",
    )
    create_p.add_argument("--strategy-template-id", required=True)
    create_p.add_argument("--strategy-run-id", required=True)
    create_p.add_argument("--backtest-id", required=True)
    create_p.add_argument("--backtest-path", required=True)
    create_p.add_argument("--metrics-path", default=None, help="Path to metrics.json (default: <backtest-path>/metrics.json)")
    create_p.add_argument("--manifest-path", default=None, help="Path to manifest.json (default: <backtest-path>/manifest.json)")
    create_p.add_argument("--experiment-id", default=None)
    create_p.add_argument("--label-id", default=None)
    create_p.add_argument("--created-by", default="manual")
    create_p.add_argument("--top-n", type=int, default=None)
    create_p.add_argument("--rebalance-freq", default=None)
    create_p.add_argument("--notes", default=None)
    create_p.add_argument("--overwrite", action="store_true",
                          help="Overwrite existing candidate.yaml")
    create_p.add_argument("--promote-to", choices=["shadow"],
                          help="Immediately promote to shadow after creation")
    create_p.add_argument("--promoted-by", default=None,
                          help="Promoter identity (required with --promote-to)")
    create_p.add_argument("--overwrite-pointer", action="store_true",
                          help="Overwrite existing shadow promotion pointer")

    # ── promote ─────────────────────────────────────────────────────────
    promote_p = sub.add_parser("promote", help="Promote a candidate to shadow")
    promote_p.add_argument("--candidate-id", required=True)
    promote_p.add_argument("--target", required=True, choices=["shadow", "production"],
                           help="Promotion target (production is not implemented)")
    promote_p.add_argument("--promoted-by", default="manual")
    promote_p.add_argument("--overwrite-pointer", action="store_true")

    return parser


# ── Validation ────────────────────────────────────────────────────────────


def _validate_create_args(args: argparse.Namespace) -> None:
    """Validate CLI arguments for the *create* subcommand."""
    errors: list[str] = []

    # Required fields check
    required = {
        "candidate_id": args.candidate_id,
        "signal_id": args.signal_id,
        "signal_run_id": args.signal_run_id,
        "strategy_config_id": args.strategy_config_id,
        "strategy_id": args.strategy_id,
        "strategy_template_id": args.strategy_template_id,
        "strategy_run_id": args.strategy_run_id,
        "backtest_id": args.backtest_id,
        "backtest_path": args.backtest_path,
    }
    for name, val in required.items():
        if not val or (isinstance(val, str) and not val.strip()):
            errors.append(f"--{name.replace('_', '-')} is required")

    # backtest_path existence
    bt_path = Path(args.backtest_path)
    if not bt_path.exists():
        errors.append(f"backtest_path does not exist: {bt_path}")

    # metrics_path / manifest_path existence (only if explicitly provided)
    if args.metrics_path:
        mp = Path(args.metrics_path)
        if not mp.exists():
            errors.append(f"metrics_path does not exist: {mp}")
    if args.manifest_path:
        mp = Path(args.manifest_path)
        if not mp.exists():
            errors.append(f"manifest_path does not exist: {mp}")

    # promote-to requires promoted-by
    if args.promote_to and not args.promoted_by:
        errors.append("--promoted-by is required when using --promote-to")

    if errors:
        print("ERROR: validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)


def _validate_promote_args(args: argparse.Namespace) -> None:
    """Validate CLI arguments for the *promote* subcommand."""
    if args.target == "production":
        print(
            "ERROR: Production promotion (--target production) is not implemented. "
            "Only --target shadow is supported.",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Handlers ──────────────────────────────────────────────────────────────


def _handle_create(args: argparse.Namespace) -> None:
    research_root = args.research_root

    _validate_create_args(args)

    # Build evidence from backtest artifacts
    evidence = load_backtest_evidence(
        args.backtest_path,
        metrics_path=args.metrics_path,
        manifest_path=args.manifest_path,
    )

    # Build source
    source: dict[str, Any] = {
        "experiment_id": args.experiment_id,
        "label_id": args.label_id,
        "notes": args.notes,
    }

    signal_ref = {"signal_id": args.signal_id, "signal_run_id": args.signal_run_id}
    strategy: dict[str, Any] = {
        "strategy_id": args.strategy_id,
        "strategy_config_id": args.strategy_config_id,
        "strategy_config_path": args.strategy_config_path,
        "strategy_template_id": args.strategy_template_id,
        "top_n": args.top_n,
        "rebalance_freq": args.rebalance_freq,
    }
    backtest_ref = {
        "strategy_run_id": args.strategy_run_id,
        "backtest_id": args.backtest_id,
        "path": str(Path(args.backtest_path).resolve()),
    }

    # Pre-check: when promoting to shadow, ensure pointer is writable
    # before writing any artifacts (avoid orphan candidate.yaml).
    if args.promote_to == "shadow":
        pointer_path = Path(research_root) / "promotions" / "shadow.yaml"
        if pointer_path.exists() and not args.overwrite_pointer:
            print(
                f"ERROR: Shadow promotion pointer already exists at {pointer_path}. "
                f"Use --overwrite-pointer to replace.",
                file=sys.stderr,
            )
            sys.exit(1)

    candidate = build_candidate_payload(
        candidate_id=args.candidate_id,
        signal_ref=signal_ref,
        strategy=strategy,
        backtest_ref=backtest_ref,
        evidence=evidence,
        source=source,
        created_by=args.created_by,
    )

    # Write candidate
    candidate_path = write_candidate(
        candidate,
        research_root=research_root,
        overwrite=args.overwrite,
    )
    result: dict[str, Any] = {
        "status": "created",
        "candidate_id": args.candidate_id,
        "candidate_path": str(candidate_path),
        "pointer_path": None,
    }

    # One-step promote
    if args.promote_to == "shadow":
        pointer = promote_candidate_to_shadow(
            args.candidate_id,
            research_root=research_root,
            promoted_by=args.promoted_by or "manual",
            overwrite_pointer=args.overwrite_pointer,
            project_root=Path.cwd(),
        )
        result["status"] = "promoted_shadow"
        result["pointer_path"] = str(
            Path(research_root) / "promotions" / "shadow.yaml"
        )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)


def _handle_promote(args: argparse.Namespace) -> None:
    _validate_promote_args(args)

    pointer = promote_candidate_to_shadow(
        args.candidate_id,
        research_root=args.research_root,
        promoted_by=args.promoted_by,
        overwrite_pointer=args.overwrite_pointer,
        project_root=Path.cwd(),
    )

    result = {
        "status": "promoted_shadow",
        "candidate_id": args.candidate_id,
        "candidate_path": pointer.get("candidate_path"),
        "pointer_path": str(Path(args.research_root) / "promotions" / "shadow.yaml"),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "create":
        _handle_create(args)
    elif args.command == "promote":
        _handle_promote(args)


if __name__ == "__main__":
    main()
