from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from multitrade.domain import (
    AssetClass,
    OptionLeg,
    OptionRight,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
    ZERO,
)


OPTION_DATA_URL = "https://data.alpaca.markets"
_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z0-9.]{1,6})(?P<expiry>\d{6})"
    r"(?P<right>[CP])(?P<strike>\d{8})$"
)


class OptionDataError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OptionSnapshot:
    symbol: str
    underlying: str
    expiration: date
    right: OptionRight
    strike: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    implied_volatility: Decimal | None
    delta: Decimal | None
    quote_timestamp: str
    feed: str

    def __post_init__(self) -> None:
        if self.bid < ZERO or self.ask < ZERO:
            raise ValueError("Option quotes cannot be negative")
        if self.ask and self.bid > self.ask:
            raise ValueError("Option bid cannot exceed ask")
        if self.strike <= ZERO:
            raise ValueError("Option strike must be positive")

    @property
    def midpoint(self) -> Decimal:
        if self.bid > ZERO and self.ask > ZERO:
            return (self.bid + self.ask) / Decimal("2")
        return max(self.bid, self.ask)

    @property
    def relative_spread(self) -> Decimal | None:
        midpoint = self.midpoint
        if midpoint <= ZERO or self.ask <= ZERO:
            return None
        return (self.ask - self.bid) / midpoint

    def leg(
        self, side: Side, *, conservative_price: Decimal | None = None
    ) -> OptionLeg:
        return OptionLeg(
            symbol=self.symbol,
            underlying=self.underlying,
            expiration=self.expiration,
            right=self.right,
            strike=self.strike,
            side=side,
            ratio=1,
            mark_price=(
                conservative_price
                if conservative_price is not None
                else self.midpoint
            ),
        )


def parse_occ_symbol(symbol: str) -> tuple[str, date, OptionRight, Decimal]:
    normalized = symbol.strip().upper()
    match = _OCC_SYMBOL.fullmatch(normalized)
    if match is None:
        raise ValueError(f"Unsupported OCC option symbol: {symbol}")
    expiration = datetime.strptime(
        match.group("expiry"), "%y%m%d"
    ).date()
    right = (
        OptionRight.CALL
        if match.group("right") == "C"
        else OptionRight.PUT
    )
    strike = Decimal(match.group("strike")) / Decimal("1000")
    return match.group("root"), expiration, right, strike


