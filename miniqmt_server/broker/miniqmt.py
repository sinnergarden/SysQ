from __future__ import annotations

from miniqmt_server.broker.base import BrokerAdapter
from miniqmt_server.models import CancelRequest, OrderRequest


class MiniQMTBrokerAdapter(BrokerAdapter):
    """Placeholder adapter for the future Windows-native MiniQMT connection.

    This shell documents the server-side contract without pretending the real
    broker bridge already works. The production implementation should wire these
    methods to MiniQMT login state, account queries, order submission and
    cancel/readback callbacks on a Windows host with MiniQMT installed.
    """

    def get_health(self) -> dict:
        return {
            "status": "degraded",
            "broker_mode": "miniqmt",
            "miniqmt_connected": False,
            "account_query_ready": False,
            "submit_enabled": False,
            "server_version": "adapter-shell",
            "trade_date": "",
            "account_id": "",
            "last_sync_time": "",
            "error": {
                "code": "adapter_not_implemented",
                "message": "real MiniQMT adapter still needs Windows-side wiring",
            },
        }

    def get_account(self) -> dict:
        raise NotImplementedError("MiniQMT account query is not implemented yet")

    def get_positions(self) -> list[dict]:
        raise NotImplementedError("MiniQMT position query is not implemented yet")

    def list_orders(self, filters: dict[str, str]) -> list[dict]:
        raise NotImplementedError("MiniQMT order query is not implemented yet")

    def list_trades(self, filters: dict[str, str]) -> list[dict]:
        raise NotImplementedError("MiniQMT trade query is not implemented yet")

    def validate_orders(self, request: OrderRequest) -> dict:
        raise NotImplementedError("MiniQMT static validation must be implemented with broker rules")

    def submit_orders(self, request: OrderRequest) -> dict:
        raise NotImplementedError("MiniQMT submit is not implemented yet")

    def cancel_orders(self, request: CancelRequest) -> dict:
        raise NotImplementedError("MiniQMT cancel is not implemented yet")

    def get_latest_snapshot(self) -> dict:
        raise NotImplementedError("MiniQMT snapshot readback is not implemented yet")
