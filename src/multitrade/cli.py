from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from multitrade.audit import SqliteAuditStore
from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.config import Settings, load_env_file
from multitrade.dashboard import (
    DashboardData,
    create_dashboard_server,
    dashboard_healthcheck,
)
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.engine import TradingEngine
from multitrade.health import check_health, write_health
from multitrade.risk import RiskEngine


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class _DemoBroker:
    def __init__(self) -> None:
        self.submit_calls = 0

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity=Decimal("100000"),
            start_of_day_equity=Decimal("100000"),
            peak_equity=Decimal("100000"),
        )

    def submit_order(self, intent, approved_quantity):
        self.submit_calls += 1
        raise AssertionError("Demo broker must never submit an order")


def _demo() -> int:
    broker = _DemoBroker()
    store = SqliteAuditStore(":memory:")
    engine = TradingEngine(
        broker=broker,
        risk_engine=RiskEngine(),
        audit_store=store,
        enable_order_submission=False,
    )
    intent = TradeIntent(
        strategy_id="demo-stock-strategy",
        asset_class=AssetClass.STOCK,
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=Decimal("25"),
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        reference_price=Decimal("200"),
        stop_price=Decimal("190"),
        limit_price=Decimal("200"),
    )
    result = engine.process(intent)
    print(
        json.dumps(
            {
                "decision": asdict(result.decision),
                "dry_run": result.dry_run,
                "broker_submit_calls": broker.submit_calls,
                "active_risk_after_run": store.active_risk(),
                "events": store.recent_events(),
            },
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _doctor() -> int:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"configuration_error: {exc}", file=sys.stderr)
        return 2

    try:
        settings.require_dashboard_credentials()
        dashboard_credentials_valid = True
        dashboard_configuration_error = None
    except ValueError as exc:
        dashboard_credentials_valid = False
        dashboard_configuration_error = str(exc)

    checks = {
        "paper_endpoint_locked": True,
        "paper_order_submission_enabled": settings.enable_paper_orders,
        "api_key_present": bool(settings.alpaca_key_id),
        "api_secret_present": bool(settings.alpaca_secret_key),
        "database_path": str(settings.db_path),
        "health_path": str(settings.health_path),
        "health_max_age_seconds": settings.health_max_age_seconds,
        "dashboard_username_present": bool(
            settings.dashboard_username
        ),
        "dashboard_password_present": bool(
            settings.dashboard_password
        ),
        "dashboard_credentials_valid": dashboard_credentials_valid,
        "dashboard_configuration_error": dashboard_configuration_error,
        "dashboard_listen": (
            f"{settings.dashboard_host}:{settings.dashboard_port}"
        ),
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    return (
        0
        if (
            checks["api_key_present"]
            and checks["api_secret_present"]
            and checks["dashboard_credentials_valid"]
        )
        else 1
    )


def _run(once: bool) -> int:
    settings = Settings.from_env()
    settings.require_alpaca_credentials()
    broker = AlpacaPaperBroker(
        key_id=settings.alpaca_key_id,
        secret_key=settings.alpaca_secret_key,
        base_url=settings.alpaca_base_url,
    )
    store = SqliteAuditStore(settings.db_path)
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            reconciliation = broker.reconcile()
            snapshot = reconciliation.account_snapshot()
            active_risk = store.active_risk()
            summary = {
                "account_status": reconciliation.account.status,
                "equity": reconciliation.account.equity,
                "buying_power": reconciliation.account.buying_power,
                "gross_notional": reconciliation.account.gross_notional,
                "positions_count": len(reconciliation.positions),
                "open_orders_count": len(reconciliation.open_orders),
                "market_open": reconciliation.market.is_open,
                "request_ids": reconciliation.request_ids,
                "reserved_active_risk": active_risk,
            }
            store.record_broker_state(
                "alpaca-paper",
                reconciliation.observed_at,
                asdict(reconciliation),
                summary,
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "environment": "paper",
                        "equity": format(snapshot.equity, "f"),
                        "active_risk": format(active_risk, "f"),
                        "market_open": reconciliation.market.is_open,
                        "positions": len(reconciliation.positions),
                        "open_orders": len(reconciliation.open_orders),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            write_health(
                settings.health_path,
                "ok",
                {
                    "environment": "paper",
                    "equity": format(snapshot.equity, "f"),
                    "active_risk": format(active_risk, "f"),
                    "market_open": reconciliation.market.is_open,
                    "positions_count": len(reconciliation.positions),
                    "open_orders_count": len(
                        reconciliation.open_orders
                    ),
                    "observed_at": (
                        reconciliation.observed_at.isoformat()
                    ),
                },
            )
        except Exception as exc:
            store.record_event(
                "heartbeat_failed",
                "alpaca-paper",
                {"error_type": type(exc).__name__, "message": str(exc)},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            write_health(
                settings.health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.heartbeat_seconds):
            print(
                json.dumps(
                    {"status": "stopped", "reason": "shutdown_requested"},
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.health_path, settings.health_max_age_seconds
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _dashboard() -> int:
    settings = Settings.from_env()
    settings.require_dashboard_credentials()
    data_service = DashboardData(
        db_path=settings.db_path,
        health_path=settings.health_path,
        health_max_age_seconds=settings.health_max_age_seconds,
        max_total_open=settings.risk_policy.max_total_open,
        max_per_trade=settings.risk_policy.max_per_trade,
    )
    server = create_dashboard_server(
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        data_service=data_service,
        username=settings.dashboard_username,
        password=settings.dashboard_password,
    )
    print(
        json.dumps(
            {
                "status": "listening",
                "component": "read_only_dashboard",
                "address": (
                    f"{settings.dashboard_host}:"
                    f"{settings.dashboard_port}"
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _dashboard_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, status = dashboard_healthcheck(settings.dashboard_port)
    print(json.dumps({"status": status}, sort_keys=True))
    return 0 if healthy else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multitrade")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "demo", help="Run a local, zero-network dry-run"
    )
    subparsers.add_parser(
        "doctor", help="Validate Paper configuration without connecting"
    )
    run_parser = subparsers.add_parser(
        "run", help="Run the Alpaca Paper account heartbeat"
    )
    run_parser.add_argument(
        "--once", action="store_true", help="Run one heartbeat and exit"
    )
    subparsers.add_parser(
        "healthcheck", help="Check whether the latest Paper heartbeat is fresh"
    )
    subparsers.add_parser(
        "dashboard", help="Run the authenticated read-only dashboard"
    )
    subparsers.add_parser(
        "dashboard-healthcheck",
        help="Check the local dashboard HTTP endpoint",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        load_env_file()
    except (OSError, ValueError) as exc:
        print(f"configuration_error: {exc}", file=sys.stderr)
        return 2

    args = build_parser().parse_args(argv)
    try:
        if args.command == "demo":
            return _demo()
        if args.command == "doctor":
            return _doctor()
        if args.command == "run":
            return _run(args.once)
        if args.command == "healthcheck":
            return _healthcheck()
        if args.command == "dashboard":
            return _dashboard()
        if args.command == "dashboard-healthcheck":
            return _dashboard_healthcheck()
    except (ValueError, OSError) as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2
    return 2
