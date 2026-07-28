from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from multitrade.audit import SqliteAuditStore
from multitrade.automation import PaperAutomationService
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
from multitrade.health import check_health, write_health
from multitrade.market import AlpacaMarketDataClient, closed_bars
from multitrade.options import (
    AlpacaOptionChainClient,
    OptionLiquidityPolicy,
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
from multitrade.strategies import default_equity_strategies


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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        portfolio_configuration_valid = False
        portfolio_configuration_error = str(exc)
        enabled_accounts = []
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
        "dashboard_credentials_valid": dashboard_credentials_valid,
        "dashboard_configuration_error": dashboard_configuration_error,
        "dashboard_listen": (
            f"{settings.dashboard_host}:{settings.dashboard_port}"
        ),
        "market_data_feed": settings.market_data_feed,
        "option_data_feed": settings.option_data_feed,
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
        "enabled_accounts": enabled_accounts,
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    return (
        0
        if (
            checks["api_key_present"]
            and checks["api_secret_present"]
            and checks["dashboard_credentials_valid"]
            and checks["portfolio_configuration_valid"]
            and checks["research_configuration_valid"]
            and len(checks["enabled_accounts"]) == 1
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
            snapshot = store.apply_account_equity_state(
                "alpaca-paper",
                reconciliation.account_snapshot(),
                reconciliation.observed_at,
            )
            store.record_order_reconciliation(
                "alpaca-paper", reconciliation
            )
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


def _automate(once: bool) -> int:
    settings = Settings.from_env()
    service = PaperAutomationService.from_settings(settings)
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = service.run_cycle()
            print(
                json.dumps(
                    asdict(result),
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:
            try:
                service.store.record_event(
                    "strategy_cycle_failed",
                    service.account_plan.account_id,
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            finally:
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


def _research(once: bool) -> int:
    settings = Settings.from_env()
    service = ContinuousResearchService.from_settings(settings)
    stop_event = threading.Event()

    def request_shutdown(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)

    while True:
        try:
            result = service.run_cycle()
            print(
                json.dumps(
                    asdict(result),
                    default=_json_default,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:
            try:
                service.store.record_event(
                    "research_cycle_failed",
                    service.account_plan.account_id,
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            finally:
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
        automation_enabled=settings.automation_enabled,
        paper_order_submission_enabled=settings.enable_paper_orders,
        emergency_stop=settings.emergency_stop,
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
        if args.command == "automate":
            return _automate(args.once)
        if args.command == "automation-healthcheck":
            return _automation_healthcheck()
        if args.command == "research":
            return _research(args.once)
        if args.command == "research-healthcheck":
            return _research_healthcheck()
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
