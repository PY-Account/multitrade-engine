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
    DefinedRiskOptionSelector,
    OptionExecutionPolicy,
    OptionSnapshot,
    OptionStructure,
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
    delta: str = "0.50",
    theta: str | None = None,
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
        delta=Decimal(delta),
        quote_timestamp="2026-07-28T14:30:00Z",
        feed="indicative",
        theta=Decimal(theta) if theta is not None else None,
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
    def test_zero_dte_policy_requires_timed_same_day_exit(self) -> None:
        policy = OptionExecutionPolicy(
            structure=OptionStructure.IRON_CONDOR,
            source_strategy_id="zero_dte_iron_condor",
            minimum_dte=0,
            maximum_dte=0,
            short_delta_target=Decimal("0.15"),
            maximum_short_delta=Decimal("0.20"),
            exit_before_expiry_days=0,
            maximum_holding_minutes=210,
        )
        self.assertEqual(policy.maximum_holding_minutes, 210)
        with self.assertRaisesRegex(ValueError, "zero exit"):
            OptionExecutionPolicy(
                structure=OptionStructure.IRON_CONDOR,
                source_strategy_id="zero_dte_iron_condor",
                minimum_dte=0,
                maximum_dte=0,
                exit_before_expiry_days=1,
            )

    def test_put_income_policy_validates_delta_and_credit_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            OptionExecutionPolicy(
                structure=OptionStructure.BULL_PUT_CREDIT,
                source_strategy_id="support_delta_put_income",
                short_delta_target=Decimal("0.25"),
                maximum_short_delta=Decimal("0.22"),
            )
        policy = OptionExecutionPolicy(
            structure=OptionStructure.BULL_PUT_CREDIT,
            source_strategy_id="support_delta_put_income",
            short_delta_target=Decimal("0.20"),
            maximum_short_delta=Decimal("0.22"),
            wing_delta_target=Decimal("0.08"),
            maximum_strike_width=Decimal("5"),
            minimum_credit_to_risk=Decimal("0.20"),
        )
        self.assertEqual(policy.maximum_short_delta, Decimal("0.22"))
        self.assertEqual(policy.minimum_credit_to_risk, Decimal("0.20"))

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
        preview = intent.explanation["option_order_preview"]
        self.assertEqual(preview["natural_price"], Decimal("1.20"))
        self.assertEqual(preview["midpoint_price"], Decimal("1.05"))
        self.assertEqual(preview["optimistic_price"], Decimal("0.90"))
        self.assertFalse(preview["market_order_used"])
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

    def test_credit_spread_uses_negative_alpaca_net_price_and_theta(
        self,
    ) -> None:
        short_put = snapshot(
            "AAPL260918P00150000",
            strike="150",
            right=OptionRight.PUT,
            bid="1.20",
            ask="1.30",
            delta="-0.25",
            theta="-0.08",
        )
        long_put = snapshot(
            "AAPL260918P00145000",
            strike="145",
            right=OptionRight.PUT,
            bid="0.34",
            ask="0.40",
            delta="-0.10",
            theta="-0.02",
        )

        intent = DefinedRiskOptionFactory().bull_put_credit_spread(
            account_id="alpaca-paper",
            strategy_id="trend_pullback_bull_put_theta",
            short_put=short_put,
            long_put=long_put,
            requested_quantity=Decimal("1"),
            risk_budget_fraction=Decimal("0.01"),
            signal_id="credit-signal",
        )

        self.assertEqual(intent.limit_price, Decimal("-0.80"))
        preview = intent.explanation["option_order_preview"]
        self.assertEqual(preview["natural_price"], Decimal("-0.80"))
        self.assertEqual(preview["midpoint_price"], Decimal("-0.88"))
        self.assertEqual(preview["optimistic_price"], Decimal("-0.96"))
        self.assertEqual(
            intent.explanation[
                "modeled_theta_per_day_per_package"
            ],
            Decimal("6.00"),
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
        self.assertEqual(
            decision.risk_per_unit, Decimal("425.00")
        )

    def test_selector_enforces_positive_theta_credit_policy(
        self,
    ) -> None:
        chain = (
            snapshot(
                "AAPL260918P00150000",
                strike="150",
                right=OptionRight.PUT,
                bid="1.20",
                ask="1.30",
                delta="-0.25",
                theta="-0.08",
            ),
            snapshot(
                "AAPL260918P00145000",
                strike="145",
                right=OptionRight.PUT,
                bid="0.34",
                ask="0.40",
                delta="-0.10",
                theta="-0.02",
            ),
        )
        policy = OptionExecutionPolicy(
            structure=OptionStructure.BULL_PUT_CREDIT,
            source_strategy_id="trend_pullback",
            minimum_modeled_theta=Decimal("1"),
        )

        intent = DefinedRiskOptionSelector(policy).build_intent(
            account_id="alpaca-paper",
            strategy_id="trend_pullback_bull_put_theta",
            underlying="AAPL",
            underlying_price=Decimal("155"),
            direction="bullish",
            chain=chain,
            requested_quantity=Decimal("10"),
            risk_budget_fraction=Decimal("0.005"),
            signal_id="selected-credit",
            as_of=date(2026, 7, 28),
        )

        self.assertEqual(
            intent.explanation["structure"],
            "bull_put_credit_spread",
        )
        self.assertEqual(
            intent.explanation["required_options_trading_level"], 3
        )
        self.assertEqual(
            intent.explanation["theta_attribution"],
            "decision_time_model_only_not_realized_profit",
        )
        self.assertEqual(
            len(intent.explanation["decision_option_snapshots"]), 2
        )
        self.assertEqual(
            intent.explanation["decision_option_snapshots"][0]["feed"],
            "indicative",
        )
        self.assertFalse(
            intent.explanation["historical_bar_greeks_available"]
        )

    def test_bull_put_selector_can_target_fixed_strike_width(
        self,
    ) -> None:
        chain = (
            snapshot(
                "AAPL260918P00640000",
                strike="640",
                right=OptionRight.PUT,
                bid="12.00",
                ask="12.50",
                delta="-0.12",
                theta="-0.60",
            ),
            snapshot(
                "AAPL260918P00630000",
                strike="630",
                right=OptionRight.PUT,
                bid="8.00",
                ask="8.40",
                delta="-0.08",
                theta="-0.35",
            ),
            snapshot(
                "AAPL260918P00615000",
                strike="615",
                right=OptionRight.PUT,
                bid="4.10",
                ask="4.40",
                delta="-0.05",
                theta="-0.20",
            ),
        )
        policy = OptionExecutionPolicy(
            structure=OptionStructure.BULL_PUT_CREDIT,
            source_strategy_id="index_put_credit_14dte",
            minimum_dte=30,
            maximum_dte=60,
            short_delta_target=Decimal("0.12"),
            maximum_short_delta=Decimal("0.13"),
            wing_delta_target=Decimal("0.05"),
            target_strike_width=Decimal("25"),
            maximum_strike_width=Decimal("25"),
        )

        intent = DefinedRiskOptionSelector(policy).build_intent(
            account_id="alpaca-paper",
            strategy_id="spx_rut_put_credit_14dte",
            underlying="AAPL",
            underlying_price=Decimal("660"),
            direction="bullish",
            chain=chain,
            requested_quantity=Decimal("1"),
            risk_budget_fraction=Decimal("0.003"),
            signal_id="index-credit",
            as_of=date(2026, 7, 28),
        )

        put_strikes = sorted(leg.strike for leg in intent.option_legs)
        self.assertEqual(put_strikes, [Decimal("615"), Decimal("640")])
        self.assertEqual(
            intent.explanation["target_strike_width"],
            Decimal("25"),
        )
