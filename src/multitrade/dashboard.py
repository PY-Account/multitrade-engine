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
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from multitrade import __version__
from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.experiments import (
    StrategyExperimentProgram,
    experiment_program_payload,
)
from multitrade.health import check_health
from multitrade.portfolio import (
    AccountPlan,
    apply_strategy_configuration_overrides,
)
from multitrade.research import evidence_catalog
from multitrade.risk import FirmRiskPolicy
from multitrade.universe import AssetUniverseProgram, program_payload


_DASHBOARD_HTML = (
    Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
)

_LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MultiTrade sign in</title>
<style nonce="{{NONCE}}">
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#08131d;
color:#eaf4fb;font:16px system-ui,sans-serif}main{width:min(390px,calc(100% - 32px));
background:#101f2b;border:1px solid #294152;border-radius:16px;padding:28px;
box-sizing:border-box}h1{margin:0 0 8px}p{color:#a9c0cf}label{display:block;margin:18px 0 6px}
input{box-sizing:border-box;width:100%;padding:12px;border-radius:8px;border:1px solid
#3c596b;background:#07121b;color:#fff}button{width:100%;margin-top:22px;padding:12px;
border:0;border-radius:8px;background:#3ba6ff;color:#04111b;font-weight:700;cursor:pointer}
.error{color:#ff9c9c}</style></head><body><main><h1>MultiTrade Operations</h1>
<p>Secure operator sign in</p>{{ERROR}}<form method="post" action="/login">
<input type="hidden" name="csrf_token" value="{{CSRF_TOKEN}}">
<label for="username">Username</label><input id="username" name="username"
autocomplete="username" required maxlength="128"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password"
required maxlength="512"><button type="submit">Sign in</button></form></main></body></html>"""


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
        option_evidence_health_path: str | Path | None = None,
        option_evidence_health_max_age_seconds: int = 10800,
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
        firm_risk_policy: FirmRiskPolicy | None = None,
        release_version: str = __version__,
        build_commit: str | None = None,
    ) -> None:
        self.reader = SqliteAuditReader(db_path)
        self.configuration_store = SqliteAuditStore(db_path)
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
        self.option_evidence_health_path = (
            Path(option_evidence_health_path)
            if option_evidence_health_path is not None
            else None
        )
        self.option_evidence_health_max_age_seconds = (
            option_evidence_health_max_age_seconds
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
        self.firm_risk_policy = (
            firm_risk_policy or FirmRiskPolicy(enabled=False)
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

    def effective_account_plans(self) -> tuple[AccountPlan, ...]:
        return apply_strategy_configuration_overrides(
            self.account_plans,
            self.configuration_store.strategy_configuration_overrides(),
            strict=False,
        )

    def update_strategy_configuration(
        self,
        payload: dict[str, Any],
        *,
        updated_by: str,
    ) -> dict[str, Any]:
        account_id = str(payload.get("account_id", "")).strip()
        strategy_id = str(payload.get("strategy_id", "")).strip()
        enabled = payload.get("enabled")
        paper_allowed = payload.get("paper_execution_allowed")
        expected_revision = payload.get("expected_revision")
        if (
            not account_id
            or not strategy_id
            or type(enabled) is not bool
            or type(paper_allowed) is not bool
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ValueError("invalid_configuration_request")
        if payload.get("confirmation") != "APPLY PAPER CONFIG":
            raise ValueError("paper_confirmation_required")
        if paper_allowed and not enabled:
            raise ValueError("disabled_strategy_cannot_trade")
        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, list):
            raise ValueError("symbols_must_be_a_list")
        if len(raw_symbols) > 100:
            raise ValueError("too_many_symbols")
        symbols = tuple(
            dict.fromkeys(
                str(symbol).strip().upper() for symbol in raw_symbols
            )
        )
        if any(
            not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", symbol)
            for symbol in symbols
        ):
            raise ValueError("invalid_symbol")

        account = next(
            (
                plan
                for plan in self.account_plans
                if plan.account_id == account_id
            ),
            None,
        )
        if account is None or account.environment != "paper":
            raise ValueError("unknown_paper_account")
        if strategy_id not in account.allocations:
            raise ValueError("unknown_strategy")

        current = self.configuration_store.strategy_configuration_overrides()
        proposed = [
            row
            for row in current
            if (
                row["account_id"],
                row["strategy_id"],
            )
            != (account_id, strategy_id)
        ]
        proposed.append(
            {
                "account_id": account_id,
                "strategy_id": strategy_id,
                "enabled": enabled,
                "paper_execution_allowed": paper_allowed,
                "symbols": list(symbols),
            }
        )
        apply_strategy_configuration_overrides(
            self.account_plans, proposed
        )
        return self.configuration_store.set_strategy_configuration_override(
            account_id=account_id,
            strategy_id=strategy_id,
            enabled=enabled,
            paper_execution_allowed=paper_allowed,
            symbols=symbols,
            expected_revision=expected_revision,
            updated_by=updated_by,
        )

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def overview(self, event_limit: int = 40) -> dict[str, Any]:
        effective_account_plans = self.effective_account_plans()
        configuration_overrides = (
            self.configuration_store.strategy_configuration_overrides()
        )
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
        if self.option_evidence_health_path is not None:
            option_evidence_healthy, option_evidence_health = check_health(
                self.option_evidence_health_path,
                self.option_evidence_health_max_age_seconds,
            )
        else:
            option_evidence_healthy = False
            option_evidence_health = {"status": "not_configured"}
        if self.asset_universe_health_path is not None:
            universe_healthy, universe_health = check_health(
                self.asset_universe_health_path,
                self.asset_universe_health_max_age_seconds,
            )
        else:
            universe_healthy = False
            universe_health = {"status": "not_configured"}
        account_ids = [
            plan.account_id for plan in effective_account_plans
        ] or ["alpaca-paper"]
        primary_account_id = account_ids[0]
        try:
            broker_states = {
                account_id: self.reader.latest_broker_state(
                    account_id
                )
                for account_id in account_ids
            }
            active_risks = {
                account_id: self.reader.active_risk(account_id)
                for account_id in account_ids
            }
            reservation_summaries = {
                account_id: self.reader.reservation_summary(
                    account_id
                )
                for account_id in account_ids
            }
            events = self.reader.recent_events(event_limit)
            signals = self.reader.recent_signals(event_limit)
            strategy_runtime = self.reader.strategy_runtime()
            trade_records = self.reader.recent_trade_records(event_limit)
            strategy_performance = (
                self.reader.strategy_performance()
            )
            option_observations = (
                self.reader.recent_option_observations(100)
            )
            option_package_evidence = (
                self.reader.recent_option_package_evidence(100)
            )
            firm_risk = self.reader.firm_risk_summary(
                self.firm_risk_policy
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
            accelerated_validation_runs = (
                self.reader.recent_accelerated_validation_runs(20)
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
            broker_states = {}
            active_risks = {}
            reservation_summaries = {}
            events = []
            signals = []
            strategy_runtime = []
            trade_records = []
            strategy_performance = []
            option_observations = []
            option_package_evidence = []
            firm_risk = {
                "enabled": self.firm_risk_policy.enabled,
                "status": "unavailable",
            }
            backtests = []
            validations = []
            research_decisions = []
            research_backtests = []
            portfolio_risk_reports = []
            strategy_lab_reports = []
            accelerated_validation_runs = []
            strategy_model_trials = []
            strategy_experiment_summaries = []
            asset_universe_reports = []
            storage = {"status": "unavailable"}

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
                "credential_env_prefix": (
                    plan.credential_env_prefix
                ),
                "broker_identity_pinned": bool(
                    plan.expected_broker_account_id
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
                                "maximum_short_delta": format(
                                    allocation.option_policy.maximum_short_delta,
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
                                "minimum_credit_to_risk": format(
                                    allocation.option_policy.minimum_credit_to_risk,
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
                                "maximum_holding_minutes": (
                                    allocation.option_policy.maximum_holding_minutes
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
            for plan in effective_account_plans
        ]

        account_views: dict[str, dict[str, Any]] = {}
        for account_id in account_ids:
            state = broker_states.get(account_id)
            account: dict[str, Any] | None = None
            market: dict[str, Any] | None = None
            positions: list[dict[str, Any]] = []
            open_orders: list[dict[str, Any]] = []
            connection: dict[str, Any] = {
                "broker": "alpaca",
                "environment": "paper",
                "operating_mode": operating_mode,
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
                    "environment": payload.get(
                        "environment", "paper"
                    ),
                    "operating_mode": operating_mode,
                    "observed_at": state["observed_at"],
                    "request_ids": (
                        payload.get("request_ids") or []
                    ),
                }
            active_risk = active_risks.get(
                account_id, Decimal("0")
            )
            equity = (
                self._decimal(account.get("equity"))
                if account is not None
                else Decimal("0")
            )
            aggregate_capacity = equity * self.max_total_open
            per_trade_capacity = equity * self.max_per_trade
            utilization = (
                active_risk
                / aggregate_capacity
                * Decimal("100")
                if aggregate_capacity > 0
                else Decimal("0")
            )
            account_views[account_id] = {
                "account_id": account_id,
                "account": account,
                "market": market,
                "positions": positions,
                "open_orders": open_orders,
                "connection": connection,
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
                    "utilization_percent": format(
                        utilization, ".4f"
                    ),
                    "reservations": (
                        reservation_summaries.get(account_id, {})
                    ),
                },
                "option_observations": [
                    row
                    for row in option_observations
                    if row["account_id"] == account_id
                ],
                "option_package_evidence": [
                    row
                    for row in option_package_evidence
                    if row["account_id"] == account_id
                ],
            }

        primary_view = account_views[primary_account_id]
        account = primary_view["account"]
        market = primary_view["market"]
        positions = primary_view["positions"]
        open_orders = primary_view["open_orders"]
        connection = primary_view["connection"]
        risk = primary_view["risk"]
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
            "option_evidence": {
                "healthy": option_evidence_healthy,
                "details": option_evidence_health,
                "execution_enabled": False,
                "evidence_type": "exact_contract_trade_bar_proxy",
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
            "account_views": account_views,
            "configured_accounts": configured_accounts,
            "strategy_configuration": {
                "scope": "paper_only",
                "live_trading_available": False,
                "changes_apply_on_next_cycle": True,
                "overrides": configuration_overrides,
            },
            "market": market,
            "positions": positions,
            "open_orders": open_orders,
            "risk": risk,
            "firm_risk": firm_risk,
            "events": events,
            "signals": signals,
            "strategy_runtime": strategy_runtime,
            "trade_records": trade_records,
            "strategy_performance": strategy_performance,
            "option_observations": option_observations,
            "option_package_evidence": option_package_evidence,
            "backtests": backtests,
            "validations": validations,
            "research_decisions": research_decisions,
            "research_backtests": research_backtests,
            "portfolio_risk_reports": portfolio_risk_reports,
            "strategy_lab_reports": strategy_lab_reports,
            "accelerated_validation_runs": (
                accelerated_validation_runs
            ),
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
        center_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,19}", normalized_symbol):
            raise ValueError("invalid_symbol")
        if not re.fullmatch(
            r"(?:[1-9][0-9]?(?:Min|T)|[1-9][0-9]?(?:Hour|H)|1(?:Day|D))",
            timeframe,
        ):
            raise ValueError("invalid_timeframe")
        if center_at is not None:
            try:
                datetime.fromisoformat(
                    center_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("invalid_center_at") from exc
        return {
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "center_at": center_at,
            "bars": self.reader.market_bars(
                normalized_symbol,
                timeframe,
                limit=limit,
                center_at=center_at,
            ),
        }

    @staticmethod
    def _analyst_safe(value: Any) -> Any:
        denied_fragments = (
            "api_key",
            "secret",
            "password",
            "credential",
            "token",
            "request_id",
            "broker_order_id",
        )
        if isinstance(value, dict):
            return {
                str(key): DashboardData._analyst_safe(item)
                for key, item in value.items()
                if not any(
                    fragment in str(key).lower()
                    for fragment in denied_fragments
                )
            }
        if isinstance(value, list):
            return [DashboardData._analyst_safe(item) for item in value]
        if isinstance(value, tuple):
            return [DashboardData._analyst_safe(item) for item in value]
        return value

    def analyst_snapshot(self, event_limit: int = 100) -> dict[str, Any]:
        """Return a redacted, versioned research and operations snapshot."""

        overview = self.overview(event_limit)
        selected = {
            "schema_version": "analyst.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "release": overview.get("release", {}),
            "environment": overview.get("environment", "paper"),
            "operating_mode": overview.get("operating_mode", {}),
            "engine": overview.get("engine", {}),
            "automation": overview.get("automation", {}),
            "research": overview.get("research", {}),
            "strategy_lab": overview.get("strategy_lab", {}),
            "asset_universe": overview.get("asset_universe", {}),
            "configured_accounts": overview.get(
                "configured_accounts", []
            ),
            "account_views": overview.get("account_views", {}),
            "firm_risk": overview.get("firm_risk", {}),
            "strategy_runtime": overview.get("strategy_runtime", []),
            "strategy_performance": overview.get(
                "strategy_performance", []
            ),
            "trade_records": overview.get("trade_records", []),
            "signals": overview.get("signals", []),
            "research_decisions": overview.get(
                "research_decisions", []
            ),
            "research_backtests": overview.get(
                "research_backtests", []
            ),
            "portfolio_risk_reports": overview.get(
                "portfolio_risk_reports", []
            ),
            "strategy_lab_reports": overview.get(
                "strategy_lab_reports", []
            ),
            "accelerated_validation_runs": overview.get(
                "accelerated_validation_runs", []
            ),
            "strategy_model_trials": overview.get(
                "strategy_model_trials", []
            ),
            "strategy_experiments": overview.get(
                "strategy_experiments", {}
            ),
            "asset_universe_reports": overview.get(
                "asset_universe_reports", []
            ),
            "option_observations": overview.get(
                "option_observations", []
            ),
            "option_package_evidence": overview.get(
                "option_package_evidence", []
            ),
        }
        return self._analyst_safe(selected)

    def record_analyst_access(self, endpoint: str, client: str) -> None:
        self.configuration_store.record_event(
            "analyst_api_read",
            f"analyst-{secrets.token_hex(12)}",
            {
                "endpoint": endpoint,
                "client": client,
                "access": "read_only",
            },
        )


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = f"MultiTradeDashboard/{__version__}"
    sys_version = ""
    data_service: DashboardData
    expected_authorization: str
    auth_lock = threading.Lock()
    auth_failures: dict[str, list[float]] = {}
    auth_blocked_until: dict[str, float] = {}
    csrf_lock = threading.Lock()
    csrf_tokens: dict[str, tuple[str, float]] = {}
    config_lock = threading.Lock()
    configured_username: str
    session_lock = threading.Lock()
    sessions: dict[str, float] = {}
    session_lifetime_seconds: int = 28800
    session_cookie_name = "__Host-multitrade_session"
    analyst_api_enabled: bool = False
    analyst_api_token: str = ""
    analyst_requests_per_minute: int = 30
    analyst_lock = threading.Lock()
    analyst_requests: dict[str, list[float]] = {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path.startswith("/api/analyst/v1/"):
            self._serve_analyst_get(parsed)
            return
        if parsed.path == "/login":
            self._send_login_page()
            return
        if not self._authorized():
            if parsed.path == "/":
                self.send_response(303)
                self._security_headers()
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json(401, {"error": "authentication_required"})
            return
        if parsed.path == "/":
            nonce = secrets.token_urlsafe(18)
            csrf_token = secrets.token_urlsafe(32)
            self._register_csrf_token(csrf_token)
            payload = (
                _DASHBOARD_HTML.replace("{{NONCE}}", nonce)
                .replace("{{CSRF_TOKEN}}", csrf_token)
                .encode("utf-8")
            )
            self.send_response(200)
            self._security_headers(nonce, allow_forms=True)
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
                center_at = values.get("center_at", [None])[0]
                chart = self.data_service.chart(
                    symbol,
                    timeframe,
                    limit=limit,
                    center_at=center_at,
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, chart)
            return
        if parsed.path == "/api/export/analyst-snapshot.json":
            values = parse_qs(parsed.query).get("limit", ["500"])
            try:
                limit = max(1, min(int(values[0]), 1000))
            except ValueError:
                self._send_json(400, {"error": "invalid_limit"})
                return
            try:
                payload = self.data_service.analyst_snapshot(limit)
                self.data_service.record_analyst_access(
                    parsed.path, self.client_address[0]
                )
            except sqlite3.Error:
                self._send_json(503, {"error": "audit_store_unavailable"})
                return
            self._send_download_json(
                200,
                payload,
                "multitrade-analyst-snapshot.json",
            )
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self._handle_login()
            return
        if parsed.path == "/logout":
            self._handle_logout()
            return
        if parsed.path.startswith("/api/analyst/"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if 0 < length <= 32768:
                self.rfile.read(length)
            self.send_response(405)
            self._security_headers()
            self.send_header("Allow", "GET")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.close_connection = True
            return
        if not self._authorized():
            self._send_json(401, {"error": "authentication_required"})
            return
        if parsed.path != "/api/config/strategy":
            self._send_json(404, {"error": "not_found"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith(
            "application/json"
        ):
            self._send_json(415, {"error": "json_required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length < 2 or length > 32768:
            self._send_json(413, {"error": "request_size_invalid"})
            return
        body = self.rfile.read(length)
        if not self._valid_csrf_token(
            self.headers.get("X-CSRF-Token", "")
        ):
            self._send_json(403, {"error": "invalid_csrf_token"})
            return
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("json_object_required")
            with self.config_lock:
                result = self.data_service.update_strategy_configuration(
                    payload,
                    updated_by=self.configured_username,
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return
        except ValueError as exc:
            status = (
                409
                if str(exc) == "configuration_revision_conflict"
                else 400
            )
            self._send_json(status, {"error": str(exc)})
            return
        except sqlite3.Error:
            self._send_json(503, {"error": "configuration_store_unavailable"})
            return
        self._send_json(200, {"status": "updated", "configuration": result})

    def _send_login_page(self, error: bool = False) -> None:
        nonce = secrets.token_urlsafe(18)
        csrf_token = secrets.token_urlsafe(32)
        self._register_csrf_token(csrf_token)
        error_html = (
            '<p class="error">Invalid username or password.</p>'
            if error
            else ""
        )
        payload = (
            _LOGIN_HTML.replace("{{NONCE}}", nonce)
            .replace("{{CSRF_TOKEN}}", csrf_token)
            .replace("{{ERROR}}", error_html)
            .encode("utf-8")
        )
        self.send_response(401 if error else 200)
        self._security_headers(nonce, allow_forms=True)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_form(self) -> dict[str, str] | None:
        if not self.headers.get("Content-Type", "").lower().startswith(
            "application/x-www-form-urlencoded"
        ):
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 1 or length > 4096:
            return None
        try:
            values = parse_qs(
                self.rfile.read(length).decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=4,
            )
        except (UnicodeDecodeError, ValueError):
            return None
        return {key: rows[0] for key, rows in values.items() if rows}

    def _handle_login(self) -> None:
        form = self._read_form()
        if form is None or not self._valid_csrf_token(
            form.get("csrf_token", "")
        ):
            self._send_json(400, {"error": "invalid_login_request"})
            return
        encoded = base64.b64encode(
            f"{form.get('username', '')}:{form.get('password', '')}".encode(
                "utf-8"
            )
        ).decode("ascii")
        if not self._authorization_attempt(f"Basic {encoded}"):
            self._send_login_page(error=True)
            return
        session_id = secrets.token_urlsafe(48)
        expires_at = time.monotonic() + self.session_lifetime_seconds
        handler = type(self)
        with handler.session_lock:
            handler.sessions[session_id] = expires_at
        self.send_response(303)
        self._security_headers()
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{self.session_cookie_name}={session_id}; Path=/; "
            "Secure; HttpOnly; SameSite=Strict; Max-Age="
            f"{self.session_lifetime_seconds}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_logout(self) -> None:
        if not self._authorized():
            self._send_json(401, {"error": "authentication_required"})
            return
        form = self._read_form()
        if form is None or not self._valid_csrf_token(
            form.get("csrf_token", "")
        ):
            self._send_json(403, {"error": "invalid_csrf_token"})
            return
        session_id = self._session_id()
        if session_id:
            handler = type(self)
            with handler.session_lock:
                handler.sessions.pop(session_id, None)
        self.send_response(303)
        self._security_headers()
        self.send_header("Location", "/login")
        self.send_header(
            "Set-Cookie",
            f"{self.session_cookie_name}=; Path=/; Secure; HttpOnly; "
            "SameSite=Strict; Max-Age=0",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_analyst_get(self, parsed: Any) -> None:
        auth_status = self._analyst_auth_status()
        if auth_status != 200:
            self._send_json(
                auth_status,
                {
                    "error": (
                        "rate_limit_exceeded"
                        if auth_status == 429
                        else "analyst_authentication_required"
                    )
                },
            )
            return
        routes = {
            "/api/analyst/v1/snapshot": "snapshot",
            "/api/analyst/v1/validation-runs": (
                "accelerated_validation_runs"
            ),
            "/api/analyst/v1/strategies": "strategies",
            "/api/analyst/v1/trades": "trade_records",
            "/api/analyst/v1/health": "health",
        }
        route = routes.get(parsed.path)
        if route is None:
            self._send_json(404, {"error": "not_found"})
            return
        values = parse_qs(parsed.query).get("limit", ["100"])
        try:
            limit = max(1, min(int(values[0]), 200))
        except ValueError:
            self._send_json(400, {"error": "invalid_limit"})
            return
        snapshot = self.data_service.analyst_snapshot(limit)
        if route == "snapshot":
            payload = snapshot
        elif route == "strategies":
            payload = {
                "schema_version": snapshot["schema_version"],
                "generated_at": snapshot["generated_at"],
                "configured_accounts": snapshot["configured_accounts"],
                "strategy_runtime": snapshot["strategy_runtime"],
                "strategy_performance": snapshot["strategy_performance"],
                "strategy_experiments": snapshot["strategy_experiments"],
                "strategy_lab_reports": snapshot["strategy_lab_reports"],
            }
        elif route == "health":
            payload = {
                "schema_version": snapshot["schema_version"],
                "generated_at": snapshot["generated_at"],
                "release": snapshot["release"],
                "operating_mode": snapshot["operating_mode"],
                "engine": snapshot["engine"],
                "automation": snapshot["automation"],
                "research": snapshot["research"],
                "strategy_lab": snapshot["strategy_lab"],
                "asset_universe": snapshot["asset_universe"],
            }
        else:
            payload = {
                "schema_version": snapshot["schema_version"],
                "generated_at": snapshot["generated_at"],
                route: snapshot[route],
            }
        try:
            self.data_service.record_analyst_access(
                parsed.path, self.client_address[0]
            )
        except sqlite3.Error:
            self._send_json(503, {"error": "audit_store_unavailable"})
            return
        self._send_json(200, payload)

    def _analyst_auth_status(self) -> int:
        if not self.analyst_api_enabled or not self.analyst_api_token:
            return 404
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.analyst_api_token}"
        if not compare_digest(supplied, expected):
            return 401
        client = self.client_address[0]
        now = time.monotonic()
        handler = type(self)
        with handler.analyst_lock:
            recent = [
                timestamp
                for timestamp in handler.analyst_requests.get(client, [])
                if now - timestamp < 60
            ]
            if len(recent) >= self.analyst_requests_per_minute:
                handler.analyst_requests[client] = recent
                return 429
            recent.append(now)
            handler.analyst_requests[client] = recent
        return 200

    def _authorized(self) -> bool:
        session_id = self._session_id()
        if session_id:
            now = time.monotonic()
            handler = type(self)
            with handler.session_lock:
                expires_at = handler.sessions.get(session_id, 0)
                if expires_at > now:
                    return True
                handler.sessions.pop(session_id, None)
        return self._authorization_attempt(
            self.headers.get("Authorization", "")
        )

    def _session_id(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(self.session_cookie_name)
        return morsel.value if morsel is not None else ""

    def _authorization_attempt(self, supplied: str) -> bool:
        client = self.client_address[0]
        now = time.monotonic()
        authorized = compare_digest(
            supplied, self.expected_authorization
        )
        with self.auth_lock:
            blocked_until = self.auth_blocked_until.get(client, 0)
            if authorized:
                self.auth_failures.pop(client, None)
                self.auth_blocked_until.pop(client, None)
                return True
            if blocked_until > now:
                return False
            if blocked_until:
                self.auth_blocked_until.pop(client, None)
        with self.auth_lock:
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

    def _register_csrf_token(self, token: str) -> None:
        client = self.client_address[0]
        expires_at = time.monotonic() + 3600
        now = time.monotonic()
        handler = type(self)
        with handler.csrf_lock:
            handler.csrf_tokens = {
                value: details
                for value, details in handler.csrf_tokens.items()
                if details[1] > now
            }
            handler.csrf_tokens[token] = (client, expires_at)

    def _valid_csrf_token(self, token: str) -> bool:
        if not token:
            return False
        client = self.client_address[0]
        now = time.monotonic()
        handler = type(self)
        with handler.csrf_lock:
            details = handler.csrf_tokens.get(token)
            return bool(
                details
                and details[0] == client
                and details[1] > now
            )

    def _security_headers(
        self, nonce: str | None = None, *, allow_forms: bool = False
    ) -> None:
        script_source = f"'nonce-{nonce}'" if nonce else "'none'"
        style_source = f"'nonce-{nonce}'" if nonce else "'none'"
        form_source = "'self'" if allow_forms else "'none'"
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            f"script-src {script_source}; style-src {style_source}; "
            "connect-src 'self'; img-src 'self'; "
            "base-uri 'none'; "
            f"form-action {form_source}; "
            "frame-ancestors 'none'",
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

    def _send_download_json(
        self, status: int, value: Any, filename: str
    ) -> None:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
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
    analyst_api_enabled: bool = False,
    analyst_api_token: str = "",
    analyst_requests_per_minute: int = 30,
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
    ConfiguredHandler.csrf_lock = threading.Lock()
    ConfiguredHandler.csrf_tokens = {}
    ConfiguredHandler.config_lock = threading.Lock()
    ConfiguredHandler.configured_username = username
    ConfiguredHandler.session_lock = threading.Lock()
    ConfiguredHandler.sessions = {}
    ConfiguredHandler.analyst_api_enabled = analyst_api_enabled
    ConfiguredHandler.analyst_api_token = analyst_api_token
    ConfiguredHandler.analyst_requests_per_minute = (
        analyst_requests_per_minute
    )
    ConfiguredHandler.analyst_lock = threading.Lock()
    ConfiguredHandler.analyst_requests = {}
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def dashboard_healthcheck(port: int) -> tuple[bool, str]:
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=3
        ) as response:
            return response.status == 200, f"http_{response.status}"
    except (OSError, URLError):
        return False, "unreachable"
