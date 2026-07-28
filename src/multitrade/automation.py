from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from multitrade.audit import SqliteAuditStore
from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.brokers.base import BrokerReconciliation
from multitrade.config import Settings
from multitrade.engine import TradingEngine
from multitrade.domain import OptionLeg, OptionRight, Side
from multitrade.features import FeatureEngine, MarketRegime
from multitrade.health import write_health
from multitrade.market import (
    AlpacaMarketDataClient,
    MarketBar,
    closed_bars,
    timeframe_seconds,
)
from multitrade.options import (
    AlpacaOptionChainClient,
    DefinedRiskOptionFactory,
    DefinedRiskOptionSelector,
    OptionDataError,
    OptionStructure,
)
from multitrade.portfolio import (
    AccountPlan,
    SignalAllocator,
    load_account_plans,
)
from multitrade.risk import RiskEngine
from multitrade.strategies import default_equity_strategies
from multitrade.strategies.base import StrategyContext, StrategySignal
from multitrade.strategies.base import SignalAction


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
    option_exits_submitted: int
    bars_ingested: int
    execution_enabled: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccountCycleFailure:
    account_id: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class AutomationPortfolioCycleResult:
    status: str
    accounts_configured: int
    accounts_succeeded: int
    accounts_failed: int
    results: tuple[AutomationCycleResult, ...]
    failures: tuple[AccountCycleFailure, ...]


