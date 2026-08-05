from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OptionRight,
    RiskDecision,
    Side,
    TradeIntent,
    ZERO,
)


ONE = Decimal("1")
BPS = Decimal("10000")
CRYPTO_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    max_per_trade: Decimal = Decimal("0.03")
    max_option_per_trade: Decimal = Decimal("0.03")
    max_total_open: Decimal = Decimal("0.10")
    max_daily_loss: Decimal = Decimal("0.03")
    max_drawdown: Decimal = Decimal("0.10")
    max_notional_per_trade: Decimal = Decimal("0.25")
    stock_stress_move: Decimal = Decimal("0.05")
    crypto_stress_move: Decimal = Decimal("0.10")
    stock_slippage_bps: Decimal = Decimal("25")
    crypto_slippage_bps: Decimal = Decimal("100")
    option_slippage_per_package: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        fractional_fields = (
            self.max_per_trade,
            self.max_option_per_trade,
            self.max_total_open,
            self.max_daily_loss,
            self.max_drawdown,
            self.max_notional_per_trade,
            self.stock_stress_move,
            self.crypto_stress_move,
        )
        if any(value <= ZERO or value > ONE for value in fractional_fields):
            raise ValueError("Risk fractions must be in the interval (0, 1]")
        if self.max_per_trade > self.max_total_open:
            raise ValueError("Per-trade risk cannot exceed total-open risk")
        if self.max_option_per_trade > self.max_total_open:
            raise ValueError(
                "Option per-trade risk cannot exceed total-open risk"
            )
        if (
            self.stock_slippage_bps < ZERO
            or self.crypto_slippage_bps < ZERO
            or self.option_slippage_per_package < ZERO
        ):
            raise ValueError("Slippage assumptions cannot be negative")


@dataclass(frozen=True, slots=True)
class FirmRiskPolicy:
    """Consolidated limits applied atomically across managed accounts."""

    enabled: bool = True
    max_total_open: Decimal = Decimal("0.10")
    max_symbol_open: Decimal = Decimal("0.03")
    max_strategy_open: Decimal = Decimal("0.05")
    equity_max_age_seconds: int = 900

    def __post_init__(self) -> None:
        fractions = (
            self.max_total_open,
            self.max_symbol_open,
            self.max_strategy_open,
        )
        if any(value <= ZERO or value > ONE for value in fractions):
            raise ValueError(
                "Firm risk fractions must be in the interval (0, 1]"
            )
        if self.max_symbol_open > self.max_total_open:
            raise ValueError(
                "Firm symbol risk cannot exceed total-open risk"
            )
        if self.max_strategy_open > self.max_total_open:
            raise ValueError(
                "Firm strategy risk cannot exceed total-open risk"
            )
        if not 60 <= self.equity_max_age_seconds <= 86400:
            raise ValueError(
                "Firm equity freshness must be between 60 and 86400 seconds"
            )


