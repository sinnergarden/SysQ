from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from miniqmt_server.models import CancelRequest, OrderRequest


class BrokerAdapter(ABC):
    @abstractmethod
    def get_health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_orders(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_trades(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def validate_orders(self, request: OrderRequest) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_orders(self, request: OrderRequest) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_orders(self, request: CancelRequest) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError
