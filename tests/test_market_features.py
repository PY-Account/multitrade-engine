import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
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
        self.assertEqual(result["AAPL"][0].adjustment, "raw")

    def test_research_adjustment_is_sent_and_recorded(self) -> None:
        client = AlpacaMarketDataClient(
            "paper-key", "paper-secret", feed="iex"
        )
        calls = []

        def request(path, query):
            calls.append((path, query))
            return {
                "bars": {
                    "SPY": [
                        {
                            "t": "2026-01-02T05:00:00Z",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100,
                            "v": 5000,
                            "n": 200,
                            "vw": 100,
                        }
                    ]
                },
                "next_page_token": None,
            }

        client._request = request
        start = datetime(2026, 1, 2, tzinfo=timezone.utc)
        result = client.fetch_stock_bars(
            ["SPY"],
            "1Day",
            start,
            start + timedelta(days=2),
            adjustment="all",
        )

        self.assertEqual(calls[0][1]["adjustment"], "all")
        self.assertEqual(result["SPY"][0].adjustment, "all")

    def test_most_active_symbols_are_normalized(self) -> None:
        client = AlpacaMarketDataClient(
            "paper-key", "paper-secret", feed="iex"
        )
        calls = []

        def request(path, query):
            calls.append((path, query))
            return {
                "most_actives": [
                    {"symbol": "amd", "volume": 1000},
                    {"symbol": "AMD", "volume": 900},
                    {"symbol": "PLTR", "volume": 800},
                ]
            }

        client._request = request

        result = client.fetch_most_active_stocks(top=25)

        self.assertEqual(result, ("AMD", "PLTR"))
        self.assertEqual(
            calls,
            [
                (
                    "/v1beta1/screener/stocks/most-actives",
                    {"top": "25", "by": "volume"},
                )
            ],
        )

    def test_existing_database_adds_adjustment_provenance(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE market_bars (
                        asset_class TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        feed TEXT NOT NULL,
                        bar_timestamp TEXT NOT NULL,
                        open_price TEXT NOT NULL,
                        high_price TEXT NOT NULL,
                        low_price TEXT NOT NULL,
                        close_price TEXT NOT NULL,
                        volume TEXT NOT NULL,
                        trade_count INTEGER NOT NULL,
                        vwap TEXT,
                        ingested_at TEXT NOT NULL,
                        PRIMARY KEY (
                            asset_class, symbol, timeframe, feed,
                            bar_timestamp
                        )
                    )
                    """
                )
                connection.commit()
            store = SqliteAuditStore(path)
            adjusted = MarketBar(
                symbol="SPY",
                asset_class=AssetClass.STOCK,
                timeframe="1Day",
                timestamp=datetime(
                    2026, 1, 2, 5, tzinfo=timezone.utc
                ),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000"),
                trade_count=100,
                vwap=Decimal("100"),
                feed="iex",
                adjustment="all",
            )
            store.record_market_bars((adjusted,))
            rows = SqliteAuditReader(path).market_bars(
                "SPY", "1Day"
            )

            self.assertEqual(rows[0]["adjustment"], "all")


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
