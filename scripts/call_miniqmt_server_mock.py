from __future__ import annotations

import argparse
import json
from typing import Any
from urllib import request


DEFAULT_PAYLOAD = {
    "request_id": "demo-request-001",
    "strategy_id": "demo_strategy",
    "trade_date": "2026-04-06",
    "account_id": "mock_account",
    "dry_run": False,
    "orders": [
        {
            "intent_id": "demo-intent-001",
            "symbol": "600000.SH",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "limit_price": 12.34,
            "time_in_force": "DAY",
            "reason": "demo_rebalance",
            "target_weight": 0.01,
            "notes": "sent from scripts/call_miniqmt_server_mock.py",
        }
    ],
}



def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(http_request) as response:
        return json.loads(response.read().decode("utf-8"))



def main() -> None:
    parser = argparse.ArgumentParser(description="Call the local MiniQMT mock server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8811")
    args = parser.parse_args()

    validate_result = post_json(f"{args.base_url}/orders/validate", DEFAULT_PAYLOAD)
    submit_result = post_json(f"{args.base_url}/orders/submit", DEFAULT_PAYLOAD)
    print("validate:")
    print(json.dumps(validate_result, indent=2, ensure_ascii=True, sort_keys=True))
    print("submit:")
    print(json.dumps(submit_result, indent=2, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
