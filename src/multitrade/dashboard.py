from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from multitrade import __version__
from multitrade.audit import SqliteAuditReader
from multitrade.experiments import (
    StrategyExperimentProgram,
    experiment_program_payload,
)
from multitrade.health import check_health
from multitrade.portfolio import AccountPlan
from multitrade.research import evidence_catalog
from multitrade.universe import AssetUniverseProgram, program_payload


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
        strategy_health_path: str | Path | None = None,
        strategy_health_max_age_seconds: int = 900,
        research_health_path: str | Path | None = None,
        research_health_max_age_seconds: int = 10800,
        strategy_lab_health_path: str | Path | None = None,
        strategy_lab_health_max_age_seconds: int = 64800,
        asset_universe_health_path: str | Path | None = None,
        asset_universe_health_max_age_seconds: int = 259200,
        automation_enabled: bool = False,
        paper_order_submission_enabled: bool = False,
        emergency_stop: bool = False,
        account_plans: tuple[AccountPlan, ...] = (),
        asset_universe_program: AssetUniverseProgram | None = None,
        strategy_experiment_program: (
            StrategyExperimentProgram | None
        ) = None,
        release_version: str = __version__,
        build_commit: str | None = None,
    ) -> None:
        self.reader = SqliteAuditReader(db_path)
        self.health_path = Path(health_path)
        self.health_max_age_seconds = health_max_age_seconds
        self.max_total_open = max_total_open
        self.max_per_trade = max_per_trade
        self.strategy_health_path = (
            Path(strategy_health_path)
            if strategy_health_path is not None
            else None
        )
        self.strategy_health_max_age_seconds = (
            strategy_health_max_age_seconds
        )
        self.research_health_path = (
            Path(research_health_path)
            if research_health_path is not None
            else None
        )
        self.research_health_max_age_seconds = (
            research_health_max_age_seconds
        )
        self.strategy_lab_health_path = (
            Path(strategy_lab_health_path)
            if strategy_lab_health_path is not None
            else None
        )
        self.strategy_lab_health_max_age_seconds = (
            strategy_lab_health_max_age_seconds
        )
        self.asset_universe_health_path = (
            Path(asset_universe_health_path)
            if asset_universe_health_path is not None
            else None
        )
        self.asset_universe_health_max_age_seconds = (
            asset_universe_health_max_age_seconds
        )
        self.automation_enabled = automation_enabled
        self.paper_order_submission_enabled = (
            paper_order_submission_enabled
        )
        self.emergency_stop = emergency_stop
        self.account_plans = account_plans
        self.asset_universe_program = asset_universe_program
        self.strategy_experiment_program = (
            strategy_experiment_program
        )
        self.release_version = release_version
        candidate_commit = (
            build_commit
            if build_commit is not None
            else os.getenv("MULTITRADE_BUILD_COMMIT", "unknown")
        ).strip().lower()
        self.build_commit = (
            candidate_commit
            if re.fullmatch(r"[0-9a-f]{40,64}", candidate_commit)
            else "unknown"
        )

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
        if self.strategy_health_path is not None:
            automation_healthy, automation_health = check_health(
                self.strategy_health_path,
                self.strategy_health_max_age_seconds,
            )
        else:
            automation_healthy = False
            automation_health = {"status": "not_configured"}
        if self.research_health_path is not None:
            research_healthy, research_health = check_health(
                self.research_health_path,
                self.research_health_max_age_seconds,
            )
        else:
            research_healthy = False
            research_health = {"status": "not_configured"}
        if self.strategy_lab_health_path is not None:
            strategy_lab_healthy, strategy_lab_health = check_health(
                self.strategy_lab_health_path,
                self.strategy_lab_health_max_age_seconds,
            )
        else:
            strategy_lab_healthy = False
            strategy_lab_health = {"status": "not_configured"}
        if self.asset_universe_health_path is not None:
            universe_healthy, universe_health = check_health(
                self.asset_universe_health_path,
                self.asset_universe_health_max_age_seconds,
            )
        else:
            universe_healthy = False
            universe_health = {"status": "not_configured"}
        try:
            state = self.reader.latest_broker_state("alpaca-paper")
            active_risk = self.reader.active_risk()
            reservations = self.reader.reservation_summary()
            events = self.reader.recent_events(event_limit)
            signals = self.reader.recent_signals(event_limit)
            strategy_runtime = self.reader.strategy_runtime()
            trade_records = self.reader.recent_trade_records(event_limit)
            strategy_performance = (
                self.reader.strategy_performance()
            )
            backtests = self.reader.recent_backtests(20)
            validations = self.reader.recent_validations(20)
            research_decisions = (
                self.reader.recent_research_decisions(event_limit)
            )
            research_backtests = (
                self.reader.recent_research_backtests(30)
            )
            portfolio_risk_reports = (
                self.reader.recent_portfolio_risk_reports(10)
            )
            strategy_lab_reports = (
                self.reader.recent_strategy_lab_reports(40)
            )
            strategy_model_trials = (
                self.reader.recent_strategy_model_trials(100)
            )
            strategy_experiment_summaries = (
                self.reader.strategy_experiment_summaries()
            )
            asset_universe_reports = (
                self.reader.recent_asset_universe_reports(20)
            )
            storage: dict[str, Any] = {"status": "ok"}
        except (FileNotFoundError, OSError, sqlite3.Error):
            state = None
            active_risk = Decimal("0")
            reservations = {}
            events = []
            signals = []
            strategy_runtime = []
            trade_records = []
            strategy_performance = []
            backtests = []
            validations = []
            research_decisions = []
            research_backtests = []
            portfolio_risk_reports = []
            strategy_lab_reports = []
            strategy_model_trials = []
            strategy_experiment_summaries = []
            asset_universe_reports = []
            storage = {"status": "unavailable"}

        account: dict[str, Any] | None = None
        market: dict[str, Any] | None = None
        positions: list[dict[str, Any]] = []
        open_orders: list[dict[str, Any]] = []
        operating_mode = {
            "automation_enabled": self.automation_enabled,
            "paper_order_submission_enabled": (
                self.paper_order_submission_enabled
            ),
            "emergency_stop": self.emergency_stop,
            "paper_execution_enabled": (
                self.automation_enabled
                and self.paper_order_submission_enabled
                and not self.emergency_stop
            ),
        }
        connection: dict[str, Any] = {
            "broker": "alpaca",
            "environment": "paper",
            "operating_mode": operating_mode,
            "observed_at": None,
            "request_ids": [],
        }
        configured_accounts = [
            {
                "account_id": plan.account_id,
                "broker": plan.broker,
                "environment": plan.environment,
                "enabled": plan.enabled,
                "asset_classes": [
                    asset_class.value
                    for asset_class in plan.asset_classes
                ],
                "watchlist": list(plan.watchlist),
                "timeframe": plan.timeframe,
                "maximum_positions": plan.maximum_positions,
                "maximum_daily_orders": plan.maximum_daily_orders,
                "symbol_cooldown_minutes": (
                    plan.symbol_cooldown_minutes
                ),
                "allocations": [
                    {
                        "strategy_id": allocation.strategy_id,
                        "enabled": allocation.enabled,
                        "capital_weight": format(
                            allocation.capital_weight, "f"
                        ),
                        "risk_fraction": format(
                            allocation.risk_fraction, "f"
                        ),
                        "minimum_confidence": format(
                            allocation.minimum_confidence, "f"
                        ),
                        "paper_execution_allowed": (
                            allocation.paper_execution_allowed
                        ),
                        "asset_class": (
                            allocation.asset_class.value
                        ),
                        "source_strategy_id": (
                            allocation.source_strategy_id
                        ),
                        "option_policy": (
                            {
                                "structure": (
                                    allocation.option_policy.structure.value
                                ),
                                "minimum_dte": (
                                    allocation.option_policy.minimum_dte
                                ),
                                "maximum_dte": (
                                    allocation.option_policy.maximum_dte
                                ),
                                "long_delta_target": format(
                                    allocation.option_policy.long_delta_target,
                                    "f",
                                ),
                                "short_delta_target": format(
                                    allocation.option_policy.short_delta_target,
                                    "f",
                                ),
                                "wing_delta_target": format(
                                    allocation.option_policy.wing_delta_target,
                                    "f",
                                ),
                                "maximum_strike_width": format(
                                    allocation.option_policy.maximum_strike_width,
                                    "f",
                                ),
                                "minimum_modeled_theta": format(
                                    allocation.option_policy.minimum_modeled_theta,
                                    "f",
                                ),
                                "profit_target_fraction": format(
                                    allocation.option_policy.profit_target_fraction,
                                    "f",
                                ),
                                "loss_limit_multiple": format(
                                    allocation.option_policy.loss_limit_multiple,
                                    "f",
                                ),
                                "exit_before_expiry_days": (
                                    allocation.option_policy.exit_before_expiry_days
                                ),
                                "maximum_quote_age_seconds": (
                                    allocation.option_policy.maximum_quote_age_seconds
                                ),
                                "required_trading_level": (
                                    allocation.option_policy.required_trading_level
                                ),
                                "theta_objective": (
                                    allocation.option_policy.theta_objective
                                ),
                            }
                            if allocation.option_policy is not None
                            else None
                        ),
                        "symbols": list(allocation.symbols),
                    }
                    for allocation in plan.allocations.values()
                ],
            }
            for plan in self.account_plans
        ]

        if state is not None:
            payload = state["payload"]
            account = payload.get("account")
            market = payload.get("market")
            positions = payload.get("positions") or []
            open_orders = payload.get("open_orders") or []
            connection = {
                "broker": payload.get("broker", "alpaca"),
                "environment": payload.get("environment", "paper"),
                "operating_mode": operating_mode,
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
            "release": {
                "version": self.release_version,
                "commit": self.build_commit,
                "short_commit": (
                    self.build_commit[:8]
                    if self.build_commit != "unknown"
                    else "unknown"
                ),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": "paper",
            "engine": {"healthy": healthy, "details": health},
            "automation": {
                "healthy": automation_healthy,
                "details": automation_health,
            },
            "research": {
                "healthy": research_healthy,
                "details": research_health,
                "execution_enabled": False,
            },
            "strategy_lab": {
                "healthy": strategy_lab_healthy,
                "details": strategy_lab_health,
                "execution_enabled": False,
            },
            "asset_universe": {
                "healthy": universe_healthy,
                "details": universe_health,
                "configuration": (
                    program_payload(self.asset_universe_program)
                    if self.asset_universe_program is not None
                    else None
                ),
                "execution_enabled": False,
            },
            "storage": storage,
            "connection": connection,
            "operating_mode": operating_mode,
            "account": account,
            "configured_accounts": configured_accounts,
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
            "signals": signals,
            "strategy_runtime": strategy_runtime,
            "trade_records": trade_records,
            "strategy_performance": strategy_performance,
            "backtests": backtests,
            "validations": validations,
            "research_decisions": research_decisions,
            "research_backtests": research_backtests,
            "portfolio_risk_reports": portfolio_risk_reports,
            "strategy_lab_reports": strategy_lab_reports,
            "strategy_model_trials": strategy_model_trials,
            "strategy_experiments": {
                "configuration": (
                    experiment_program_payload(
                        self.strategy_experiment_program
                    )
                    if self.strategy_experiment_program
                    is not None
                    else None
                ),
                "summaries": strategy_experiment_summaries,
                "execution_enabled": False,
            },
            "asset_universe_reports": asset_universe_reports,
            "evidence_catalog": evidence_catalog(),
        }

    def chart(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 160,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,19}", normalized_symbol):
            raise ValueError("invalid_symbol")
        if not re.fullmatch(
            r"(?:[1-9][0-9]?(?:Min|T)|[1-9][0-9]?(?:Hour|H)|1(?:Day|D))",
            timeframe,
        ):
            raise ValueError("invalid_timeframe")
        return {
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "bars": self.reader.market_bars(
                normalized_symbol, timeframe, limit=limit
            ),
        }


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "MultiTradeDashboard/0.12.0"
    sys_version = ""
    data_service: DashboardData
    expected_authorization: str
    auth_lock = threading.Lock()
    auth_failures: dict[str, list[float]] = {}
    auth_blocked_until: dict[str, float] = {}

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
        if parsed.path == "/api/chart":
            values = parse_qs(parsed.query)
            symbol = values.get("symbol", [""])[0]
            timeframe = values.get("timeframe", ["5Min"])[0]
            try:
                limit = max(
                    20,
                    min(int(values.get("limit", ["160"])[0]), 500),
                )
                chart = self.data_service.chart(
                    symbol, timeframe, limit=limit
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, chart)
            return
        self._send_json(404, {"error": "not_found"})

    def _authorized(self) -> bool:
        client = self.client_address[0]
        now = time.monotonic()
        with self.auth_lock:
            blocked_until = self.auth_blocked_until.get(client, 0)
            if blocked_until > now:
                return False
            if blocked_until:
                self.auth_blocked_until.pop(client, None)
        supplied = self.headers.get("Authorization", "")
        authorized = compare_digest(
            supplied, self.expected_authorization
        )
        with self.auth_lock:
            if authorized:
                self.auth_failures.pop(client, None)
                return True
            recent = [
                timestamp
                for timestamp in self.auth_failures.get(client, [])
                if now - timestamp <= 300
            ]
            recent.append(now)
            self.auth_failures[client] = recent
            if len(recent) >= 5:
                self.auth_blocked_until[client] = now + 900
                self.auth_failures.pop(client, None)
        return False

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
    ConfiguredHandler.auth_lock = threading.Lock()
    ConfiguredHandler.auth_failures = {}
    ConfiguredHandler.auth_blocked_until = {}
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def dashboard_healthcheck(port: int) -> tuple[bool, str]:
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=3
        ) as response:
            return response.status == 200, f"http_{response.status}"
    except (OSError, URLError):
        return False, "unreachable"
