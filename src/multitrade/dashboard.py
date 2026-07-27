from __future__ import annotations

import base64
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from multitrade.audit import SqliteAuditReader
from multitrade.health import check_health


_DASHBOARD_HTML = (
    Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
)


class DashboardData:
    def __init__(
        self,
        db_path: str | Path,
        health_path: str | Path,
        health_max_age_seconds: int,
        max_total_open: Decimal,
        max_per_trade: Decimal,
    ) -> None:
        self.reader = SqliteAuditReader(db_path)
        self.health_path = Path(health_path)
        self.health_max_age_seconds = health_max_age_seconds
        self.max_total_open = max_total_open
        self.max_per_trade = max_per_trade

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def overview(self, event_limit: int = 40) -> dict[str, Any]:
        healthy, health = check_health(
            self.health_path, self.health_max_age_seconds
        )
        try:
            state = self.reader.latest_broker_state("alpaca-paper")
            active_risk = self.reader.active_risk()
            reservations = self.reader.reservation_summary()
            events = self.reader.recent_events(event_limit)
            storage: dict[str, Any] = {"status": "ok"}
        except (FileNotFoundError, OSError, sqlite3.Error):
            state = None
            active_risk = Decimal("0")
            reservations = {}
            events = []
            storage = {"status": "unavailable"}

        account: dict[str, Any] | None = None
        market: dict[str, Any] | None = None
        positions: list[dict[str, Any]] = []
        open_orders: list[dict[str, Any]] = []
        connection: dict[str, Any] = {
            "broker": "alpaca",
            "environment": "paper",
            "observed_at": None,
            "request_ids": [],
        }

        if state is not None:
            payload = state["payload"]
            account = payload.get("account")
            market = payload.get("market")
            positions = payload.get("positions") or []
            open_orders = payload.get("open_orders") or []
            connection = {
                "broker": payload.get("broker", "alpaca"),
                "environment": payload.get("environment", "paper"),
                "observed_at": state["observed_at"],
                "request_ids": payload.get("request_ids") or [],
            }
        elif storage["status"] == "ok":
            heartbeat = self.reader.latest_event("account_heartbeat")
            if heartbeat is not None:
                account = heartbeat["payload"]
                connection["observed_at"] = heartbeat["occurred_at"]

        equity = (
            self._decimal(account.get("equity"))
            if account is not None
            else Decimal("0")
        )
        aggregate_capacity = equity * self.max_total_open
        per_trade_capacity = equity * self.max_per_trade
        utilization = (
            active_risk / aggregate_capacity * Decimal("100")
            if aggregate_capacity > 0
            else Decimal("0")
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": "paper",
            "engine": {"healthy": healthy, "details": health},
            "storage": storage,
            "connection": connection,
            "account": account,
            "market": market,
            "positions": positions,
            "open_orders": open_orders,
            "risk": {
                "active_amount": format(active_risk, "f"),
                "aggregate_ceiling_fraction": format(
                    self.max_total_open, "f"
                ),
                "per_trade_ceiling_fraction": format(
                    self.max_per_trade, "f"
                ),
                "aggregate_capacity_amount": format(
                    aggregate_capacity, "f"
                ),
                "per_trade_capacity_amount": format(
                    per_trade_capacity, "f"
                ),
                "utilization_percent": format(utilization, ".4f"),
                "reservations": reservations,
            },
            "events": events,
        }


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "MultiTradeDashboard/0.2"
    sys_version = ""
    data_service: DashboardData
    expected_authorization: str

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if not self._authorized():
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Basic realm="MultiTrade Operations", charset="UTF-8"',
            )
            self._security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/":
            nonce = secrets.token_urlsafe(18)
            payload = _DASHBOARD_HTML.replace(
                "{{NONCE}}", nonce
            ).encode("utf-8")
            self.send_response(200)
            self._security_headers(nonce)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/overview":
            values = parse_qs(parsed.query).get("limit", ["40"])
            try:
                limit = max(1, min(int(values[0]), 200))
            except ValueError:
                self._send_json(400, {"error": "invalid_limit"})
                return
            self._send_json(200, self.data_service.overview(limit))
            return
        self._send_json(404, {"error": "not_found"})

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return compare_digest(supplied, self.expected_authorization)

    def _security_headers(self, nonce: str | None = None) -> None:
        script_source = f"'nonce-{nonce}'" if nonce else "'none'"
        style_source = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            f"script-src {script_source}; style-src {style_source}; "
            "connect-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def create_dashboard_server(
    host: str,
    port: int,
    data_service: DashboardData,
    username: str,
    password: str,
) -> ThreadingHTTPServer:
    credentials = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    class ConfiguredHandler(DashboardRequestHandler):
        pass

    ConfiguredHandler.data_service = data_service
    ConfiguredHandler.expected_authorization = f"Basic {credentials}"
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def dashboard_healthcheck(port: int) -> tuple[bool, str]:
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=3
        ) as response:
            return response.status == 200, f"http_{response.status}"
    except (OSError, URLError):
        return False, "unreachable"
