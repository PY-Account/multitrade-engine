from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from multitrade.domain import ZERO
from multitrade.market import MarketBar


class MarketRegime(StrEnum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    symbol: str
    bar_timestamp: str
    close: Decimal
    sma_fast: Decimal
    sma_slow: Decimal
    atr: Decimal
    atr_percent: Decimal
    average_volume: Decimal
    relative_volume: Decimal
    return_volatility: Decimal
    relative_volatility: Decimal
    donchian_high: Decimal
    donchian_low: Decimal
    trend_strength: Decimal
    regime: MarketRegime
    sample_size: int


def _mean(values: Iterable[Decimal]) -> Decimal:
    materialized = tuple(values)
    if not materialized:
        return ZERO
    return sum(materialized, start=ZERO) / Decimal(len(materialized))


def _standard_deviation(values: Iterable[Decimal]) -> Decimal:
    materialized = tuple(values)
    if len(materialized) < 2:
        return ZERO
    average = _mean(materialized)
    variance = _mean((value - average) ** 2 for value in materialized)
    return variance.sqrt()


class FeatureEngine:
    def __init__(
        self,
        *,
        fast_window: int = 10,
        slow_window: int = 30,
        atr_window: int = 14,
        channel_window: int = 20,
        volume_window: int = 20,
    ) -> None:
        windows = (
            fast_window,
            slow_window,
            atr_window,
            channel_window,
            volume_window,
        )
        if any(window < 2 for window in windows):
            raise ValueError("Feature windows must be at least two")
        if fast_window >= slow_window:
            raise ValueError("Fast window must be below slow window")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.atr_window = atr_window
        self.channel_window = channel_window
        self.volume_window = volume_window

    @property
    def minimum_bars(self) -> int:
        return max(
            self.slow_window + 1,
            self.atr_window + 1,
            self.channel_window + 1,
            self.volume_window + 1,
        )

    def calculate(self, bars: Iterable[MarketBar]) -> FeatureSnapshot:
        ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
        if not ordered:
            raise ValueError("At least one market bar is required")
        latest = ordered[-1]
        if len(ordered) < self.minimum_bars:
            return FeatureSnapshot(
                symbol=latest.symbol,
                bar_timestamp=latest.timestamp.isoformat(),
                close=latest.close,
                sma_fast=ZERO,
                sma_slow=ZERO,
                atr=ZERO,
                atr_percent=ZERO,
                average_volume=ZERO,
                relative_volume=ZERO,
                return_volatility=ZERO,
                relative_volatility=ZERO,
                donchian_high=ZERO,
                donchian_low=ZERO,
                trend_strength=ZERO,
                regime=MarketRegime.INSUFFICIENT_DATA,
                sample_size=len(ordered),
            )

        closes = tuple(bar.close for bar in ordered)
        sma_fast = _mean(closes[-self.fast_window :])
        sma_slow = _mean(closes[-self.slow_window :])
        true_ranges: list[Decimal] = []
        for previous, current in zip(ordered, ordered[1:]):
            true_ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
        atr = _mean(true_ranges[-self.atr_window :])
        previous_volumes = tuple(
            bar.volume
            for bar in ordered[-self.volume_window - 1 : -1]
        )
        average_volume = _mean(previous_volumes)
        relative_volume = (
            latest.volume / average_volume
            if average_volume > ZERO
            else ZERO
        )
        returns = tuple(
            current / previous - Decimal("1")
            for previous, current in zip(closes, closes[1:])
            if previous > ZERO
        )
        baseline_returns = returns[-self.slow_window :]
        recent_returns = returns[-self.fast_window :]
        baseline_volatility = _standard_deviation(baseline_returns)
        return_volatility = _standard_deviation(recent_returns)
        relative_volatility = (
            return_volatility / baseline_volatility
            if baseline_volatility > ZERO
            else Decimal("1")
        )
        channel = ordered[-self.channel_window - 1 : -1]
        donchian_high = max(bar.high for bar in channel)
        donchian_low = min(bar.low for bar in channel)
        trend_strength = (
            abs(sma_fast - sma_slow) / latest.close
            if latest.close > ZERO
            else ZERO
        )
        atr_percent = (
            atr / latest.close if latest.close > ZERO else ZERO
        )

        if (
            relative_volatility >= Decimal("1.8")
            and return_volatility > ZERO
        ):
            regime = MarketRegime.HIGH_VOLATILITY
        elif (
            sma_fast > sma_slow * Decimal("1.001")
            and latest.close > sma_fast
        ):
            regime = MarketRegime.TREND_UP
        elif (
            sma_fast < sma_slow * Decimal("0.999")
            and latest.close < sma_fast
        ):
            regime = MarketRegime.TREND_DOWN
        else:
            regime = MarketRegime.RANGE

        return FeatureSnapshot(
            symbol=latest.symbol,
            bar_timestamp=latest.timestamp.isoformat(),
            close=latest.close,
            sma_fast=sma_fast,
            sma_slow=sma_slow,
            atr=atr,
            atr_percent=atr_percent,
            average_volume=average_volume,
            relative_volume=relative_volume,
            return_volatility=return_volatility,
            relative_volatility=relative_volatility,
            donchian_high=donchian_high,
            donchian_low=donchian_low,
            trend_strength=trend_strength,
            regime=regime,
            sample_size=len(ordered),
        )
