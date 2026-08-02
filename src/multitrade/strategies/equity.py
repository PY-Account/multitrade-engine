from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal

from multitrade.domain import ZERO
from multitrade.features import MarketRegime
from multitrade.patterns import (
    PatternDirection,
    detect_chart_patterns,
)
from multitrade.strategies.base import (
    SignalAction,
    Strategy,
    StrategyContext,
    StrategySignal,
    create_signal,
)


def _target(reference: Decimal, stop: Decimal, multiple: Decimal) -> Decimal:
    return reference + (reference - stop) * multiple


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, start=ZERO) / Decimal(len(values))


def _standard_deviation(values: tuple[Decimal, ...]) -> Decimal:
    average = _mean(values)
    variance = _mean(tuple((value - average) ** 2 for value in values))
    return variance.sqrt()


def _ema(values: tuple[Decimal, ...], length: int) -> tuple[Decimal, ...]:
    if length < 1:
        raise ValueError("EMA length must be positive")
    if not values:
        return ()
    alpha = Decimal("2") / Decimal(length + 1)
    rows = [values[0]]
    for value in values[1:]:
        rows.append(alpha * value + (Decimal("1") - alpha) * rows[-1])
    return tuple(rows)


def _tillson_t3(
    values: tuple[Decimal, ...], length: int, factor: Decimal
) -> tuple[Decimal, ...]:
    stages = values
    emas = []
    for _ in range(6):
        stages = _ema(stages, length)
        emas.append(stages)
    e3, e4, e5, e6 = emas[2], emas[3], emas[4], emas[5]
    c1 = -(factor**3)
    c2 = Decimal("3") * factor**2 + Decimal("3") * factor**3
    c3 = -(
        Decimal("6") * factor**2
        + Decimal("3") * factor
        + Decimal("3") * factor**3
    )
    c4 = (
        Decimal("1")
        + Decimal("3") * factor
        + Decimal("3") * factor**2
        + factor**3
    )
    return tuple(
        c1 * e6[index]
        + c2 * e5[index]
        + c3 * e4[index]
        + c4 * e3[index]
        for index in range(len(values))
    )


def _range_filter(
    values: tuple[Decimal, ...], period: int, multiplier: Decimal
) -> tuple[Decimal, ...]:
    changes = (ZERO,) + tuple(
        abs(current - previous)
        for previous, current in zip(values, values[1:])
    )
    smooth = _ema(_ema(changes, period), period * 2 - 1)
    ranges = tuple(value * multiplier for value in smooth)
    filtered = [values[0]]
    for price, threshold in zip(values[1:], ranges[1:]):
        previous = filtered[-1]
        if price > previous:
            filtered.append(max(previous, price - threshold))
        elif price < previous:
            filtered.append(min(previous, price + threshold))
        else:
            filtered.append(previous)
    return tuple(filtered)


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


