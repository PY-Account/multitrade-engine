from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.domain import AccountSnapshot, AssetClass
from multitrade.features import FeatureEngine
from multitrade.market import MarketBar
from multitrade.portfolio import SignalAllocator, load_account_plans
from multitrade.strategies.base import StrategyContext
from multitrade.strategies.equity import BreakoutRetestStrategy


def market_bar(
    index: int,
    *,
    open_price: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
    volume: str = "100",
) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        timeframe="5Min",
        timestamp=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        + timedelta(minutes=index * 5),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        trade_count=10,
        vwap=Decimal(close),
        feed="iex",
    )


class StrategyTests(TestCase):
    def test_breakout_retest_signal_is_deterministic_and_explainable(
        self,
    ) -> None:
        bars = [
            market_bar(
                index,
                close=str(
                    Decimal("99.8")
                    + Decimal(index) / Decimal("100")
                ),
            )
            for index in range(38)
        ]
        bars.extend(
            (
                market_bar(
                    38,
                    open_price="100.8",
                    high="103",
                    low="100.5",
                    close="102",
                    volume="200",
                ),
                market_bar(
                    39,
                    open_price="101.5",
                    high="103.5",
                    low="100.9",
                    close="102.5",
                    volume="140",
                ),
            )
        )
        ordered = tuple(bars)
        evaluated_at = ordered[-1].timestamp + timedelta(minutes=5)
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=ordered,
            features=FeatureEngine().calculate(ordered),
            evaluated_at=evaluated_at,
        )
        strategy = BreakoutRetestStrategy()

        first = strategy.evaluate(context)
        second = strategy.evaluate(context)

        self.assertIsNotNone(first)
        self.assertEqual(first.signal_id, second.signal_id)
        self.assertIn(
            "retest_held_resistance", first.reason_codes
        )
        self.assertLess(first.stop_price, first.reference_price)
        self.assertGreater(first.target_price, first.reference_price)

    def test_signal_allocation_builds_protected_bracket_order(self) -> None:
        config_path = (
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )
        plan = load_account_plans(config_path)[0]
        allocation = plan.allocations["breakout_retest"]
        option_allocation = plan.allocations[
            "trend_pullback_bull_put_theta"
        ]
        self.assertEqual(
            option_allocation.source_strategy_id,
            "trend_pullback",
        )
        self.assertEqual(
            option_allocation.option_policy.required_trading_level, 3
        )
        self.assertFalse(
            option_allocation.paper_execution_allowed
        )
        bars = [
            market_bar(
                index,
                close=str(
                    Decimal("99.8")
                    + Decimal(index) / Decimal("100")
                ),
            )
            for index in range(38)
        ]
        bars.extend(
            (
                market_bar(
                    38,
                    open_price="100.8",
                    high="103",
                    low="100.5",
                    close="102",
                    volume="200",
                ),
                market_bar(
                    39,
                    open_price="101.5",
                    high="103.5",
                    low="100.9",
                    close="102.5",
                    volume="140",
                ),
            )
        )
        ordered = tuple(bars)
        context = StrategyContext(
            account_id=plan.account_id,
            bars=ordered,
            features=FeatureEngine().calculate(ordered),
            evaluated_at=ordered[-1].timestamp + timedelta(minutes=5),
        )
        signal = BreakoutRetestStrategy().evaluate(context)
        snapshot = AccountSnapshot(
            equity=Decimal("10000"),
            start_of_day_equity=Decimal("10000"),
            peak_equity=Decimal("10000"),
        )

        intent = SignalAllocator().allocate(
            signal, allocation, snapshot
        )
        payload = AlpacaPaperBroker.build_order_payload(
            intent, Decimal("2")
        )

        self.assertEqual(intent.risk_budget_fraction, Decimal("0.005"))
        self.assertEqual(payload["order_class"], "bracket")
        self.assertIn("take_profit", payload)
        self.assertIn("stop_loss", payload)
