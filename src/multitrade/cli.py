from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from multitrade.accelerated_validation import (
    AcceleratedValidationService,
    accelerated_validation_payload,
)
from multitrade.audit import SqliteAuditStore
from multitrade.automation import PaperAutomationSupervisor
from multitrade.backtest import Backtester, StrategyValidator
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
from multitrade.experiments import (
    load_strategy_experiment_program,
)
from multitrade.health import check_health, write_health
from multitrade.market import (
    AlpacaMarketDataClient,
    closed_bars,
    timeframe_seconds,
)
from multitrade.options import (
    AlpacaOptionChainClient,
    OptionLiquidityPolicy,
)
from multitrade.option_evidence import (
    ContinuousOptionEvidenceService,
)
from multitrade.portfolio import load_account_plans
from multitrade.research import (
    ContinuousResearchService,
    EvidenceWeightedMarketModel,
    evidence_catalog,
    load_research_program,
)
from multitrade.research_validation import (
    ResearchBacktestConfig,
    ResearchModelBacktester,
)
from multitrade.risk import RiskEngine
from multitrade.strategies import (
    default_equity_strategies,
    equity_strategy_from_parameters,
)
from multitrade.strategy_lab import ContinuousStrategyLabService
from multitrade.universe import (
    ContinuousAssetUniverseService,
    load_asset_universe_program,
)


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
    try:
        plans = load_account_plans(settings.portfolio_config_path)
        portfolio_configuration_valid = True
        portfolio_configuration_error = None
        enabled_accounts = [
            plan.account_id for plan in plans if plan.enabled
        ]
        account_credentials = []
        for plan in plans:
            if not plan.enabled:
                continue
            try:
                settings.alpaca_credentials_for(
                    plan.credential_env_prefix
                )
                credentials_ready = True
                credential_error = None
            except ValueError as exc:
                credentials_ready = False
                credential_error = str(exc)
            account_credentials.append(
                {
                    "account_id": plan.account_id,
                    "environment_prefix": (
                        plan.credential_env_prefix
                    ),
                    "credentials_ready": credentials_ready,
                    "broker_identity_pinned": bool(
                        plan.expected_broker_account_id
                    ),
                    "error": credential_error,
                }
            )
        option_allocations = [
            {
                "account_id": plan.account_id,
                "strategy_id": allocation.strategy_id,
                "structure": (
                    allocation.option_policy.structure.value
                ),
                "enabled": allocation.enabled,
                "paper_execution_allowed": (
                    allocation.paper_execution_allowed
                ),
                "required_trading_level": (
                    allocation.option_policy.required_trading_level
                ),
            }
            for plan in plans
            for allocation in plan.allocations.values()
            if allocation.option_policy is not None
        ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        portfolio_configuration_valid = False
        portfolio_configuration_error = str(exc)
        enabled_accounts = []
        account_credentials = []
        option_allocations = []
    try:
        research_program = load_research_program(
            settings.research_program_path
        )
        research_configuration_valid = True
        research_configuration_error = None
        research_universe = list(research_program.universe)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        research_configuration_valid = False
        research_configuration_error = str(exc)
        research_universe = []
    try:
        universe_program = load_asset_universe_program(
            settings.asset_universe_config_path
        )
        asset_universe_configuration_valid = True
        asset_universe_configuration_error = None
        universe_policies = list(universe_program.policies)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        asset_universe_configuration_valid = False
        asset_universe_configuration_error = str(exc)
        universe_policies = []
    try:
        experiment_program = load_strategy_experiment_program(
            settings.strategy_experiment_program_path
        )
        strategies = default_equity_strategies()
        configured_strategy_ids = set(
            experiment_program.experiments_by_strategy
        )
        runtime_strategy_ids = set(strategies)
        if configured_strategy_ids != runtime_strategy_ids:
            missing = runtime_strategy_ids - configured_strategy_ids
            extra = configured_strategy_ids - runtime_strategy_ids
            details = []
            if missing:
                details.append(
                    "missing=" + ",".join(sorted(missing))
                )
            if extra:
                details.append(
                    "unknown=" + ",".join(sorted(extra))
                )
            raise ValueError(
                "Experiment strategy coverage differs from runtime: "
                + "; ".join(details)
            )
        for strategy in strategies.values():
            experiment_program.bind(
                strategy,
                evaluated_at=datetime.now(timezone.utc),
            )
        for experiment_id, experiment in (
            experiment_program.comparison_experiments_by_id.items()
        ):
            candidate = equity_strategy_from_parameters(
                experiment.expected_parameters
            )
            experiment_program.bind(
                candidate,
                evaluated_at=datetime.now(timezone.utc),
                experiment_id=experiment_id,
            )
        strategy_experiments_valid = True
        strategy_experiments_error = None
        strategy_experiment_families = sorted(
            {
                experiment.family_id
                for experiment in (
                    experiment_program.experiments_by_strategy.values()
                )
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        strategy_experiments_valid = False
        strategy_experiments_error = str(exc)
        strategy_experiment_families = []

    checks = {
        "paper_endpoint_locked": True,
        "automation_enabled": settings.automation_enabled,
        "paper_order_submission_enabled": settings.enable_paper_orders,
        "emergency_stop": settings.emergency_stop,
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_requires_all_controls": True,
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
        "analyst_api_enabled": settings.analyst_api_enabled,
        "analyst_api_token_present": bool(settings.analyst_api_token),
        "analyst_api_requests_per_minute": (
            settings.analyst_api_requests_per_minute
        ),
        "dashboard_credentials_valid": dashboard_credentials_valid,
        "dashboard_configuration_error": dashboard_configuration_error,
        "dashboard_listen": (
            f"{settings.dashboard_host}:{settings.dashboard_port}"
        ),
        "market_data_feed": settings.market_data_feed,
        "option_data_feed": settings.option_data_feed,
        "allow_indicative_paper_options": (
            settings.allow_indicative_paper_options
        ),
        "option_allocations": option_allocations,
        "option_execution_requires_opra": (
            not settings.allow_indicative_paper_options
        ),
        "option_execution_pricing_mode": (
            "indicative_paper_limit_preview"
            if settings.option_data_feed == "indicative"
            and settings.allow_indicative_paper_options
            else "opra_limit"
            if settings.option_data_feed == "opra"
            else "blocked"
        ),
        "option_theta_is_modeled_not_realized": True,
        "option_evidence_timeframe": (
            settings.option_evidence_timeframe
        ),
        "option_evidence_maximum_observations": (
            settings.option_evidence_maximum_observations
        ),
        "option_evidence_slippage_per_leg": format(
            settings.option_evidence_slippage_per_leg, "f"
        ),
        "option_evidence_type": "exact_contract_trade_bar_proxy",
        "option_evidence_execution_enabled": False,
        "portfolio_config_path": str(settings.portfolio_config_path),
        "portfolio_configuration_valid": portfolio_configuration_valid,
        "portfolio_configuration_error": (
            portfolio_configuration_error
        ),
        "research_program_path": str(settings.research_program_path),
        "research_configuration_valid": research_configuration_valid,
        "research_configuration_error": research_configuration_error,
        "research_universe": research_universe,
        "research_lookback_days": settings.research_lookback_days,
        "research_bar_adjustment": "all",
        "research_execution_enabled": False,
        "strategy_lab_lookback_days": (
            settings.strategy_lab_lookback_days
        ),
        "strategy_lab_base_cost_bps": format(
            settings.strategy_lab_base_cost_bps, "f"
        ),
        "strategy_lab_stressed_cost_bps": format(
            settings.strategy_lab_stressed_cost_bps, "f"
        ),
        "strategy_lab_execution_enabled": False,
        "strategy_experiment_program_path": str(
            settings.strategy_experiment_program_path
        ),
        "strategy_experiments_valid": (
            strategy_experiments_valid
        ),
        "strategy_experiments_error": (
            strategy_experiments_error
        ),
        "strategy_experiment_families": (
            strategy_experiment_families
        ),
        "strategy_experiment_candidate_count": (
            len(experiment_program.all_experiments)
            if strategy_experiments_valid
            else 0
        ),
        "strategy_experiment_execution_enabled": False,
        "strategy_lab_comparison_variants": (
            settings.strategy_lab_comparison_variants
        ),
        "asset_universe_config_path": str(
            settings.asset_universe_config_path
        ),
        "asset_universe_configuration_valid": (
            asset_universe_configuration_valid
        ),
        "asset_universe_configuration_error": (
            asset_universe_configuration_error
        ),
        "asset_universe_policies": universe_policies,
        "asset_universe_execution_enabled": False,
        "sec_user_agent_present": bool(settings.sec_user_agent),
        "enabled_accounts": enabled_accounts,
        "account_credentials": account_credentials,
        "multi_account_runtime": True,
        "firm_risk_enabled": settings.firm_risk_policy.enabled,
        "firm_max_total_open": format(
            settings.firm_risk_policy.max_total_open, "f"
        ),
        "firm_max_symbol_open": format(
            settings.firm_risk_policy.max_symbol_open, "f"
        ),
        "firm_max_strategy_open": format(
            settings.firm_risk_policy.max_strategy_open, "f"
        ),
        "firm_equity_max_age_seconds": (
            settings.firm_risk_policy.equity_max_age_seconds
        ),
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    return (
        0
        if (
            checks["dashboard_credentials_valid"]
            and checks["portfolio_configuration_valid"]
            and checks["research_configuration_valid"]
            and checks["asset_universe_configuration_valid"]
            and checks["strategy_experiments_valid"]
            and len(checks["enabled_accounts"]) >= 1
            and all(
                account["credentials_ready"]
                for account in checks["account_credentials"]
            )
        )
        else 1
    )


def _run(once: bool) -> int:
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    runtimes = []
    for plan in plans:
        key_id, secret_key, base_url = (
            settings.alpaca_credentials_for(
                plan.credential_env_prefix
            )
        )
        runtimes.append(
            (
                plan,
                AlpacaPaperBroker(
                    key_id=key_id,
                    secret_key=secret_key,
                    base_url=base_url,
                ),
            )
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
        results = []
        failures = []
        for plan, broker in runtimes:
            try:
                reconciliation = broker.reconcile()
                expected = plan.expected_broker_account_id
                observed = (
                    reconciliation.account.broker_account_id
                )
                if expected and observed != expected:
                    raise ValueError(
                        "Broker account identity mismatch for "
                        f"{plan.account_id}: expected {expected}, "
                        f"observed {observed or 'missing'}"
                    )
                snapshot = store.apply_account_equity_state(
                    plan.account_id,
                    reconciliation.account_snapshot(),
                    reconciliation.observed_at,
                )
                store.record_order_reconciliation(
                    plan.account_id, reconciliation
                )
                active_risk = store.active_risk(plan.account_id)
                summary = {
                    "account_status": reconciliation.account.status,
                    "equity": reconciliation.account.equity,
                    "buying_power": (
                        reconciliation.account.buying_power
                    ),
                    "gross_notional": (
                        reconciliation.account.gross_notional
                    ),
                    "positions_count": len(
                        reconciliation.positions
                    ),
                    "open_orders_count": len(
                        reconciliation.open_orders
                    ),
                    "market_open": (
                        reconciliation.market.is_open
                    ),
                    "request_ids": reconciliation.request_ids,
                    "reserved_active_risk": active_risk,
                }
                store.record_broker_state(
                    plan.account_id,
                    reconciliation.observed_at,
                    asdict(reconciliation),
                    summary,
                )
                results.append(
                    {
                        "account_id": plan.account_id,
                        "environment": "paper",
                        "equity": format(snapshot.equity, "f"),
                        "active_risk": format(active_risk, "f"),
                        "market_open": (
                            reconciliation.market.is_open
                        ),
                        "positions": len(
                            reconciliation.positions
                        ),
                        "open_orders": len(
                            reconciliation.open_orders
                        ),
                        "observed_at": (
                            reconciliation.observed_at.isoformat()
                        ),
                    }
                )
            except Exception as exc:
                failure = {
                    "account_id": plan.account_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                failures.append(failure)
                store.record_event(
                    "heartbeat_failed",
                    plan.account_id,
                    failure,
                )
        status = (
            "ok"
            if not failures
            else "degraded"
            if results
            else "error"
        )
        output = {
            "status": status,
            "environment": "paper",
            "accounts_configured": len(runtimes),
            "accounts_succeeded": len(results),
            "accounts_failed": len(failures),
            "accounts": results,
            "failures": failures,
        }
        print(
            json.dumps(output, sort_keys=True),
            file=sys.stderr if failures else sys.stdout,
            flush=True,
        )
        write_health(settings.health_path, status, output)
        if once and failures:
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


def _automate(once: bool) -> int:
    settings = Settings.from_env()
    supervisor = PaperAutomationSupervisor.from_settings(settings)
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = supervisor.run_cycle()
            print(
                json.dumps(
                    asdict(result),
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
            if once and result.accounts_failed:
                return 1
        except Exception as exc:
            write_health(
                settings.strategy_health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "component": "paper_automation",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.strategy_cycle_seconds):
            print(
                json.dumps(
                    {
                        "status": "stopped",
                        "component": "paper_automation",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _automation_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.strategy_health_path,
        settings.strategy_health_max_age_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _run_account_services_cycle(
    services,
    *,
    component: str,
    health_path,
) -> dict[str, Any]:
    results = []
    failures = []
    for service in services:
        try:
            results.append(asdict(service.run_cycle()))
        except Exception as exc:
            failure = {
                "account_id": service.account_plan.account_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            failures.append(failure)
            service.store.record_event(
                f"{component}_account_cycle_failed",
                service.account_plan.account_id,
                failure,
            )
    status = (
        "ok"
        if not failures
        else "degraded"
        if results
        else "error"
    )
    payload = {
        "status": status,
        "component": component,
        "accounts_configured": len(services),
        "accounts_succeeded": len(results),
        "accounts_failed": len(failures),
        "results": results,
        "failures": failures,
    }
    serializable_payload = json.loads(
        json.dumps(payload, default=_json_default)
    )
    write_health(health_path, status, serializable_payload)
    return serializable_payload


def _research(once: bool) -> int:
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    research_store = SqliteAuditStore(settings.db_path)
    research_program = load_research_program(
        settings.research_program_path
    )
    services = tuple(
        ContinuousResearchService.from_account_plan(
            settings,
            plan,
            store=research_store,
            program=research_program,
        )
        for plan in plans
    )
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = _run_account_services_cycle(
                services,
                component="continuous_research",
                health_path=settings.research_health_path,
            )
            print(
                json.dumps(
                    result,
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
            if once and result["accounts_failed"]:
                return 1
        except Exception as exc:
            write_health(
                settings.research_health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "component": "continuous_research",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.research_cycle_seconds):
            print(
                json.dumps(
                    {
                        "status": "stopped",
                        "component": "continuous_research",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _research_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.research_health_path,
        settings.research_health_max_age_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _strategy_lab(once: bool) -> int:
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    strategy_lab_store = SqliteAuditStore(settings.db_path)
    services = tuple(
        ContinuousStrategyLabService.from_account_plan(
            settings,
            plan,
            store=strategy_lab_store,
        )
        for plan in plans
    )
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = _run_account_services_cycle(
                services,
                component="continuous_strategy_lab",
                health_path=settings.strategy_lab_health_path,
            )
            print(
                json.dumps(
                    result,
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
            if once and result["accounts_failed"]:
                return 1
        except Exception as exc:
            write_health(
                settings.strategy_lab_health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "component": "strategy_lab",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.strategy_lab_cycle_seconds):
            print(
                json.dumps(
                    {
                        "status": "stopped",
                        "component": "strategy_lab",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _strategy_lab_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.strategy_lab_health_path,
        settings.strategy_lab_health_max_age_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _accelerated_validation(
    workers: int,
    *,
    optimize: bool = False,
    max_candidates: int = 48,
    force_all: bool = False,
    timeframes: tuple[str, ...] = (),
) -> int:
    if not 1 <= workers <= 8:
        raise ValueError(
            "Accelerated validation workers must be between 1 and 8"
        )
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    store = SqliteAuditStore(settings.db_path)
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    research_timeframes = timeframes or tuple(
        dict.fromkeys(plan.timeframe for plan in plans)
    )
    for timeframe in research_timeframes:
        timeframe_seconds(timeframe)
    research_plans = tuple(
        replace(plan, timeframe=timeframe)
        for plan in plans
        for timeframe in research_timeframes
    )
    for plan in research_plans:
        try:
            run = AcceleratedValidationService.from_account_plan(
                settings,
                plan,
                store=store,
                workers=workers,
            ).run(
                optimize=optimize,
                max_optimization_candidates=max_candidates,
                force_all=force_all,
            )
            runs.append(accelerated_validation_payload(run))
        except Exception as exc:
            failures.append(
                {
                    "account_id": plan.account_id,
                    "timeframe": plan.timeframe,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    payload = {
        "status": (
            "ok"
            if not failures
            else ("degraded" if runs else "error")
        ),
        "component": "accelerated_validation",
        "accounts_configured": len(plans),
        "research_runs_configured": len(research_plans),
        "accounts_succeeded": len(runs),
        "accounts_failed": len(failures),
        "workers_per_account": workers,
        "runs": runs,
        "failures": failures,
        "prospective_trial_count_incremented": False,
        "execution_enabled": False,
        "parameter_optimization_enabled": optimize,
        "timeframes": research_timeframes,
    }
    print(
        json.dumps(
            payload,
            default=_json_default,
            sort_keys=True,
        ),
        flush=True,
    )
    return 1 if failures else 0


def _option_evidence(once: bool) -> int:
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    evidence_store = SqliteAuditStore(settings.db_path)
    services = tuple(
        ContinuousOptionEvidenceService.from_account_plan(
            settings,
            plan,
            store=evidence_store,
        )
        for plan in plans
    )
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = _run_account_services_cycle(
                services,
                component="continuous_option_evidence",
                health_path=settings.option_evidence_health_path,
            )
            print(
                json.dumps(
                    result,
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
            if once and result["accounts_failed"]:
                return 1
        except Exception as exc:
            write_health(
                settings.option_evidence_health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "component": "option_evidence",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.option_evidence_cycle_seconds):
            print(
                json.dumps(
                    {
                        "status": "stopped",
                        "component": "option_evidence",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _option_evidence_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.option_evidence_health_path,
        settings.option_evidence_health_max_age_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _asset_universe(once: bool) -> int:
    settings = Settings.from_env()
    plans = tuple(
        plan
        for plan in load_account_plans(
            settings.portfolio_config_path
        )
        if plan.enabled
    )
    if not plans:
        raise ValueError(
            "At least one enabled Paper account plan is required"
        )
    universe_store = SqliteAuditStore(settings.db_path)
    universe_program = load_asset_universe_program(
        settings.asset_universe_config_path
    )
    services = tuple(
        ContinuousAssetUniverseService.from_account_plan(
            settings,
            plan,
            store=universe_store,
            program=universe_program,
        )
        for plan in plans
    )
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = _run_account_services_cycle(
                services,
                component="continuous_asset_universe",
                health_path=settings.asset_universe_health_path,
            )
            print(
                json.dumps(
                    result,
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
            if once and result["accounts_failed"]:
                return 1
        except Exception as exc:
            write_health(
                settings.asset_universe_health_path,
                "error",
                {"error_type": type(exc).__name__},
            )
            print(
                json.dumps(
                    {
                        "status": "error",
                        "component": "asset_universe",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return 1
        if once:
            return 0
        if stop_event.wait(settings.asset_universe_cycle_seconds):
            print(
                json.dumps(
                    {
                        "status": "stopped",
                        "component": "asset_universe",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0


def _asset_universe_healthcheck() -> int:
    settings = Settings.from_env()
    healthy, result = check_health(
        settings.asset_universe_health_path,
        settings.asset_universe_health_max_age_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


def _evidence_catalog() -> int:
    print(
        json.dumps(
            {
                "execution_policy": (
                    "Evidence admission does not authorize Paper or live "
                    "orders. Internal validation and explicit configuration "
                    "approval remain mandatory."
                ),
                "records": evidence_catalog(),
            },
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _research_backtest(
    symbol: str,
    start_value: str | None,
    end_value: str | None,
    cost_bps_value: str,
) -> int:
    settings = Settings.from_env()
    settings.require_alpaca_credentials()
    program = load_research_program(settings.research_program_path)
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("Research backtest symbol is required")
    try:
        cost_bps = Decimal(cost_bps_value)
    except Exception as exc:
        raise ValueError("Research cost bps must be a decimal") from exc
    end = _parse_boundary(end_value, datetime.now(timezone.utc))
    start = _parse_boundary(
        start_value, end - timedelta(days=1500)
    )
    symbols = tuple(
        dict.fromkeys((normalized_symbol, program.benchmark))
    )
    client = AlpacaMarketDataClient(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        feed=settings.market_data_feed,
    )
    fetched = client.fetch_stock_bars(
        symbols, "1Day", start, end, adjustment="all"
    )
    usable = {
        item: closed_bars(fetched.get(item, ()), now=end)
        for item in symbols
    }
    report = ResearchModelBacktester(
        EvidenceWeightedMarketModel(),
        config=ResearchBacktestConfig(one_way_cost_bps=cost_bps),
    ).run(
        symbol_bars=usable.get(normalized_symbol, ()),
        benchmark_bars=usable.get(program.benchmark, ()),
    )
    store = SqliteAuditStore(settings.db_path)
    store.record_market_bars(
        bar for rows in usable.values() for bar in rows
    )
    store.record_research_backtest(report)
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "model_id": report.model_id,
                "model_version": report.model_version,
                "symbol": report.symbol,
                "benchmark": report.benchmark,
                "metrics": asdict(report.metrics),
                "gates": report.gates,
                "warnings": report.warnings,
                "promotion_status": report.promotion_status,
                "execution_eligible": report.execution_eligible,
                "feed": settings.market_data_feed,
                "request_ids": client.request_ids,
            },
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _parse_boundary(value: str | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Backtest boundaries must be ISO-8601 dates or timestamps"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _store_backtest(store: SqliteAuditStore, result) -> None:
    store.record_backtest(
        run_id=result.run_id,
        strategy_id=result.strategy_id,
        strategy_version=result.strategy_version,
        symbol=result.symbol,
        timeframe=result.timeframe,
        started_at=result.started_at,
        completed_at=result.completed_at,
        config=asdict(result.config),
        metrics=asdict(result.metrics),
        trades=[asdict(trade) for trade in result.trades],
    )


def _backtest(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start_value: str | None,
    end_value: str | None,
    validate: bool,
) -> int:
    settings = Settings.from_env()
    settings.require_alpaca_credentials()
    strategies = default_equity_strategies()
    if strategy_id not in strategies:
        raise ValueError(
            "Unknown strategy. Available: "
            + ", ".join(sorted(strategies))
        )
    end = _parse_boundary(
        end_value, datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    start = _parse_boundary(
        start_value,
        end - timedelta(days=settings.market_lookback_days),
    )
    client = AlpacaMarketDataClient(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        feed=settings.market_data_feed,
    )
    fetched = client.fetch_stock_bars(
        [symbol.upper()], timeframe, start, end
    )
    bars = closed_bars(fetched.get(symbol.upper(), ()), now=end)
    if not bars:
        raise ValueError("No closed bars were returned for the backtest")
    store = SqliteAuditStore(settings.db_path)
    strategy = strategies[strategy_id]
    if validate:
        report = StrategyValidator(strategy).validate(bars)
        _store_backtest(store, report.in_sample)
        _store_backtest(store, report.out_of_sample)
        store.record_validation(
            validation_id=report.out_of_sample.run_id,
            strategy_id=report.strategy_id,
            strategy_version=report.strategy_version,
            symbol=report.out_of_sample.symbol,
            timeframe=report.out_of_sample.timeframe,
            passed=report.passed,
            gates=report.gates,
            warnings=report.warnings,
            in_sample_run_id=report.in_sample.run_id,
            out_of_sample_run_id=report.out_of_sample.run_id,
            completed_at=report.out_of_sample.completed_at,
        )
        output = {
            "mode": "walk_forward_validation",
            "strategy_id": report.strategy_id,
            "strategy_version": report.strategy_version,
            "passed": report.passed,
            "gates": report.gates,
            "warnings": report.warnings,
            "in_sample": {
                "run_id": report.in_sample.run_id,
                "metrics": asdict(report.in_sample.metrics),
            },
            "out_of_sample": {
                "run_id": report.out_of_sample.run_id,
                "metrics": asdict(report.out_of_sample.metrics),
            },
            "feed": settings.market_data_feed,
            "request_ids": client.request_ids,
        }
    else:
        result = Backtester(strategy).run(bars)
        _store_backtest(store, result)
        output = {
            "mode": "backtest",
            "run_id": result.run_id,
            "strategy_id": result.strategy_id,
            "strategy_version": result.strategy_version,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "metrics": asdict(result.metrics),
            "warnings": result.warnings,
            "feed": settings.market_data_feed,
            "request_ids": client.request_ids,
        }
    print(
        json.dumps(
            output,
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _dashboard() -> int:
    settings = Settings.from_env()
    settings.require_dashboard_credentials()
    data_service = DashboardData(
        db_path=settings.db_path,
        health_path=settings.health_path,
        health_max_age_seconds=settings.health_max_age_seconds,
        max_total_open=settings.risk_policy.max_total_open,
        max_per_trade=settings.risk_policy.max_per_trade,
        strategy_health_path=settings.strategy_health_path,
        strategy_health_max_age_seconds=(
            settings.strategy_health_max_age_seconds
        ),
        research_health_path=settings.research_health_path,
        research_health_max_age_seconds=(
            settings.research_health_max_age_seconds
        ),
        strategy_lab_health_path=settings.strategy_lab_health_path,
        strategy_lab_health_max_age_seconds=(
            settings.strategy_lab_health_max_age_seconds
        ),
        option_evidence_health_path=(
            settings.option_evidence_health_path
        ),
        option_evidence_health_max_age_seconds=(
            settings.option_evidence_health_max_age_seconds
        ),
        asset_universe_health_path=(
            settings.asset_universe_health_path
        ),
        asset_universe_health_max_age_seconds=(
            settings.asset_universe_health_max_age_seconds
        ),
        automation_enabled=settings.automation_enabled,
        paper_order_submission_enabled=settings.enable_paper_orders,
        emergency_stop=settings.emergency_stop,
        account_plans=load_account_plans(
            settings.portfolio_config_path
        ),
        asset_universe_program=load_asset_universe_program(
            settings.asset_universe_config_path
        ),
        strategy_experiment_program=(
            load_strategy_experiment_program(
                settings.strategy_experiment_program_path
            )
        ),
        firm_risk_policy=settings.firm_risk_policy,
    )
    server = create_dashboard_server(
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        data_service=data_service,
        username=settings.dashboard_username,
        password=settings.dashboard_password,
        analyst_api_enabled=settings.analyst_api_enabled,
        analyst_api_token=settings.analyst_api_token,
        analyst_requests_per_minute=(
            settings.analyst_api_requests_per_minute
        ),
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


def _option_scan(
    underlying: str, minimum_dte: int, maximum_dte: int
) -> int:
    if minimum_dte < 1 or maximum_dte < minimum_dte:
        raise ValueError("Option DTE range is invalid")
    settings = Settings.from_env()
    settings.require_alpaca_credentials()
    today = datetime.now(timezone.utc).date()
    client = AlpacaOptionChainClient(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        feed=settings.option_data_feed,
    )
    chain = client.fetch_chain(
        underlying,
        expiration_gte=today + timedelta(days=minimum_dte),
        expiration_lte=today + timedelta(days=maximum_dte),
    )
    liquidity = OptionLiquidityPolicy()
    liquid = tuple(item for item in chain if liquidity.accepts(item))
    sample = sorted(
        liquid,
        key=lambda item: (
            item.relative_spread
            if item.relative_spread is not None
            else Decimal("999"),
            item.expiration,
        ),
    )[:20]
    print(
        json.dumps(
            {
                "mode": "read_only_option_chain_scan",
                "underlying": underlying.upper(),
                "feed": settings.option_data_feed,
                "execution_allowed": False,
                "minimum_dte": minimum_dte,
                "maximum_dte": maximum_dte,
                "contracts_received": len(chain),
                "contracts_passing_basic_liquidity": len(liquid),
                "sample": [
                    {
                        "symbol": item.symbol,
                        "expiration": item.expiration,
                        "right": item.right,
                        "strike": item.strike,
                        "bid": item.bid,
                        "ask": item.ask,
                        "relative_spread": item.relative_spread,
                        "delta": item.delta,
                        "implied_volatility": (
                            item.implied_volatility
                        ),
                    }
                    for item in sample
                ],
                "request_ids": client.request_ids,
            },
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
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
    automate_parser = subparsers.add_parser(
        "automate",
        help="Run the strategy and guarded Alpaca Paper cycle",
    )
    automate_parser.add_argument(
        "--once", action="store_true", help="Run one strategy cycle and exit"
    )
    subparsers.add_parser(
        "automation-healthcheck",
        help="Check whether the latest strategy cycle is fresh",
    )
    research_parser = subparsers.add_parser(
        "research",
        help="Run the observation-only daily evidence model",
    )
    research_parser.add_argument(
        "--once", action="store_true", help="Run one research cycle and exit"
    )
    subparsers.add_parser(
        "research-healthcheck",
        help="Check whether the latest research cycle is fresh",
    )
    strategy_lab_parser = subparsers.add_parser(
        "strategy-lab",
        help=(
            "Continuously validate configured intraday models across "
            "their assigned research symbols"
        ),
    )
    strategy_lab_parser.add_argument(
        "--once",
        action="store_true",
        help="Run one Strategy Lab cycle and exit",
    )
    subparsers.add_parser(
        "strategy-lab-healthcheck",
        help="Check whether the latest Strategy Lab cycle is fresh",
    )
    accelerated_parser = subparsers.add_parser(
        "accelerated-validation",
        help=(
            "Screen every frozen baseline and comparison candidate in "
            "one non-executable historical cycle"
        ),
    )
    accelerated_parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Parallel candidate evaluations per account (1-8)",
    )
    accelerated_parser.add_argument(
        "--timeframes",
        default="",
        help=(
            "Comma-separated research timeframes, for example "
            "1Hour,4Hour,1Day. Each timeframe is stored as a separate run."
        ),
    )
    accelerated_parser.add_argument(
        "--optimize",
        action="store_true",
        help=(
            "Run a bounded nested parameter search after frozen-candidate "
            "screening; execution remains disabled"
        ),
    )
    accelerated_parser.add_argument(
        "--max-candidates",
        type=int,
        default=48,
        help="Maximum generated research candidates across all strategies",
    )
    accelerated_parser.add_argument(
        "--force-all",
        action="store_true",
        help=(
            "Re-evaluate unchanged rejected candidates instead of using "
            "incremental research selection"
        ),
    )
    option_evidence_parser = subparsers.add_parser(
        "option-evidence",
        help=(
            "Analyze frozen option packages with exact-contract "
            "historical trade-bar proxies"
        ),
    )
    option_evidence_parser.add_argument(
        "--once",
        action="store_true",
        help="Run one option-evidence cycle and exit",
    )
    subparsers.add_parser(
        "option-evidence-healthcheck",
        help="Check whether the latest option-evidence cycle is fresh",
    )
    universe_parser = subparsers.add_parser(
        "asset-universe",
        help=(
            "Build evidence-gated stock recommendations for research"
        ),
    )
    universe_parser.add_argument(
        "--once",
        action="store_true",
        help="Run one asset-universe cycle and exit",
    )
    subparsers.add_parser(
        "asset-universe-healthcheck",
        help="Check whether the latest asset-universe cycle is fresh",
    )
    subparsers.add_parser(
        "evidence-catalog",
        help="Print strategy evidence, caveats, and admission status",
    )
    research_backtest_parser = subparsers.add_parser(
        "research-backtest",
        help=(
            "Backtest the daily evidence model with next-open execution "
            "and benchmark comparison"
        ),
    )
    research_backtest_parser.add_argument("--symbol", required=True)
    research_backtest_parser.add_argument("--start")
    research_backtest_parser.add_argument("--end")
    research_backtest_parser.add_argument(
        "--cost-bps", default="10"
    )
    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Backtest or walk-forward validate a strategy",
    )
    backtest_parser.add_argument("--strategy", required=True)
    backtest_parser.add_argument("--symbol", required=True)
    backtest_parser.add_argument("--timeframe", default="5Min")
    backtest_parser.add_argument("--start")
    backtest_parser.add_argument("--end")
    backtest_parser.add_argument(
        "--validate",
        action="store_true",
        help="Run a chronological in/out-of-sample validation",
    )
    option_scan_parser = subparsers.add_parser(
        "option-scan",
        help="Inspect an Alpaca option chain without constructing an order",
    )
    option_scan_parser.add_argument("--underlying", required=True)
    option_scan_parser.add_argument("--minimum-dte", type=int, default=21)
    option_scan_parser.add_argument("--maximum-dte", type=int, default=60)
    subparsers.add_parser(
        "dashboard",
        help=(
            "Run the authenticated operations dashboard with audited "
            "Paper-only strategy controls"
        ),
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
        if args.command == "automate":
            return _automate(args.once)
        if args.command == "automation-healthcheck":
            return _automation_healthcheck()
        if args.command == "research":
            return _research(args.once)
        if args.command == "research-healthcheck":
            return _research_healthcheck()
        if args.command == "strategy-lab":
            return _strategy_lab(args.once)
        if args.command == "strategy-lab-healthcheck":
            return _strategy_lab_healthcheck()
        if args.command == "accelerated-validation":
            return _accelerated_validation(
                args.workers,
                optimize=args.optimize,
                max_candidates=args.max_candidates,
                force_all=args.force_all,
                timeframes=tuple(
                    dict.fromkeys(
                        item.strip()
                        for item in args.timeframes.split(",")
                        if item.strip()
                    )
                ),
            )
        if args.command == "option-evidence":
            return _option_evidence(args.once)
        if args.command == "option-evidence-healthcheck":
            return _option_evidence_healthcheck()
        if args.command == "asset-universe":
            return _asset_universe(args.once)
        if args.command == "asset-universe-healthcheck":
            return _asset_universe_healthcheck()
        if args.command == "evidence-catalog":
            return _evidence_catalog()
        if args.command == "research-backtest":
            return _research_backtest(
                args.symbol,
                args.start,
                args.end,
                args.cost_bps,
            )
        if args.command == "backtest":
            return _backtest(
                args.strategy,
                args.symbol,
                args.timeframe,
                args.start,
                args.end,
                args.validate,
            )
        if args.command == "option-scan":
            return _option_scan(
                args.underlying,
                args.minimum_dte,
                args.maximum_dte,
            )
        if args.command == "dashboard":
            return _dashboard()
        if args.command == "dashboard-healthcheck":
            return _dashboard_healthcheck()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2
    return 2
