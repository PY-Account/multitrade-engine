from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase

from multitrade.domain import AssetClass
from multitrade.features import FeatureEngine
from multitrade.market import MarketBar
from multitrade.patterns import (
    PatternDirection,
    detect_chart_patterns,
)
from multitrade.strategies.base import StrategyContext
from multitrade.strategies.equity import (
    ChartPatternConfluenceStrategy,
    equity_strategy_from_parameters,
)


def bar(
    index: int,
    close: str,
    *,
    open_price: str | None = None,
    high: str | None = None,
    low: str | None = None,
    volume: str = "100",
) -> MarketBar:
    price = Decimal(close)
    opening = Decimal(open_price) if open_price else price
    return MarketBar(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        timeframe="5Min",
        timestamp=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        + timedelta(minutes=index * 5),
        open=opening,
        high=Decimal(high) if high else max(opening, price) + Decimal("0.2"),
        low=Decimal(low) if low else min(opening, price) - Decimal("0.2"),
        close=price,
        volume=Decimal(volume),
        trade_count=10,
        vwap=price,
        feed="iex",
    )


class ChartPatternTests(TestCase):
    def bull_flag_bars(self) -> tuple[MarketBar, ...]:
        rows = [bar(index, "90") for index in range(17)]
        pole = ("90", "92", "94", "96", "98", "100", "102", "104", "106")
        rows.extend(bar(17 + index, close) for index, close in enumerate(pole))
        flag = ("105.5", "105", "104.5", "104")
        rows.extend(bar(26 + index, close) for index, close in enumerate(flag))
        rows.append(
            bar(
                30,
                "107",
                open_price="104",
                high="107.5",
                low="103.8",
                volume="300",
            )
        )
        return tuple(rows)

    def test_bull_flag_has_measured_geometry_and_invalidation(self) -> None:
        matches = detect_chart_patterns(self.bull_flag_bars())
        flag = next(match for match in matches if match.pattern_id == "bull_flag")

        self.assertIs(flag.direction, PatternDirection.BULLISH)
        self.assertGreaterEqual(flag.evidence["pole_return"], Decimal("0.025"))
        self.assertLessEqual(flag.evidence["retracement"], Decimal("0.50"))
        self.assertEqual(flag.invalidation_price, Decimal("103.8"))

    def test_bear_trap_is_a_close_back_above_breached_support(self) -> None:
        rows = [bar(index, "100", high="101", low="99") for index in range(30)]
        rows.append(
            bar(
                30,
                "100.5",
                open_price="98.5",
                high="101",
                low="98",
                volume="250",
            )
        )

        matches = detect_chart_patterns(tuple(rows))

        trap = next(match for match in matches if match.pattern_id == "bear_trap")
        self.assertIs(trap.direction, PatternDirection.BULLISH)
        self.assertEqual(trap.evidence["support"], Decimal("99"))
        self.assertEqual(trap.invalidation_price, Decimal("98"))

    def test_strategy_requires_pattern_regime_and_volume_confluence(self) -> None:
        bars = self.bull_flag_bars()
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=bars,
            features=FeatureEngine().calculate(bars),
            evaluated_at=bars[-1].timestamp + timedelta(minutes=5),
        )

        signal = ChartPatternConfluenceStrategy().evaluate(context)

        self.assertIsNotNone(signal)
        self.assertIn("bull_flag", signal.reason_codes)
        self.assertIn("market_regime_confirmed", signal.reason_codes)
        self.assertEqual(signal.evidence["source"], "tradingkit_pattern_catalog_math_spec")
        self.assertLess(signal.stop_price, signal.reference_price)
        self.assertGreater(signal.target_price, signal.reference_price)

    def test_parameter_factory_rejects_partial_pattern_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly match"):
            equity_strategy_from_parameters(
                {
                    "strategy_id": "chart_pattern_confluence",
                    "version": "1.0.0",
                }
            )
