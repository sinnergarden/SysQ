from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from miniqmt_server import __version__


@dataclass
class MockBrokerConfig:
    account_id: str = "mock_account"
    allow_submit: bool = True
    auto_fill: bool = False
    miniqmt_connected: bool = False
    query_ready: bool = True
    submit_enabled: bool = True
    account: dict[str, Any] = field(
        default_factory=lambda: {
            "account_id": "mock_account",
            "total_assets": 100000.0,
            "available_cash": 50000.0,
            "market_value": 50000.0,
            "frozen_cash": 0.0,
            "daily_pnl": 0.0,
        }
    )
    positions: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "symbol": "600000.SH",
                "volume": 1000,
                "available_volume": 1000,
                "cost_price": 10.0,
                "market_price": 10.5,
                "market_value": 10500.0,
                "pnl": 500.0,
                "pnl_pct": 0.05,
            }
        ]
    )


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8811
    version: str = __version__
    broker_mode: str = "mock"
    data_dir: Path = Path("miniqmt_server/data")
    mock: MockBrokerConfig = field(default_factory=MockBrokerConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}



def load_config(path: str | Path | None = None) -> ServerConfig:
    if path is None:
        return ServerConfig()

    config_path = Path(path)
    payload = _read_yaml(config_path)
    server_payload = payload.get("server") or {}
    broker_payload = payload.get("broker") or {}
    mock_payload = broker_payload.get("mock") or {}

    data_dir_value = server_payload.get("data_dir") or "miniqmt_server/data"
    data_dir = Path(data_dir_value)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir

    mock_config = MockBrokerConfig(
        account_id=str(mock_payload.get("account_id") or "mock_account"),
        allow_submit=bool(mock_payload.get("allow_submit", True)),
        auto_fill=bool(mock_payload.get("auto_fill", False)),
        miniqmt_connected=bool(mock_payload.get("miniqmt_connected", False)),
        query_ready=bool(mock_payload.get("query_ready", True)),
        submit_enabled=bool(mock_payload.get("submit_enabled", True)),
        account=dict(mock_payload.get("account") or {}),
        positions=list(mock_payload.get("positions") or []),
    )
    if not mock_config.account:
        mock_config.account = MockBrokerConfig().account
    if not mock_config.positions:
        mock_config.positions = MockBrokerConfig().positions
    if not mock_config.account.get("account_id"):
        mock_config.account["account_id"] = mock_config.account_id

    return ServerConfig(
        host=str(server_payload.get("host") or "127.0.0.1"),
        port=int(server_payload.get("port") or 8811),
        version=str(server_payload.get("version") or __version__),
        broker_mode=str(broker_payload.get("mode") or "mock"),
        data_dir=data_dir,
        mock=mock_config,
    )
