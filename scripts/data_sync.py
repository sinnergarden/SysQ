#!/usr/bin/env python3
"""Data Sync CLI — UC-1.

Usage:
    python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
    python scripts/data_sync.py --universe csi800 --target-date 2026-06-12
    python scripts/data_sync.py --universe csi1800 --target-date 2026-08-21 --apply
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PROJ = Path(__file__).resolve().parent.parent
_CRASH_EVIDENCE: dict | None = None


def _run_market_child(cmd: list[str], *, do_apply: bool, writer_lock) -> None:
    kwargs = {"cwd": str(PROJ), "check": True}
    if do_apply:
        if writer_lock is None:
            raise RuntimeError("applied wrapper requires active data-root writer lock")
        from qsys.data.source_audit import WRITER_LOCK_FD_ENV

        cmd.append("--wrapper-managed-finalize")
        child_env = os.environ.copy()
        child_env[WRITER_LOCK_FD_ENV] = str(writer_lock.fileno())
        kwargs.update({"env": child_env, "pass_fds": (writer_lock.fileno(),)})
    subprocess.run(cmd, **kwargs)


def _start_wrapper_evidence(*, data_root: Path, run_id: str, universe: str, target_date: str):
    from qsys.data.source_audit import SourceAuditStore

    audit_dir = data_root / "audit"
    store = SourceAuditStore(audit_dir / "audit.db")
    receipt_root = audit_dir / "source_runs"
    store.append_event(
        run_id,
        "run_started",
        {
            "entrypoint": "scripts/data_sync.py",
            "universe": universe,
            "target_date": str(target_date).replace("-", ""),
        },
    )
    return store, receipt_root


def _prepare_applied_market_child(
    cmd: list[str], *, data_root: Path, universe: str, target_date: str
):
    from qsys.data.source_audit import new_run_id

    run_id = new_run_id("data_sync")
    cmd.extend(["--apply", "--run-id", run_id])
    audit_store, receipt_root = _start_wrapper_evidence(
        data_root=data_root,
        run_id=run_id,
        universe=universe,
        target_date=target_date,
    )
    crash_evidence = {
        "store": audit_store,
        "run_id": run_id,
        "receipt_root": receipt_root,
        "entrypoint": "scripts/data_sync.py",
    }
    return run_id, audit_store, receipt_root, crash_evidence


def _attach_explicit_resume(
    cmd: list[str],
    *,
    audit_store,
    run_id: str,
    resume_from_run_id: str,
    universe: str,
    target_date: str,
) -> dict[str, str]:
    """Validate one explicit failed wrapper run and bind it to the fresh run."""

    proof = audit_store.validate_resume_run(
        resume_from_run_id=resume_from_run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe=universe,
        target_date=target_date,
    )
    audit_store.append_event(
        run_id,
        "resume_from_run",
        {
            "resume_from_run_id": proof["resume_from_run_id"],
            "source_receipt_sha256": proof["receipt_sha256"],
            "entrypoint": proof["entrypoint"],
            "universe": proof["universe"],
            "target_date": proof["target_date"],
        },
    )
    cmd.extend(
        [
            "--resume-from-run-id",
            proof["resume_from_run_id"],
            "--resume-from-receipt-sha256",
            proof["receipt_sha256"],
        ]
    )
    return proof


def _finalize_wrapper_evidence(
    *, audit_store, run_id: str, receipt_root: Path, final_readiness_ok: bool
) -> dict:
    """Finalize once, after every wrapper repair and final readiness check."""

    audit_store.append_event(
        run_id,
        "outer_readiness",
        {"status": "success" if final_readiness_ok else "failed", "completed_after_repairs": True},
    )
    evidence_summary = audit_store.run_evidence_summary(run_id)
    inner_gate_events = [
        event for event in evidence_summary["events"]
        if event["event_type"] == "inner_terminal_gates"
    ]
    if not inner_gate_events:
        raise RuntimeError("inner market sync did not publish terminal gate evidence")
    terminal = inner_gate_events[-1]["payload"]
    terminal_gates = dict(terminal.get("gates") or {})
    terminal_gates["readiness"] = bool(terminal_gates.get("readiness")) and final_readiness_ok
    if terminal.get("mode") == "unchanged":
        return audit_store.finalize_unchanged(
            run_id=run_id,
            gates=terminal_gates,
            receipt_root=receipt_root,
            prior_trusted=bool(terminal.get("prior_trusted")) and all(terminal_gates.values()),
        )
    return audit_store.finalize_run(
        run_id=run_id,
        source=str(terminal["source"]),
        scope_key=str(terminal["scope_key"]),
        range_start=str(terminal["range_start"]),
        range_end=str(terminal["range_end"]),
        fields=list(terminal["fields"]),
        gates=terminal_gates,
        receipt_root=receipt_root,
        trust_state="trusted" if all(terminal_gates.values()) else "untrusted",
        previous_open_session=terminal.get("previous_open_session"),
    )


def _block_untrusted_history_mutation(*, audit_store, run_id: str, result: dict) -> None:
    """Do not certify an outer core-history repair with target-day receipts."""

    mutated_symbols = sorted(
        {str(value) for value in result.get("canonical_mutated_symbols", [])}
    )
    if not result.get("apply") or not mutated_symbols:
        return
    from qsys.data.source_audit import stable_scope_hash

    mutation_range = result.get("canonical_mutation_range") or {}
    audit_store.append_event(
        run_id,
        "untrusted_outer_repair_scope",
        {
            "repair": "universe_history",
            "symbol_count": len(mutated_symbols),
            "symbols_sha256": stable_scope_hash(mutated_symbols),
            "range_start": mutation_range.get("range_start"),
            "range_end": mutation_range.get("range_end"),
            "scope_semantics": result.get("canonical_mutation_scope_semantics"),
            "reason": "outer history mutation lacks source receipts and canonical mutations",
            "recovery": "rerun after repair for a separately evidenced target-day sync",
        },
    )
    raise RuntimeError(
        "universe history mutated untrusted core scope; rerun is required before target-day certification"
    )


def _validate_universe_history_result(
    *, audit_store, run_id: str, universe: str, result: dict
) -> None:
    """Record canonical scope before considering the catch-up status."""

    _block_untrusted_history_mutation(
        audit_store=audit_store,
        run_id=run_id,
        result=result,
    )
    if result["status"] not in {"healthy", "success"}:
        raise RuntimeError(
            f"{universe} universe feature-history catch-up failed: "
            f"{result['summary_path']}"
        )


def _require_exact_sync_target(
    resolved: dict,
    *,
    requested_target: str,
    universe: str,
) -> str:
    requested = datetime.strptime(
        str(requested_target).replace("-", ""), "%Y%m%d"
    ).strftime("%Y-%m-%d")
    actual = resolved.get("resolved_trade_date")
    if not actual or actual != requested or resolved.get("status") != "success":
        raise RuntimeError(
            f"cannot resolve exact synced {universe} target date: {resolved}"
        )
    return actual


def _data_sync_run_root(data_root: Path, run_id: str) -> Path:
    """Keep repair summaries and backups beside the configured data SOT."""

    from qsys.data.source_audit import resolve_under, validate_run_id

    root = Path(data_root).resolve()
    return resolve_under(
        root, root / "audit" / "data_sync" / validate_run_id(run_id)
    )


def _shareholder_required_history_start_date(
    target_date: str, lookback_days: int
) -> str:
    if lookback_days <= 0:
        raise ValueError("shareholder history lookback days must be positive")
    target = datetime.strptime(str(target_date).replace("-", ""), "%Y%m%d")
    return (target - timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def _main_under_writer_lock(writer_lock=None):
    global _CRASH_EVIDENCE
    p = argparse.ArgumentParser(description="Data Sync — UC-1")
    p.add_argument("--config", default=None)
    p.add_argument("--universe", default=None)
    p.add_argument("--target-date", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument(
        "--force-fetch",
        action="store_true",
        help="Force the applied CSI800/CSI1800 market child to refetch the target scope",
    )
    p.add_argument(
        "--resume-from-run-id",
        default=None,
        help="Explicit failed run whose verified durable remote shards may be reused",
    )
    p.add_argument(
        "--skip-margin-repair",
        action="store_true",
        help="Skip financial_rc margin-history coverage repair after csi800 sync",
    )
    p.add_argument(
        "--skip-shareholder-repair",
        action="store_true",
        help="Skip PIT shareholder sidecar catch-up and freshness validation",
    )
    p.add_argument(
        "--skip-universe-history-catchup",
        action="store_true",
        help="Skip feature-lookback backfill for newly added universe members",
    )
    p.add_argument(
        "--universe-history-lookback-days",
        type=int,
        default=1461,
        help="Canonical history required for long-window semantic features",
    )
    p.add_argument(
        "--shareholder-start-date",
        default=None,
        help="Force a bounded shareholder announcement-date backfill start",
    )
    p.add_argument(
        "--shareholder-history-lookback-days",
        type=int,
        default=1461,
        help="Calendar-day PIT shareholder history required from target date",
    )
    p.add_argument(
        "--margin-lookback-days",
        type=int,
        default=120,
        help="Calendar-day history required for 60-session margin features",
    )
    p.add_argument(
        "--margin-min-active",
        type=int,
        default=None,
        help=(
            "Minimum symbols with margin balance per session; defaults to 450 "
            "for CSI800 and 1300 for CSI1800"
        ),
    )
    p.add_argument(
        "--margin-min-exchange-coverage",
        type=float,
        default=None,
        help=(
            "Minimum per-exchange universe share with margin balance; defaults "
            "to 0.90 for CSI800 and 0.75 for CSI1800 because CSI1000 includes "
            "more non-margin-eligible names"
        ),
    )
    p.add_argument(
        "--margin-lag-sessions",
        type=int,
        default=1,
        help=(
            "Margin publication lag in open sessions. Daily post-close sync "
            "defaults to repairing through the previous open session."
        ),
    )
    args = p.parse_args()
    if args.force_fetch and args.resume_from_run_id:
        p.error("--force-fetch and --resume-from-run-id are mutually exclusive")
    if args.margin_lag_sessions < 1:
        p.error("--margin-lag-sessions must be at least 1 for post-close sync")
    if args.shareholder_history_lookback_days <= 0:
        p.error("--shareholder-history-lookback-days must be positive")
    universe = args.universe
    target_date = args.target_date
    do_apply = args.apply
    sync_run_id = None
    wrapper_audit = None
    receipt_root = None
    if args.config:
        import yaml
        c = yaml.safe_load(Path(args.config).read_text())
        target_date = str(c.get("date_range", {}).get("end_date", "")).strip() or None
        if target_date is None:
            from scripts.ops.sync_csi800_daily import _resolve_target_date

            target_date = _resolve_target_date(None)
        configured_universe = c.get("universe", universe)
        if isinstance(configured_universe, dict):
            universe = configured_universe.get("name", universe)
        else:
            universe = configured_universe
        universe = universe or "csi800"
        do_apply = c.get("execution", {}).get("apply", False) or do_apply
        if args.resume_from_run_id and not do_apply:
            p.error("--resume-from-run-id is apply-only")
        from qsys.config import cfg

        data_root = Path(cfg.get_path("root")).resolve() if do_apply else None
        if c.get("tasks", {}).get("qlib_bin", True):
            cmd = [
                sys.executable,
                str(PROJ / "scripts/ops/sync_csi800_daily.py"),
                "--universe",
                universe,
                "--target-date",
                target_date,
            ]
            if do_apply and args.force_fetch:
                cmd.append("--force-fetch")
            if do_apply:
                sync_run_id, wrapper_audit, receipt_root, _CRASH_EVIDENCE = _prepare_applied_market_child(
                    cmd,
                    data_root=data_root,
                    universe=universe,
                    target_date=target_date,
                )
                if args.resume_from_run_id:
                    _attach_explicit_resume(
                        cmd,
                        audit_store=wrapper_audit,
                        run_id=sync_run_id,
                        resume_from_run_id=args.resume_from_run_id,
                        universe=universe,
                        target_date=target_date,
                    )
            _run_market_child(cmd, do_apply=do_apply, writer_lock=writer_lock)
    elif universe in {"csi800", "csi1800"}:
        if args.resume_from_run_id and not do_apply:
            p.error("--resume-from-run-id is apply-only")
        if target_date is None:
            from scripts.ops.sync_csi800_daily import _resolve_target_date

            target_date = _resolve_target_date(None)
        cmd = [
            sys.executable,
            str(PROJ / "scripts/ops/sync_csi800_daily.py"),
            "--universe",
            universe,
            "--target-date",
            target_date,
        ]
        if do_apply and args.force_fetch:
            cmd.append("--force-fetch")
        if do_apply:
            from qsys.config import cfg

            data_root = Path(cfg.get_path("root")).resolve()
            sync_run_id, wrapper_audit, receipt_root, _CRASH_EVIDENCE = _prepare_applied_market_child(
                cmd,
                data_root=data_root,
                universe=universe,
                target_date=target_date,
            )
            if args.resume_from_run_id:
                _attach_explicit_resume(
                    cmd,
                    audit_store=wrapper_audit,
                    run_id=sync_run_id,
                    resume_from_run_id=args.resume_from_run_id,
                    universe=universe,
                    target_date=target_date,
                )
        _run_market_child(cmd, do_apply=do_apply, writer_lock=writer_lock)
    else:
        print(
            "Specify --config or --universe csi800|csi1800",
            file=sys.stderr,
        )
        sys.exit(1)

    if not do_apply or universe not in {"csi800", "csi1800"}:
        return

    margin_min_active = (
        args.margin_min_active
        if args.margin_min_active is not None
        else (1300 if universe == "csi1800" else 450)
    )
    margin_min_exchange_coverage = (
        args.margin_min_exchange_coverage
        if args.margin_min_exchange_coverage is not None
        else (0.75 if universe == "csi1800" else 0.90)
    )

    from qsys.data.adapter import QlibAdapter
    from qsys.config import cfg
    from qsys.ops.instrument_coverage import read_instrument_file
    from qsys.data.storage import StockDataStore
    from qsys.ops.raw_sync import (
        resolve_margin_availability_date,
        run_margin_history_repair,
    )
    from qsys.ops.trade_date import resolve_daily_trade_date

    store = StockDataStore()

    resolved = resolve_daily_trade_date(
        target_date,
        universe=universe,
        allow_fallback_to_latest=False,
    )
    resolved_target = _require_exact_sync_target(
        resolved,
        requested_target=target_date,
        universe=universe,
    )
    adapter = QlibAdapter()
    instruments = read_instrument_file(
        adapter.qlib_dir / "instruments" / f"{universe}.txt"
    )
    target_ts = datetime.strptime(resolved_target, "%Y-%m-%d")
    active = instruments[
        (instruments["start_date"] <= target_ts)
        & (instruments["end_date"] >= target_ts)
    ]
    symbols = sorted(active["instrument"].astype(str).unique().tolist())
    if sync_run_id is None:
        raise RuntimeError("applied data sync did not establish a shared run_id")
    run_id = sync_run_id
    data_root = Path(cfg.get_path("root")).resolve()
    audit_run_root = _data_sync_run_root(data_root, run_id)
    report = {}
    if not args.skip_universe_history_catchup:
        from qsys.ops.universe_history import (
            UniverseHistoryCatchupError,
            run_universe_history_catchup,
        )

        try:
            history_result = run_universe_history_catchup(
                data_root=data_root,
                symbols=symbols,
                as_of_date=resolved_target,
                lookback_calendar_days=args.universe_history_lookback_days,
                output_dir=(
                    audit_run_root / "universe_history"
                ),
                apply=True,
            )
        except UniverseHistoryCatchupError as exc:
            history_result = exc.result
            report["universe_history"] = history_result
            _validate_universe_history_result(
                audit_store=wrapper_audit,
                run_id=run_id,
                universe=universe,
                result=history_result,
            )
            raise
        report["universe_history"] = history_result
        _validate_universe_history_result(
            audit_store=wrapper_audit,
            run_id=run_id,
            universe=universe,
            result=history_result,
        )
        if universe == "csi1800":
            from qsys.ops.pit_universe_snapshot import write_current_qlib_registry

            report["csi1800_registry_after_history"] = write_current_qlib_registry(
                qlib_dir=adapter.qlib_dir,
                universe=universe,
                instruments=symbols,
                as_of_date=resolved_target,
            )
    if not args.skip_margin_repair:
        margin_asof_date = resolve_margin_availability_date(
            store,
            signal_date=resolved_target,
            lag_sessions=args.margin_lag_sessions,
        )
        margin_asof_ts = datetime.strptime(margin_asof_date, "%Y-%m-%d")
        repair_start = (
            margin_asof_ts - timedelta(days=max(args.margin_lookback_days, 90))
        ).strftime("%Y-%m-%d")
        margin_result = run_margin_history_repair(
            symbols=symbols,
            start_date=repair_start,
            end_date=margin_asof_date,
            min_active=margin_min_active,
            min_exchange_coverage=margin_min_exchange_coverage,
            apply=True,
            output_dir=audit_run_root / "margin_repair",
            store=store,
            signal_date=resolved_target,
            availability_lag_sessions=args.margin_lag_sessions,
            universe=universe,
        )
        report["margin_availability"] = {
            "signal_date": resolved_target,
            "as_of_date": margin_asof_date,
            "lag_sessions": args.margin_lag_sessions,
            "source": "tushare.margin_detail",
        }
        report["margin_repair"] = margin_result
        if margin_result["status"] not in {"healthy", "success"}:
            raise RuntimeError(
                f"{universe} margin history repair failed: "
                f"{margin_result['summary_path']}"
            )

    if not args.skip_shareholder_repair:
        from qsys.common.config import load_strategy_config
        from qsys.feature.freshness import normalise_shareholder_freshness
        from qsys.ops.shareholder_sync import run_shareholder_history_repair

        financial_config = load_strategy_config("financial_rc", PROJ)
        freshness = normalise_shareholder_freshness(
            financial_config.get("feature_freshness", {}).get("shareholder")
        )
        shareholder_result = run_shareholder_history_repair(
            data_root=data_root,
            symbols=symbols,
            end_date=resolved_target,
            contract=freshness,
            apply=True,
            output_dir=(
                audit_run_root / "shareholder_repair"
            ),
            start_date=args.shareholder_start_date,
            required_history_start_date=(
                _shareholder_required_history_start_date(
                    resolved_target, args.shareholder_history_lookback_days
                )
            ),
        )
        report["shareholder_repair"] = shareholder_result
        if shareholder_result["status"] not in {"healthy", "success"}:
            raise RuntimeError(
                f"{universe} shareholder history repair failed: "
                f"{shareholder_result['summary_path']}"
            )

    from qsys.data.health import inspect_qlib_data_health

    final_readiness = inspect_qlib_data_health(
        resolved_target,
        ["$open", "$high", "$low", "$close", "$volume", "$factor"],
        universe=universe,
        min_active_instruments=1750 if universe == "csi1800" else 750,
    )
    report["final_readiness"] = final_readiness.to_dict()
    if final_readiness.blocking_issues:
        raise RuntimeError(
            f"{universe} final readiness failed: "
            + "; ".join(final_readiness.blocking_issues)
        )

    if wrapper_audit is None or receipt_root is None:
        raise RuntimeError("applied wrapper did not initialize source evidence")
    evidence_result = _finalize_wrapper_evidence(
        audit_store=wrapper_audit,
        run_id=run_id,
        receipt_root=receipt_root,
        final_readiness_ok=not final_readiness.blocking_issues,
    )
    if evidence_result.get("trust_state") not in {"trusted", "trusted_unchanged"}:
        raise RuntimeError(f"wrapper terminal evidence did not become trusted: {evidence_result}")
    report["source_evidence"] = evidence_result

    print(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        )
    )


def _invocation_applies(argv: list[str]) -> bool:
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--apply", action="store_true")
    probe.add_argument("--config", default=None)
    values, _ = probe.parse_known_args(argv)
    if values.apply:
        return True
    if not values.config:
        return False
    import yaml

    config = yaml.safe_load(Path(values.config).read_text()) or {}
    return bool(config.get("execution", {}).get("apply", False))


def main():
    global _CRASH_EVIDENCE
    _CRASH_EVIDENCE = None
    if not _invocation_applies(sys.argv[1:]):
        return _main_under_writer_lock(None)
    from qsys.config import cfg
    from qsys.data.source_audit import data_writer_lock

    data_root = Path(cfg.get_path("root")).resolve()
    with data_writer_lock(data_root) as writer_lock:
        try:
            return _main_under_writer_lock(writer_lock)
        except Exception as exc:
            if _CRASH_EVIDENCE is not None:
                try:
                    _CRASH_EVIDENCE["store"].record_crash_receipt(
                        run_id=_CRASH_EVIDENCE["run_id"],
                        receipt_root=_CRASH_EVIDENCE["receipt_root"],
                        entrypoint=_CRASH_EVIDENCE["entrypoint"],
                        error=repr(exc),
                    )
                except Exception:
                    pass
            raise

if __name__ == "__main__":
    main()
