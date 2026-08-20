from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from multitrade.domain import OptionRight, Side, ZERO


ONE = Decimal("1")
CONTRACT_MULTIPLIER = Decimal("100")


@dataclass(frozen=True, slots=True)
class OptionProbabilityEstimate:
    probability_of_profit: Decimal
    probability_of_touch: Decimal
    expected_value_per_package: Decimal
    expected_return_on_risk: Decimal
    max_profit_per_package: Decimal
    max_loss_per_package: Decimal
    breakeven_low: Decimal | None
    breakeven_high: Decimal | None
    model: str

    def payload(self) -> dict[str, object]:
        return {
            "probability_of_profit": self.probability_of_profit,
            "probability_of_touch": self.probability_of_touch,
            "expected_value_per_package": self.expected_value_per_package,
            "expected_return_on_risk": self.expected_return_on_risk,
            "max_profit_per_package": self.max_profit_per_package,
            "max_loss_per_package": self.max_loss_per_package,
            "breakeven_low": self.breakeven_low,
            "breakeven_high": self.breakeven_high,
            "model": self.model,
            "record_only": True,
        }


def defined_risk_probability_estimate(
    *,
    structure: str,
    legs: tuple,
    net_price: Decimal,
    underlying_price: Decimal,
    expiration: date,
    as_of: date,
) -> OptionProbabilityEstimate:
    dte = max((expiration - as_of).days, 0)
    max_loss, max_profit = _expiry_extremes(
        legs=legs,
        net_price=net_price,
    )
    breakeven_low, breakeven_high = _breakevens(
        legs=legs,
        net_price=net_price,
        underlying_price=underlying_price,
    )
    pop = _probability_of_profit(
        structure=structure,
        legs=legs,
        breakeven_low=breakeven_low,
        breakeven_high=breakeven_high,
        underlying_price=underlying_price,
        dte=dte,
    )
    touch = min(ONE, terminal_breach_to_touch(ONE - pop))
    expected_value = (
        pop * max_profit - (ONE - pop) * max_loss
        if max_loss > ZERO
        else ZERO
    )
    expected_return_on_risk = (
        expected_value / max_loss if max_loss > ZERO else ZERO
    )
    return OptionProbabilityEstimate(
        probability_of_profit=pop,
        probability_of_touch=touch,
        expected_value_per_package=expected_value,
        expected_return_on_risk=expected_return_on_risk,
        max_profit_per_package=max_profit,
        max_loss_per_package=max_loss,
        breakeven_low=breakeven_low,
        breakeven_high=breakeven_high,
        model="delta_distance_iv_dte_v1_record_only",
    )


def terminal_breach_to_touch(probability_of_breach: Decimal) -> Decimal:
    """Approximate touch odds from terminal breach odds.

    For short options, practitioners often use roughly 2x delta as a
    path-touch heuristic. We cap the value because this is intentionally a
    conservative record-only approximation, not a pricing model.
    """

    return min(ONE, probability_of_breach * Decimal("2"))


def _expiry_extremes(
    *, legs: tuple, net_price: Decimal
) -> tuple[Decimal, Decimal]:
    strikes = sorted({leg.strike for leg in legs})
    points = sorted(
        {
            *(strikes or [ZERO]),
            *(strike - Decimal("0.01") for strike in strikes),
            *(strike + Decimal("0.01") for strike in strikes),
        }
    )
    profits = tuple(_expiry_profit(legs, price, net_price) for price in points)
    max_profit = max(profits)
    max_loss = abs(min(profits))
    return max_loss, max_profit


def _expiry_profit(legs: tuple, underlying_price: Decimal, net_price: Decimal) -> Decimal:
    intrinsic = ZERO
    for leg in legs:
        if leg.right is OptionRight.CALL:
            payoff = max(ZERO, underlying_price - leg.strike)
        else:
            payoff = max(ZERO, leg.strike - underlying_price)
        signed = payoff if leg.side is Side.BUY else -payoff
        intrinsic += signed * Decimal(leg.ratio) * Decimal(leg.multiplier)
    return intrinsic - net_price * CONTRACT_MULTIPLIER


