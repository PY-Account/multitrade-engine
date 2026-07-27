from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.automation import PaperAutomationService
from multitrade.brokers.base import (
    BrokerAccount,
    BrokerMarketClock,
    BrokerReconciliation,
)
from multitrade.config import Settings
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan, StrategyAllocation


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
