from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from multitrade.domain import ZERO
from multitrade.features import MarketRegime
from multitrade.strategies.base import (
    SignalAction,
    Strategy,
    StrategyContext,
    StrategySignal,
    create_signal,
)


def _target(reference: Decimal, stop: Decimal, multiple: Decimal) -> Decimal:
    return reference + (reference - stop) * multiple


@dataclass(frozen=True, slots=True)
class BreakoutRetestStrategy:
    strategy_id: str = "breakout_retest"
    version: str = "1.0.0"
    lookback: int = 20
    retest_tolerance: Decimal = Decimal("0.003")
    volume_multiplier: Decimal = Decimal("1.15")
    reward_multiple: Decimal = Decimal("2")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        bars = context.bars
        if len(bars) < self.lookback + 3:
            return None
        history = bars[-self.lookback - 2 : -2]
        breakout = bars[-2]
        retest = bars[-1]
        resistance = max(bar.high for bar in history)
        average_volume = (
            sum((bar.volume for bar in history), start=ZERO)
            / Decimal(len(history))
        )
        breakout_confirmed = (
            breakout.close > resistance
            and breakout.volume
            >= average_volume * self.volume_multiplier
        )
        retest_confirmed = (
            retest.low
            <= resistance * (Decimal("1") + self.retest_tolerance)
            and retest.close > resistance
            and retest.close > retest.open
        )
        if not breakout_confirmed or not retest_confirmed:
            return None
        stop = min(
            retest.low,
            resistance - context.features.atr * Decimal("0.35"),
        )
        if stop <= ZERO or stop >= retest.close:
            return None
        confidence = min(
            Decimal("0.90"),
            Decimal("0.55")
            + min(
                Decimal("0.20"),
                breakout.volume / max(average_volume, Decimal("1"))
                * Decimal("0.05"),
            )
            + (
                Decimal("0.10")
                if context.features.regime is MarketRegime.TREND_UP
                else ZERO
            ),
        )
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=confidence,
            reference_price=retest.close,
            stop_price=stop,
            target_price=_target(
                retest.close, stop, self.reward_multiple
            ),
            reason_codes=(
                "closed_above_prior_resistance",
                "breakout_volume_confirmed",
                "retest_held_resistance",
                "bullish_retest_close",
            ),
            evidence={
                "resistance": resistance,
                "breakout_close": breakout.close,
                "breakout_volume": breakout.volume,
                "average_volume": average_volume,
                "retest_low": retest.low,
                "retest_close": retest.close,
                "regime": context.features.regime,
            },
        )


@dataclass(frozen=True, slots=True)
class TrendPullbackStrategy:
    strategy_id: str = "trend_pullback"
    version: str = "1.0.0"
    tolerance: Decimal = Decimal("0.004")
    reward_multiple: Decimal = Decimal("2")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        if len(context.bars) < 35:
            return None
        features = context.features
        latest = context.bars[-1]
        if features.regime is not MarketRegime.TREND_UP:
            return None
        touched_fast_average = (
            latest.low
            <= features.sma_fast * (Decimal("1") + self.tolerance)
        )
        confirmed = (
            latest.close > features.sma_fast
            and latest.close > latest.open
            and features.relative_volume >= Decimal("0.75")
        )
        if not touched_fast_average or not confirmed:
            return None
        swing_low = min(bar.low for bar in context.bars[-5:])
        stop = min(
            swing_low,
            latest.close - features.atr * Decimal("1.25"),
        )
        if stop <= ZERO or stop >= latest.close:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=min(
                Decimal("0.85"),
                Decimal("0.58")
                + min(
                    Decimal("0.15"),
                    features.trend_strength * Decimal("20"),
                ),
            ),
            reference_price=latest.close,
            stop_price=stop,
            target_price=_target(
                latest.close, stop, self.reward_multiple
            ),
            reason_codes=(
                "uptrend_regime",
                "pullback_touched_fast_average",
                "bullish_close_above_fast_average",
            ),
            evidence={
                "sma_fast": features.sma_fast,
                "sma_slow": features.sma_slow,
                "trend_strength": features.trend_strength,
                "relative_volume": features.relative_volume,
                "atr": features.atr,
            },
        )


