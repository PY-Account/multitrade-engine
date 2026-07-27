from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock

from multitrade.domain import AssetClass
from multitrade.features import FeatureEngine, MarketRegime
from multitrade.market import (
    AlpacaMarketDataClient,
    MarketBar,
    closed_bars,
    timeframe_seconds,
)


def bar(
    index: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        timeframe="5Min",
        timestamp=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        + timedelta(minutes=5 * index),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        trade_count=10,
        vwap=Decimal(close),
        feed="iex",
    )


class MarketDataTests(TestCase):
    def test_timeframe_validation(self) -> None:
        self.assertEqual(timeframe_seconds("5Min"), 300)
        self.assertEqual(timeframe_seconds("1Day"), 86400)
        with self.assertRaises(ValueError):
            timeframe_seconds("60Min")

    def test_only_closed_bars_are_returned(self) -> None:
        bars = (
            bar(
                0,
                open_price="100",
                high="101",
                low="99",
                close="100",
            ),
            bar(
                1,
                open_price="100",
                high="101",
                low="99",
                close="100",
            ),
        )
        result = closed_bars(
            bars,
            now=bars[1].timestamp + timedelta(minutes=4),
        )
        self.assertEqual(result, (bars[0],))

    def test_alpaca_stock_bars_are_normalized(self) -> None:
        client = AlpacaMarketDataClient(
            "paper-key", "paper-secret", feed="iex"
        )
        client._request = Mock(
            return_value={
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-01-02T14:30:00Z",
                            "o": 100,
                            "h": 102,
                            "l": 99,
                            "c": 101,
                            "v": 5000,
                            "n": 200,
                            "vw": 100.8,
                        }
                    ]
                },
                "next_page_token": None,
            }
        )
        start = datetime(2026, 1, 2, tzinfo=timezone.utc)
        result = client.fetch_stock_bars(
            ["AAPL"], "5Min", start, start + timedelta(days=1)
        )

        self.assertEqual(result["AAPL"][0].close, Decimal("101"))
        self.assertEqual(result["AAPL"][0].feed, "iex")


class FeatureTests(TestCase):
    def test_trending_bars_produce_features_without_lookahead(self) -> None:
        bars = tuple(
            bar(
                index,
                open_price=str(100 + index),
                high=str(Decimal(101 + index)),
                low=str(Decimal("99.5") + index),
                close=str(Decimal("100.75") + index),
                volume=str(1000 + index * 10),
            )
            for index in range(40)
        )

        features = FeatureEngine().calculate(bars)

        self.assertEqual(features.sample_size, 40)
        self.assertEqual(features.regime, MarketRegime.TREND_UP)
        self.assertGreater(features.atr, Decimal("0"))
        self.assertGreater(features.sma_fast, features.sma_slow)
