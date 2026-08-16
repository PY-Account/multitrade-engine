from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from multitrade.domain import AssetClass, ZERO


MARKET_DATA_URL = "https://data.alpaca.markets"
_STOCK_BAR_SYMBOL_BATCH_SIZE = 25
_STOCK_BAR_SYMBOL_REQUEST_LIMIT = 600
_STOCK_BAR_TIME_WINDOW_DAYS = 30
_STOCK_BAR_TOTAL_PAGE_LIMIT = 240
_MARKET_DATA_MIN_REQUEST_INTERVAL_SECONDS = 0.20
_MARKET_DATA_RATE_LIMIT_ATTEMPTS = 8
_TIMEFRAME = re.compile(
    r"^(?P<count>[1-9][0-9]*)(?P<unit>Min|T|Hour|H|Day|D)$"
)


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    asset_class: AssetClass
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int
    vwap: Decimal | None
    feed: str
    adjustment: str = "raw"

    def __post_init__(self) -> None:
        if not self.symbol or not self.feed:
            raise ValueError("Market bar symbol and feed are required")
        if self.adjustment not in {
            "raw",
            "split",
            "dividend",
            "spin-off",
            "all",
        }:
            raise ValueError("Unsupported market-bar adjustment")
        if self.timestamp.tzinfo is None:
            raise ValueError("Market bar timestamp must be timezone-aware")
        if min(self.open, self.high, self.low, self.close) <= ZERO:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("Bar high is inconsistent with OHLC values")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("Bar low is inconsistent with OHLC values")
        if self.volume < ZERO or self.trade_count < 0:
            raise ValueError("Volume and trade count cannot be negative")


def timeframe_seconds(value: str) -> int:
    match = _TIMEFRAME.fullmatch(value)
    if match is None:
        raise ValueError(f"Unsupported timeframe: {value}")
    count = int(match.group("count"))
    unit = match.group("unit")
    multiplier = {
        "Min": 60,
        "T": 60,
        "Hour": 3600,
        "H": 3600,
        "Day": 86400,
        "D": 86400,
    }[unit]
    if unit in {"Min", "T"} and count > 59:
        raise ValueError("Minute timeframe must be between 1 and 59")
    if unit in {"Hour", "H"} and count > 23:
        raise ValueError("Hour timeframe must be between 1 and 23")
    if unit in {"Day", "D"} and count != 1:
        raise ValueError("Only the 1Day timeframe is supported")
    return count * multiplier


def closed_bars(
    bars: Iterable[MarketBar],
    *,
    now: datetime | None = None,
) -> tuple[MarketBar, ...]:
    checked_at = now or datetime.now(timezone.utc)
    result = [
        bar
        for bar in bars
        if (
            bar.timestamp.timestamp() + timeframe_seconds(bar.timeframe)
            <= checked_at.timestamp()
        )
    ]
    return tuple(sorted(result, key=lambda bar: bar.timestamp))


