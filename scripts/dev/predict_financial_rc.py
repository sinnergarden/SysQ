#!/usr/bin/env python3
"""Daily predict for financial_rc — UC-10A standalone entry.

Usage:
    python scripts/dev/predict_financial_rc.py --trade-date 2026-06-26 --top-k 5
    → outputs/2026-06-26/candidates.json

Training must be done via UC-4 / daily_retrain / weekly_retrain first.
This script only serves. No training.
"""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dev.financial_rc.adapter import FinancialRCAdapter

parser = argparse.ArgumentParser(description="UC-10A: daily candidate export")
parser.add_argument("--trade-date", required=True)
parser.add_argument("--top-k", type=int, default=5)
parser.add_argument("--weight-60d", type=float, default=0.3)
parser.add_argument("--weight-180d", type=float, default=0.7)
parser.add_argument("--output-root", default="outputs")
parser.add_argument("--force", action="store_true")
args = parser.parse_args()

adapter = FinancialRCAdapter()
adapter.load_model(args.trade_date)
result = adapter.predict(args.trade_date, top_k=args.top_k,
                          w60=args.weight_60d, w180=args.weight_180d)

out = Path(args.output_root) / args.trade_date / "candidates.json"
if out.exists() and not args.force:
    raise FileExistsError(f"{out} exists. Use --force to overwrite.")
out.parent.mkdir(parents=True, exist_ok=True)
tmp = out.with_suffix(".tmp.json")
tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, out)
print(f"-> {out}")
for c in result["candidates"][:5]:
    print(f"  {c['rank']}. {c['ts_code']} {c.get('name','')[:8]:<8s} score={c['ranking_score']:.4f}")