@dataclass(frozen=True, slots=True)
class T3RangeTrendStrategy:
    """Transparent equity adaptation of a YouTube gold strategy concept."""

    strategy_id: str = "t3_range_trend"
    version: str = "1.0.0"
    t3_length: int = 8
    t3_factor: Decimal = Decimal("0.7")
    range_period: int = 20
    range_multiplier: Decimal = Decimal("2.5")
    stop_atr_multiple: Decimal = Decimal("1.4")
    reward_multiple: Decimal = Decimal("3.8")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        required = max(80, self.range_period * 3, self.t3_length * 8)
        if len(context.bars) < required:
            return None
        closes = tuple(bar.close for bar in context.bars[-required:])
        t3 = _tillson_t3(closes, self.t3_length, self.t3_factor)
        range_line = _range_filter(
            closes, self.range_period, self.range_multiplier
        )
        current_bullish = (
            t3[-1] > t3[-2]
            and closes[-1] > t3[-1]
            and range_line[-1] > range_line[-2]
            and closes[-1] > range_line[-1]
        )
        previous_bullish = (
            t3[-2] > t3[-3]
            and closes[-2] > t3[-2]
            and range_line[-2] > range_line[-3]
            and closes[-2] > range_line[-2]
        )
        if not current_bullish or previous_bullish:
            return None
        latest = context.bars[-1]
        stop = latest.close - (
            context.features.atr * self.stop_atr_multiple
        )
        if stop <= ZERO or stop >= latest.close:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=Decimal("0.65"),
            reference_price=latest.close,
            stop_price=stop,
            target_price=_target(
                latest.close, stop, self.reward_multiple
            ),
            reason_codes=(
                "tillson_t3_rising",
                "price_above_tillson_t3",
                "range_filter_rising",
                "price_above_range_filter",
            ),
            evidence={
                "t3": t3[-1],
                "range_filter": range_line[-1],
                "atr": context.features.atr,
                "source": "youtube_BPFwaD0CgZ8_equity_adaptation",
                "source_limitations": (
                    "Video gold/Asia-session settings were undisclosed; "
                    "this is not an exact reproduction."
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class ChartPatternConfluenceStrategy:
    """Research-only, mathematically specified pattern confluence."""

    strategy_id: str = "chart_pattern_confluence"
    version: str = "1.0.0"
    trap_lookback: int = 20
    trap_tolerance: Decimal = Decimal("0.002")
    pole_bars: int = 8
    flag_bars: int = 5
    minimum_pole_return: Decimal = Decimal("0.025")
    maximum_flag_retracement: Decimal = Decimal("0.50")
    volume_multiplier: Decimal = Decimal("1.10")
    minimum_pattern_score: Decimal = Decimal("0.70")
    stop_atr_buffer: Decimal = Decimal("0.25")
    reward_multiple: Decimal = Decimal("2")

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        matches = detect_chart_patterns(
            context.bars,
            trap_lookback=self.trap_lookback,
            trap_tolerance=self.trap_tolerance,
            pole_bars=self.pole_bars,
            flag_bars=self.flag_bars,
            minimum_pole_return=self.minimum_pole_return,
            maximum_flag_retracement=(
                self.maximum_flag_retracement
            ),
            volume_multiplier=self.volume_multiplier,
        )
        features = context.features
        latest = context.bars[-1]
        bullish = tuple(
            match
            for match in matches
            if match.direction is PatternDirection.BULLISH
        )
        bearish = tuple(
            match
            for match in matches
            if match.direction is PatternDirection.BEARISH
        )
        bullish_score = sum(
            (match.score for match in bullish), start=ZERO
        )
        bearish_score = sum(
            (match.score for match in bearish), start=ZERO
        )
        long_confirmed = (
            bullish
            and bullish_score >= self.minimum_pattern_score
            and bullish_score > bearish_score
            and features.regime is MarketRegime.TREND_UP
            and features.relative_volume >= self.volume_multiplier
        )
        short_confirmed = (
            bearish
            and bearish_score >= self.minimum_pattern_score
            and bearish_score > bullish_score
            and features.regime is MarketRegime.TREND_DOWN
            and features.relative_volume >= self.volume_multiplier
        )
        if not long_confirmed and not short_confirmed:
            return None

        selected = bullish if long_confirmed else bearish
        action = (
            SignalAction.ENTER_LONG
            if long_confirmed
            else SignalAction.ENTER_SHORT
        )
        if long_confirmed:
            structure = min(
                match.invalidation_price for match in selected
            )
            stop = min(
                structure,
                latest.close
                - features.atr * self.stop_atr_buffer,
            )
            target = _target(
                latest.close, stop, self.reward_multiple
            )
            valid = stop > ZERO and stop < latest.close < target
        else:
            structure = max(
                match.invalidation_price for match in selected
            )
            stop = max(
                structure,
                latest.close
                + features.atr * self.stop_atr_buffer,
            )
            target = latest.close - (
                stop - latest.close
            ) * self.reward_multiple
            valid = target > ZERO and target < latest.close < stop
        if not valid:
            return None

        total_score = bullish_score if long_confirmed else bearish_score
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=action,
            confidence=min(
                Decimal("0.90"),
                Decimal("0.50") + total_score * Decimal("0.20"),
            ),
            reference_price=latest.close,
            stop_price=stop,
            target_price=target,
            reason_codes=(
                "mathematical_pattern_detected",
                "market_regime_confirmed",
                "relative_volume_confirmed",
                *(match.pattern_id for match in selected),
            ),
            evidence={
                "pattern_direction": selected[0].direction,
                "aggregate_pattern_score": total_score,
                "patterns": [
                    {
                        "pattern_id": match.pattern_id,
                        "score": match.score,
                        "invalidation_price": (
                            match.invalidation_price
                        ),
                        "measurements": match.evidence,
                    }
                    for match in selected
                ],
                "relative_volume": features.relative_volume,
                "regime": features.regime,
                "source": "tradingkit_pattern_catalog_math_spec",
            },
        )


@dataclass(frozen=True, slots=True)
class SupportDeltaPutIncomeStrategy:
    """Bullish support-rejection signal for defined-risk put spreads."""

    strategy_id: str = "support_delta_put_income"
    version: str = "1.0.0"
    bollinger_window: int = 20
    bollinger_deviations: Decimal = Decimal("2")
    support_lookback: int = 40
    proximity_atr: Decimal = Decimal("0.50")
    stop_atr_buffer: Decimal = Decimal("0.50")
    reward_multiple: Decimal = Decimal("1.50")

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        minimum = max(self.bollinger_window, self.support_lookback) + 1
        if len(context.bars) < minimum or context.features.atr <= ZERO:
            return None
        bars = context.bars
        latest = bars[-1]
        previous = bars[-2]
        closes = tuple(bar.close for bar in bars[-self.bollinger_window :])
        middle = _mean(closes)
        lower_band = middle - (
            _standard_deviation(closes) * self.bollinger_deviations
        )
        support_window = bars[-self.support_lookback - 1 : -1]
        structural_support = min(bar.low for bar in support_window)
        nearest_support = max(lower_band, structural_support)
        distance = abs(latest.low - nearest_support)
        near_support = distance <= context.features.atr * self.proximity_atr
        rejected = (
            latest.low <= nearest_support
            and latest.close > nearest_support
            and latest.close > latest.open
            and latest.close > previous.close
        )
        trend_safe = (
            context.features.regime is not MarketRegime.TREND_DOWN
            and latest.close >= context.features.sma_slow
        )
        if not (near_support and rejected and trend_safe):
            return None
        stop = min(latest.low, nearest_support) - (
            context.features.atr * self.stop_atr_buffer
        )
        if stop <= ZERO or stop >= latest.close:
            return None
        distance_score = max(
            ZERO,
            Decimal("1")
            - distance / (context.features.atr * self.proximity_atr),
        )
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=min(
                Decimal("0.90"),
                Decimal("0.62") + distance_score * Decimal("0.20"),
            ),
            reference_price=latest.close,
            stop_price=stop,
            target_price=_target(latest.close, stop, self.reward_multiple),
            reason_codes=(
                "lower_band_or_support_proximity",
                "bullish_support_rejection",
                "strong_downtrend_excluded",
                "defined_risk_put_spread_candidate",
            ),
            evidence={
                "bollinger_middle": middle,
                "bollinger_lower": lower_band,
                "structural_support": structural_support,
                "selected_support": nearest_support,
                "support_distance_atr": distance / context.features.atr,
                "slow_average": context.features.sma_slow,
                "market_regime": context.features.regime,
                "vehicle_constraint": "bull_put_credit_spread_only",
                "hypothesis_status": "research_only_unproven",
            },
        )


