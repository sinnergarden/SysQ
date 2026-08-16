#!/usr/bin/env python3
"""A0-A5 execution-policy ablation launcher.

Causal ablation of the four posterior exit rules against a fixed
equal_weight entry + hold-drift skeleton (rank_exit disabled).

Each run uses IDENTICAL signal / universe / window / cost / execution /
rebalance / top_n / weighting / initial-entry.  ONLY exit rules differ.

Disabled rules use "never-trigger" dummy thresholds so the engine's own
validate() passes without code changes:
  hard_stop            -> posterior_stop_loss = 0.999  (needs -99.9%)
  score_delta          -> min_observations = 1e9       (threshold stays None)
  winner_trailing      -> activation_return = 0.9999   (needs +9999%)
  stale_replacement    -> stale_after_days = 10000     (never reached)

A5 uses the original run2 values (repro check).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # SysQ-execution-ledger
RESEARCH_ROOT = Path("/home/liuming/.openclaw/workspace/SysQ/data/research")
CLI = REPO / "scripts" / "research" / "backtest_from_signal.py"

SIGNAL_ID = "financial_rc_60d_180d_50_50__daily_zscore"
SIGNAL_RUN_ID = "blend__007a93600f45de00"
START = "2021-01-04"
END = "2026-07-31"

BASE = {
    "signal_id": SIGNAL_ID,
    "signal_run_id": SIGNAL_RUN_ID,
    "research_root": str(RESEARCH_ROOT),
    "score_column": "score",
    "top_n": "5",
    "commission": "0.0003",
    "stamp_duty": "0.001",
    "min_commission": "5.0",
    "slippage": "0.001",
    "rebalance_freq": "weekly",
    "holding_policy": "posterior_confirmed",
    "strategy_template_id": "posterior_confirmed_top5_financial_rc_50_50_v1",
    "start_date": START,
    "end_date": END,
    "initial_capital": "10000000",
    "overwrite": True,
}

# Never-trigger dummy values (documented above).
# winner_trailing is disabled via winner_trailing_stop=0.999 (exit needs a
# -99.9% drawdown from peak -> mathematically impossible), NOT via the
# activation threshold: validate() forces activation_return < 1.0, and genuine
# A-share multi-baggers (e.g. 300487 44.69->peak>89) exceed any activation
# threshold below 100%.  Activation with a dead trailing-stop is harmless.
NEVER = {
    "score_delta_min_observations": "1000000000",
    "posterior_stop_loss": "0.999",
    "winner_activation_return": "0.9999",
    "winner_trailing_stop": "0.999",
    "stale_after_days": "10000",
    "replacement_rank_gap": "1000000",
}

# Original run2 values (A5 reproduce).
ORIG = {
    "score_delta_lookback": "20",
    "score_delta_quantile": "0.10",
    "score_delta_history_days": "504",
    "score_delta_min_observations": "500",
    "posterior_stop_loss": "0.09",
    "winner_activation_return": "0.20",
    "winner_trailing_stop": "0.125",
    "stale_after_days": "20",
    "stale_max_return": "0.03",
    "replacement_rank_gap": "20",
}

# Each run: (name, {param: value}) overlay on BASE.
RUNS = {
    "A0_none": NEVER,
    "A1_hard_stop": {
        "posterior_stop_loss": "0.09",
        "score_delta_min_observations": NEVER["score_delta_min_observations"],
        "winner_activation_return": NEVER["winner_activation_return"],
        "winner_trailing_stop": NEVER["winner_trailing_stop"],
        "stale_after_days": NEVER["stale_after_days"],
        "replacement_rank_gap": NEVER["replacement_rank_gap"],
    },
    "A2_score_delta": {
        "score_delta_lookback": "20",
        "score_delta_quantile": "0.10",
        "score_delta_history_days": "504",
        "score_delta_min_observations": "500",
        "posterior_stop_loss": NEVER["posterior_stop_loss"],
        "winner_activation_return": NEVER["winner_activation_return"],
        "winner_trailing_stop": NEVER["winner_trailing_stop"],
        "stale_after_days": NEVER["stale_after_days"],
        "replacement_rank_gap": NEVER["replacement_rank_gap"],
    },
    "A3_winner_trailing": {
        "winner_activation_return": "0.20",
        "winner_trailing_stop": "0.125",
        "score_delta_min_observations": NEVER["score_delta_min_observations"],
        "posterior_stop_loss": NEVER["posterior_stop_loss"],
        "stale_after_days": NEVER["stale_after_days"],
        "replacement_rank_gap": NEVER["replacement_rank_gap"],
    },
    "A4_stale": {
        "stale_after_days": "20",
        "stale_max_return": "0.03",
        "replacement_rank_gap": "20",
        "score_delta_min_observations": NEVER["score_delta_min_observations"],
        "posterior_stop_loss": NEVER["posterior_stop_loss"],
        "winner_activation_return": NEVER["winner_activation_return"],
        "winner_trailing_stop": NEVER["winner_trailing_stop"],
    },
    "A5_all": ORIG,
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


def _finish_one(
    name: str, proc: subprocess.Popen, manifest: dict, out_root: Path
) -> None:
    log_path = out_root / f"{name}.log"
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    tail = text.strip().splitlines()[-12:]
    print(f"\n=== {name} (exit {proc.returncode}) ===")
    print("\n".join(tail))
    if proc.returncode != 0:
        print("STDERR tail:", text.strip().splitlines()[-8:])
        manifest[name] = {"status": "failed", "log": str(log_path)}
    else:
        try:
            last = text.strip().splitlines()[-1]
            res = json.loads(last)
            manifest[name] = {"status": "completed", **res}
        except Exception:
            manifest[name] = {"status": "completed", "log": str(log_path)}
    (out_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False)
    )


def main() -> int:
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="all", help="all, or comma-separated run names e.g. A1_hard_stop,A2_score_delta")
    ap.add_argument("--parallel", type=int, default=1, help="max concurrent backtests")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_root = REPO / "data" / "research" / "ablation" / "execution_policy"
    out_root.mkdir(parents=True, exist_ok=True)

    selected = list(RUNS) if args.run == "all" else [s.strip() for s in args.run.split(",") if s.strip()]
    spec_path = out_root / "run_spec.json"
    if args.dry_run:
        spec = {name: build_cmd(name, ov, out_root / name) for name, ov in RUNS.items()}
        spec_path.write_text(json.dumps(spec, indent=1))
        print(f"dry-run spec -> {spec_path}")
        print(json.dumps({k: v for k, v in spec.items()}, indent=1)[:3000])
        return 0

    manifest = {}
    running: dict[subprocess.Popen, str] = {}
    max_par = max(1, min(args.parallel, len(selected)))
    done = 0
    idx = 0

    while idx < len(selected) or running:
        while idx < len(selected) and len(running) < max_par:
            name = selected[idx]
            overlay = RUNS[name]
            out = out_root / name
            cmd = build_cmd(name, overlay, out)
            idx += 1
            print(f"[start] {name} (slot {len(running)+1}/{max_par})", flush=True)
            logf = open(out_root / f"{name}.log", "w")
            p = subprocess.Popen(
                cmd, cwd=REPO, stdout=logf, stderr=subprocess.STDOUT, text=True
            )
            p._logf = logf  # type: ignore[attr-defined]
            running[p] = name

        for p in list(running):
            if p.poll() is not None:
                p._logf.close()  # type: ignore[attr-defined]
                name = running.pop(p)
                _finish_one(name, p, manifest, out_root)
                done += 1
        if running:
            time.sleep(2)

    print(f"\nall done: {done}/{len(selected)} runs", flush=True)
    print(f"manifest -> {out_root / 'run_manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
