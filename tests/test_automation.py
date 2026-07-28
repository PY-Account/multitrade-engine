import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.automation import (
    PaperAutomationService,
    PaperAutomationSupervisor,
)
from multitrade.brokers.base import (
    BrokerAccount,
    BrokerMarketClock,
    BrokerOrder,
    BrokerReconciliation,
)
from multitrade.config import Settings
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.options import OptionSnapshot
from multitrade.portfolio import AccountPlan, StrategyAllocation
from multitrade.options import OptionExecutionPolicy, OptionStructure
from multitrade.domain import OptionRight


def test_bars(now: datetime) -> tuple[MarketBar, ...]:
    start = now - timedelta(minutes=5 * 40)
    bars = [
        MarketBar(
            symbol="AAPL",
            asset_class=AssetClass.STOCK,
            timeframe="5Min",
            timestamp=start + timedelta(minutes=5 * index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("99.8")
            + Decimal(index) / Decimal("100"),
            volume=Decimal("100"),
            trade_count=10,
            vwap=Decimal("100"),
            feed="iex",
        )
        for index in range(38)
    ]
    bars.extend(
        (
            MarketBar(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                timeframe="5Min",
                timestamp=start + timedelta(minutes=5 * 38),
                open=Decimal("100.8"),
                high=Decimal("103"),
                low=Decimal("100.5"),
                close=Decimal("102"),
                volume=Decimal("200"),
                trade_count=20,
                vwap=Decimal("102"),
                feed="iex",
            ),
            MarketBar(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                timeframe="5Min",
                timestamp=start + timedelta(minutes=5 * 39),
                open=Decimal("101.5"),
                high=Decimal("103.5"),
                low=Decimal("100.9"),
                close=Decimal("102.5"),
                volume=Decimal("140"),
                trade_count=15,
                vwap=Decimal("102.4"),
                feed="iex",
            ),
        )
    )
    return tuple(bars)


class FakeBroker:
    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.submit_calls = 0

    def reconcile(self) -> BrokerReconciliation:
        return BrokerReconciliation(
            broker="alpaca",
            environment="paper",
            observed_at=self.observed_at,
            account=BrokerAccount(
                status="active",
                currency="USD",
                equity=Decimal("10000"),
                last_equity=Decimal("10000"),
                cash=Decimal("10000"),
                buying_power=Decimal("20000"),
                long_market_value=Decimal("0"),
                short_market_value=Decimal("0"),
                maintenance_margin=Decimal("0"),
                gross_notional=Decimal("0"),
                daytrade_count=0,
                pattern_day_trader=False,
                trading_blocked=False,
                transfers_blocked=False,
                account_blocked=False,
                trade_suspended_by_user=False,
                shorting_enabled=True,
            ),
            market=BrokerMarketClock(
                timestamp=self.observed_at.isoformat(),
                is_open=True,
                next_open=self.observed_at.isoformat(),
                next_close=(
                    self.observed_at + timedelta(hours=4)
                ).isoformat(),
            ),
            positions=(),
            open_orders=(),
        )

    def get_account_snapshot(self):
        return self.reconcile().account_snapshot()

    def submit_order(self, intent, approved_quantity):
        del intent, approved_quantity
        self.submit_calls += 1
        raise AssertionError("Observed signals must not submit orders")


class FakeMarketData:
    def __init__(self, bars: tuple[MarketBar, ...]) -> None:
        self.bars = bars
        self.request_ids = ["market-request-id"]

    def fetch_stock_bars(self, symbols, timeframe, start, end):
        del symbols, timeframe, start, end
        return {"AAPL": self.bars}


class OptionFakeBroker(FakeBroker):
    def __init__(self, observed_at: datetime) -> None:
        super().__init__(observed_at)
        self.submitted = []

    def reconcile(self) -> BrokerReconciliation:
        result = super().reconcile()
        return replace(
            result,
            account=replace(
                result.account,
                options_buying_power=Decimal("10000"),
                options_approved_level=3,
                options_trading_level=3,
            ),
        )

    def submit_order(self, intent, approved_quantity):
        self.submit_calls += 1
        self.submitted.append((intent, approved_quantity))
        return BrokerOrder(
            broker_order_id="paper-option-order",
            status="accepted",
            raw={},
        )


class FailingBroker(FakeBroker):
    def reconcile(self) -> BrokerReconciliation:
        raise RuntimeError("account connection failed")


class FakeOptionData:
    feed = "opra"

    def __init__(self, now: datetime) -> None:
        self.expiration = (now + timedelta(days=40)).date()

    def fetch_chain(self, underlying, **kwargs):
        del kwargs
        return (
            OptionSnapshot(
                symbol=(
                    f"AAPL{self.expiration:%y%m%d}C00100000"
                ),
                underlying=underlying,
                expiration=self.expiration,
                right=OptionRight.CALL,
                strike=Decimal("100"),
                bid=Decimal("0.45"),
                ask=Decimal("0.50"),
                bid_size=10,
                ask_size=10,
                implied_volatility=Decimal("0.25"),
                delta=Decimal("0.55"),
                quote_timestamp="2026-01-05T17:59:00Z",
                feed=self.feed,
                theta=Decimal("-0.08"),
            ),
            OptionSnapshot(
                symbol=(
                    f"AAPL{self.expiration:%y%m%d}C00105000"
                ),
                underlying=underlying,
                expiration=self.expiration,
                right=OptionRight.CALL,
                strike=Decimal("105"),
                bid=Decimal("0.12"),
                ask=Decimal("0.14"),
                bid_size=10,
                ask_size=10,
                implied_volatility=Decimal("0.25"),
                delta=Decimal("0.30"),
                quote_timestamp="2026-01-05T17:59:00Z",
                feed=self.feed,
                theta=Decimal("-0.05"),
            ),
        )


class AutomationTests(TestCase):
    def test_signal_observation_is_idempotent_and_never_submits(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            now = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
            settings = replace(
                Settings.from_env(),
                automation_enabled=False,
                enable_paper_orders=False,
                db_path=Path(directory) / "trading.db",
                strategy_health_path=Path(directory) / "strategy-health.json",
                market_max_bar_age_seconds=900,
            )
            plan = AccountPlan(
                account_id="alpaca-paper",
                broker="alpaca",
                environment="paper",
                enabled=True,
                asset_classes=(AssetClass.STOCK,),
                watchlist=("AAPL",),
                timeframe="5Min",
                maximum_positions=4,
                maximum_daily_orders=6,
                symbol_cooldown_minutes=60,
                allocations={
                    "breakout_retest": StrategyAllocation(
                        strategy_id="breakout_retest",
                        enabled=True,
                        capital_weight=Decimal("0.20"),
                        risk_fraction=Decimal("0.005"),
                        minimum_confidence=Decimal("0.60"),
                    )
                },
            )
            broker = FakeBroker(now)
            store = SqliteAuditStore(settings.db_path)
            service = PaperAutomationService(
                settings=settings,
                broker=broker,
                market_data=FakeMarketData(test_bars(now)),
                store=store,
                account_plan=plan,
            )

            first = service.run_cycle(now=now)
            second = service.run_cycle(now=now)
            signals = SqliteAuditReader(
                settings.db_path
            ).recent_signals()

            self.assertEqual(first.signals_new, 1)
            self.assertEqual(first.signals_observed, 1)
            self.assertEqual(second.signals_new, 0)
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0]["status"], "observed")
            self.assertEqual(broker.submit_calls, 0)

    def test_defined_risk_option_allocation_can_reach_paper_broker(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            now = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
            settings = replace(
                Settings.from_env(),
                automation_enabled=True,
                enable_paper_orders=True,
                emergency_stop=False,
                option_data_feed="opra",
                db_path=Path(directory) / "trading.db",
                strategy_health_path=(
                    Path(directory) / "strategy-health.json"
                ),
                market_max_bar_age_seconds=900,
            )
            allocation = StrategyAllocation(
                strategy_id="breakout_retest_bull_call",
                enabled=True,
                capital_weight=Decimal("0.10"),
                risk_fraction=Decimal("0.005"),
                minimum_confidence=Decimal("0.60"),
                paper_execution_allowed=True,
                symbols=("AAPL",),
                asset_class=AssetClass.OPTION,
                option_policy=OptionExecutionPolicy(
                    structure=OptionStructure.BULL_CALL_DEBIT,
                    source_strategy_id="breakout_retest",
                    maximum_strike_width=Decimal("10"),
                ),
            )
            plan = AccountPlan(
                account_id="alpaca-paper",
                broker="alpaca",
                environment="paper",
                enabled=True,
                asset_classes=(
                    AssetClass.STOCK,
                    AssetClass.OPTION,
                ),
                watchlist=("AAPL",),
                timeframe="5Min",
                maximum_positions=4,
                maximum_daily_orders=6,
                symbol_cooldown_minutes=60,
                allocations={allocation.strategy_id: allocation},
            )
            broker = OptionFakeBroker(now)
            store = SqliteAuditStore(settings.db_path)
            service = PaperAutomationService(
                settings=settings,
                broker=broker,
                market_data=FakeMarketData(test_bars(now)),
                option_data=FakeOptionData(now),
                store=store,
                account_plan=plan,
            )

            result = service.run_cycle(now=now)
            trades = SqliteAuditReader(
                settings.db_path
            ).recent_trade_records()
            observations = SqliteAuditReader(
                settings.db_path
            ).recent_option_observations(
                account_id="alpaca-paper"
            )

            self.assertEqual(result.orders_submitted, 1)
            self.assertEqual(broker.submit_calls, 1)
            self.assertEqual(
                broker.submitted[0][0].asset_class,
                AssetClass.OPTION,
            )
            self.assertEqual(
                broker.submitted[0][0].explanation["structure"],
                "bull_call_debit_spread",
            )
            self.assertEqual(trades[0]["asset_class"], "option")
            self.assertEqual(
                trades[0]["strategy_id"],
                "breakout_retest_bull_call",
            )
            self.assertEqual(len(observations), 1)
            self.assertEqual(
                observations[0]["status"],
                "selected_for_risk_review",
            )
            self.assertEqual(len(observations[0]["legs"]), 2)
            self.assertFalse(observations[0]["execution_proof"])

    def test_multi_account_supervisor_isolates_account_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            now = datetime(
                2026, 1, 5, 18, 0, tzinfo=timezone.utc
            )
            settings = replace(
                Settings.from_env(),
                automation_enabled=False,
                enable_paper_orders=False,
                db_path=Path(directory) / "trading.db",
                strategy_health_path=(
                    Path(directory) / "strategy-health.json"
                ),
                market_max_bar_age_seconds=900,
            )

            def plan(account_id: str) -> AccountPlan:
                allocation = StrategyAllocation(
                    strategy_id="breakout_retest",
                    enabled=True,
                    capital_weight=Decimal("0.20"),
                    risk_fraction=Decimal("0.005"),
                    minimum_confidence=Decimal("0.60"),
                )
                return AccountPlan(
                    account_id=account_id,
                    broker="alpaca",
                    environment="paper",
                    enabled=True,
                    asset_classes=(AssetClass.STOCK,),
                    watchlist=("AAPL",),
                    timeframe="5Min",
                    maximum_positions=4,
                    maximum_daily_orders=6,
                    symbol_cooldown_minutes=60,
                    allocations={
                        allocation.strategy_id: allocation
                    },
                )

            store = SqliteAuditStore(settings.db_path)
            successful = PaperAutomationService(
                settings=settings,
                broker=FakeBroker(now),
                market_data=FakeMarketData(test_bars(now)),
                store=store,
                account_plan=plan("paper-a"),
            )
            failing = PaperAutomationService(
                settings=settings,
                broker=FailingBroker(now),
                market_data=FakeMarketData(test_bars(now)),
                store=store,
                account_plan=plan("paper-b"),
            )
            result = PaperAutomationSupervisor(
                settings=settings,
                services=(successful, failing),
                store=store,
            ).run_cycle(now=now)
            health = json.loads(
                settings.strategy_health_path.read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(result.status, "degraded")
            self.assertEqual(result.accounts_succeeded, 1)
            self.assertEqual(result.accounts_failed, 1)
            self.assertEqual(
                result.failures[0].account_id, "paper-b"
            )
            self.assertEqual(health["status"], "degraded")
            self.assertEqual(
                health["details"]["accounts_configured"], 2
            )

    def test_expected_broker_identity_mismatch_fails_closed(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            now = datetime(
                2026, 1, 5, 18, 0, tzinfo=timezone.utc
            )
            settings = replace(
                Settings.from_env(),
                db_path=Path(directory) / "trading.db",
                strategy_health_path=(
                    Path(directory) / "strategy-health.json"
                ),
            )
            plan = AccountPlan(
                account_id="paper-a",
                broker="alpaca",
                environment="paper",
                enabled=True,
                asset_classes=(AssetClass.STOCK,),
                watchlist=("AAPL",),
                timeframe="5Min",
                maximum_positions=4,
                maximum_daily_orders=6,
                symbol_cooldown_minutes=60,
                allocations={},
                expected_broker_account_id="expected-account",
            )
            service = PaperAutomationService(
                settings=settings,
                broker=FakeBroker(now),
                market_data=FakeMarketData(test_bars(now)),
                store=SqliteAuditStore(settings.db_path),
                account_plan=plan,
            )

            with self.assertRaisesRegex(
                ValueError, "identity mismatch"
            ):
                service.run_cycle(now=now)
