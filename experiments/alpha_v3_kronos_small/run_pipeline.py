#!/usr/bin/env python3
"""Alpha V3 Kronos-small — Zero-Shot Signal Spike Experiment Pipeline.

Usage
-----
# Smoke test (synthetic fallback, CSI300, 3 months):
python experiments/alpha_v3_kronos_small/run_pipeline.py \\
    --smoke --universe csi300 --start-date 2024-07-01 --end-date 2024-09-30 \\
    --allow-synthetic

# Full run (CSI800, synthetic fallback):
python experiments/alpha_v3_kronos_small/run_pipeline.py --allow-synthetic

# Full run with Kronos model (if available):
python experiments/alpha_v3_kronos_small/run_pipeline.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from qsys.config.loader import load_yaml_config


def setup_environment(args) -> dict:
    """Step 0: Check GPU, model availability, determine pipeline mode."""
    print("=" * 70)
    print("Alpha V3 Kronos-small — Pipeline")
    print("=" * 70)
    print()

    status = {
        "gpu_available": False,
        "gpu_name": None,
        "kronos_available": False,
        "pipeline_mode": "synthetic",
    }

    # GPU check
    try:
        import torch
        if torch.cuda.is_available():
            status["gpu_available"] = True
            status["gpu_name"] = torch.cuda.get_device_name(0)
            print(f"[GPU] {torch.cuda.get_device_name(0)} (CUDA {torch.version.cuda})")
        else:
            print("[GPU] CUDA not available, using CPU")
    except ImportError:
        print("[GPU] torch not installed")

    # Kronos model check
    from experiments.alpha_v3_kronos_small.lib.kronos_inference import check_model_available
    status["kronos_available"] = check_model_available()

    if status["kronos_available"]:
        status["pipeline_mode"] = "kronos"
        print("[Model] Kronos-small available — using real inference")
    elif args.allow_synthetic:
        status["pipeline_mode"] = "synthetic"
        print("[Model] Kronos-small not found, using SYNTHETIC fallback")
    else:
        print("[Model] Kronos-small not found and --allow-synthetic not set")
        print("  Pass --allow-synthetic to use synthetic fallback signals.")
        print("  Or download the model: pip install huggingface_hub &&")
        print("  python -c \"from transformers import AutoModel; AutoModel.from_pretrained('NeoQuasar/Kronos-small')\"")
        sys.exit(1)

    return status


def step_load_data(config: dict, args) -> pd.DataFrame:
    """Step 1: Load fq OHLCV data."""
    from experiments.alpha_v3_kronos_small.lib.data import load_fq_ohlcv

    universe = args.universe or config.get("data", {}).get("universe", "csi800")
    start = args.start_date or config.get("data", {}).get("start_date", "2024-07-01")
    end = args.end_date or config.get("data", {}).get("end_date")
    lookback = config.get("data", {}).get("lookback", 90)

    # Extend start date to include lookback history (~4.5 mo for 90 trading days)
    extended_start = (
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=int(lookback * 2.5))
    ).strftime("%Y-%m-%d")

    print(f"\n{'='*70}")
    print(f"Step 1: Load Data ({universe}, {start} ~ {end or 'today'})")
    print(f"(extended to {extended_start} for lookback={lookback})")
    print(f"{'='*70}")

    df = load_fq_ohlcv(universe=universe, start_date=extended_start, end_date=end)
    return df


def step_generate_signals(
    ohlcv_df: pd.DataFrame, env: dict, config: dict, args, output_dir: Path,
) -> pd.DataFrame:
    """Step 2: Generate signals (Kronos inference or synthetic fallback)."""
    print(f"\n{'='*70}")
    print("Step 2: Generate Signals")
    print(f"{'='*70}")

    if env["pipeline_mode"] == "kronos":
        return _generate_kronos_signals(ohlcv_df, config, args, output_dir)
    else:
        return _generate_synthetic_signals(ohlcv_df, config, args, output_dir)


def _generate_kronos_signals(ohlcv_df, config, args, output_dir):
    """Generate signals using real Kronos-small inference."""
    from experiments.alpha_v3_kronos_small.lib.kronos_inference import (
        load_model, run_inference, save_raw_predictions, load_raw_predictions,
    )
    from experiments.alpha_v3_kronos_small.lib.signal_builder import (
        build_signals, save_signal_artifact,
    )

    # ── Skip inference when cached ──
    raw_path = output_dir / "raw_predictions.parquet"
    if args.skip_inference and raw_path.exists():
        print(f"  [Cached] Loading raw predictions from {raw_path}")
        raw_preds = load_raw_predictions(raw_path)
        signals = build_signals(raw_preds, ohlcv_df)
        save_signal_artifact(signals, output_dir / "signals")
        return signals

    lookback = config.get("data", {}).get("lookback", 90)
    pred_len = config.get("prediction", {}).get("pred_len", 5)
    device = config.get("model", {}).get("device", "cuda")
    rebalance_freq = config.get("backtest", {}).get("portfolio", {}).get("rebalance_freq", "weekly")

    # Only predict on rebalance dates — huge speedup
    from qsys.backtest import get_rebalance_dates
    all_dates = sorted(ohlcv_df["trade_date"].unique())
    rebal_dates = get_rebalance_dates(all_dates, rebalance_freq)
    target_dates = sorted(str(d)[:10] for d in rebal_dates)

    # Load model (returns predictor, tokenizer, status)
    predictor, tokenizer, status = load_model(device=device)
    if predictor is None:
        print(f"  [ERROR] Model loading failed: {status.get('reason', 'unknown')}")
        print("  Falling back to synthetic signals...")
        return _generate_synthetic_signals(ohlcv_df, config, args, output_dir)

    # Run inference — only on rebalance dates
    raw_preds = run_inference(
        ohlcv_df, predictor, lookback=lookback, pred_len=pred_len,
        target_dates=target_dates,
    )

    # Save raw predictions
    raw_path = output_dir / "raw_predictions.parquet"
    save_raw_predictions(raw_preds, raw_path)

    # Build signal artifact
    signals = build_signals(raw_preds, ohlcv_df)
    save_signal_artifact(signals, output_dir / "signals")
    return signals


def _generate_synthetic_signals(ohlcv_df, config, args, output_dir):
    """Generate synthetic fallback signals."""
    from experiments.alpha_v3_kronos_small.lib.synthetic import (
        generate_signals, save_synthetic_artifact,
    )

    lookback = config.get("data", {}).get("lookback", 90)
    start_date = args.start_date or config.get("data", {}).get("start_date", "2024-07-01")

    signals = generate_signals(ohlcv_df, start_date=start_date, lookback=lookback)
    save_synthetic_artifact(signals, output_dir, run_id=f"alpha_v3_synthetic_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    return signals


def step_backtest(
    ohlcv_df: pd.DataFrame, signals_df: pd.DataFrame, config: dict, args, output_dir: Path,
) -> dict[str, dict]:
    """Step 3: Run backtests for each score column and alpha_v1 baseline."""
    from experiments.alpha_v3_kronos_small.lib.backtest_runner import run_backtest

    score_cols = config.get("signals", {}).get("score_cols", ["kronos_ret_5d_zscore"])

    results: dict[str, dict] = {}

    # Kronos backtests
    for score_col in score_cols:
        label = f"kronos_{score_col}"
        result = run_backtest(ohlcv_df, signals_df, config, score_col, output_dir, label=label)
        results[result["label"]] = result

    return results


def step_evaluate(signals_df: pd.DataFrame, ohlcv_df: pd.DataFrame, output_dir: Path) -> dict:
    """Step 4: Signal layer evaluation (IC, RankIC, ICIR, group returns)."""
    from experiments.alpha_v3_kronos_small.lib.signal_builder import evaluate_signals

    print(f"\n{'='*70}")
    print("Step 4: Signal Layer Evaluation")
    print(f"{'='*70}")

    eval_results = evaluate_signals(signals_df, ohlcv_df)

    ev_dir = output_dir / "evaluation"
    ev_dir.mkdir(parents=True, exist_ok=True)

    # Save daily IC
    ic_daily = eval_results.get("ic_daily")
    if ic_daily is not None and not ic_daily.empty:
        ic_daily.to_csv(ev_dir / "ic_daily.csv", index=False)
        print(f"  → {ev_dir / 'ic_daily.csv'} ({len(ic_daily)} dates)")

    # Print IC summary
    summary = eval_results.get("ic_summary", {})
    if summary:
        print(f"\n{'─'*60}")
        print("  IC Summary (5d forward return)")
        print(f"{'─'*60}")
        rows = []
        for col, s in sorted(summary.items()):
            rows.append(f"  {col:45s} IC={s['ic_mean']:+.4f}  ICIR={s['icir']:+.4f}  "
                        f"RankIC={s['rankic_mean']:+.4f}  IC>0={s['ic_positive_pct']:.0f}%  "
                        f"n={s['n_dates']}")
        print("\n".join(rows))

        # Save summary
        summary_df = pd.DataFrame(summary).T
        summary_df.to_csv(ev_dir / "ic_summary.csv")
        print(f"  → {ev_dir / 'ic_summary.csv'}")

    # Save group returns
    grp = eval_results.get("group_returns", {})
    if grp:
        for col, gdf in grp.items():
            safe_name = col.replace(" ", "_")
            gdf.to_csv(ev_dir / f"group_returns_{safe_name}.csv")
            print(f"  → {ev_dir / f'group_returns_{safe_name}.csv'}")

    # Legacy json for compatibility
    serializable = {}
    for k, v in eval_results.items():
        if isinstance(v, pd.DataFrame):
            serializable[k] = f"{k}.csv"
        elif isinstance(v, dict):
            serializable[k] = {sk: str(sv) for sk, sv in v.items()}
        else:
            serializable[k] = str(v)
    with open(ev_dir / "signal_eval_results.json", "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"  → {ev_dir / 'signal_eval_results.json'}")

    return eval_results


def step_comparison(
    results: dict[str, dict], output_dir: Path,
) -> dict:
    """Step 5: Generate comparison report."""
    from experiments.alpha_v3_kronos_small.lib.comparison import build_report

    print(f"\n{'='*70}")
    print("Step 5: Comparison Report")
    print(f"{'='*70}")

    manifest = build_report(results, output_dir / "comparison")
    return manifest


def step_export_ui(results: dict[str, dict], output_dir: Path) -> None:
    """Step 6: Export UI-compatible backtest reports for each strategy."""
    from experiments.alpha_v3_kronos_small.lib.comparison import export_ui_report

    print(f"\n{'='*70}")
    print("Step 6: Export UI Reports")
    print(f"{'='*70}")

    reports_dir = output_dir.parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    for label, result in results.items():
        run_id = export_ui_report(result, label, reports_dir)
        print(f"  Exported: {label} → {run_id}")
    print(f"  Reports dir: {reports_dir}")


def save_run_manifest(env, args, config, output_dir):
    """Save pipeline run manifest."""
    manifest = {
        "run_id": f"alpha_v3_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "execution_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "pipeline",
        "pipeline_mode": env["pipeline_mode"],
        "gpu_available": env["gpu_available"],
        "gpu_name": env["gpu_name"],
        "kronos_available": env["kronos_available"],
        "args": {
            "smoke": args.smoke,
            "universe": args.universe,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "allow_synthetic": args.allow_synthetic,
        },
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    print(f"\n  → {manifest_path}")
    return manifest


def main():
    t_start = time.time()
    parser = argparse.ArgumentParser(
        description="Alpha V3 Kronos-small — Zero-Shot Signal Spike Experiment"
    )
    parser.add_argument("--smoke", action="store_true", help="Smoke test mode (short period)")
    parser.add_argument("--universe", default=None, choices=["csi300", "csi800"])
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--allow-synthetic", action="store_true",
                        help="Allow synthetic fallback when Kronos model unavailable")
    parser.add_argument("--skip-inference", action="store_true",
                        help="Skip Kronos inference; reuse cached raw_predictions.parquet")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    # Load config
    config_path = Path(__file__).parent / "config.yaml"
    config = load_yaml_config(str(config_path)) if config_path.exists() else {}

    if args.smoke:
        if args.end_date is None:
            args.end_date = config.get("pipeline", {}).get("smoke_end_date", "2024-09-30")
        if args.universe is None:
            args.universe = "csi300"

    # Resolve output dir
    base_output = Path(args.output_dir) if args.output_dir else \
        Path(__file__).parent / config.get("signals", {}).get("output_dir", "outputs")
    output_dir = Path(base_output) if isinstance(base_output, str) else base_output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 0: Environment check
    env = setup_environment(args)

    # Step 1: Load data
    ohlcv_df = step_load_data(config, args)

    # Step 2: Generate signals
    signals_df = step_generate_signals(ohlcv_df, env, config, args, output_dir)
    print(f"\n  Signals: {len(signals_df)} rows, columns={list(signals_df.columns)}")

    # Step 2b: Signal smoothing (EMA across rebalance dates)
    signals_cfg = config.get("signals", {})
    smoothing_cfg = signals_cfg.get("smoothing", {})
    if smoothing_cfg.get("enabled", False) and env["pipeline_mode"] == "kronos":
        from experiments.alpha_v3_kronos_small.lib.signal_builder import smooth_signals
        alphas = smoothing_cfg.get("alphas", [0.4])
        signals_df = smooth_signals(signals_df, alphas=alphas)

    # Step 2c: Add momentum signals
    from experiments.alpha_v3_kronos_small.lib.signal_builder import (
        add_momentum_signals, add_risk_filter_signals, add_blended_signals,
    )
    if signals_cfg.get("momentum", {}).get("enabled", True):
        mom_windows = signals_cfg.get("momentum", {}).get("windows", [5, 20])
        signals_df = add_momentum_signals(signals_df, ohlcv_df, windows=mom_windows)

    # Step 2d: Add risk-filtered signals
    rf_cfg = signals_cfg.get("risk_filter", {})
    if rf_cfg.get("enabled", False):
        signals_df = add_risk_filter_signals(
            signals_df,
            filter_pcts=rf_cfg.get("filter_pcts", [0.1]),
            base_col=rf_cfg.get("base_col", "momentum_20d_zscore"),
            risk_col=rf_cfg.get("risk_col", "kronos_ret_5d"),
        )

    # Step 2e: Add blended signals (Kronos + momentum)
    blend_cfg = signals_cfg.get("blend", {})
    if blend_cfg.get("enabled", False):
        signals_df = add_blended_signals(signals_df, blends=blend_cfg.get("blends"))

    # Step 3: Backtest
    results = step_backtest(ohlcv_df, signals_df, config, args, output_dir)

    # Step 4: Evaluation (IC, RankIC, group returns)
    eval_results = step_evaluate(signals_df, ohlcv_df, output_dir)

    # Step 5: Comparison report
    comparison = step_comparison(results, output_dir)

    # Step 6: Export UI reports
    step_export_ui(results, output_dir)

    # Save run manifest
    save_run_manifest(env, args, config, output_dir)

    total_time = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"Pipeline complete — {total_time:.0f}s")
    print(f"Output: {output_dir}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
