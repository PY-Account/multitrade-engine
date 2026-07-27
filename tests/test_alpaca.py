from datetime import date
from decimal import Decimal
from unittest import TestCase

from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.domain import (
    AssetClass,
    OptionLeg,
    OptionRight,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)


class AlpacaPayloadTests(TestCase):
    def test_live_endpoint_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            AlpacaPaperBroker(
                "paper-key",
                "paper-secret",
                base_url="https://api.alpaca.markets",
            )

    def test_multileg_payload_uses_one_parent_order(self) -> None:
        expiration = date(2027, 1, 15)
        intent = TradeIntent(
            strategy_id="spread",
            asset_class=AssetClass.OPTION,
            symbol="AAPL",
            side=Side.BUY,
            requested_quantity=Decimal("2"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=Decimal("1.25"),
            option_legs=(
                OptionLeg(
                    symbol="AAPL270115P00100000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.PUT,
                    strike=Decimal("100"),
                    side=Side.BUY,
                    ratio=1,
                    mark_price=Decimal("2"),
                ),
                OptionLeg(
                    symbol="AAPL270115P00110000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.PUT,
                    strike=Decimal("110"),
                    side=Side.SELL,
                    ratio=1,
                    mark_price=Decimal("3.25"),
                ),
            ),
        )

        payload = AlpacaPaperBroker.build_order_payload(
            intent, Decimal("2")
        )

        self.assertEqual(payload["order_class"], "mleg")
        self.assertEqual(payload["qty"], "2")
        self.assertEqual(payload["limit_price"], "1.25")
        self.assertEqual(len(payload["legs"]), 2)
        self.assertNotIn("symbol", payload)

    def test_single_leg_option_uses_simple_order(self) -> None:
        expiration = date(2027, 1, 15)
        intent = TradeIntent(
            strategy_id="long-call",
            asset_class=AssetClass.OPTION,
            symbol="AAPL",
            side=Side.BUY,
            requested_quantity=Decimal("1"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=Decimal("2.10"),
            option_legs=(
                OptionLeg(
                    symbol="AAPL270115C00150000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.CALL,
                    strike=Decimal("150"),
                    side=Side.BUY,
                    ratio=1,
                    mark_price=Decimal("2.10"),
                ),
            ),
        )

        payload = AlpacaPaperBroker.build_order_payload(
            intent, Decimal("1")
        )

        self.assertEqual(payload["symbol"], "AAPL270115C00150000")
        self.assertEqual(payload["side"], "buy")
        self.assertNotIn("order_class", payload)
        self.assertNotIn("legs", payload)
