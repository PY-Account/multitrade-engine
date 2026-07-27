from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from multitrade.audit import SqliteAuditStore
from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.brokers.base import BrokerReconciliation
from multitrade.config import Settings
from multitrade.engine import TradingEngine
from multitrade.features import FeatureEngine, MarketRegime
from multitrade.health import write_health
from multitrade.market import (
    AlpacaMarketDataClient,
    MarketBar,
    closed_bars,
    timeframe_seconds,
)
from multitrade.portfolio import (
    AccountPlan,
    SignalAllocator,
    load_account_plans,
)
from multitrade.risk import RiskEngine
from multitrade.strategies import default_equity_strategies
from multitrade.strategies.base import StrategyContext, StrategySignal


@dataclass(frozen=True, slots=True)
class AutomationCycleResult:
    status: str
    account_id: str
    market_open: bool
    symbols_evaluated: int
    strategies_evaluated: int
    signals_generated: int
    signals_new: int
    signals_observed: int
    signals_blocked: int
    dry_runs: int
    orders_submitted: int
    bars_ingested: int
    execution_enabled: bool
    reasons: tuple[str, ...]


class PaperAutomationService:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: AlpacaPaperBroker,
        market_data: AlpacaMarketDataClient,
        store: SqliteAuditStore,
        account_plan: AccountPlan,
    ) -> None:
        if account_plan.environment != "paper":
            raise ValueError("Automation service accepts only Paper plans")
        if account_plan.broker != "alpaca":
            raise ValueError(
                "Only the Alpaca Paper account is implemented"
            )
        self.settings = settings
        self.broker = broker
        self.market_data = market_data
        self.store = store
        self.account_plan = account_plan
        self.feature_engine = FeatureEngine()
        self.strategies = default_equity_strategies()
        unknown_strategies = (
            set(account_plan.allocations) - set(self.strategies)
        )
        if unknown_strategies:
            raise ValueError(
                "Unknown strategies in account plan: "
                + ", ".join(sorted(unknown_strategies))
            )
        self.allocator = SignalAllocator()
        self.trading_engine = TradingEngine(
            broker=broker,
            risk_engine=RiskEngine(settings.risk_policy),
            audit_store=store,
            enable_order_submission=settings.paper_execution_enabled,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "PaperAutomationService":
        settings.require_alpaca_credentials()
        plans = load_account_plans(settings.portfolio_config_path)
        enabled_plans = tuple(plan for plan in plans if plan.enabled)
        if len(enabled_plans) != 1:
            raise ValueError(
                "The current runtime requires exactly one enabled "
                "Paper account plan"
            )
        broker = AlpacaPaperBroker(
            settings.alpaca_key_id,
            settings.alpaca_secret_key,
            base_url=settings.alpaca_base_url,
        )
        market_data = AlpacaMarketDataClient(
            settings.alpaca_key_id,
            settings.alpaca_secret_key,
            feed=settings.market_data_feed,
        )
        return cls(
            settings=settings,
            broker=broker,
            market_data=market_data,
            store=SqliteAuditStore(settings.db_path),
            account_plan=enabled_plans[0],
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> AutomationCycleResult:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        reconciliation = self.broker.reconcile()
        snapshot = self.store.apply_account_equity_state(
            self.account_plan.account_id,
            reconciliation.account_snapshot(),
            reconciliation.observed_at,
        )
        self.store.record_order_reconciliation(
            self.account_plan.account_id, reconciliation
        )
        active_risk = self.store.active_risk()
        self.store.record_broker_state(
            self.account_plan.account_id,
            reconciliation.observed_at,
            asdict(reconciliation),
            {
                "source": "strategy_cycle",
                "account_status": reconciliation.account.status,
                "equity": reconciliation.account.equity,
                "positions_count": len(reconciliation.positions),
                "open_orders_count": len(reconciliation.open_orders),
                "market_open": reconciliation.market.is_open,
                "reserved_active_risk": active_risk,
                "request_ids": reconciliation.request_ids,
            },
        )

        start = checked_at - timedelta(
            days=self.settings.market_lookback_days
        )
        fetched = self.market_data.fetch_stock_bars(
            self.account_plan.watchlist,
            self.account_plan.timeframe,
            start,
            checked_at,
        )
        bars_ingested = self.store.record_market_bars(
            bar
            for symbol_bars in fetched.values()
            for bar in symbol_bars
        )

        counters = {
            "symbols_evaluated": 0,
            "strategies_evaluated": 0,
            "signals_generated": 0,
            "signals_new": 0,
            "signals_observed": 0,
            "signals_blocked": 0,
            "dry_runs": 0,
            "orders_submitted": 0,
        }
        cycle_reasons: set[str] = set()
        for symbol in self.account_plan.watchlist:
            usable_bars = closed_bars(
                fetched.get(symbol, ()), now=checked_at
            )
            if not usable_bars:
                cycle_reasons.add(f"no_closed_bars:{symbol}")
                self._record_all_runtime(
                    symbol,
                    "insufficient_data",
                    None,
                    {"reason": "no_closed_bars"},
                    checked_at,
                )
                continue
            counters["symbols_evaluated"] += 1
            features = self.feature_engine.calculate(usable_bars)
            if features.regime is MarketRegime.INSUFFICIENT_DATA:
                cycle_reasons.add(f"insufficient_data:{symbol}")
                self._record_all_runtime(
                    symbol,
                    "insufficient_data",
                    usable_bars[-1].timestamp.isoformat(),
                    {
                        "sample_size": features.sample_size,
                        "minimum_bars": self.feature_engine.minimum_bars,
                    },
                    checked_at,
                )
                continue

            for strategy_id, allocation in (
                self.account_plan.allocations.items()
            ):
                if not allocation.enabled:
                    continue
                counters["strategies_evaluated"] += 1
                strategy = self.strategies[strategy_id]
                context = StrategyContext(
                    account_id=self.account_plan.account_id,
                    bars=usable_bars,
                    features=features,
                    evaluated_at=checked_at,
                )
                signal = strategy.evaluate(context)
                runtime_details = {
                    "regime": features.regime,
                    "close": features.close,
                    "atr": features.atr,
                    "relative_volume": features.relative_volume,
                    "sample_size": features.sample_size,
                }
                self.store.record_strategy_runtime(
                    self.account_plan.account_id,
                    strategy_id,
                    symbol,
                    "signal" if signal else "no_signal",
                    usable_bars[-1].timestamp.isoformat(),
                    runtime_details,
                    checked_at,
                )
                if signal is None:
                    continue
                counters["signals_generated"] += 1
                if not self.store.record_signal(signal):
                    continue
                counters["signals_new"] += 1
                block_reason = self._signal_block_reason(
                    signal,
                    usable_bars[-1],
                    features.regime,
                    reconciliation,
                    checked_at,
                )
                if block_reason is not None:
                    counters["signals_blocked"] += 1
                    cycle_reasons.add(block_reason)
                    self.store.update_signal_status(
                        signal.signal_id,
                        "blocked",
                        {"reason": block_reason},
                    )
                    continue

                intent = self.allocator.allocate(
                    signal, allocation, snapshot
                )
                if intent is None:
                    counters["signals_blocked"] += 1
                    cycle_reasons.add("allocation_filter")
                    self.store.update_signal_status(
                        signal.signal_id,
                        "filtered",
                        {
                            "reason": "allocation_or_confidence_filter",
                            "minimum_confidence": (
                                allocation.minimum_confidence
                            ),
                        },
                    )
                    continue

                if not self.settings.automation_enabled:
                    counters["signals_observed"] += 1
                    self.store.update_signal_status(
                        signal.signal_id,
                        "observed",
                        {
                            "reason": "automation_disabled",
                            "would_risk_fraction": (
                                allocation.risk_fraction
                            ),
                        },
                    )
                    continue

                result = self.trading_engine.process(
                    intent,
                    snapshot=snapshot,
                    allow_submission=(
                        allocation.paper_execution_allowed
                    ),
                )
                self.store.record_trade_result(signal, intent, result)
                if not result.decision.approved:
                    counters["signals_blocked"] += 1
                    self.store.update_signal_status(
                        signal.signal_id,
                        "risk_rejected",
                        {"reason": result.decision.reason},
                    )
                elif result.dry_run:
                    counters["dry_runs"] += 1
                    self.store.update_signal_status(
                        signal.signal_id,
                        "risk_approved_dry_run",
                        {
                            "approved_quantity": (
                                result.decision.approved_quantity
                            ),
                            "reserved_risk": (
                                result.decision.reserved_risk
                            ),
                            "reason": (
                                "strategy_paper_execution_not_approved"
                                if not allocation.paper_execution_allowed
                                else "paper_order_submission_disabled"
                            ),
                        },
                    )
                else:
                    counters["orders_submitted"] += 1
                    self.store.update_signal_status(
                        signal.signal_id,
                        "paper_order_submitted",
                        {
                            "broker_order_id": (
                                result.order.broker_order_id
                                if result.order
                                else None
                            ),
                            "approved_quantity": (
                                result.decision.approved_quantity
                            ),
                            "reserved_risk": (
                                result.decision.reserved_risk
                            ),
                        },
                    )

        result = AutomationCycleResult(
            status="ok",
            account_id=self.account_plan.account_id,
            market_open=reconciliation.market.is_open,
            bars_ingested=bars_ingested,
            execution_enabled=self.settings.paper_execution_enabled,
            reasons=tuple(sorted(cycle_reasons)),
            **counters,
        )
        write_health(
            self.settings.strategy_health_path,
            "ok",
            asdict(result),
        )
        self.store.record_event(
            "strategy_cycle_completed",
            self.account_plan.account_id,
            asdict(result),
        )
        return result

    def _record_all_runtime(
        self,
        symbol: str,
        state: str,
        bar_timestamp: str | None,
        details: dict[str, Any],
        evaluated_at: datetime,
    ) -> None:
        for strategy_id, allocation in (
            self.account_plan.allocations.items()
        ):
            if allocation.enabled:
                self.store.record_strategy_runtime(
                    self.account_plan.account_id,
                    strategy_id,
                    symbol,
                    state,
                    bar_timestamp,
                    details,
                    evaluated_at,
                )

    def _signal_block_reason(
        self,
        signal: StrategySignal,
        latest_bar: MarketBar,
        regime: MarketRegime,
        reconciliation: BrokerReconciliation,
        checked_at: datetime,
    ) -> str | None:
        account = reconciliation.account
        if not reconciliation.market.is_open:
            return "market_closed"
        if account.status != "active":
            return f"account_status_{account.status}"
        if (
            account.trading_blocked
            or account.account_blocked
            or account.trade_suspended_by_user
        ):
            return "account_trading_blocked"
        if regime is MarketRegime.HIGH_VOLATILITY:
            return "high_volatility_regime"
        if signal.expires_at <= checked_at:
            return "signal_expired"
        bar_closed_at = latest_bar.timestamp + timedelta(
            seconds=timeframe_seconds(latest_bar.timeframe)
        )
        bar_age = (checked_at - bar_closed_at).total_seconds()
        if bar_age < -5:
            return "bar_not_closed"
        if bar_age > self.settings.market_max_bar_age_seconds:
            return "market_data_stale"
        position_symbols = {
            position.symbol for position in reconciliation.positions
        }
        managed_order_ids, managed_symbols = (
            self.store.active_reservation_identity()
        )
        if position_symbols - managed_symbols:
            return "unmanaged_broker_position_present"
        if any(
            order.client_order_id not in managed_order_ids
            for order in reconciliation.open_orders
        ):
            return "unmanaged_broker_order_present"
        if signal.symbol in position_symbols:
            return "symbol_position_already_open"
        order_symbols = {
            order.symbol for order in reconciliation.open_orders
        }
        if signal.symbol in order_symbols:
            return "symbol_order_already_open"
        if (
            len(reconciliation.positions)
            >= self.account_plan.maximum_positions
        ):
            return "maximum_positions_reached"
        day_start = checked_at.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if (
            self.store.submitted_orders_since(
                self.account_plan.account_id, day_start
            )
            >= self.account_plan.maximum_daily_orders
        ):
            return "maximum_daily_orders_reached"
        last_submitted = self.store.last_submitted_at(
            self.account_plan.account_id, signal.symbol
        )
        if (
            last_submitted is not None
            and checked_at - last_submitted
            < timedelta(
                minutes=self.account_plan.symbol_cooldown_minutes
            )
        ):
            return "symbol_cooldown_active"
        return None