@dataclass(frozen=True, slots=True)
class VolatilityContractionBreakoutStrategy:
    strategy_id: str = "volatility_contraction"
    version: str = "1.0.0"
    contraction_ratio: Decimal = Decimal("0.70")
    volume_multiplier: Decimal = Decimal("1.20")
    reward_multiple: Decimal = Decimal("2.25")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        bars = context.bars
        if len(bars) < 40:
            return None
        latest = bars[-1]
        recent = bars[-6:-1]
        earlier = bars[-16:-6]
        recent_range = sum(
            (bar.high - bar.low for bar in recent), start=ZERO
        ) / Decimal(len(recent))
        earlier_range = sum(
            (bar.high - bar.low for bar in earlier), start=ZERO
        ) / Decimal(len(earlier))
        resistance = max(bar.high for bar in recent)
        contracted = (
            earlier_range > ZERO
            and recent_range
            <= earlier_range * self.contraction_ratio
        )
        confirmed = (
            latest.close > resistance
            and latest.close > latest.open
            and context.features.relative_volume
            >= self.volume_multiplier
        )
        if not contracted or not confirmed:
            return None
        stop = min(
            min(bar.low for bar in recent),
            latest.close - context.features.atr * Decimal("1.25"),
        )
        if stop <= ZERO or stop >= latest.close:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=Decimal("0.68"),
            reference_price=latest.close,
            stop_price=stop,
            target_price=_target(
                latest.close, stop, self.reward_multiple
            ),
            reason_codes=(
                "range_contracted",
                "closed_above_contraction_high",
                "relative_volume_confirmed",
            ),
            evidence={
                "recent_average_range": recent_range,
                "earlier_average_range": earlier_range,
                "resistance": resistance,
                "relative_volume": context.features.relative_volume,
                "regime": context.features.regime,
            },
        )


@dataclass(frozen=True, slots=True)
class RangeMeanReversionStrategy:
    strategy_id: str = "range_mean_reversion"
    version: str = "1.0.0"
    deviation_multiple: Decimal = Decimal("2")
    reward_multiple: Decimal = Decimal("1.5")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        if len(context.bars) < 35:
            return None
        if context.features.regime is not MarketRegime.RANGE:
            return None
        recent = context.bars[-21:-1]
        latest = context.bars[-1]
        mean = sum(
            (bar.close for bar in recent), start=ZERO
        ) / Decimal(len(recent))
        variance = sum(
            ((bar.close - mean) ** 2 for bar in recent), start=ZERO
        ) / Decimal(len(recent))
        deviation = variance.sqrt()
        lower_band = mean - deviation * self.deviation_multiple
        if not (
            latest.low < lower_band
            and latest.close > lower_band
            and latest.close > latest.open
        ):
            return None
        stop = min(
            latest.low - context.features.atr * Decimal("0.25"),
            latest.close - context.features.atr * Decimal("1.5"),
        )
        if (
            stop <= ZERO
            or stop >= latest.close
            or mean <= latest.close
        ):
            return None
        target = min(
            mean,
            _target(latest.close, stop, self.reward_multiple),
        )
        if target <= latest.close:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=Decimal("0.57"),
            reference_price=latest.close,
            stop_price=stop,
            target_price=target,
            reason_codes=(
                "range_regime",
                "lower_band_rejection",
                "bullish_reversal_close",
            ),
            evidence={
                "range_mean": mean,
                "lower_band": lower_band,
                "price_deviation": deviation,
                "atr": context.features.atr,
            },
        )


def default_equity_strategies() -> dict[str, Strategy]:
    strategies: tuple[Strategy, ...] = (
        BreakoutRetestStrategy(),
        TrendPullbackStrategy(),
        VolatilityContractionBreakoutStrategy(),
        RangeMeanReversionStrategy(),
    )
    return {
        strategy.strategy_id: strategy for strategy in strategies
    }