def _breakevens(
    *,
    legs: tuple,
    net_price: Decimal,
    underlying_price: Decimal,
) -> tuple[Decimal | None, Decimal | None]:
    strikes = sorted({leg.strike for leg in legs})
    puts = [leg for leg in legs if leg.right is OptionRight.PUT]
    calls = [leg for leg in legs if leg.right is OptionRight.CALL]
    if len(puts) == 2 and not calls:
        credit = -net_price if net_price < ZERO else ZERO
        debit = net_price if net_price > ZERO else ZERO
        if net_price < ZERO:
            short_put = next(leg for leg in puts if leg.side is Side.SELL)
            return short_put.strike - credit, None
        long_put = max(puts, key=lambda leg: leg.strike)
        return long_put.strike - debit, None
    if len(calls) == 2 and not puts:
        credit = -net_price if net_price < ZERO else ZERO
        debit = net_price if net_price > ZERO else ZERO
        if net_price < ZERO:
            short_call = next(leg for leg in calls if leg.side is Side.SELL)
            return None, short_call.strike + credit
        long_call = min(calls, key=lambda leg: leg.strike)
        return None, long_call.strike + debit
    if len(puts) == 2 and len(calls) == 2 and net_price < ZERO:
        short_put = max(
            (leg for leg in puts if leg.side is Side.SELL),
            key=lambda leg: leg.strike,
        )
        short_call = min(
            (leg for leg in calls if leg.side is Side.SELL),
            key=lambda leg: leg.strike,
        )
        credit = -net_price
        return short_put.strike - credit, short_call.strike + credit
    return None, None


def _probability_of_profit(
    *,
    structure: str,
    legs: tuple,
    breakeven_low: Decimal | None,
    breakeven_high: Decimal | None,
    underlying_price: Decimal,
    dte: int,
) -> Decimal:
    short_abs_delta = tuple(
        abs(leg.delta)
        for leg in legs
        if leg.side is Side.SELL and leg.delta is not None
    )
    if structure == "iron_condor":
        breach = sum(short_abs_delta, start=ZERO)
        return _clamp_probability(ONE - breach)
    if structure in {
        "bull_put_credit_spread",
        "bear_call_credit_spread",
    } and short_abs_delta:
        return _clamp_probability(ONE - short_abs_delta[0])
    if structure == "bear_put_debit_spread" and breakeven_low is not None:
        return _distance_probability(
            threshold=breakeven_low,
            underlying_price=underlying_price,
            dte=dte,
            direction="below",
            fallback=Decimal("0.45"),
        )
    if structure == "bull_call_debit_spread" and breakeven_high is not None:
        return _distance_probability(
            threshold=breakeven_high,
            underlying_price=underlying_price,
            dte=dte,
            direction="above",
            fallback=Decimal("0.45"),
        )
    return Decimal("0.50")


def _distance_probability(
    *,
    threshold: Decimal,
    underlying_price: Decimal,
    dte: int,
    direction: str,
    fallback: Decimal,
) -> Decimal:
    if underlying_price <= ZERO:
        return fallback
    distance = abs(threshold / underlying_price - ONE)
    time_scale = max(Decimal(dte), ONE) / Decimal("365")
    # A deliberately simple proxy: larger required moves reduce POP.
    penalty = min(Decimal("0.30"), distance / max(time_scale.sqrt(), Decimal("0.01")))
    base = Decimal("0.50") - penalty
    if direction == "below" and threshold >= underlying_price:
        base = Decimal("0.55")
    if direction == "above" and threshold <= underlying_price:
        base = Decimal("0.55")
    return _clamp_probability(base)


def _clamp_probability(value: Decimal) -> Decimal:
    return min(Decimal("0.99"), max(Decimal("0.01"), value))
