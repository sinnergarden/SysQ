#!/usr/bin/env python3
"""Demo: cross-signal IC matrix via DuckDB analytics layer.

Usage
-----
    python scripts/research/demo_signal_analytics.py

Requires existing signal + label artifacts under ``data/research/``.
"""

from __future__ import annotations

from qsys.research.signal_analytics import SignalAnalytics


def main() -> None:
    with SignalAnalytics() as sa:
        # 1. Discover available signals and labels
        sigs = sa.list_signals()
        lbls = sa.list_labels()

        print(f"=== Signals ({len(sigs)}) ===")
        print(sigs.to_string(index=False))

        print(f"\n=== Labels ({len(lbls)}) ===")
        print(lbls.to_string(index=False))

        if sigs.empty or lbls.empty:
            print("No signal or label data found. Run a research pipeline first.")
            return

        # 2. IC matrix (Pearson)
        ic = sa.compute_ic_matrix()
        print(f"\n=== IC Matrix ({len(ic)} pairs) ===")
        print(ic.to_string(index=False))

        # 3. Rank IC matrix (Spearman)
        rank_ic = sa.compute_rank_ic_matrix()
        print(f"\n=== Rank IC Matrix ({len(rank_ic)} pairs) ===")
        print(rank_ic.to_string(index=False))

        # 4. Pivot view
        if not ic.empty:
            pivot = ic.pivot_table(
                index="signal_id", columns="label_id",
                values="ic_mean", aggfunc="first",
            )
            print(f"\n=== IC Pivot (signal × label) ===")
            print(pivot.to_string())

        # 5. Daily IC series for first pair
        if not sigs.empty and not lbls.empty:
            first_sig = sigs["signal_id"].iloc[0]
            first_run = sigs["signal_run_id"].iloc[0]
            first_lbl = lbls["label_id"].iloc[0]
            daily = sa.daily_ic(first_sig, first_run, first_lbl)
            print(f"\n=== Daily IC: {first_sig}/{first_run} × {first_lbl} ===")
            print(f"  {len(daily)} trading days")
            print(f"  Mean IC: {daily['ic'].mean():.4f}")
            print(f"  ICIR:    {daily['ic'].mean() / daily['ic'].std():.4f}")


if __name__ == "__main__":
    main()
