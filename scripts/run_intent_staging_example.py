from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.strategy.portfolio import build_portfolio_intent, save_reason_codes, save_target_weights
from qsys.trader.staging import save_orders, save_staging_reason_codes, stage_orders


def build_example_inputs() -> tuple[pd.DataFrame, dict, pd.DataFrame, dict]:
    raw_scores = pd.DataFrame(
        [
            {"ts_code": "600000.SH", "score": 0.92, "industry": "Bank"},
            {"ts_code": "000001.SZ", "score": 0.88, "industry": "Bank"},
            {"ts_code": "600519.SH", "score": 0.79, "industry": "Consumer"},
            {"ts_code": "300750.SZ", "score": 0.74, "industry": "Battery"},
        ]
    )
    broker_snapshot = {
        "account_snapshot": {
            "account_name": "real",
            "cash": 15000.0,
            "available_cash": 15000.0,
            "total_assets": 50000.0,
        },
        "positions": [
            {"symbol": "600000.SH", "total_amount": 300, "sellable_amount": 300},
            {"symbol": "300750.SZ", "total_amount": 200, "sellable_amount": 200},
        ],
    }
    market_data = pd.DataFrame(
        [
            {"ts_code": "600000.SH", "latest_price": 10.0, "limit_up_price": 11.0, "limit_down_price": 9.0},
            {"ts_code": "000001.SZ", "latest_price": 12.0, "limit_up_price": 13.2, "limit_down_price": 10.8},
            {"ts_code": "600519.SH", "latest_price": 1500.0, "limit_up_price": 1650.0, "limit_down_price": 1350.0},
            {"ts_code": "300750.SZ", "latest_price": 200.0, "limit_up_price": 220.0, "limit_down_price": 180.0},
        ]
    )
    risk_rules = {
        "blacklist": ["300750.SZ"],
        "max_positions": 3,
        "max_industry_weight": 0.45,
    }
    return raw_scores, broker_snapshot, market_data, risk_rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the minimal portfolio -> staging example")
    parser.add_argument(
        "--output-dir",
        default=str(project_root / "runs" / "examples" / "intent_staging"),
        help="Directory for target_weights.csv and orders.csv",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_scores, broker_snapshot, market_data, risk_rules = build_example_inputs()
    portfolio_result = build_portfolio_intent(raw_scores, broker_snapshot=broker_snapshot, risk_rules=risk_rules)
    staging_result = stage_orders(
        portfolio_result.target_weights,
        broker_snapshot=broker_snapshot,
        market_data=market_data,
    )

    target_weights_path = save_target_weights(portfolio_result.target_weights, output_dir / "target_weights.csv")
    portfolio_reasons_path = save_reason_codes(portfolio_result.reason_codes, output_dir / "reason_codes.json")
    orders_path = save_orders(staging_result.orders, output_dir / "orders.csv")
    staging_reasons_path = save_staging_reason_codes(staging_result.reason_codes, output_dir / "staging_reason_codes.json")

    print(f"target_weights: {target_weights_path}")
    print(portfolio_result.target_weights.to_string(index=False))
    print(f"portfolio_reason_codes: {portfolio_reasons_path}")
    print(f"orders: {orders_path}")
    print(staging_result.orders.to_string(index=False))
    print(f"staging_reason_codes: {staging_reasons_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
