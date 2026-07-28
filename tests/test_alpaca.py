from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, call

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

    def test_active_stock_catalog_is_read_only_and_normalized(
        self,
    ) -> None:
        broker = AlpacaPaperBroker("paper-key", "paper-secret")
        broker._request = Mock(
            return_value=[
                {
                    "symbol": "aapl",
                    "status": "active",
                    "class": "us_equity",
                    "tradable": True,
                    "exchange": "NASDAQ",
                }
            ]
        )

        result = broker.list_active_stock_assets()

        self.assertIn("AAPL", result)
        broker._request.assert_called_once_with(
            "GET",
            "/v2/assets",
            query={
                "status": "active",
                "asset_class": "us_equity",
            },
        )

    def test_reconciliation_normalizes_read_only_broker_state(self) -> None:
        broker = AlpacaPaperBroker("paper-key", "paper-secret")
        broker._request = Mock(
            side_effect=[
                {
                    "id": "paper-account-uuid",
                    "status": "ACTIVE",
                    "currency": "usd",
                    "equity": "10000",
                    "last_equity": "9900",
                    "cash": "5000",
                    "buying_power": "20000",
                    "long_market_value": "1000",
                    "short_market_value": "-500",
                    "maintenance_margin": "250",
                    "daytrade_count": 1,
                    "pattern_day_trader": False,
                    "trading_blocked": False,
                    "transfers_blocked": False,
                    "account_blocked": False,
                    "trade_suspended_by_user": False,
                    "shorting_enabled": True,
                },
                [
                    {
                        "symbol": "AAPL",
                        "asset_class": "us_equity",
                        "side": "long",
                        "qty": "5",
                        "market_value": "1000",
                        "cost_basis": "950",
                        "avg_entry_price": "190",
                        "current_price": "200",
                        "unrealized_pl": "50",
                        "unrealized_plpc": "0.05263158",
                    },
                    {
                        "symbol": "TSLA",
                        "asset_class": "us_equity",
                        "side": "short",
                        "qty": "-2",
                        "market_value": "-500",
                        "cost_basis": "-520",
                        "avg_entry_price": "260",
                        "current_price": "250",
                        "unrealized_pl": "20",
                        "unrealized_plpc": "0.03846154",
                    },
                ],
                [
                    {
                        "id": "order-1",
                        "client_order_id": "intent-1",
                        "symbol": "MSFT",
                        "asset_class": "us_equity",
                        "side": "buy",
                        "type": "limit",
                        "order_class": "simple",
                        "status": "new",
                        "qty": "2",
                        "filled_qty": "0",
                        "limit_price": "410",
                        "stop_price": None,
                        "submitted_at": "2026-07-27T14:30:00Z",
                    }
                ],
                {
                    "timestamp": "2026-07-27T14:31:00Z",
                    "is_open": False,
                    "next_open": "2026-07-28T13:30:00Z",
                    "next_close": "2026-07-28T20:00:00Z",
                },
            ]
        )

        reconciliation = broker.reconcile()
        snapshot = reconciliation.account_snapshot()

        self.assertEqual(reconciliation.environment, "paper")
        self.assertEqual(reconciliation.account.status, "active")
        self.assertEqual(
            reconciliation.account.broker_account_id,
            "paper-account-uuid",
        )
        self.assertEqual(
            reconciliation.account.gross_notional, Decimal("1500")
        )
        self.assertEqual(len(reconciliation.positions), 2)
        self.assertEqual(len(reconciliation.open_orders), 1)
        self.assertFalse(reconciliation.market.is_open)
        self.assertEqual(snapshot.positions["AAPL"], Decimal("5"))
        self.assertEqual(snapshot.positions["TSLA"], Decimal("-2"))
        self.assertEqual(
            broker._request.call_args_list,
            [
                call("GET", "/v2/account"),
                call("GET", "/v2/positions"),
                call(
                    "GET",
                    "/v2/orders",
                    query={
                        "status": "all",
                        "limit": "500",
                        "nested": "true",
                        "direction": "desc",
                    },
                ),
                call("GET", "/v2/clock"),
            ],
        )

    def test_reconciliation_rejects_malformed_positions(self) -> None:
        broker = AlpacaPaperBroker("paper-key", "paper-secret")
        broker._request = Mock(
            side_effect=[
                {"equity": "10000"},
                {"unexpected": "object"},
                [],
                {},
            ]
        )

        with self.assertRaisesRegex(
            RuntimeError, "positions response was not a list"
        ):
            broker.reconcile()

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
        self.assertEqual(
            payload["legs"][0]["position_intent"],
            "buy_to_open",
        )
        self.assertNotIn("symbol", payload)

        closing = replace(
            intent,
            reduce_only=True,
            parent_intent_id="opening-intent",
            intent_id="closing-intent",
            option_legs=tuple(
                replace(
                    leg,
                    side=(
                        Side.SELL
                        if leg.side is Side.BUY
                        else Side.BUY
                    ),
                )
                for leg in intent.option_legs
            ),
        )
        close_payload = AlpacaPaperBroker.build_order_payload(
            closing, Decimal("2")
        )
        self.assertEqual(
            close_payload["legs"][0]["position_intent"],
            "sell_to_close",
        )
        self.assertEqual(
            close_payload["legs"][1]["position_intent"],
            "buy_to_close",
        )

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
