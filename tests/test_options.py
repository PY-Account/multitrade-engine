from datetime import date
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock

from multitrade.domain import (
    AccountSnapshot,
    OptionRight,
    Side,
)
from multitrade.options import (
    AlpacaOptionChainClient,
    DefinedRiskOptionFactory,
    OptionSnapshot,
    parse_occ_symbol,
)
from multitrade.risk import RiskEngine


def snapshot(
    symbol: str,
    *,
    strike: str,
    right: OptionRight,
    bid: str,
    ask: str,
) -> OptionSnapshot:
    return OptionSnapshot(
        symbol=symbol,
        underlying="AAPL",
        expiration=date(2026, 9, 18),
        right=right,
        strike=Decimal(strike),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=10,
        ask_size=10,
        implied_volatility=Decimal("0.25"),
        delta=Decimal("0.50"),
        quote_timestamp="2026-07-28T14:30:00Z",
        feed="indicative",
    )


class OptionDataTests(TestCase):
    def test_occ_symbol_is_parsed_without_float_rounding(self) -> None:
        root, expiration, right, strike = parse_occ_symbol(
            "AAPL260918C00150000"
        )
        self.assertEqual(root, "AAPL")
        self.assertEqual(expiration, date(2026, 9, 18))
        self.assertIs(right, OptionRight.CALL)
        self.assertEqual(strike, Decimal("150"))

    def test_option_chain_response_is_normalized(self) -> None:
        client = AlpacaOptionChainClient(
            "paper-key", "paper-secret", feed="indicative"
        )
        client._request = Mock(
            return_value={
                "snapshots": {
                    "AAPL260918C00150000": {
                        "latestQuote": {
                            "bp": 2.0,
                            "ap": 2.2,
                            "bs": 5,
                            "as": 6,
                            "t": "2026-07-28T14:30:00Z",
                        },
                        "impliedVolatility": 0.25,
                        "greeks": {"delta": 0.52},
                    }
                },
                "next_page_token": None,
            }
        )

        chain = client.fetch_chain(
            "AAPL",
            expiration_gte=date(2026, 9, 1),
            expiration_lte=date(2026, 9, 30),
        )

        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].midpoint, Decimal("2.1"))
        self.assertEqual(chain[0].delta, Decimal("0.52"))
        self.assertEqual(chain[0].feed, "indicative")


class DefinedRiskOptionFactoryTests(TestCase):
    def test_bull_call_spread_uses_conservative_debit_for_risk(self) -> None:
        long_call = snapshot(
            "AAPL260918C00150000",
            strike="150",
            right=OptionRight.CALL,
            bid="2.00",
            ask="2.20",
        )
        short_call = snapshot(
            "AAPL260918C00155000",
            strike="155",
            right=OptionRight.CALL,
            bid="1.00",
            ask="1.10",
        )
        intent = DefinedRiskOptionFactory().bull_call_debit_spread(
            account_id="alpaca-paper",
            strategy_id="option_breakout_debit",
            long_call=long_call,
            short_call=short_call,
            requested_quantity=Decimal("5"),
            risk_budget_fraction=Decimal("0.005"),
            signal_id="option-signal-1",
        )

        self.assertEqual(intent.limit_price, Decimal("1.20"))
        self.assertIs(intent.option_legs[0].side, Side.BUY)
        self.assertEqual(
            intent.option_legs[0].mark_price, Decimal("2.20")
        )
        self.assertEqual(
            intent.option_legs[1].mark_price, Decimal("1.00")
        )
        decision = RiskEngine().evaluate(
            intent,
            AccountSnapshot(
                equity=Decimal("100000"),
                start_of_day_equity=Decimal("100000"),
                peak_equity=Decimal("100000"),
            ),
        )
        self.assertTrue(decision.approved)
        self.assertLessEqual(
            decision.reserved_risk, Decimal("100000") * Decimal("0.005")
        )

    def test_illiquid_spread_is_rejected(self) -> None:
        long_call = snapshot(
            "AAPL260918C00150000",
            strike="150",
            right=OptionRight.CALL,
            bid="1.00",
            ask="2.00",
        )
        short_call = snapshot(
            "AAPL260918C00155000",
            strike="155",
            right=OptionRight.CALL,
            bid="1.00",
            ask="1.10",
        )
        with self.assertRaisesRegex(ValueError, "liquidity"):
            DefinedRiskOptionFactory().bull_call_debit_spread(
                account_id="alpaca-paper",
                strategy_id="option_breakout_debit",
                long_call=long_call,
                short_call=short_call,
                requested_quantity=Decimal("1"),
                risk_budget_fraction=Decimal("0.005"),
                signal_id="option-signal-2",
            )