class RiskEngine:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    def evaluate(
        self, intent: TradeIntent, snapshot: AccountSnapshot
    ) -> RiskDecision:
        if intent.reduce_only:
            return self._evaluate_reduce_only(intent, snapshot)
        guard_rejection = self._account_guard(intent, snapshot)
        if guard_rejection is not None:
            return guard_rejection

        try:
            risk_per_unit = self._risk_per_unit(intent)
        except ValueError as exc:
            return self._reject(intent, str(exc), snapshot.active_risk)

        if risk_per_unit <= ZERO:
            return self._reject(
                intent, "calculated_risk_must_be_positive", snapshot.active_risk
            )

        asset_ceiling = (
            self.policy.max_option_per_trade
            if intent.asset_class is AssetClass.OPTION
            else self.policy.max_per_trade
        )
        requested_ceiling = (
            intent.risk_budget_fraction
            if intent.risk_budget_fraction is not None
            else asset_ceiling
        )
        trade_ceiling = snapshot.equity * min(
            asset_ceiling, requested_ceiling
        )
        portfolio_ceiling = snapshot.equity * self.policy.max_total_open
        remaining_portfolio_risk = max(
            ZERO, portfolio_ceiling - snapshot.active_risk
        )
        risk_budget = min(trade_ceiling, remaining_portfolio_risk)
        if risk_budget <= ZERO:
            return self._reject(
                intent, "portfolio_risk_budget_exhausted", snapshot.active_risk
            )

        max_quantity = self._floor_quantity(
            risk_budget / risk_per_unit, intent.asset_class
        )
        max_quantity = min(
            max_quantity,
            self._notional_limited_quantity(intent, snapshot),
        )
        approved_quantity = min(intent.requested_quantity, max_quantity)
        approved_quantity = self._floor_quantity(
            approved_quantity, intent.asset_class
        )

        if approved_quantity <= ZERO:
            return self._reject(
                intent,
                "minimum_position_exceeds_risk_budget",
                snapshot.active_risk,
            )

        reserved_risk = approved_quantity * risk_per_unit
        return RiskDecision(
            approved=True,
            intent_id=intent.intent_id,
            reason="approved",
            approved_quantity=approved_quantity,
            risk_per_unit=risk_per_unit,
            reserved_risk=reserved_risk,
            projected_active_risk=snapshot.active_risk + reserved_risk,
        )

    def estimate_risk_per_unit(
        self, intent: TradeIntent
    ) -> Decimal:
        """Expose the same conservative unit-risk model used for approval."""
        if intent.reduce_only:
            return ZERO
        return self._risk_per_unit(intent)

    def quantity_for_risk_budget(
        self,
        intent: TradeIntent,
        risk_budget: Decimal,
        risk_per_unit: Decimal,
    ) -> Decimal:
        """Apply the engine's asset-specific quantity granularity."""
        if risk_budget <= ZERO or risk_per_unit <= ZERO:
            return ZERO
        return self._floor_quantity(
            risk_budget / risk_per_unit,
            intent.asset_class,
        )

    def _account_guard(
        self, intent: TradeIntent, snapshot: AccountSnapshot
    ) -> RiskDecision | None:
        daily_loss = max(ZERO, snapshot.start_of_day_equity - snapshot.equity)
        if daily_loss >= (
            snapshot.start_of_day_equity * self.policy.max_daily_loss
        ):
            return self._reject(
                intent, "daily_loss_kill_switch", snapshot.active_risk
            )

        drawdown = max(ZERO, snapshot.peak_equity - snapshot.equity)
        if drawdown >= snapshot.peak_equity * self.policy.max_drawdown:
            return self._reject(
                intent, "drawdown_kill_switch", snapshot.active_risk
            )

        if (
            intent.asset_class is AssetClass.CRYPTO
            and intent.side is Side.SELL
        ):
            return self._reject(
                intent,
                "alpaca_crypto_shorting_is_not_supported",
                snapshot.active_risk,
            )
        return None

    def _evaluate_reduce_only(
        self,
        intent: TradeIntent,
        snapshot: AccountSnapshot,
    ) -> RiskDecision:
        if intent.asset_class is not AssetClass.OPTION:
            return self._reject(
                intent,
                "only_option_reduce_only_is_implemented",
                snapshot.active_risk,
            )
        if not intent.option_legs:
            return self._reject(
                intent,
                "reduce_only_option_legs_are_required",
                snapshot.active_risk,
            )
        for leg in intent.option_legs:
            position = snapshot.positions.get(leg.symbol, ZERO)
            required = (
                intent.requested_quantity * Decimal(leg.ratio)
            )
            if leg.side is Side.SELL:
                available = max(ZERO, position)
            else:
                available = max(ZERO, -position)
            if available < required:
                return self._reject(
                    intent,
                    f"reduce_only_position_insufficient:{leg.symbol}",
                    snapshot.active_risk,
                )
        return RiskDecision(
            approved=True,
            intent_id=intent.intent_id,
            reason="reduce_only_approved",
            approved_quantity=intent.requested_quantity,
            risk_per_unit=ZERO,
            reserved_risk=ZERO,
            projected_active_risk=snapshot.active_risk,
        )

    def _risk_per_unit(self, intent: TradeIntent) -> Decimal:
        if intent.asset_class is AssetClass.OPTION:
            return self._option_max_loss(intent)

        if intent.reference_price is None or intent.stop_price is None:
            raise ValueError("reference_and_stop_prices_are_required")
        if intent.reference_price <= ZERO or intent.stop_price <= ZERO:
            raise ValueError("reference_and_stop_prices_must_be_positive")

        if intent.side is Side.BUY and intent.stop_price >= intent.reference_price:
            raise ValueError("long_position_stop_must_be_below_reference")
        if (
            intent.side is Side.SELL
            and intent.stop_price <= intent.reference_price
        ):
            raise ValueError("short_position_stop_must_be_above_reference")

        stop_loss = abs(intent.reference_price - intent.stop_price)
        if intent.asset_class is AssetClass.STOCK:
            stress_loss = (
                intent.reference_price * self.policy.stock_stress_move
            )
            slippage = (
                intent.reference_price
                * self.policy.stock_slippage_bps
                / BPS
            )
        else:
            stress_loss = (
                intent.reference_price * self.policy.crypto_stress_move
            )
            slippage = (
                intent.reference_price
                * self.policy.crypto_slippage_bps
                / BPS
            )
        return max(stop_loss, stress_loss) + slippage

    def _option_max_loss(self, intent: TradeIntent) -> Decimal:
        legs = intent.option_legs
        if len(legs) > 4:
            raise ValueError("alpaca_mleg_supports_at_most_four_legs")

        underlyings = {leg.underlying for leg in legs}
        expirations = {leg.expiration for leg in legs}
        multipliers = {leg.multiplier for leg in legs}
        if len(underlyings) != 1 or intent.symbol not in underlyings:
            raise ValueError("option_legs_must_share_the_intent_underlying")
        if len(expirations) != 1:
            raise ValueError("mixed_expiry_option_risk_not_supported_in_mvp")
        if len(multipliers) != 1:
            raise ValueError("mixed_option_multipliers_are_not_supported")

        call_slope_at_infinity = sum(
            (
                Decimal(leg.ratio * leg.multiplier)
                if leg.side is Side.BUY
                else -Decimal(leg.ratio * leg.multiplier)
            )
            for leg in legs
            if leg.right is OptionRight.CALL
        )
        if call_slope_at_infinity < ZERO:
            raise ValueError("unlimited_option_loss_is_rejected")

        breakpoints = {ZERO, *(leg.strike for leg in legs)}
        minimum_profit = min(
            self._option_expiry_profit(legs, underlying_price)
            for underlying_price in breakpoints
        )
        maximum_loss = max(ZERO, -minimum_profit)
        return maximum_loss + self.policy.option_slippage_per_package

    @staticmethod
    def _option_expiry_profit(legs, underlying_price: Decimal) -> Decimal:
        total = ZERO
        for leg in legs:
            if leg.right is OptionRight.CALL:
                intrinsic = max(ZERO, underlying_price - leg.strike)
            else:
                intrinsic = max(ZERO, leg.strike - underlying_price)
            long_profit = (
                intrinsic - leg.mark_price
            ) * leg.ratio * leg.multiplier
            total += long_profit if leg.side is Side.BUY else -long_profit
        return total

    def _notional_limited_quantity(
        self, intent: TradeIntent, snapshot: AccountSnapshot
    ) -> Decimal:
        if intent.asset_class is AssetClass.OPTION:
            return intent.requested_quantity
        if intent.reference_price is None or intent.reference_price <= ZERO:
            return ZERO
        notional_budget = (
            snapshot.equity * self.policy.max_notional_per_trade
        )
        return self._floor_quantity(
            notional_budget / intent.reference_price, intent.asset_class
        )

    @staticmethod
    def _floor_quantity(
        quantity: Decimal, asset_class: AssetClass
    ) -> Decimal:
        if asset_class is AssetClass.CRYPTO:
            return quantity.quantize(CRYPTO_QUANTUM, rounding=ROUND_DOWN)
        return quantity.to_integral_value(rounding=ROUND_DOWN)

    @staticmethod
    def _reject(
        intent: TradeIntent, reason: str, active_risk: Decimal
    ) -> RiskDecision:
        return RiskDecision(
            approved=False,
            intent_id=intent.intent_id,
            reason=reason,
            projected_active_risk=active_risk,
        )
