#!/usr/bin/env python3
"""Portfolio backtests for the raw-ranking phase-robustness study (Sec 1 + Sec 5).

Runs (all: Top5, equal-weight entry + hold drift, rank_exit on, dead exit
rules, 20d cadence at the phase's grid offset — retrain-triggered rebalance):

  RR_P0_capped  = existing production signal (score = zscore(clip(zscore(raw))))
                  rebalance_offset 0  — the Sec 1 regression baseline
  RR_P0_raw     = raw-ranking P0 (score = zscore(raw), no cap), offset 0
  RR_P5_raw     = raw-ranking P5 (schedule +5 td), offset 5
  RR_P10_raw    = raw-ranking P10 (schedule +10 td), offset 10
  RR_P15_raw    = raw-ranking P15 (schedule +15 td), offset 15

P0_capped uses the STORED S180 run (ba710797 already exists as S180_20d with
the identical config); this script re-runs it fresh so all five share one
pipeline + manifest.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = Path("/home/liuming/.openclaw/workspace/SysQ/data/research")
CLI = REPO / "scripts" / "research" / "backtest_from_signal.py"

S180_SIGNAL_ID = "fwd_ret_180d_raw__daily_zscore"
S180_SIGNAL_RUN_ID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3__"
    "v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__"
    "2021-01-01_2026-07-31"
)
EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3"
START, END = "2021-01-04", "2026-07-31"

NEVER = {
    "score_delta_min_observations": "1000000000",
    "posterior_stop_loss": "0.999",
    "winner_activation_return": "0.9999",
    "winner_trailing_stop": "0.999",
    "stale_after_days": "10000",
    "replacement_rank_gap": "1000000",
}

BASE = {
    "signal_id": S180_SIGNAL_ID,
    "signal_run_id": S180_SIGNAL_RUN_ID,
    "research_root": str(RESEARCH_ROOT),
    "score_column": "score",
    "top_n": "5",
    "commission": "0.0003",
    "stamp_duty": "0.001",
    "min_commission": "5.0",
    "slippage": "0.001",
    "rebalance_freq": "20d",
    "rebalance_offset": "0",
    "holding_policy": "posterior_confirmed",
    "strategy_template_id": "posterior_confirmed_top5_financial_rc_50_50_v1",
    "start_date": START,
    "end_date": END,
    "initial_capital": "10000000",
    "overwrite": True,
    **NEVER,
    "rank_exit": True,
}


def _raw_run_id(phase: str) -> str:
    return f"{S180_SIGNAL_ID}__rr_{phase}__rawrank__{EXPERIMENT}"


RUNS = {
    "RR_P0_capped": {"signal_run_id": S180_SIGNAL_RUN_ID, "rebalance_offset": "0"},
    "RR_P0_raw": {"signal_run_id": _raw_run_id("p0"), "rebalance_offset": "0"},
    "RR_P5_raw": {"signal_run_id": _raw_run_id("p5"), "rebalance_offset": "5"},
    "RR_P10_raw": {"signal_run_id": _raw_run_id("p10"), "rebalance_offset": "10"},
    "RR_P15_raw": {"signal_run_id": _raw_run_id("p15"), "rebalance_offset": "15"},
}


def build_cmd(name: str, overlay: dict, output_dir: Path) -> list[str]:
    params = dict(BASE)
    params.update(overlay)
    cmd = [sys.executable, str(CLI)]
    for k, v in params.items():
        flag = f"--{k.replace('_', '-')}"
        if isinstance(v, bool):
            if v:
                cmd.append(flag)
            continue
        cmd.append(flag)
        cmd.append(str(v))
    cmd.extend(["--output-dir", str(output_dir)])
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="all")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_root = REPO / "data" / "research" / "ablation" / "execution_policy"
    out_root.mkdir(parents=True, exist_ok=True)
    selected = list(RUNS) if args.run == "all" else [
        s.strip() for s in args.run.split(",") if s.strip()]

    if args.dry_run:
        for name in selected:
            print(" ".join(build_cmd(name, RUNS[name], out_root / name)))
        return 0

    manifest = {}
    running: dict[subprocess.Popen, str] = {}
    max_par = max(1, min(args.parallel, len(selected)))
    done, idx = 0, 0
    while idx < len(selected) or running:
        while idx < len(selected) and len(running) < max_par:
            name = selected[idx]
            out = out_root / name
            cmd = build_cmd(name, RUNS[name], out)
            idx += 1
            print(f"[start] {name}", flush=True)
            logf = open(out_root / f"{name}.log", "w")
            p = subprocess.Popen(cmd, cwd=REPO, stdout=logf,
                                 stderr=subprocess.STDOUT, text=True)
            p._logf = logf  # type: ignore[attr-defined]
            running[p] = name
        for p in list(running):
            if p.poll() is not None:
                p._logf.close()  # type: ignore[attr-defined]
                name = running.pop(p)
                log_path = out_root / f"{name}.log"
                text = log_path.read_text(errors="replace") if log_path.exists() else ""
                tail = text.strip().splitlines()[-8:]
                print(f"=== {name} (exit {p.returncode}) ===")
                print("\n".join(tail))
                if p.returncode != 0:
                    manifest[name] = {"status": "failed"}
                else:
                    manifest[name] = {"status": "completed"}
                done += 1
        if running:
            time.sleep(2)
    (out_root / "rr_run_manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"\nall done: {done}/{len(selected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
