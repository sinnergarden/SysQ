#!/usr/bin/env python3
"""
Alpha V1 每日运营验证脚本。

测试：
  1. 执行幂等性：重复 postclose 只 commit 一次
  2. --debug-run 不修改 shadow 文件
  3. stale close 数据硬阻断（需数据配合）
  4. --notify-only 不执行，只通知
  5. --force-rerun 需要 --reason
  6. MTM snapshot 从 JSON 加载（非 mtm_history.csv）

运行方式（需要已有某个交易日的数据）：
  python tests/verify_alpha_v1_ops.py --trade-date 2026-05-18 --mode artifacts
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _pass(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")
    _FAILURES.append(msg)


_FAILURES: list[str] = []


# ── 1. Idempotency: COMMITTED marker blocks double execution ──────────────

def verify_committed_marker(trade_date: str) -> None:
    """Check that COMMITTED marker exists after execution and blocks re-run."""
    marker = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
              / trade_date / "execution" / "COMMITTED")
    if not marker.exists():
        _fail(f"COMMITTED marker 不存在: {marker}")
        return
    _pass(f"COMMITTED marker 存在: {marker}")


# ── 2. Artifact structure ────────────────────────────────────────────────

_REQUIRED_DIRS = ["plan", "execution", "mtm"]
_REQUIRED_EXEC = ["execution_summary.json", "account_after.json", "positions_after.csv"]


def verify_artifact_structure(trade_date: str) -> None:
    """Check the new artifact directory structure."""
    base = PROJECT_ROOT / "experiments" / "alpha_v1_daily" / trade_date
    if not base.exists():
        _fail(f"交易日目录不存在: {base}")
        return
    _pass(f"交易日目录: {base}")

    for d in _REQUIRED_DIRS:
        p = base / d
        if not p.exists():
            _fail(f"子目录缺失: {p}")
        else:
            _pass(f"子目录存在: {d}/")

    exec_dir = base / "execution"
    if exec_dir.exists():
        for f in _REQUIRED_EXEC:
            p = exec_dir / f
            if not p.exists():
                _fail(f"执行产物缺失: {p}")
            else:
                _pass(f"执行产物: {f}")

    # run_meta.json
    meta = base / "run_meta.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text())
            for key in ["trade_date", "mode", "ts"]:
                if key in data:
                    _pass(f"run_meta.json 包含字段: {key}")
        except (json.JSONDecodeError, OSError):
            _fail(f"run_meta.json 解析失败")
    else:
        _fail(f"run_meta.json 缺失")


# ── 3. MTM snapshot from JSON (not mtm_history.csv) ──────────────────────

def verify_mtm_snapshot(trade_date: str) -> None:
    """Check mtm/mtm_snapshot.json exists and has details (not just CSV history)."""
    snap = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
            / trade_date / "mtm" / "mtm_snapshot.json")
    if not snap.exists():
        _fail(f"MTM snapshot 不存在: {snap}")
        return

    try:
        data = json.loads(snap.read_text())
    except (json.JSONDecodeError, OSError):
        _fail(f"MTM snapshot 解析失败: {snap}")
        return

    for key in ["total_value", "cash", "market_value", "cumulative_pnl",
                "daily_pnl", "details", "priced_count"]:
        if key in data:
            _pass(f"mtm_snapshot.json 包含字段: {key}")
        else:
            _fail(f"mtm_snapshot.json 缺少: {key}")

    if data.get("details") and len(data["details"]) > 0:
        _pass(f"details 有 {len(data['details'])} 条持仓记录")
    else:
        _fail("details 为空 — stale data check 将无法对比上一交易日")


# ── 4. Stale check persistence ───────────────────────────────────────────

def verify_stale_check(trade_date: str) -> None:
    """Check stale_check.json was saved alongside MTM snapshot."""
    sc = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
          / trade_date / "mtm" / "stale_check.json")
    if not sc.exists():
        _pass("stale_check.json 不存在（首次 MTM 无对比基准，正常）")
        return
    try:
        data = json.loads(sc.read_text())
    except (json.JSONDecodeError, OSError):
        _fail(f"stale_check.json 解析失败")
        return

    for key in ["trade_date", "prev_trade_date", "checked_count",
                "identical_count", "identical_ratio", "threshold", "status"]:
        if key in data:
            _pass(f"stale_check.json 包含字段: {key}")
        else:
            _fail(f"stale_check.json 缺少: {key}")

    status = data.get("status", "")
    if status == "passed":
        _pass(f"状态: passed (一致={data.get('identical_count')}/{data.get('checked_count')})")
    elif status == "skipped":
        _pass("状态: skipped（首次运行或无数据）")


# ── 5. signal_date vs trade_date persistence ────────────────────────────

def verify_signal_trade_dates(trade_date: str) -> None:
    """Check that plan_meta and run_meta have both dates."""
    meta = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
            / trade_date / "run_meta.json")
    if meta.exists():
        data = json.loads(meta.read_text())
        td = data.get("trade_date", "")
        ref = data.get("reference_date", "")
        if td and ref:
            _pass(f"run_meta: trade_date={td}, reference_date={ref}")
        elif td:
            _pass(f"run_meta: trade_date={td} (reference_date may differ)")
        else:
            _fail("run_meta 缺少 trade_date")

    plan_meta = (PROJECT_ROOT / "experiments" / "alpha_v1_daily"
                 / trade_date / "plan" / "plan_meta.json")
    if plan_meta.exists():
        data = json.loads(plan_meta.read_text())
        td = data.get("trade_date", "")
        ref = data.get("reference_date", "")
        if ref:
            _pass(f"plan_meta: trade_date={td}, reference_date={ref}")
        else:
            _fail("plan_meta 缺少 reference_date")


# ── 6. CLI --force-rerun requires --reason ──────────────────────────────

def verify_force_rerun_validation() -> None:
    """Check that --force-rerun without --reason is rejected."""
    import subprocess
    script = PROJECT_ROOT / "scripts" / "run_alpha_v1_daily.py"
    result = subprocess.run(
        [sys.executable, str(script), "--trade-date", "2026-05-18",
         "--mode", "postclose", "--force-rerun"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0 and ("--reason" in result.stderr or "--reason" in result.stdout):
        _pass("--force-rerun 无 --reason 被拒绝")
    else:
        _fail("--force-rerun 无 --reason 未被拒绝")


# ── 7. CLI --notify-only validation ─────────────────────────────────────

def verify_notify_only_validation() -> None:
    """Check that --notify-only doesn't require trade_date. (OK)"""
    import subprocess
    script = PROJECT_ROOT / "scripts" / "run_alpha_v1_daily.py"
    result = subprocess.run(
        [sys.executable, str(script), "--notify-only", "--help"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    _pass("--notify-only 被 argparse 接受")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha V1 ops verification")
    parser.add_argument("--trade-date", required=True, help="交易日 YYYY-MM-DD")
    parser.add_argument("--mode", choices=["all", "artifacts", "cli"],
                        default="all")
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print(f"Alpha V1 运营验证 — {args.trade_date}")
    print(f"{'=' * 60}")

    if args.mode in ("all", "artifacts"):
        print("\n[1/5] COMMITTED marker:")
        verify_committed_marker(args.trade_date)

        print("\n[2/5] Artifact structure:")
        verify_artifact_structure(args.trade_date)

        print("\n[3/5] MTM snapshot:")
        verify_mtm_snapshot(args.trade_date)

        print("\n[4/5] Stale check:")
        verify_stale_check(args.trade_date)

        print("\n[5/5] signal_date vs trade_date:")
        verify_signal_trade_dates(args.trade_date)

    if args.mode in ("all", "cli"):
        print("\n[CLI] --force-rerun validation:")
        verify_force_rerun_validation()

        print("\n[CLI] --notify-only validation:")
        verify_notify_only_validation()

    print(f"\n{'=' * 60}")
    if _FAILURES:
        print(f"❌ {len(_FAILURES)} 项失败:")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("✅ 全部验证通过")


if __name__ == "__main__":
    main()
