from datetime import date
from decimal import Decimal
from unittest import TestCase

from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OptionLeg,
    OptionRight,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.risk import RiskEngine, RiskPolicy


def snapshot(
    *,
    equity: str = "100000",
    start: str = "100000",
    peak: str = "100000",
    active_risk: str = "0",
) -> AccountSnapshot:
    return AccountSnapshot(
        equity=Decimal(equity),
        start_of_day_equity=Decimal(start),
        peak_equity=Decimal(peak),
        active_risk=Decimal(active_risk),
    )


class RiskEngineTests(TestCase):
    def test_stock_quantity_is_capped_by_three_percent(self) -> None:
        policy = RiskPolicy(max_notional_per_trade=Decimal("1"))
        engine = RiskEngine(policy)
        intent = TradeIntent(
            strategy_id="stock-test",
            asset_class=AssetClass.STOCK,
            symbol="AAPL",
            side=Side.BUY,
            requested_quantity=Decimal("1000"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            reference_price=Decimal("100"),
            stop_price=Decimal("95"),
            limit_price=Decimal("100"),
        )

        decision = engine.evaluate(intent, snapshot())

        self.assertTrue(decision.approved)
        self.assertLessEqual(decision.reserved_risk, Decimal("3000"))
        self.assertEqual(decision.risk_per_unit, Decimal("5.25"))
        self.assertEqual(decision.approved_quantity, Decimal("571"))

    def test_total_open_risk_is_a_hard_ceiling(self) -> None:
        engine = RiskEngine(
            RiskPolicy(max_notional_per_trade=Decimal("1"))
        )
        intent = TradeIntent(
            strategy_id="stock-test",
            asset_class=AssetClass.STOCK,
            symbol="MSFT",
            side=Side.BUY,
            requested_quantity=Decimal("100"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            reference_price=Decimal("100"),
            stop_price=Decimal("95"),
            limit_price=Decimal("100"),
        )

        decision = engine.evaluate(
            intent, snapshot(active_risk="10000")
        )

        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason, "portfolio_risk_budget_exhausted"
        )

    def test_daily_loss_kill_switch(self) -> None:
        intent = TradeIntent(
            strategy_id="stock-test",
            asset_class=AssetClass.STOCK,
            symbol="AAPL",
            side=Side.BUY,
            requested_quantity=Decimal("1"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            reference_price=Decimal("100"),
            stop_price=Decimal("90"),
        )

        decision = RiskEngine().evaluate(
            intent, snapshot(equity="96000", start="100000")
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "daily_loss_kill_switch")

    def test_defined_risk_call_spread_is_sized(self) -> None:
        expiration = date(2027, 1, 15)
        intent = TradeIntent(
            strategy_id="vertical-spread",
            asset_class=AssetClass.OPTION,
            symbol="AAPL",
            side=Side.BUY,
            requested_quantity=Decimal("20"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=Decimal("3"),
            option_legs=(
                OptionLeg(
                    symbol="AAPL270115C00100000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.CALL,
                    strike=Decimal("100"),
                    side=Side.BUY,
                    ratio=1,
                    mark_price=Decimal("5"),
                ),
                OptionLeg(
                    symbol="AAPL270115C00110000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.CALL,
                    strike=Decimal("110"),
                    side=Side.SELL,
                    ratio=1,
                    mark_price=Decimal("2"),
                ),
            ),
        )

        decision = RiskEngine().evaluate(intent, snapshot())

        self.assertTrue(decision.approved)
        self.assertEqual(decision.risk_per_unit, Decimal("305"))
        self.assertEqual(decision.approved_quantity, Decimal("9"))
        self.assertEqual(decision.reserved_risk, Decimal("2745"))

    def test_unlimited_short_call_is_rejected(self) -> None:
        expiration = date(2027, 1, 15)
        intent = TradeIntent(
            strategy_id="unsafe-option",
            asset_class=AssetClass.OPTION,
            symbol="AAPL",
            side=Side.SELL,
            requested_quantity=Decimal("1"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=Decimal("2"),
            option_legs=(
                OptionLeg(
                    symbol="AAPL270115C00110000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.CALL,
                    strike=Decimal("110"),
                    side=Side.SELL,
                    ratio=1,
                    mark_price=Decimal("2"),
                ),
            ),
        )

        decision = RiskEngine().evaluate(intent, snapshot())

        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason, "unlimited_option_loss_is_rejected"
        )

    def test_crypto_short_is_rejected(self) -> None:
        intent = TradeIntent(
            strategy_id="crypto-test",
            asset_class=AssetClass.CRYPTO,
            symbol="BTC/USD",
            side=Side.SELL,
            requested_quantity=Decimal("0.1"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            reference_price=Decimal("100000"),
            stop_price=Decimal("105000"),
            limit_price=Decimal("100000"),
        )

        decision = RiskEngine().evaluate(intent, snapshot())

        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason, "alpaca_crypto_shorting_is_not_supported"
        )