class PaperAutomationService:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: AlpacaPaperBroker,
        market_data: AlpacaMarketDataClient,
        store: SqliteAuditStore,
        account_plan: AccountPlan,
        option_data: AlpacaOptionChainClient | None = None,
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
        self.option_data = option_data
        self.feature_engine = FeatureEngine()
        self.strategies = default_equity_strategies()
        unknown_strategies = (
            {
                allocation.source_strategy_id
                for allocation in account_plan.allocations.values()
            }
            - set(self.strategies)
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
            enable_reduce_only_submission=(
                settings.enable_paper_orders
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "PaperAutomationService":
        plans = load_account_plans(settings.portfolio_config_path)
        enabled_plans = tuple(plan for plan in plans if plan.enabled)
        if len(enabled_plans) != 1:
            raise ValueError(
                "PaperAutomationService.from_settings requires exactly "
                "one enabled Paper account; use "
                "PaperAutomationSupervisor for multiple accounts"
            )
        return cls.from_account_plan(
            settings, enabled_plans[0]
        )

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
    ) -> "PaperAutomationService":
        key_id, secret_key, base_url = (
            settings.alpaca_credentials_for(
                account_plan.credential_env_prefix
            )
        )
        broker = AlpacaPaperBroker(
            key_id,
            secret_key,
            base_url=base_url,
        )
        market_data = AlpacaMarketDataClient(
            key_id,
            secret_key,
            feed=settings.market_data_feed,
        )
        option_data = AlpacaOptionChainClient(
            key_id,
            secret_key,
            feed=settings.option_data_feed,
        )
        return cls(
            settings=settings,
            broker=broker,
            market_data=market_data,
            store=store or SqliteAuditStore(settings.db_path),
            account_plan=account_plan,
            option_data=option_data,
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> AutomationCycleResult:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        reconciliation = self.broker.reconcile()
        self._validate_broker_identity(reconciliation)
        snapshot = self.store.apply_account_equity_state(
            self.account_plan.account_id,
            reconciliation.account_snapshot(),
            reconciliation.observed_at,
        )
        self.store.record_order_reconciliation(
            self.account_plan.account_id, reconciliation
        )
        active_risk = self.store.active_risk(
            self.account_plan.account_id
        )
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
        option_exits_submitted = self._manage_option_positions(
            reconciliation,
            snapshot,
            checked_at,
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
            "option_exits_submitted": option_exits_submitted,
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
                if (
                    allocation.symbols
                    and symbol not in allocation.symbols
                ):
                    continue
                counters["strategies_evaluated"] += 1
                strategy = self.strategies[
                    allocation.source_strategy_id
                ]
                context = StrategyContext(
                    account_id=self.account_plan.account_id,
                    bars=usable_bars,
                    features=features,
                    evaluated_at=checked_at,
                )
                source_signal = strategy.evaluate(context)
                signal = (
                    self._allocation_signal(
                        source_signal, allocation
                    )
                    if source_signal is not None
                    else None
                )
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
                    allocation,
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

                try:
                    intent = self._allocate_intent(
                        signal,
                        allocation,
                        snapshot,
                        checked_at,
                    )
                except (OptionDataError, ValueError) as exc:
                    counters["signals_blocked"] += 1
                    reason = (
                        "option_package_unavailable:"
                        f"{type(exc).__name__}"
                    )
                    cycle_reasons.add(reason)
                    if allocation.asset_class.value == "option":
                        self.store.record_option_observation(
                            signal,
                            None,
                            status="construction_rejected",
                            details={
                                "reason": reason,
                                "message": str(exc),
                                "structure": (
                                    allocation.option_policy
                                    .structure.value
                                    if allocation.option_policy is not None
                                    else None
                                ),
                            },
                        )
                    self.store.update_signal_status(
                        signal.signal_id,
                        "filtered",
                        {
                            "reason": reason,
                            "message": str(exc),
                        },
                    )
                    continue
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

                if intent.asset_class.value == "option":
                    self.store.record_option_observation(
                        signal,
                        intent,
                        status=(
                            "selected_for_risk_review"
                            if self.settings.automation_enabled
                            else "selected_observation_only"
                        ),
                        details={
                            "paper_execution_configured": (
                                allocation.paper_execution_allowed
                            ),
                            "automation_enabled": (
                                self.settings.automation_enabled
                            ),
                        },
                    )
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

    def _validate_broker_identity(
        self, reconciliation: BrokerReconciliation
    ) -> None:
        expected = self.account_plan.expected_broker_account_id
        observed = reconciliation.account.broker_account_id
        if expected and observed != expected:
            raise ValueError(
                "Broker account identity mismatch for "
                f"{self.account_plan.account_id}: expected {expected}, "
                f"observed {observed or 'missing'}"
            )

    def _manage_option_positions(
        self,
        reconciliation: BrokerReconciliation,
        snapshot,
        checked_at: datetime,
    ) -> int:
        if (
            not reconciliation.market.is_open
            or self.option_data is None
        ):
            return 0
        submitted = 0
        chain_cache: dict[
            tuple[str, str], tuple
        ] = {}
        for trade in self.store.open_option_trades(
            self.account_plan.account_id
        ):
            if trade["state"] not in {
                "broker_filled",
                "position_open",
                "position_close_pending",
            }:
                continue
            explanation = trade["explanation"]
            expiration_text = explanation.get("expiration")
            if not expiration_text:
                self.store.record_event(
                    "option_exit_evaluation_failed",
                    trade["intent_id"],
                    {"reason": "expiration_missing"},
                )
                continue
            expiration = date.fromisoformat(
                str(expiration_text)
            )
            cache_key = (
                trade["symbol"],
                expiration.isoformat(),
            )
            try:
                chain = chain_cache.get(cache_key)
                if chain is None:
                    chain = self.option_data.fetch_chain(
                        trade["symbol"],
                        expiration_gte=expiration,
                        expiration_lte=expiration,
                    )
                    chain_cache[cache_key] = chain
                snapshots = {
                    contract.symbol: contract
                    for contract in chain
                }
                opening_legs = tuple(
                    self._option_leg_from_payload(payload)
                    for payload in trade["option_legs"]
                )
                self._validate_option_quote_freshness(
                    tuple(
                        snapshots[leg.symbol]
                        for leg in opening_legs
                        if leg.symbol in snapshots
                    ),
                    checked_at,
                    int(
                        explanation.get(
                            "maximum_quote_age_seconds", 120
                        )
                    ),
                )
                factory = DefinedRiskOptionFactory()
                candidate = factory.close_package(
                    account_id=self.account_plan.account_id,
                    strategy_id=trade["strategy_id"],
                    parent_intent_id=trade["intent_id"],
                    opening_legs=opening_legs,
                    snapshots=snapshots,
                    quantity=Decimal(
                        trade["approved_quantity"]
                    ),
                    reason="policy_evaluation",
                )
                if trade["opening_net_price"] is None:
                    raise ValueError("opening_net_price_missing")
                opening_price = Decimal(
                    str(trade["opening_net_price"])
                )
                closing_price = candidate.limit_price
                if closing_price is None:
                    raise ValueError("closing_price_missing")
                quantity = Decimal(
                    trade["approved_quantity"]
                )
                estimated_pnl = -(
                    opening_price + closing_price
                ) * Decimal("100") * quantity
                premium_basis = (
                    abs(opening_price)
                    * Decimal("100")
                    * quantity
                )
                days_to_expiration = (
                    expiration - checked_at.date()
                ).days
                profit_target = Decimal(
                    str(
                        explanation.get(
                            "profit_target_fraction", "0.50"
                        )
                    )
                )
                loss_multiple = Decimal(
                    str(
                        explanation.get(
                            "loss_limit_multiple", "1.50"
                        )
                    )
                )
                exit_days = int(
                    explanation.get(
                        "exit_before_expiry_days", 7
                    )
                )
                reason = None
                if days_to_expiration <= exit_days:
                    reason = "expiration_window"
                elif estimated_pnl >= (
                    premium_basis * profit_target
                ):
                    reason = "profit_target"
                elif estimated_pnl <= -(
                    premium_basis * loss_multiple
                ):
                    reason = "loss_limit"
                self.store.record_event(
                    "option_exit_evaluated",
                    trade["intent_id"],
                    {
                        "days_to_expiration": days_to_expiration,
                        "estimated_pnl": estimated_pnl,
                        "profit_target_amount": (
                            premium_basis * profit_target
                        ),
                        "loss_limit_amount": (
                            premium_basis * loss_multiple
                        ),
                        "exit_reason": reason,
                        "closing_net_price": closing_price,
                        "theta_attribution": (
                            "not_used_as_realized_profit"
                        ),
                    },
                )
                if reason is None:
                    continue
                exit_intent = factory.close_package(
                    account_id=self.account_plan.account_id,
                    strategy_id=trade["strategy_id"],
                    parent_intent_id=trade["intent_id"],
                    opening_legs=opening_legs,
                    snapshots=snapshots,
                    quantity=quantity,
                    reason=reason,
                )
                result = self.trading_engine.process(
                    exit_intent,
                    snapshot=snapshot,
                    allow_submission=True,
                )
                if not result.decision.approved:
                    self.store.record_event(
                        "option_exit_risk_rejected",
                        trade["intent_id"],
                        {"reason": result.decision.reason},
                    )
                elif not result.dry_run:
                    submitted += 1
            except (
                ArithmeticError,
                OptionDataError,
                ValueError,
            ) as exc:
                self.store.record_event(
                    "option_exit_evaluation_failed",
                    trade["intent_id"],
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
        return submitted

    @staticmethod
    def _option_leg_from_payload(
        payload: dict[str, Any]
    ) -> OptionLeg:
        return OptionLeg(
            symbol=str(payload["symbol"]),
            underlying=str(payload["underlying"]),
            expiration=date.fromisoformat(
                str(payload["expiration"])
            ),
            right=OptionRight(payload["right"]),
            strike=Decimal(str(payload["strike"])),
            side=Side(payload["side"]),
            ratio=int(payload["ratio"]),
            mark_price=Decimal(str(payload["mark_price"])),
            multiplier=int(payload.get("multiplier", 100)),
            delta=(
                Decimal(str(payload["delta"]))
                if payload.get("delta") is not None
                else None
            ),
            gamma=(
                Decimal(str(payload["gamma"]))
                if payload.get("gamma") is not None
                else None
            ),
            theta=(
                Decimal(str(payload["theta"]))
                if payload.get("theta") is not None
                else None
            ),
            vega=(
                Decimal(str(payload["vega"]))
                if payload.get("vega") is not None
                else None
            ),
            implied_volatility=(
                Decimal(str(payload["implied_volatility"]))
                if payload.get("implied_volatility") is not None
                else None
            ),
        )

    @staticmethod
    def _allocation_signal(
        signal: StrategySignal,
        allocation,
    ) -> StrategySignal:
        if (
            signal.strategy_id == allocation.strategy_id
            and allocation.asset_class.value == "stock"
        ):
            return signal
        identity = (
            f"{signal.signal_id}|{allocation.strategy_id}|"
            f"{allocation.asset_class.value}"
        )
        signal_id = (
            "mt-"
            + hashlib.sha256(identity.encode()).hexdigest()[:32]
        )
        return replace(
            signal,
            signal_id=signal_id,
            strategy_id=allocation.strategy_id,
            strategy_version=(
                f"{signal.strategy_version}+"
                f"{allocation.asset_class.value}"
            ),
            reason_codes=(
                *signal.reason_codes,
                f"source_strategy:{signal.strategy_id}",
                f"execution_vehicle:{allocation.asset_class.value}",
            ),
            evidence={
                **signal.evidence,
                "source_strategy_id": signal.strategy_id,
                "allocation_strategy_id": allocation.strategy_id,
                "execution_asset_class": allocation.asset_class.value,
            },
        )

    def _allocate_intent(
        self,
        signal: StrategySignal,
        allocation,
        snapshot,
        checked_at: datetime,
    ):
        if allocation.asset_class.value == "stock":
            return self.allocator.allocate(
                signal, allocation, snapshot
            )
        if signal.confidence < allocation.minimum_confidence:
            return None
        if allocation.option_policy is None:
            raise ValueError("option_policy_missing")
        if self.option_data is None:
            raise ValueError("option_data_client_missing")
        expiration_gte = (
            checked_at.date()
            + timedelta(days=allocation.option_policy.minimum_dte)
        )
        expiration_lte = (
            checked_at.date()
            + timedelta(days=allocation.option_policy.maximum_dte)
        )
        chain = self.option_data.fetch_chain(
            signal.symbol,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
        )
        structure = allocation.option_policy.structure
        if structure is OptionStructure.IRON_CONDOR:
            direction = "neutral"
        elif structure is OptionStructure.PROTECTIVE_PUT:
            direction = "hedge"
        else:
            direction = (
                "bullish"
                if signal.action is SignalAction.ENTER_LONG
                else "bearish"
            )
        requested_quantity = Decimal("1000000")
        if structure is OptionStructure.PROTECTIVE_PUT:
            requested_quantity = (
                max(
                    Decimal("0"),
                    snapshot.positions.get(
                        signal.symbol, Decimal("0")
                    ),
                )
                / Decimal("100")
            ).to_integral_value(rounding=ROUND_DOWN)
            if requested_quantity <= Decimal("0"):
                return None
        intent = DefinedRiskOptionSelector(
            allocation.option_policy
        ).build_intent(
            account_id=self.account_plan.account_id,
            strategy_id=allocation.strategy_id,
            underlying=signal.symbol,
            underlying_price=signal.reference_price,
            direction=direction,
            chain=chain,
            requested_quantity=requested_quantity,
            risk_budget_fraction=allocation.risk_fraction,
            signal_id=signal.signal_id,
            as_of=checked_at.date(),
        )
        if allocation.paper_execution_allowed:
            selected_symbols = {
                leg.symbol for leg in intent.option_legs
            }
            self._validate_option_quote_freshness(
                tuple(
                    contract
                    for contract in chain
                    if contract.symbol in selected_symbols
                ),
                checked_at,
                allocation.option_policy.maximum_quote_age_seconds,
            )
        per_package_risk = (
            self.trading_engine.risk_engine.estimate_risk_per_unit(
                intent
            )
        )
        if per_package_risk <= Decimal("0"):
            raise ValueError("option_package_risk_not_positive")
        capital_capacity = (
            snapshot.equity * allocation.capital_weight
        )
        capital_limited_quantity = (
            capital_capacity / per_package_risk
        ).to_integral_value(rounding=ROUND_DOWN)
        quantity = min(
            intent.requested_quantity,
            capital_limited_quantity,
        )
        if quantity <= Decimal("0"):
            return None
        return replace(
            intent,
            requested_quantity=quantity,
            explanation={
                **intent.explanation,
                "capital_weight": allocation.capital_weight,
                "capital_risk_capacity": capital_capacity,
                "estimated_risk_per_package": per_package_risk,
            },
        )

    @staticmethod
    def _validate_option_quote_freshness(
        chain: tuple,
        checked_at: datetime,
        maximum_age_seconds: int,
    ) -> None:
        if not chain:
            raise ValueError("option_chain_empty")
        now = checked_at.astimezone(timezone.utc)
        for contract in chain:
            timestamp_text = str(
                contract.quote_timestamp or ""
            ).replace("Z", "+00:00")
            if not timestamp_text:
                raise ValueError(
                    f"option_quote_timestamp_missing:{contract.symbol}"
                )
            quote_time = datetime.fromisoformat(timestamp_text)
            if quote_time.tzinfo is None:
                raise ValueError(
                    f"option_quote_timezone_missing:{contract.symbol}"
                )
            quote_time = quote_time.astimezone(timezone.utc)
            age = (now - quote_time).total_seconds()
            if age < -30 or age > maximum_age_seconds:
                raise ValueError(
                    f"option_quote_stale:{contract.symbol}:{age:.0f}s"
                )

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
            if (
                allocation.enabled
                and (
                    not allocation.symbols
                    or symbol in allocation.symbols
                )
            ):
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
        allocation,
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
        if allocation.asset_class.value == "option":
            if self.option_data is None:
                return "option_data_not_configured"
            required_level = (
                allocation.option_policy.required_trading_level
                if allocation.option_policy is not None
                else 3
            )
            if account.options_trading_level < required_level:
                return (
                    "options_trading_level_"
                    f"{account.options_trading_level}_below_"
                    f"required_{required_level}"
                )
            if (
                allocation.paper_execution_allowed
                and self.option_data.feed != "opra"
            ):
                return "option_execution_requires_opra_feed"
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
        protective_put = (
            allocation.option_policy is not None
            and allocation.option_policy.structure
            is OptionStructure.PROTECTIVE_PUT
        )
        if protective_put:
            if not any(
                position.asset_class.value == "stock"
                and position.symbol == signal.symbol
                and position.side == "long"
                and position.quantity >= Decimal("100")
                for position in reconciliation.positions
            ):
                return "protective_put_requires_100_long_shares"
            if any(
                position.asset_class.value == "option"
                and position.symbol.startswith(signal.symbol)
                for position in reconciliation.positions
            ):
                return "protective_option_position_already_open"
        managed_order_ids, managed_symbols = (
            self.store.active_reservation_identity(
                self.account_plan.account_id
            )
        )
        if position_symbols - managed_symbols:
            return "unmanaged_broker_position_present"
        if any(
            order.client_order_id not in managed_order_ids
            for order in reconciliation.open_orders
        ):
            return "unmanaged_broker_order_present"
        if not protective_put and (
            signal.symbol in position_symbols
            or signal.symbol in managed_symbols
        ):
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


class PaperAutomationSupervisor:
    """Run account-isolated Paper cycles inside one supervised process."""

    def __init__(
        self,
        *,
        settings: Settings,
        services: tuple[PaperAutomationService, ...],
        store: SqliteAuditStore,
    ) -> None:
        if not services:
            raise ValueError(
                "At least one enabled Paper account is required"
            )
        account_ids = [
            service.account_plan.account_id for service in services
        ]
        if len(set(account_ids)) != len(account_ids):
            raise ValueError(
                "Automation services must have unique account IDs"
            )
        self.settings = settings
        self.services = services
        self.store = store

    @classmethod
    def from_settings(
        cls, settings: Settings
    ) -> "PaperAutomationSupervisor":
        plans = load_account_plans(settings.portfolio_config_path)
        enabled_plans = tuple(plan for plan in plans if plan.enabled)
        if not enabled_plans:
            raise ValueError(
                "At least one enabled Paper account plan is required"
            )
        store = SqliteAuditStore(settings.db_path)
        services = tuple(
            PaperAutomationService.from_account_plan(
                settings,
                plan,
                store=store,
            )
            for plan in enabled_plans
        )
        return cls(
            settings=settings,
            services=services,
            store=store,
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> AutomationPortfolioCycleResult:
        results: list[AutomationCycleResult] = []
        failures: list[AccountCycleFailure] = []
        for service in self.services:
            try:
                results.append(service.run_cycle(now=now))
            except Exception as exc:
                failure = AccountCycleFailure(
                    account_id=service.account_plan.account_id,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                failures.append(failure)
                self.store.record_event(
                    "strategy_account_cycle_failed",
                    service.account_plan.account_id,
                    asdict(failure),
                )
        if not failures:
            status = "ok"
        elif results:
            status = "degraded"
        else:
            status = "error"
        aggregate = AutomationPortfolioCycleResult(
            status=status,
            accounts_configured=len(self.services),
            accounts_succeeded=len(results),
            accounts_failed=len(failures),
            results=tuple(results),
            failures=tuple(failures),
        )
        write_health(
            self.settings.strategy_health_path,
            status,
            asdict(aggregate),
        )
        self.store.record_event(
            "strategy_portfolio_cycle_completed",
            "paper-portfolio",
            asdict(aggregate),
        )
        return aggregate