@dataclass(frozen=True, slots=True)
class SupportDeltaPutIncomeV2Strategy(SupportDeltaPutIncomeStrategy):
    """Preregistered cost-aware V2; it excludes the V1 loss regime."""

    strategy_id: str = "support_delta_put_income_v2"
    version: str = "2.0.0"
    minimum_sma_slope: Decimal = Decimal("0.0005")
    maximum_atr_percent: Decimal = Decimal("0.04")

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        slow_window = 30
        if len(context.bars) < max(self.support_lookback + 1, slow_window + 1):
            return None
        current_slow = _mean(
            tuple(bar.close for bar in context.bars[-slow_window:])
        )
        previous_slow = _mean(
            tuple(bar.close for bar in context.bars[-slow_window - 1 : -1])
        )
        slope = (
            (current_slow - previous_slow) / previous_slow
            if previous_slow > ZERO
            else ZERO
        )
        if (
            context.features.regime is not MarketRegime.TREND_UP
            or slope < self.minimum_sma_slope
            or context.features.atr_percent > self.maximum_atr_percent
        ):
            return None
        # Explicit dispatch avoids CPython's zero-argument super() edge case
        # with frozen, slotted dataclass inheritance on the server runtime.
        signal = SupportDeltaPutIncomeStrategy.evaluate(self, context)
        if signal is None:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=signal.action,
            confidence=signal.confidence,
            reference_price=signal.reference_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason_codes=(
                *signal.reason_codes,
                "uptrend_regime_required",
                "positive_slow_average_slope",
                "extreme_volatility_excluded",
            ),
            evidence={
                **signal.evidence,
                "slow_average_slope": slope,
                "maximum_atr_percent": self.maximum_atr_percent,
                "v2_design_basis": (
                    "preregistered_after_v1_range_regime_loss_diagnostic"
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class SupportDeltaPutIncomeV21Strategy(SupportDeltaPutIncomeStrategy):
    """V2.1 measures cumulative slow-average return at the correct scale."""

    strategy_id: str = "support_delta_put_income_v21"
    version: str = "2.1.0"
    slope_lookback: int = 8
    minimum_sma_return: Decimal = Decimal("0.001")
    maximum_atr_percent: Decimal = Decimal("0.04")

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        slow_window = 30
        minimum = slow_window + self.slope_lookback
        if len(context.bars) < max(self.support_lookback + 1, minimum):
            return None
        current = _mean(tuple(bar.close for bar in context.bars[-slow_window:]))
        previous = _mean(tuple(
            bar.close
            for bar in context.bars[
                -slow_window - self.slope_lookback : -self.slope_lookback
            ]
        ))
        cumulative_return = current / previous - Decimal("1")
        if (
            context.features.regime is not MarketRegime.TREND_UP
            or cumulative_return < self.minimum_sma_return
            or context.features.atr_percent > self.maximum_atr_percent
        ):
            return None
        signal = SupportDeltaPutIncomeStrategy.evaluate(self, context)
        if signal is None:
            return None
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=signal.action,
            confidence=signal.confidence,
            reference_price=signal.reference_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason_codes=(*signal.reason_codes, "cumulative_trend_confirmed"),
            evidence={
                **signal.evidence,
                "slow_average_return": cumulative_return,
                "slope_lookback": self.slope_lookback,
                "v21_design_basis": "corrected_slope_scale_after_zero_trade_v2",
            },
        )


@dataclass(frozen=True, slots=True)
class SignalInversionStrategy:
    """Causal research control that mirrors a frozen source signal."""

    strategy_id: str = "inverse_control"
    version: str = "1.0.0"
    source_strategy_id: str = "breakout_retest"

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        sources = {
            "breakout_retest": BreakoutRetestStrategy,
            "trend_pullback": TrendPullbackStrategy,
            "chart_pattern_confluence": ChartPatternConfluenceStrategy,
            "t3_range_trend": T3RangeTrendStrategy,
        }
        source_type = sources.get(self.source_strategy_id)
        if source_type is None:
            raise ValueError("Unsupported inversion source strategy")
        source = source_type().evaluate(context)
        if source is None:
            return None
        risk_distance = abs(source.reference_price - source.stop_price)
        reward_distance = abs(source.target_price - source.reference_price)
        action = (
            SignalAction.ENTER_SHORT
            if source.action is SignalAction.ENTER_LONG
            else SignalAction.ENTER_LONG
        )
        stop = (
            source.reference_price + risk_distance
            if action is SignalAction.ENTER_SHORT
            else source.reference_price - risk_distance
        )
        target = (
            source.reference_price - reward_distance
            if action is SignalAction.ENTER_SHORT
            else source.reference_price + reward_distance
        )
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=action,
            confidence=source.confidence,
            reference_price=source.reference_price,
            stop_price=stop,
            target_price=target,
            reason_codes=("inverse_control", f"source:{self.source_strategy_id}"),
            evidence={
                "source_signal_id": source.signal_id,
                "source_strategy_id": self.source_strategy_id,
                "source_reason_codes": source.reason_codes,
                "inversion_role": "research_control_not_execution_authority",
            },
        )


