from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from miniqmt_server.broker.miniqmt import MiniQMTBrokerAdapter
from miniqmt_server.broker.mock import MockBrokerAdapter
from miniqmt_server.config import ServerConfig, load_config
from miniqmt_server.models import CancelRequest, OrderRequest
from miniqmt_server.storage import JsonlStorage


LOGGER = logging.getLogger("miniqmt_server")


class MiniQMTServerApp:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.storage = JsonlStorage(config.data_dir)
        if config.broker_mode == "mock":
            self.broker = MockBrokerAdapter(config, self.storage)
        elif config.broker_mode == "miniqmt":
            self.broker = MiniQMTBrokerAdapter()
        else:
            raise ValueError(f"unsupported broker mode: {config.broker_mode}")

    def handle(self, method: str, raw_path: str, body: bytes | None = None) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(raw_path)
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        try:
            if method == "GET" and path == "/health":
                return HTTPStatus.OK, self.broker.get_health()
            if method == "GET" and path == "/account":
                return HTTPStatus.OK, self.broker.get_account()
            if method == "GET" and path == "/positions":
                positions = self.broker.get_positions()
                return HTTPStatus.OK, {"status": "ok", "count": len(positions), "positions": positions}
            if method == "GET" and path == "/orders":
                orders = self.broker.list_orders(query)
                return HTTPStatus.OK, {"status": "ok", "count": len(orders), "orders": orders}
            if method == "GET" and path == "/trades":
                trades = self.broker.list_trades(query)
                return HTTPStatus.OK, {"status": "ok", "count": len(trades), "trades": trades}
            if method == "GET" and path == "/snapshots/latest":
                return HTTPStatus.OK, {"status": "ok", "snapshot": self.broker.get_latest_snapshot()}
            if method == "POST" and path == "/orders/validate":
                payload = self._load_json_body(body)
                request = OrderRequest.from_dict(payload)
                return HTTPStatus.OK, self.broker.validate_orders(request)
            if method == "POST" and path == "/orders/submit":
                payload = self._load_json_body(body)
                request = OrderRequest.from_dict(payload)
                return HTTPStatus.OK, self.broker.submit_orders(request)
            if method == "POST" and path == "/orders/cancel":
                payload = self._load_json_body(body)
                request = CancelRequest.from_dict(payload)
                return HTTPStatus.OK, self.broker.cancel_orders(request)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": {"code": "bad_request", "message": str(exc)}}
        except NotImplementedError as exc:
            return HTTPStatus.NOT_IMPLEMENTED, {
                "status": "rejected",
                "error": {"code": "not_implemented", "message": str(exc)},
            }
        except Exception as exc:  # pragma: no cover - defensive fallback
            LOGGER.exception("Unhandled MiniQMT server error")
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "status": "error",
                "error": {"code": "internal_error", "message": str(exc)},
            }
        return HTTPStatus.NOT_FOUND, {
            "status": "not_found",
            "error": {"code": "not_found", "message": f"route {path} was not found"},
        }

    def _load_json_body(self, body: bytes | None) -> dict[str, Any]:
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON body: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload


class MiniQMTRequestHandler(BaseHTTPRequestHandler):
    app: MiniQMTServerApp

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
        status_code, payload = self.server.app.handle(self.command, self.path, body)  # type: ignore[attr-defined]
        response = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)


class MiniQMTHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(self, server_address: tuple[str, int], app: MiniQMTServerApp) -> None:
        super().__init__(server_address, MiniQMTRequestHandler)
        self.app = app



def build_server(config: ServerConfig) -> MiniQMTHTTPServer:
    app = MiniQMTServerApp(config)
    return MiniQMTHTTPServer((config.host, config.port), app)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Windows MiniQMT mock server")
    parser.add_argument("--config", default=None, help="Path to YAML config file")
    parser.add_argument("--host", default=None, help="Override bind host")
    parser.add_argument("--port", type=int, default=None, help="Override bind port")
    return parser.parse_args()



def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    config = load_config(args.config)
    if args.host:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    server = build_server(config)
    LOGGER.info(
        "Starting miniqmt_server on http://%s:%s with broker_mode=%s data_dir=%s",
        config.host,
        config.port,
        config.broker_mode,
        config.data_dir,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down miniqmt_server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
