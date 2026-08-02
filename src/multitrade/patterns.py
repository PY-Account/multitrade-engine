from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from multitrade.domain import ZERO
from multitrade.market import MarketBar


ONE = Decimal("1")


class PatternDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass(frozen=True, slots=True)
class PatternMatch:
    """A causal pattern observation made only from closed bars."""

    pattern_id: str
    direction: PatternDirection
    score: Decimal
    invalidation_price: Decimal
    evidence: dict[str, object]


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, start=ZERO) / Decimal(len(values))


def _slope(values: tuple[Decimal, ...]) -> Decimal:
    """Ordinary-least-squares slope over equally spaced observations."""

    count = len(values)
    x_mean = Decimal(count - 1) / Decimal("2")
    y_mean = _mean(values)
    numerator = sum(
        (
            (Decimal(index) - x_mean) * (value - y_mean)
            for index, value in enumerate(values)
        ),
        start=ZERO,
    )
    denominator = sum(
        (
            (Decimal(index) - x_mean) ** 2
            for index in range(count)
        ),
        start=ZERO,
    )
    return numerator / denominator if denominator else ZERO


def _candle_parts(
    bar: MarketBar,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    span = bar.high - bar.low
    body = abs(bar.close - bar.open)
    upper = bar.high - max(bar.open, bar.close)
    lower = min(bar.open, bar.close) - bar.low
    return span, body, upper, lower


def _candlestick_matches(
    bars: tuple[MarketBar, ...],
    *,
    wick_body_ratio: Decimal,
    doji_body_fraction: Decimal,
) -> list[PatternMatch]:
    previous, latest = bars[-2], bars[-1]
    matches: list[PatternMatch] = []
    span, body, upper, lower = _candle_parts(latest)
    safe_body = max(body, span * Decimal("0.02"))

    bullish_engulfing = (
        previous.close < previous.open
        and latest.close > latest.open
        and latest.open <= previous.close
        and latest.close >= previous.open
    )
    bearish_engulfing = (
        previous.close > previous.open
        and latest.close < latest.open
        and latest.open >= previous.close
        and latest.close <= previous.open
    )
    if bullish_engulfing or bearish_engulfing:
        direction = (
            PatternDirection.BULLISH
            if bullish_engulfing
            else PatternDirection.BEARISH
        )
        invalidation = latest.low if bullish_engulfing else latest.high
        matches.append(
            PatternMatch(
                "engulfing_candle",
                direction,
                Decimal("0.65"),
                invalidation,
                {
                    "body_engulfed": True,
                    "previous_open": previous.open,
                    "previous_close": previous.close,
                },
            )
        )

    if (
        span > ZERO
        and latest.close > latest.open
        and lower >= safe_body * wick_body_ratio
        and upper <= safe_body
        and latest.close >= latest.low + span * Decimal("0.65")
    ):
        matches.append(
            PatternMatch(
                "bullish_pin_bar",
                PatternDirection.BULLISH,
                Decimal("0.55"),
                latest.low,
                {"lower_wick_body_ratio": lower / safe_body},
            )
        )

    if (
        span > ZERO
        and body / span <= doji_body_fraction
        and upper / span <= doji_body_fraction
        and lower / span >= Decimal("0.60")
    ):
        matches.append(
            PatternMatch(
                "dragonfly_doji",
                PatternDirection.BULLISH,
                Decimal("0.50"),
                latest.low,
                {"body_fraction": body / span, "lower_wick_fraction": lower / span},
            )
        )

    if (
        span > ZERO
        and body / span <= doji_body_fraction
        and lower / span <= doji_body_fraction
        and upper / span >= Decimal("0.60")
    ):
        matches.append(
            PatternMatch(
                "gravestone_doji",
                PatternDirection.BEARISH,
                Decimal("0.50"),
                latest.high,
                {"body_fraction": body / span, "upper_wick_fraction": upper / span},
            )
        )
    return matches


def _trap_match(
    bars: tuple[MarketBar, ...],
    *,
    lookback: int,
    tolerance: Decimal,
) -> PatternMatch | None:
    history = bars[-lookback - 1 : -1]
    latest = bars[-1]
    support = min(bar.low for bar in history)
    resistance = max(bar.high for bar in history)
    bull = (
        latest.low < support * (ONE - tolerance)
        and latest.close > support
        and latest.close > latest.open
    )
    bear = (
        latest.high > resistance * (ONE + tolerance)
        and latest.close < resistance
        and latest.close < latest.open
    )
    if not bull and not bear:
        return None
    return PatternMatch(
        "bear_trap" if bull else "bull_trap",
        PatternDirection.BULLISH if bull else PatternDirection.BEARISH,
        Decimal("0.75"),
        latest.low if bull else latest.high,
        {
            "support": support,
            "resistance": resistance,
            "false_break_fraction": (
                (support - latest.low) / support
                if bull
                else (latest.high - resistance) / resistance
            ),
        },
    )


def _bull_flag_match(
    bars: tuple[MarketBar, ...],
    *,
    pole_bars: int,
    flag_bars: int,
    minimum_pole_return: Decimal,
    maximum_retracement: Decimal,
    volume_multiplier: Decimal,
) -> PatternMatch | None:
    window = bars[-(pole_bars + flag_bars + 1) :]
    pole = window[: pole_bars + 1]
    flag = window[pole_bars:-1]
    latest = window[-1]
    pole_start = pole[0].close
    pole_high = max(bar.high for bar in pole)
    pole_return = pole[-1].close / pole_start - ONE
    pole_height = pole_high - pole_start
    flag_closes = tuple(bar.close for bar in flag)
    normalized_slope = _slope(flag_closes) / max(_mean(flag_closes), Decimal("0.01"))
    retracement = (pole_high - min(bar.low for bar in flag)) / max(
        pole_height, Decimal("0.01")
    )
    flag_high = max(bar.high for bar in flag)
    flag_volume = _mean(tuple(bar.volume for bar in flag))
    confirmed = (
        pole_return >= minimum_pole_return
        and normalized_slope <= Decimal("0.001")
        and normalized_slope >= Decimal("-0.01")
        and retracement <= maximum_retracement
        and latest.close > flag_high
        and latest.close > latest.open
        and latest.volume >= flag_volume * volume_multiplier
    )
    if not confirmed:
        return None
    return PatternMatch(
        "bull_flag",
        PatternDirection.BULLISH,
        Decimal("0.85"),
        min(bar.low for bar in flag),
        {
            "pole_return": pole_return,
            "flag_normalized_slope": normalized_slope,
            "retracement": retracement,
            "flag_high": flag_high,
            "breakout_volume_ratio": latest.volume / max(flag_volume, ONE),
        },
    )


def _fvg_retest_match(bars: tuple[MarketBar, ...]) -> PatternMatch | None:
    # Search only completed three-candle imbalances before the signal bar.
    candidates = bars[-12:-1]
    latest = bars[-1]
    for index in range(len(candidates) - 1, 1, -1):
        first = candidates[index - 2]
        third = candidates[index]
        if third.low > first.high:
            lower, upper = first.high, third.low
            midpoint = (lower + upper) / Decimal("2")
            if latest.low <= upper and latest.close > midpoint and latest.close > latest.open:
                return PatternMatch(
                    "bullish_fvg_retest",
                    PatternDirection.BULLISH,
                    Decimal("0.60"),
                    lower,
                    {"gap_low": lower, "gap_high": upper, "gap_midpoint": midpoint},
                )
        if third.high < first.low:
            lower, upper = third.high, first.low
            midpoint = (lower + upper) / Decimal("2")
            if latest.high >= lower and latest.close < midpoint and latest.close < latest.open:
                return PatternMatch(
                    "bearish_fvg_retest",
                    PatternDirection.BEARISH,
                    Decimal("0.60"),
                    upper,
                    {"gap_low": lower, "gap_high": upper, "gap_midpoint": midpoint},
                )
    return None


def _moving_average_cross(
    bars: tuple[MarketBar, ...], *, fast: int, slow: int
) -> PatternMatch | None:
    if len(bars) < slow + 1:
        return None
    closes = tuple(bar.close for bar in bars)
    fast_now = _mean(closes[-fast:])
    slow_now = _mean(closes[-slow:])
    fast_before = _mean(closes[-fast - 1 : -1])
    slow_before = _mean(closes[-slow - 1 : -1])
    bullish = fast_before <= slow_before and fast_now > slow_now
    bearish = fast_before >= slow_before and fast_now < slow_now
    if not bullish and not bearish:
        return None
    recent = bars[-fast:]
    return PatternMatch(
        "golden_cross" if bullish else "death_cross",
        PatternDirection.BULLISH if bullish else PatternDirection.BEARISH,
        Decimal("0.45"),
        min(bar.low for bar in recent) if bullish else max(bar.high for bar in recent),
        {"fast_average": fast_now, "slow_average": slow_now},
    )


def _confirmed_pivots(
    bars: tuple[MarketBar, ...], *, radius: int = 2
) -> tuple[tuple[int, str, Decimal], ...]:
    """Return pivots confirmed by later closed bars, never by the signal bar."""

    source = bars[:-1]
    pivots: list[tuple[int, str, Decimal]] = []
    for index in range(radius, len(source) - radius):
        window = source[index - radius : index + radius + 1]
        candidate = source[index]
        neighbours = window[:radius] + window[radius + 1 :]
        if candidate.high > max(bar.high for bar in neighbours):
            pivots.append((index, "high", candidate.high))
        if candidate.low < min(bar.low for bar in neighbours):
            pivots.append((index, "low", candidate.low))
    collapsed: list[tuple[int, str, Decimal]] = []
    for pivot in pivots:
        if collapsed and collapsed[-1][1] == pivot[1]:
            previous = collapsed[-1]
            more_extreme = (
                pivot[2] > previous[2]
                if pivot[1] == "high"
                else pivot[2] < previous[2]
            )
            if more_extreme:
                collapsed[-1] = pivot
        else:
            collapsed.append(pivot)
    return tuple(collapsed)


def _swing_structure_matches(
    bars: tuple[MarketBar, ...], *, similarity: Decimal = Decimal("0.15")
) -> list[PatternMatch]:
    pivots = _confirmed_pivots(bars[-80:])
    latest = bars[-1]
    matches: list[PatternMatch] = []
    if len(pivots) >= 4:
        a, b, c, d = pivots[-4:]
        alternating = tuple(row[1] for row in (a, b, c, d))
        ab = abs(b[2] - a[2])
        cd = abs(d[2] - c[2])
        leg_error = abs(cd / max(ab, Decimal("0.01")) - ONE)
        if leg_error <= similarity:
            bullish = alternating == ("low", "high", "low", "high")
            bearish = alternating == ("high", "low", "high", "low")
            if bullish or bearish:
                matches.append(
                    PatternMatch(
                        "abcd",
                        PatternDirection.BULLISH if bullish else PatternDirection.BEARISH,
                        Decimal("0.40"),
                        c[2],
                        {"ab_length": ab, "cd_length": cd, "leg_error": leg_error},
                    )
                )
    if len(pivots) >= 5:
        p1, p2, p3, p4, p5 = pivots[-5:]
        types = tuple(row[1] for row in (p1, p2, p3, p4, p5))
        shoulder_scale = max(abs(p1[2]), Decimal("0.01"))
        shoulder_error = abs(p5[2] - p1[2]) / shoulder_scale
        if (
            types == ("high", "low", "high", "low", "high")
            and p3[2] > max(p1[2], p5[2])
            and shoulder_error <= similarity
        ):
            neckline = (p2[2] + p4[2]) / Decimal("2")
            if latest.close < neckline:
                matches.append(
                    PatternMatch(
                        "head_and_shoulders",
                        PatternDirection.BEARISH,
                        Decimal("0.80"),
                        p3[2],
                        {"neckline": neckline, "shoulder_error": shoulder_error},
                    )
                )
        if (
            types == ("low", "high", "low", "high", "low")
            and p3[2] < min(p1[2], p5[2])
            and shoulder_error <= similarity
        ):
            neckline = (p2[2] + p4[2]) / Decimal("2")
            if latest.close > neckline:
                matches.append(
                    PatternMatch(
                        "inverse_head_and_shoulders",
                        PatternDirection.BULLISH,
                        Decimal("0.80"),
                        p3[2],
                        {"neckline": neckline, "shoulder_error": shoulder_error},
                    )
                )
    return matches


def detect_chart_patterns(
    bars: tuple[MarketBar, ...],
    *,
    trap_lookback: int = 20,
    trap_tolerance: Decimal = Decimal("0.002"),
    pole_bars: int = 8,
    flag_bars: int = 5,
    minimum_pole_return: Decimal = Decimal("0.025"),
    maximum_flag_retracement: Decimal = Decimal("0.50"),
    volume_multiplier: Decimal = Decimal("1.10"),
    wick_body_ratio: Decimal = Decimal("2"),
    doji_body_fraction: Decimal = Decimal("0.10"),
    fast_average: int = 10,
    slow_average: int = 30,
) -> tuple[PatternMatch, ...]:
    """Return deterministic, closed-bar pattern evidence without look-ahead."""

    required = max(trap_lookback + 1, pole_bars + flag_bars + 1, slow_average + 1)
    if len(bars) < required:
        return ()
    matches = _candlestick_matches(
        bars,
        wick_body_ratio=wick_body_ratio,
        doji_body_fraction=doji_body_fraction,
    )
    optional = (
        _trap_match(bars, lookback=trap_lookback, tolerance=trap_tolerance),
        _bull_flag_match(
            bars,
            pole_bars=pole_bars,
            flag_bars=flag_bars,
            minimum_pole_return=minimum_pole_return,
            maximum_retracement=maximum_flag_retracement,
            volume_multiplier=volume_multiplier,
        ),
        _fvg_retest_match(bars),
        _moving_average_cross(bars, fast=fast_average, slow=slow_average),
    )
    matches.extend(match for match in optional if match is not None)
    matches.extend(_swing_structure_matches(bars))
    return tuple(matches)
