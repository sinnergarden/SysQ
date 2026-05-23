"""DailyRunner — reusable multi-strategy daily pipeline skeleton.

Owns the stage orchestration sequence but delegates strategy-specific work
through the ``StrategyCandidate`` protocol (a runtime adapter interface).
No strategy-specific strings, imports, or path conventions should leak here.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.ops.commit_guard import (
    cleanup_committing,
    committed_marker,
    committing_marker,
    is_execution_committed,
)
from qsys.ops.daily_artifacts import archive_execution, save_run_meta
from qsys.ops.mtm import (
    StaleDataError,
    check_stale_prices,
    fetch_close_prices,
    load_mtm_snapshot,
    save_stale_check,
)
from qsys.ops.run_context import DailyRunContext
from qsys.strategy.base import StrategyCandidate


class DailyRunner:
    """Orchestration backbone for daily trading pipeline stages.

    Each method accepts a *DailyRunContext* and a *StrategyCandidate*.
    The runner controls the stage sequence; strategy-specific logic
    is delegated to the adapter.
    """

    # ── Path helpers ────────────────────────────────────────────────────

    @staticmethod
    def plan_dir(ctx: DailyRunContext) -> Path:
        return ctx.run_root / "plan"

    @staticmethod
    def exec_dir(ctx: DailyRunContext) -> Path:
        return ctx.run_root / "execution"

    @staticmethod
    def staging_dir(ctx: DailyRunContext) -> Path:
        return ctx.run_root / "execution" / "staging"

    @staticmethod
    def mtm_dir(ctx: DailyRunContext) -> Path:
        return ctx.run_root / "mtm"

    # ── Preopen ─────────────────────────────────────────────────────────

    def run_preopen(
        self, ctx: DailyRunContext, strategy: StrategyCandidate
    ) -> None:
        """Pre-open stage: inference → plan → notify."""
        self._validate(ctx, allowed={"preopen"})
        self._log_stage("preopen", ctx)

        t0 = time.time()
        run_root = ctx.run_root
        run_root.mkdir(parents=True, exist_ok=True)

        save_run_meta(
            run_root, ctx.trade_date, "preopen",
            data_date=ctx.data_date, debug_run=ctx.debug_run,
            reason=ctx.reason,
        )

        # [1/4] Resolve data date + load model
        print("\n[1/4] Resolving date & loading model...")
        data_date = strategy.resolve_data_date(ctx.trade_date)
        strategy.load_model()  # prints model info internally
        print(f"  Data date: {data_date}")

        # [2/4] Fetch data
        print(f"\n[2/4] Fetching data for {data_date}...")
        try:
            raw_data = strategy.fetch_data(data_date)  # prints row count internally
        except Exception as e:
            print(f"  ❌ {e}")
            if not ctx.no_notify:
                strategy.send_notification(
                    f"❌ {strategy.display_name} Pre-open {ctx.trade_date}\n数据获取失败: {e}"
                )
            return

        # [3/4] Generate predictions
        print(f"\n[3/4] Generating predictions...")
        try:
            predictions = strategy.generate_predictions(raw_data)
        except Exception as e:
            print(f"  ❌ {e}")
            if not ctx.no_notify:
                strategy.send_notification(
                    f"❌ {strategy.display_name} Pre-open {ctx.trade_date}\n预测生成失败: {e}"
                )
            return

        # Save predictions
        pred_path = run_root / "predictions" / f"predictions_{ctx.trade_date}.csv"
        pred_path.parent.mkdir(parents=True, exist_ok=True)
        predictions.to_csv(pred_path, index=False)

        # Strategy-specific prediction persistence (e.g. shared predictions dir)
        try:
            strategy.save_predictions(predictions, run_root, ctx.trade_date)
        except Exception as e:
            print(f"  ⚠ save_predictions failed: {e}")

        # ADR-7 signal sidecar
        try:
            from qsys.artifacts.adapters import adapt_predictions
            from qsys.artifacts.writer import write_artifacts, sidecar_path

            arts = list(
                adapt_predictions(
                    str(pred_path), strategy_id=ctx.strategy_id
                )
            )
            if arts:
                write_artifacts(arts, sidecar_path(pred_path))
                print(f"  → ADR-7 signal sidecar written ({len(arts)} rows)")
        except Exception as e:
            print(f"  ⚠ ADR-7 signal sidecar failed: {e}")

        # Top picks for display
        strategy.print_predictions_summary(predictions)

        # [4/4] Build trading plan
        print(f"\n[4/4] Building trading plan...")
        rebalance_skipped = not strategy.should_rebalance(ctx.trade_date)
        if rebalance_skipped:
            print(f"  ⏭ {strategy.rebalance_policy.get('rebalance_freq', 'weekly')} policy, already ran this week")
            plan_dir = self.plan_dir(ctx)
            plan_dir.mkdir(parents=True, exist_ok=True)
            from qsys.utils.json_io import write_json
            write_json(plan_dir / "plan_meta.json", {
                "trade_date": ctx.trade_date,
                "status": "skipped",
                "reason": f"{strategy.rebalance_policy.get('rebalance_freq', 'weekly')} policy, already ran this week",
                "build_ts": datetime.now().isoformat(),
            })
        else:
            try:
                strategy.build_plan(predictions, self.plan_dir(ctx))
                # ADR-7 order intent sidecar
                self._write_order_intent_sidecar(ctx)
            except Exception as e:
                print(f"  ❌ 建仓计划失败: {e}")
                if not ctx.no_notify:
                    strategy.send_notification(
                        f"❌ {strategy.display_name} Pre-open {ctx.trade_date}\n建仓计划失败: {e}"
                    )
                return

        # Notify
        if not ctx.no_notify:
            msg = strategy.build_preopen_message(
                ctx, rebalance_skipped, predictions
            )
            strategy.send_notification(msg)

        elapsed = time.time() - t0
        print(f"\n✅ Pre-open {ctx.trade_date} completed in {elapsed:.0f}s")

    # ── Postclose ───────────────────────────────────────────────────────

    def run_postclose(
        self, ctx: DailyRunContext, strategy: StrategyCandidate
    ) -> None:
        """Post-close stage: execute → MTM → notify."""
        self._validate(ctx, allowed={"postclose"})
        self._log_stage("postclose", ctx)

        t0 = time.time()
        run_root = ctx.run_root
        run_root.mkdir(parents=True, exist_ok=True)

        save_run_meta(
            run_root, ctx.trade_date, "postclose",
            debug_run=ctx.debug_run, reason=ctx.reason,
            extra={"force_rerun": ctx.force_rerun},
        )

        plan_dir = self.plan_dir(ctx)

        # Debug: fall back to production plan dir
        if ctx.debug_run and not (plan_dir / "order_intents.csv").exists():
            prod_root = ctx.project_root / "experiments" / f"{ctx.strategy_id}_daily" / ctx.trade_date
            prod_plan = prod_root / "plan"
            if (prod_plan / "order_intents.csv").exists():
                plan_dir = prod_plan
                print(f"  ℹ 使用生产路径计划: {plan_dir}")

        has_plan = (plan_dir / "order_intents.csv").exists()
        has_skip_meta = (plan_dir / "plan_meta.json").exists()
        has_skip = has_skip_meta and not has_plan
        already_committed = is_execution_committed(run_root)

        # ── COMMITTING crash recovery check ──
        committing_path = committing_marker(run_root)
        if committing_path.exists() and not already_committed:
            msg = (
                f"⛔ COMMITTING 标记存在（无 COMMITTED）！\n"
                f"上次提交中崩溃，execution/ 目录可能不完整。\n"
                f"请人工检查后手动删除 COMMITTING 文件重试。"
            )
            print(f"\n{'=' * 60}\n{msg}\n{'=' * 60}")
            sys.exit(1)

        # ── Idempotent skip ──
        if already_committed and not ctx.force_rerun:
            print(f"  ⏭ 执行已提交（COMMITTED 标记存在），跳过")
            print(f"  💡 如需重新执行请使用 --force-rerun + --reason")
            artifacts = strategy.load_artifacts_for_notification(ctx)
            mtm = load_mtm_snapshot(run_root / "mtm" / "mtm_snapshot.json")
            if not ctx.no_notify:
                msg = strategy.build_postclose_message(
                    ctx, mtm=mtm, artifacts=artifacts,
                    execution_committed=True, execution_skipped=has_skip,
                    idempotent_skip=True,
                )
                strategy.send_notification(msg)
            elapsed = time.time() - t0
            print(f"\n✅ Post-close {ctx.trade_date} (已提交，跳过) completed in {elapsed:.0f}s")
            return

        # ── Force-rerun: restore before-state, then archive ──
        if already_committed and ctx.force_rerun:
            if not ctx.reason:
                print("  ❌ --force-rerun 必须配合 --reason")
                sys.exit(1)
            print(f"  ⚠ --force-rerun 生效，原因: {ctx.reason}")
            self._restore_before_state(ctx, run_root, strategy)
            archive_execution(run_root)

        # ── Plan check ──
        if not has_plan and not has_skip:
            msg = (
                f"⛔ {strategy.display_name} Post-close {ctx.trade_date} BLOCKED\n"
                f"未找到 preopen 计划文件: {plan_dir}\n"
                f"请先运行 preopen。"
            )
            print(f"\n{msg}")
            if not ctx.no_notify:
                strategy.send_notification(msg)
            sys.exit(1)

        # ── Execution ──
        artifacts = None
        if has_plan:
            print(f"\n[1/4] Validating execution prerequisites...")
            self._validate_prerequisites(ctx, plan_dir, strategy)

            # ── Write COMMITTING before ledger write ──
            if not ctx.debug_run:
                if committing_path.exists():
                    print(f"  ❌ COMMITTING 标记已存在，疑似半提交状态。请人工检查。")
                    sys.exit(1)
                committing_path.parent.mkdir(parents=True, exist_ok=True)
                committing_path.write_text("")
                print(f"  📝 COMMITTING marker written — ledger write protected")

            try:
                artifacts = strategy.execute_plan(ctx)
                print(
                    f"  ✅ orders={artifacts.order_count}, "
                    f"total={artifacts.total_value_after:.2f}, "
                    f"cash={artifacts.cash_after:.2f}, "
                    f"turnover={artifacts.turnover:.2f}"
                )
            except Exception as e:
                if not ctx.debug_run:
                    cleanup_committing(run_root)
                print(f"  ❌ 执行失败: {e}")
                if not ctx.no_notify:
                    strategy.send_notification(
                        f"⛔ {strategy.display_name} Post-close {ctx.trade_date} FAILED\n{e}"
                    )
                sys.exit(1)

            if not ctx.debug_run:
                print(f"  Committing artifacts...")
                strategy.commit_execution(ctx, self.staging_dir(ctx))
                print(f"  ✅ Execution committed")
                self._write_execution_sidecars(ctx, plan_dir)
            else:
                print(f"  🔧 调试模式 — 不提交 shadow 账户")

        # ── MTM at CLOSE price ──
        print(f"\n{'[4/4]' if has_plan else '[1/1]'} MTM at CLOSE price...")
        mtm = strategy.mark_to_market(ctx)
        if mtm is None:
            print(f"  ⚠ 收盘价数据未就绪")
            if not ctx.no_notify:
                strategy.send_notification(
                    f"⛔ {strategy.display_name} Post-close {ctx.trade_date}\n"
                    f"收盘价数据未就绪。数据同步可能尚未完成。\n"
                    f"请先运行: python scripts/ops/sync_csi800_daily.py --apply"
                )
            sys.exit(1)

        self._write_portfolio_snapshot_sidecar(ctx, mtm)

        # ── Notify ──
        if not ctx.no_notify:
            stale_check_path = run_root / "mtm" / "stale_check.json"
            stale_check = None
            if stale_check_path.exists():
                try:
                    stale_check = json.loads(stale_check_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            msg = strategy.build_postclose_message(
                ctx, mtm=mtm, artifacts=artifacts,
                execution_committed=not ctx.debug_run,
                execution_skipped=has_skip,
                stale_check=stale_check,
            )
            strategy.send_notification(msg)

        elapsed = time.time() - t0
        print(f"\n✅ Post-close {ctx.trade_date} completed in {elapsed:.0f}s")

    # ── Notify-only ─────────────────────────────────────────────────────

    def run_notify_only(
        self, ctx: DailyRunContext, strategy: StrategyCandidate
    ) -> None:
        """Re-send notification from existing artifacts without any execution."""
        run_root = ctx.run_root
        print(f"\nNotify-only — {ctx.trade_date}")

        artifacts = strategy.load_artifacts_for_notification(ctx)
        mtm = load_mtm_snapshot(run_root / "mtm" / "mtm_snapshot.json")
        already_committed = is_execution_committed(run_root)
        has_skip = self.plan_dir(ctx).exists() and not (
            self.plan_dir(ctx) / "order_intents.csv"
        ).exists()

        stale_check_path = run_root / "mtm" / "stale_check.json"
        stale_check = None
        if stale_check_path.exists():
            try:
                stale_check = json.loads(stale_check_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        msg = strategy.build_postclose_message(
            ctx, mtm=mtm, artifacts=artifacts,
            execution_committed=already_committed,
            execution_skipped=has_skip,
            stale_check=stale_check,
        )
        strategy.send_notification(msg)
        print(f"✅ Notify-only {ctx.trade_date} completed")

    # ── Train ───────────────────────────────────────────────────────────

    def run_train(self, ctx: DailyRunContext, strategy: StrategyCandidate) -> None:
        """Train stage: delegates to weekly training script."""
        self._validate(ctx, allowed={"train"})
        self._log_stage("train", ctx)
        self._ensure_dirs(ctx)
        print(f"  ℹ {strategy.display_name} training delegated to weekly script")

    # ── Private helpers ─────────────────────────────────────────────────

    @staticmethod
    def _validate(ctx: DailyRunContext, allowed: set[str]) -> None:
        if ctx.mode not in allowed:
            raise ValueError(
                f"DailyRunner.{ctx.mode} is not a valid mode for this method. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _log_stage(stage: str, ctx: DailyRunContext) -> None:
        tag = "DEBUG" if ctx.debug_run else "PROD"
        print(
            f"\n[{tag}] DailyRunner.{stage} | {ctx.trade_date} | "
            f"strategy={ctx.strategy_id} | account={ctx.account_id}"
        )

    @staticmethod
    def _ensure_dirs(ctx: DailyRunContext) -> None:
        ctx.run_root.mkdir(parents=True, exist_ok=True)

    def _restore_before_state(
        self, ctx: DailyRunContext, run_root: Path, strategy: StrategyCandidate
    ) -> None:
        """Restore shadow account/positions to pre-execution state."""
        exec_before = run_root / "execution" / "account_before.json"
        pos_before = run_root / "execution" / "positions_before.csv"
        has_before_state = exec_before.exists() and pos_before.exists()
        if has_before_state:
            shutil.copy2(str(exec_before), str(ctx.project_root / "shadow" / "account.json"))
            shutil.copy2(str(pos_before), str(ctx.project_root / "shadow" / "positions.csv"))
            print(f"  🔄 Shadow 已恢复至执行前状态")
        else:
            msg = (
                f"⛔ {strategy.display_name} Post-close {ctx.trade_date} BLOCKED\n"
                f"--force-rerun 需要 execution/account_before.json 和 "
                f"positions_before.csv 才能重放交易。\n"
                f"文件不存在，阻断执行。"
            )
            print(f"\n{msg}")
            if not ctx.no_notify:
                strategy.send_notification(msg)
            sys.exit(1)

    def _validate_prerequisites(
        self, ctx: DailyRunContext, plan_dir: Path, strategy: StrategyCandidate
    ) -> None:
        """Open price check + stale close-price check."""
        instruments = strategy.load_plan_instruments(plan_dir)
        if instruments:
            try:
                open_prices = strategy.fetch_open_prices(
                    ctx.trade_date, instruments
                )
                if not open_prices:
                    raise ValueError("No open prices available")
                print(f"  ✅ Open prices: {len(open_prices)} instruments")
            except Exception as e:
                print(f"  ❌ 开盘价不可用: {e}")
                if not ctx.no_notify:
                    strategy.send_notification(
                        f"⛔ {strategy.display_name} Post-close {ctx.trade_date} BLOCKED\n"
                        f"开盘价数据不可用。\n{e}"
                    )
                sys.exit(1)

        # Stale close-price check
        print(f"\n[2/4] Stale close-price check...")
        all_instruments: set[str] = self._collect_position_instruments(ctx, plan_dir)
        if all_instruments:
            close_prices = fetch_close_prices(ctx.trade_date, sorted(all_instruments))
            if close_prices:
                stale_positions = pd.DataFrame(
                    {"instrument": list(all_instruments), "quantity": 0}
                )
                try:
                    stale_result = check_stale_prices(
                        ctx.trade_date, close_prices, stale_positions,
                        project_root=ctx.project_root,
                    )
                    save_stale_check(ctx.run_root, stale_result)
                    print(
                        f"  ✅ Stale check: {stale_result['status']} "
                        f"({stale_result['identical_count']}/{stale_result['checked_count']} identical)"
                    )
                except StaleDataError as e:
                    save_stale_check(ctx.run_root, e.stale_check)
                    print(f"  ❌ {e}")
                    if not ctx.no_notify:
                        strategy.send_notification(
                            f"⛔ {strategy.display_name} Post-close {ctx.trade_date} BLOCKED\n"
                            f"收盘价数据陈旧，阻断执行。\n"
                            f"一致={e.stale_check.get('identical_count', 0)}/"
                            f"{e.stale_check.get('checked_count', 0)} "
                            f"({e.stale_check.get('identical_ratio', 0)*100:.0f}%)\n"
                            f"请运行数据同步后重试。"
                        )
                    sys.exit(1)

    @staticmethod
    def _collect_position_instruments(
        ctx: DailyRunContext, plan_dir: Path
    ) -> set[str]:
        """Build union of current positions and plan instruments."""
        all_instruments: set[str] = set()
        ledger_db_path = str(ctx.project_root / "data" / "trade.db")
        if Path(ledger_db_path).exists():
            try:
                from qsys.ledger.service import LedgerService

                svc = LedgerService(ledger_db_path)
                for p in svc.get_positions(ctx.account_id):
                    if int(p["quantity"]) > 0:
                        all_instruments.add(p["symbol"])
                svc.close()
            except Exception:
                pass
        shadow_pos = ctx.project_root / "shadow" / "positions.csv"
        if shadow_pos.exists():
            pos_df = pd.read_csv(shadow_pos)
            if not pos_df.empty:
                all_instruments.update(pos_df["instrument"].tolist())
        intents_path = plan_dir / "order_intents.csv"
        if intents_path.exists():
            intents_df = pd.read_csv(intents_path)
            all_instruments.update(intents_df["instrument"].tolist())
        return all_instruments

    # ── ADR-7 sidecars ─────────────────────────────────────────────────

    @staticmethod
    def _write_order_intent_sidecar(ctx: DailyRunContext) -> None:
        """Write ADR-7 order intent sidecar if plan exists."""
        try:
            from qsys.artifacts.adapters import adapt_order_intents
            from qsys.artifacts.writer import write_artifacts, sidecar_path

            oi_path = ctx.run_root / "plan" / "order_intents.csv"
            if oi_path.exists():
                oi_arts = list(
                    adapt_order_intents(
                        str(oi_path),
                        strategy_id=ctx.strategy_id,
                        account_id=ctx.account_id,
                    )
                )
                if oi_arts:
                    write_artifacts(oi_arts, sidecar_path(oi_path))
                    print(f"  → ADR-7 order intent sidecar written ({len(oi_arts)} rows)")
        except Exception as e:
            print(f"  ⚠ ADR-7 order intent sidecar failed: {e}")

    @staticmethod
    def _write_execution_sidecars(ctx: DailyRunContext, plan_dir: Path) -> None:
        """Write ADR-7 execution sidecar + run manifest."""
        try:
            from qsys.artifacts.adapters import (
                adapt_executions,
                build_run_manifest,
                read_execution_summary,
            )
            from qsys.artifacts.writer import write_artifact, write_artifacts, sidecar_path

            exec_dir = ctx.run_root / "execution"
            lr_csv = exec_dir / "ledger_rows.csv"
            if lr_csv.exists():
                ex_arts = list(
                    adapt_executions(
                        str(lr_csv),
                        strategy_id=ctx.strategy_id,
                        account_id=ctx.account_id,
                    )
                )
                if ex_arts:
                    write_artifacts(ex_arts, sidecar_path(lr_csv))
                    print(f"  → ADR-7 execution sidecar written ({len(ex_arts)} rows)")

            summary = read_execution_summary(exec_dir / "execution_summary.json")
            manifest = build_run_manifest(
                run_id=summary.get("run_id", f"{ctx.strategy_id}_execute_{ctx.trade_date}"),
                trade_date=ctx.trade_date,
                stage="postclose",
                strategy_id=ctx.strategy_id,
                account_id=ctx.account_id,
                status="completed",
                output_artifacts=[
                    {"path": sidecar_path(lr_csv).name, "type": "ExecutionArtifact"},
                    {"path": "manifest.adr7.json", "type": "RunManifest"},
                ],
            )
            write_artifact(manifest, exec_dir / "manifest.adr7.json")
            print(f"  → ADR-7 run manifest written")
        except Exception as e:
            print(f"  ⚠ ADR-7 execution sidecar failed: {e}")

    @staticmethod
    def _write_portfolio_snapshot_sidecar(ctx: DailyRunContext, mtm: dict) -> None:
        """Write ADR-7 portfolio snapshot sidecar."""
        try:
            from qsys.artifacts.adapters import adapt_portfolio_snapshot
            from qsys.artifacts.writer import write_artifact

            exec_dir = ctx.run_root / "execution"
            exec_summary = {}
            es_path = exec_dir / "execution_summary.json"
            if es_path.exists():
                exec_summary = json.loads(es_path.read_text()) or {}
            mtm_path = ctx.run_root / "mtm" / "mtm_snapshot.json"
            snapshot = adapt_portfolio_snapshot(
                mtm_path,
                trade_date=ctx.trade_date,
                account_id=ctx.account_id,
                strategy_id=ctx.strategy_id,
                turnover=exec_summary.get("turnover", 0.0),
            )
            if snapshot:
                write_artifact(snapshot, mtm_path.with_name(mtm_path.stem + ".adr7.json"))
                print(f"  → ADR-7 portfolio snapshot sidecar written")
        except Exception as e:
            print(f"  ⚠ ADR-7 portfolio snapshot sidecar failed: {e}")