def default_equity_strategies() -> dict[str, Strategy]:
    strategies: tuple[Strategy, ...] = (
        BreakoutRetestStrategy(),
        TrendPullbackStrategy(),
        VolatilityContractionBreakoutStrategy(),
        RangeMeanReversionStrategy(),
        T3RangeTrendStrategy(),
        ChartPatternConfluenceStrategy(),
        SupportDeltaPutIncomeStrategy(),
        SupportDeltaPutIncomeV2Strategy(),
        SupportDeltaPutIncomeV21Strategy(),
        SignalInversionStrategy("breakout_retest_inverse", "1.0.0", "breakout_retest"),
        SignalInversionStrategy("trend_pullback_inverse", "1.0.0", "trend_pullback"),
        SignalInversionStrategy("chart_pattern_inverse", "1.0.0", "chart_pattern_confluence"),
        SignalInversionStrategy("t3_range_trend_inverse", "1.0.0", "t3_range_trend"),
    )
    return {
        strategy.strategy_id: strategy for strategy in strategies
    }


def equity_strategy_from_parameters(
    parameters: dict[str, object],
) -> Strategy:
    strategy_id = str(parameters.get("strategy_id", ""))
    strategy_types = {
        "breakout_retest": BreakoutRetestStrategy,
        "trend_pullback": TrendPullbackStrategy,
        "volatility_contraction": (
            VolatilityContractionBreakoutStrategy
        ),
        "range_mean_reversion": RangeMeanReversionStrategy,
        "t3_range_trend": T3RangeTrendStrategy,
        "chart_pattern_confluence": ChartPatternConfluenceStrategy,
        "support_delta_put_income": SupportDeltaPutIncomeStrategy,
        "support_delta_put_income_v2": SupportDeltaPutIncomeV2Strategy,
        "support_delta_put_income_v21": SupportDeltaPutIncomeV21Strategy,
        "breakout_retest_inverse": SignalInversionStrategy,
        "trend_pullback_inverse": SignalInversionStrategy,
        "chart_pattern_inverse": SignalInversionStrategy,
        "t3_range_trend_inverse": SignalInversionStrategy,
    }
    strategy_type = strategy_types.get(strategy_id)
    if strategy_type is None:
        raise ValueError(
            f"Unsupported equity strategy: {strategy_id}"
        )
    expected_fields = {
        definition.name
        for definition in fields(strategy_type)
    }
    if set(parameters) != expected_fields:
        raise ValueError(
            "Strategy parameters do not exactly match its "
            "constructor fields"
        )
    normalized: dict[str, object] = {}
    for definition in fields(strategy_type):
        value = parameters[definition.name]
        default = definition.default
        if isinstance(default, Decimal):
            normalized[definition.name] = Decimal(str(value))
        elif isinstance(default, int):
            normalized[definition.name] = int(value)
        elif isinstance(default, str):
            normalized[definition.name] = str(value)
        else:
            raise TypeError(
                "Unsupported strategy parameter type for "
                f"{definition.name}"
            )
    return strategy_type(**normalized)