class AlpacaOptionChainClient:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        feed: str = "indicative",
        timeout_seconds: int = 20,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca market-data credentials are required")
        if feed not in {"indicative", "opra"}:
            raise ValueError("Option feed must be indicative or opra")
        self.feed = feed
        self.timeout_seconds = timeout_seconds
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self.request_ids: list[str] = []

    def fetch_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        strike_gte: Decimal | None = None,
        strike_lte: Decimal | None = None,
        right: OptionRight | None = None,
    ) -> tuple[OptionSnapshot, ...]:
        normalized = underlying.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", normalized):
            raise ValueError("Invalid option underlying")
        query: dict[str, str] = {
            "feed": self.feed,
            "limit": "1000",
        }
        if expiration_gte is not None:
            query["expiration_date_gte"] = expiration_gte.isoformat()
        if expiration_lte is not None:
            query["expiration_date_lte"] = expiration_lte.isoformat()
        if strike_gte is not None:
            query["strike_price_gte"] = format(strike_gte, "f")
        if strike_lte is not None:
            query["strike_price_lte"] = format(strike_lte, "f")
        if right is not None:
            query["type"] = right.value

        snapshots: list[OptionSnapshot] = []
        page_token: str | None = None
        self.request_ids = []
        while True:
            page_query = dict(query)
            if page_token:
                page_query["page_token"] = page_token
            payload = self._request(
                f"/v1beta1/options/snapshots/{normalized}",
                page_query,
            )
            rows = payload.get("snapshots")
            if not isinstance(rows, dict):
                raise OptionDataError(
                    "Alpaca option-chain response omitted snapshots"
                )
            for symbol, row in rows.items():
                snapshots.append(
                    self._parse_snapshot(
                        symbol, normalized, row, self.feed
                    )
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return tuple(
            sorted(
                snapshots,
                key=lambda item: (
                    item.expiration,
                    item.right.value,
                    item.strike,
                ),
            )
        )

    @staticmethod
    def _parse_snapshot(
        symbol: str,
        underlying: str,
        row: dict[str, Any],
        feed: str,
    ) -> OptionSnapshot:
        root, expiration, right, strike = parse_occ_symbol(symbol)
        if not root.startswith(underlying):
            raise OptionDataError(
                f"Option root {root} does not match {underlying}"
            )
        quote = row.get("latestQuote") or {}
        greeks = row.get("greeks") or {}
        bid = Decimal(str(quote.get("bp") or "0"))
        ask = Decimal(str(quote.get("ap") or "0"))
        return OptionSnapshot(
            symbol=symbol,
            underlying=underlying,
            expiration=expiration,
            right=right,
            strike=strike,
            bid=bid,
            ask=ask,
            bid_size=int(quote.get("bs") or 0),
            ask_size=int(quote.get("as") or 0),
            implied_volatility=(
                Decimal(str(row["impliedVolatility"]))
                if row.get("impliedVolatility") is not None
                else None
            ),
            delta=(
                Decimal(str(greeks["delta"]))
                if greeks.get("delta") is not None
                else None
            ),
            quote_timestamp=str(quote.get("t") or ""),
            feed=feed,
        )

    def _request(self, path: str, query: dict[str, str]) -> Any:
        request = Request(
            f"{OPTION_DATA_URL}{path}?{urlencode(query)}",
            headers=self._headers,
            method="GET",
        )
        try:
            with urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                request_id = response.headers.get("X-Request-ID")
                if request_id:
                    self.request_ids.append(request_id)
                body = response.read().decode("utf-8")
                return json.loads(body)
        except HTTPError as exc:
            request_id = (
                exc.headers.get("X-Request-ID")
                if exc.headers is not None
                else None
            )
            body = exc.read().decode("utf-8", errors="replace")
            context = f" request_id={request_id}" if request_id else ""
            raise OptionDataError(
                f"Alpaca option data returned HTTP {exc.code}:"
                f"{context} {body[:1000]}"
            ) from exc
        except URLError as exc:
            raise OptionDataError(
                f"Cannot reach Alpaca option data: {exc.reason}"
            ) from exc


@dataclass(frozen=True, slots=True)
class OptionLiquidityPolicy:
    maximum_relative_spread: Decimal = Decimal("0.20")
    minimum_quote_size: int = 1

    def accepts(self, snapshot: OptionSnapshot) -> bool:
        spread = snapshot.relative_spread
        return (
            snapshot.bid > ZERO
            and snapshot.ask > ZERO
            and spread is not None
            and spread <= self.maximum_relative_spread
            and snapshot.bid_size >= self.minimum_quote_size
            and snapshot.ask_size >= self.minimum_quote_size
        )


class DefinedRiskOptionFactory:
    """Construct debit verticals; it does not choose contracts or submit."""

    def __init__(
        self, liquidity_policy: OptionLiquidityPolicy | None = None
    ) -> None:
        self.liquidity_policy = (
            liquidity_policy or OptionLiquidityPolicy()
        )

    def bull_call_debit_spread(
        self,
        *,
        account_id: str,
        strategy_id: str,
        long_call: OptionSnapshot,
        short_call: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        self._validate_vertical(
            long_call,
            short_call,
            OptionRight.CALL,
            long_strike_should_be_lower=True,
        )
        return self._debit_intent(
            account_id=account_id,
            strategy_id=strategy_id,
            long_contract=long_call,
            short_contract=short_call,
            requested_quantity=requested_quantity,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            structure="bull_call_debit_spread",
        )

    def bear_put_debit_spread(
        self,
        *,
        account_id: str,
        strategy_id: str,
        long_put: OptionSnapshot,
        short_put: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        self._validate_vertical(
            long_put,
            short_put,
            OptionRight.PUT,
            long_strike_should_be_lower=False,
        )
        return self._debit_intent(
            account_id=account_id,
            strategy_id=strategy_id,
            long_contract=long_put,
            short_contract=short_put,
            requested_quantity=requested_quantity,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            structure="bear_put_debit_spread",
        )

    def _validate_vertical(
        self,
        long_contract: OptionSnapshot,
        short_contract: OptionSnapshot,
        right: OptionRight,
        *,
        long_strike_should_be_lower: bool,
    ) -> None:
        if (
            long_contract.underlying != short_contract.underlying
            or long_contract.expiration != short_contract.expiration
        ):
            raise ValueError(
                "Vertical legs need one underlying and expiration"
            )
        if (
            long_contract.right is not right
            or short_contract.right is not right
        ):
            raise ValueError("Vertical legs have the wrong option right")
        strikes_valid = (
            long_contract.strike < short_contract.strike
            if long_strike_should_be_lower
            else long_contract.strike > short_contract.strike
        )
        if not strikes_valid:
            raise ValueError("Vertical strikes are in the wrong order")
        if not self.liquidity_policy.accepts(
            long_contract
        ) or not self.liquidity_policy.accepts(short_contract):
            raise ValueError("Option legs fail the liquidity policy")

    @staticmethod
    def _debit_intent(
        *,
        account_id: str,
        strategy_id: str,
        long_contract: OptionSnapshot,
        short_contract: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
        structure: str,
    ) -> TradeIntent:
        conservative_debit = long_contract.ask - short_contract.bid
        width = abs(long_contract.strike - short_contract.strike)
        if conservative_debit <= ZERO:
            raise ValueError("Debit spread must have a positive net debit")
        if conservative_debit >= width:
            raise ValueError(
                "Debit spread price must remain below strike width"
            )
        return TradeIntent(
            account_id=account_id,
            strategy_id=strategy_id,
            asset_class=AssetClass.OPTION,
            symbol=long_contract.underlying,
            side=Side.BUY,
            requested_quantity=requested_quantity,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=conservative_debit,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            intent_id=signal_id,
            option_legs=(
                long_contract.leg(
                    Side.BUY, conservative_price=long_contract.ask
                ),
                short_contract.leg(
                    Side.SELL, conservative_price=short_contract.bid
                ),
            ),
            explanation={
                "structure": structure,
                "pricing": "long_ask_minus_short_bid",
                "net_debit": conservative_debit,
                "strike_width": width,
                "data_feed": long_contract.feed,
                "automatic_execution": False,
            },
        )