class AlpacaMarketDataClient:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        base_url: str = MARKET_DATA_URL,
        feed: str = "iex",
        timeout_seconds: int = 20,
    ) -> None:
        if base_url.rstrip("/") != MARKET_DATA_URL:
            raise ValueError(
                "AlpacaMarketDataClient refuses unknown data endpoints"
            )
        if not key_id or not secret_key:
            raise ValueError("Alpaca market-data credentials are required")
        if feed not in {"iex", "sip"}:
            raise ValueError("Stock data feed must be iex or sip")
        self.base_url = MARKET_DATA_URL
        self.feed = feed
        self.timeout_seconds = timeout_seconds
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self.request_ids: list[str] = []
        self._last_request_monotonic: float = 0.0

    def fetch_most_active_stocks(
        self,
        *,
        top: int = 30,
        by: str = "volume",
    ) -> tuple[str, ...]:
        """Return Alpaca's current most-active US stock symbols."""
        if not 1 <= top <= 100:
            raise ValueError("Most-active result count must be 1-100")
        if by not in {"volume", "trades"}:
            raise ValueError("Most-active ranking must be volume or trades")
        self.request_ids = []
        payload = self._request(
            "/v1beta1/screener/stocks/most-actives",
            {"top": str(top), "by": by},
        )
        rows = payload.get("most_actives")
        if not isinstance(rows, list):
            raise MarketDataError(
                "Alpaca most-actives response did not contain a list"
            )
        return tuple(
            dict.fromkeys(
                str(row.get("symbol", "")).strip().upper()
                for row in rows
                if isinstance(row, dict)
                and str(row.get("symbol", "")).strip()
            )
        )

    def fetch_stock_bars(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        max_pages: int = 60,
        adjustment: str = "raw",
    ) -> dict[str, tuple[MarketBar, ...]]:
        timeframe_seconds(timeframe)
        if not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be between 1 and 100")
        normalized_symbols = tuple(
            dict.fromkeys(
                symbol.strip().upper()
                for symbol in symbols
                if symbol.strip()
            )
        )
        if not normalized_symbols:
            raise ValueError("At least one stock symbol is required")
        if len(normalized_symbols) > _STOCK_BAR_SYMBOL_REQUEST_LIMIT:
            raise ValueError(
                "At most "
                f"{_STOCK_BAR_SYMBOL_REQUEST_LIMIT} symbols may be "
                "requested"
            )
        if adjustment not in {
            "raw",
            "split",
            "dividend",
            "spin-off",
            "all",
        }:
            raise ValueError("Unsupported stock-bar adjustment")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Market-data boundaries must be timezone-aware")
        if start >= end:
            raise ValueError("Market-data start must precede end")

        self.request_ids = []
        grouped: dict[str, list[MarketBar]] = {
            symbol: [] for symbol in normalized_symbols
        }
        total_pages = 0
        for batch_start in range(
            0,
            len(normalized_symbols),
            _STOCK_BAR_SYMBOL_BATCH_SIZE,
        ):
            symbol_batch = normalized_symbols[
                batch_start : (
                    batch_start + _STOCK_BAR_SYMBOL_BATCH_SIZE
                )
            ]
            window_start = start
            while window_start < end:
                window_end = min(
                    window_start
                    + timedelta(
                        days=_STOCK_BAR_TIME_WINDOW_DAYS
                    ),
                    end,
                )
                page_token: str | None = None
                seen_page_tokens: set[str] = set()
                for _ in range(max_pages):
                    total_pages += 1
                    if total_pages > _STOCK_BAR_TOTAL_PAGE_LIMIT:
                        raise MarketDataError(
                            "Alpaca stock-bars pagination exceeded "
                            f"the {_STOCK_BAR_TOTAL_PAGE_LIMIT}-page "
                            "total safety limit"
                        )
                    query = {
                        "symbols": ",".join(symbol_batch),
                        "timeframe": timeframe,
                        "start": window_start.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "end": window_end.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "feed": self.feed,
                        "limit": "10000",
                        "sort": "asc",
                        "adjustment": adjustment,
                    }
                    if page_token:
                        query["page_token"] = page_token
                    payload = self._request(
                        "/v2/stocks/bars", query
                    )
                    bars_payload = payload.get("bars")
                    if not isinstance(bars_payload, dict):
                        raise MarketDataError(
                            "Alpaca stock-bars response did not "
                            "contain a bars object"
                        )
                    for symbol, rows in bars_payload.items():
                        if (
                            symbol not in grouped
                            or not isinstance(rows, list)
                        ):
                            continue
                        grouped[symbol].extend(
                            self._parse_stock_bar(
                                symbol,
                                timeframe,
                                row,
                                adjustment,
                            )
                            for row in rows
                        )
                    next_page_token = payload.get(
                        "next_page_token"
                    )
                    if not next_page_token:
                        break
                    page_token = str(next_page_token)
                    if page_token in seen_page_tokens:
                        raise MarketDataError(
                            "Alpaca stock-bars pagination repeated "
                            "a page token"
                        )
                    seen_page_tokens.add(page_token)
                else:
                    raise MarketDataError(
                        "Alpaca stock-bars pagination exceeded the "
                        f"{max_pages}-page per-window safety limit"
                    )
                window_start = window_end

        return {
            symbol: tuple(
                sorted(
                    {
                        bar.timestamp.isoformat(): bar
                        for bar in bars
                    }.values(),
                    key=lambda bar: bar.timestamp,
                )
            )
            for symbol, bars in grouped.items()
        }

    def _parse_stock_bar(
        self,
        symbol: str,
        timeframe: str,
        row: dict[str, Any],
        adjustment: str,
    ) -> MarketBar:
        if not isinstance(row, dict):
            raise MarketDataError("Alpaca stock bar was not an object")
        raw_timestamp = str(row.get("t", ""))
        try:
            timestamp = datetime.fromisoformat(
                raw_timestamp.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise MarketDataError(
                f"Invalid Alpaca bar timestamp for {symbol}"
            ) from exc
        return MarketBar(
            symbol=symbol,
            asset_class=AssetClass.STOCK,
            timeframe=timeframe,
            timestamp=timestamp,
            open=Decimal(str(row["o"])),
            high=Decimal(str(row["h"])),
            low=Decimal(str(row["l"])),
            close=Decimal(str(row["c"])),
            volume=Decimal(str(row.get("v", "0"))),
            trade_count=int(row.get("n") or 0),
            vwap=(
                Decimal(str(row["vw"]))
                if row.get("vw") is not None
                else None
            ),
            feed=self.feed,
            adjustment=adjustment,
        )

    def _request(
        self, path: str, query: dict[str, str]
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(query)}"
        request = Request(url, headers=self._headers, method="GET")
        attempts = 0
        try:
            while True:
                elapsed = time.monotonic() - self._last_request_monotonic
                if elapsed < _MARKET_DATA_MIN_REQUEST_INTERVAL_SECONDS:
                    time.sleep(
                        _MARKET_DATA_MIN_REQUEST_INTERVAL_SECONDS
                        - elapsed
                    )
                try:
                    with urlopen(
                        request, timeout=self.timeout_seconds
                    ) as response:
                        self._last_request_monotonic = time.monotonic()
                        request_id = response.headers.get("X-Request-ID")
                        if request_id:
                            self.request_ids.append(request_id)
                        body = response.read().decode("utf-8")
                    break
                except HTTPError as exc:
                    self._last_request_monotonic = time.monotonic()
                    if (
                        exc.code != 429
                        or attempts >= _MARKET_DATA_RATE_LIMIT_ATTEMPTS
                    ):
                        raise
                    attempts += 1
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        delay = 0.0
                    if delay <= 0:
                        delay = min(60.0, 2.0**attempts)
                    time.sleep(delay)
        except HTTPError as exc:
            request_id = (
                exc.headers.get("X-Request-ID")
                if exc.headers is not None
                else None
            )
            if request_id:
                self.request_ids.append(request_id)
            body = exc.read().decode("utf-8", errors="replace")
            context = f" request_id={request_id}" if request_id else ""
            raise MarketDataError(
                f"Alpaca market data HTTP {exc.code}:{context} {body[:1000]}"
            ) from exc
        except URLError as exc:
            raise MarketDataError(
                f"Cannot reach Alpaca market data: {exc.reason}"
            ) from exc
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise MarketDataError(
                "Alpaca market-data response was not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise MarketDataError(
                "Alpaca market-data response was not an object"
            )
        return payload
