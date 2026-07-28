from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
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
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None

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
            delta=self.delta,
            gamma=self.gamma,
            theta=self.theta,
            vega=self.vega,
            implied_volatility=self.implied_volatility,
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
            gamma=(
                Decimal(str(greeks["gamma"]))
                if greeks.get("gamma") is not None
                else None
            ),
            theta=(
                Decimal(str(greeks["theta"]))
                if greeks.get("theta") is not None
                else None
            ),
            vega=(
                Decimal(str(greeks["vega"]))
                if greeks.get("vega") is not None
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


class OptionStructure(StrEnum):
    BULL_CALL_DEBIT = "bull_call_debit_spread"
    BEAR_PUT_DEBIT = "bear_put_debit_spread"
    BULL_PUT_CREDIT = "bull_put_credit_spread"
    BEAR_CALL_CREDIT = "bear_call_credit_spread"
    IRON_CONDOR = "iron_condor"
    PROTECTIVE_PUT = "protective_put"


@dataclass(frozen=True, slots=True)
class OptionExecutionPolicy:
    structure: OptionStructure
    source_strategy_id: str
    minimum_dte: int = 30
    maximum_dte: int = 60
    long_delta_target: Decimal = Decimal("0.55")
    short_delta_target: Decimal = Decimal("0.30")
    wing_delta_target: Decimal = Decimal("0.10")
    maximum_strike_width: Decimal = Decimal("10")
    minimum_modeled_theta: Decimal = ZERO
    profit_target_fraction: Decimal = Decimal("0.50")
    loss_limit_multiple: Decimal = Decimal("1.50")
    exit_before_expiry_days: int = 7
    maximum_quote_age_seconds: int = 120

    def __post_init__(self) -> None:
        if not self.source_strategy_id:
            raise ValueError("Option source_strategy_id is required")
        if self.minimum_dte < 1 or self.maximum_dte < self.minimum_dte:
            raise ValueError("Option DTE interval is invalid")
        for value in (
            self.long_delta_target,
            self.short_delta_target,
            self.wing_delta_target,
        ):
            if not ZERO < value < Decimal("1"):
                raise ValueError("Option delta targets must be in (0, 1)")
        if self.maximum_strike_width <= ZERO:
            raise ValueError("maximum_strike_width must be positive")
        if not ZERO < self.profit_target_fraction < Decimal("1"):
            raise ValueError("profit_target_fraction must be in (0, 1)")
        if self.loss_limit_multiple <= ZERO:
            raise ValueError("loss_limit_multiple must be positive")
        if not 1 <= self.exit_before_expiry_days < self.minimum_dte:
            raise ValueError(
                "exit_before_expiry_days must be below minimum_dte"
            )
        if not 15 <= self.maximum_quote_age_seconds <= 900:
            raise ValueError(
                "maximum_quote_age_seconds must be in [15, 900]"
            )

    @property
    def required_trading_level(self) -> int:
        return 2 if self.structure is OptionStructure.PROTECTIVE_PUT else 3

    @property
    def theta_objective(self) -> str:
        if self.structure in {
            OptionStructure.BULL_PUT_CREDIT,
            OptionStructure.BEAR_CALL_CREDIT,
            OptionStructure.IRON_CONDOR,
        }:
            return "positive_theta"
        return "directional_or_hedging"


class DefinedRiskOptionFactory:
    """Construct defined-risk packages; it never submits broker orders."""

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

    def bull_put_credit_spread(
        self,
        *,
        account_id: str,
        strategy_id: str,
        short_put: OptionSnapshot,
        long_put: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        self._validate_vertical(
            long_put,
            short_put,
            OptionRight.PUT,
            long_strike_should_be_lower=True,
        )
        return self._credit_intent(
            account_id=account_id,
            strategy_id=strategy_id,
            short_contract=short_put,
            long_contract=long_put,
            requested_quantity=requested_quantity,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            structure=OptionStructure.BULL_PUT_CREDIT.value,
        )

    def bear_call_credit_spread(
        self,
        *,
        account_id: str,
        strategy_id: str,
        short_call: OptionSnapshot,
        long_call: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        self._validate_vertical(
            long_call,
            short_call,
            OptionRight.CALL,
            long_strike_should_be_lower=False,
        )
        return self._credit_intent(
            account_id=account_id,
            strategy_id=strategy_id,
            short_contract=short_call,
            long_contract=long_call,
            requested_quantity=requested_quantity,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            structure=OptionStructure.BEAR_CALL_CREDIT.value,
        )

    def iron_condor(
        self,
        *,
        account_id: str,
        strategy_id: str,
        long_put: OptionSnapshot,
        short_put: OptionSnapshot,
        short_call: OptionSnapshot,
        long_call: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        self._validate_vertical(
            long_put,
            short_put,
            OptionRight.PUT,
            long_strike_should_be_lower=True,
        )
        self._validate_vertical(
            long_call,
            short_call,
            OptionRight.CALL,
            long_strike_should_be_lower=False,
        )
        contracts = (long_put, short_put, short_call, long_call)
        if len({contract.expiration for contract in contracts}) != 1:
            raise ValueError("Iron-condor legs need one expiration")
        if short_put.strike >= short_call.strike:
            raise ValueError("Iron-condor short strikes must not cross")
        conservative_credit = (
            short_put.bid
            + short_call.bid
            - long_put.ask
            - long_call.ask
        )
        put_width = short_put.strike - long_put.strike
        call_width = long_call.strike - short_call.strike
        if conservative_credit <= ZERO:
            raise ValueError("Iron condor must have a positive net credit")
        if conservative_credit >= min(put_width, call_width):
            raise ValueError("Iron-condor credit must remain below wing width")
        theta = self._modeled_theta(
            (
                (long_put, Side.BUY),
                (short_put, Side.SELL),
                (short_call, Side.SELL),
                (long_call, Side.BUY),
            )
        )
        return TradeIntent(
            account_id=account_id,
            strategy_id=strategy_id,
            asset_class=AssetClass.OPTION,
            symbol=long_put.underlying,
            side=Side.SELL,
            requested_quantity=requested_quantity,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=-conservative_credit,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            intent_id=signal_id,
            option_legs=(
                long_put.leg(Side.BUY, conservative_price=long_put.ask),
                short_put.leg(Side.SELL, conservative_price=short_put.bid),
                short_call.leg(
                    Side.SELL, conservative_price=short_call.bid
                ),
                long_call.leg(
                    Side.BUY, conservative_price=long_call.ask
                ),
            ),
            explanation=self._explanation(
                structure=OptionStructure.IRON_CONDOR.value,
                net_price=-conservative_credit,
                theta=theta,
                feed=long_put.feed,
                extra={
                    "net_credit": conservative_credit,
                    "put_wing_width": put_width,
                    "call_wing_width": call_width,
                },
            ),
        )

    def protective_put(
        self,
        *,
        account_id: str,
        strategy_id: str,
        long_put: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
    ) -> TradeIntent:
        if not self.liquidity_policy.accepts(long_put):
            raise ValueError("Option leg fails the liquidity policy")
        if long_put.right is not OptionRight.PUT:
            raise ValueError("Protective hedge requires a put")
        theta = self._modeled_theta(((long_put, Side.BUY),))
        return TradeIntent(
            account_id=account_id,
            strategy_id=strategy_id,
            asset_class=AssetClass.OPTION,
            symbol=long_put.underlying,
            side=Side.BUY,
            requested_quantity=requested_quantity,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=long_put.ask,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            intent_id=signal_id,
            option_legs=(
                long_put.leg(
                    Side.BUY, conservative_price=long_put.ask
                ),
            ),
            explanation=self._explanation(
                structure=OptionStructure.PROTECTIVE_PUT.value,
                net_price=long_put.ask,
                theta=theta,
                feed=long_put.feed,
                extra={
                    "net_debit": long_put.ask,
                    "hedge_role": "downside_floor_for_existing_long_stock",
                },
            ),
        )

    def close_package(
        self,
        *,
        account_id: str,
        strategy_id: str,
        parent_intent_id: str,
        opening_legs: tuple[OptionLeg, ...],
        snapshots: dict[str, OptionSnapshot],
        quantity: Decimal,
        reason: str,
    ) -> TradeIntent:
        if not opening_legs:
            raise ValueError("Closing package requires opening legs")
        closing_legs: list[OptionLeg] = []
        net_price = ZERO
        quote_timestamps: list[str] = []
        for opening_leg in opening_legs:
            snapshot = snapshots.get(opening_leg.symbol)
            if snapshot is None:
                raise ValueError(
                    f"Missing closing quote for {opening_leg.symbol}"
                )
            if not self.liquidity_policy.accepts(snapshot):
                raise ValueError(
                    f"Closing quote fails liquidity: {opening_leg.symbol}"
                )
            closing_side = (
                Side.SELL
                if opening_leg.side is Side.BUY
                else Side.BUY
            )
            conservative_price = (
                snapshot.bid
                if closing_side is Side.SELL
                else snapshot.ask
            )
            closing_legs.append(
                snapshot.leg(
                    closing_side,
                    conservative_price=conservative_price,
                )
            )
            net_price += (
                conservative_price
                if closing_side is Side.BUY
                else -conservative_price
            ) * Decimal(opening_leg.ratio)
            quote_timestamps.append(snapshot.quote_timestamp)
        if net_price == ZERO:
            raise ValueError("Closing package net price cannot be zero")
        identity = "|".join(
            (
                parent_intent_id,
                reason,
                *sorted(quote_timestamps),
            )
        )
        intent_id = (
            "mx-"
            + hashlib.sha256(identity.encode()).hexdigest()[:32]
        )
        first = opening_legs[0]
        return TradeIntent(
            account_id=account_id,
            strategy_id=strategy_id,
            asset_class=AssetClass.OPTION,
            symbol=first.underlying,
            side=Side.BUY if net_price > ZERO else Side.SELL,
            requested_quantity=quantity,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=net_price,
            option_legs=tuple(closing_legs),
            reduce_only=True,
            parent_intent_id=parent_intent_id,
            intent_id=intent_id,
            explanation={
                "structure": "managed_option_exit",
                "exit_reason": reason,
                "closing_net_price": net_price,
                "pricing": (
                    "buy_legs_at_ask_minus_sell_legs_at_bid"
                ),
                "defined_risk_reduction": True,
                "data_feed": next(iter(snapshots.values())).feed,
            },
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
        theta = DefinedRiskOptionFactory._modeled_theta(
            (
                (long_contract, Side.BUY),
                (short_contract, Side.SELL),
            )
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
            explanation=DefinedRiskOptionFactory._explanation(
                structure=structure,
                net_price=conservative_debit,
                theta=theta,
                feed=long_contract.feed,
                extra={
                    "pricing": "long_ask_minus_short_bid",
                    "net_debit": conservative_debit,
                    "strike_width": width,
                },
            ),
        )

    @staticmethod
    def _credit_intent(
        *,
        account_id: str,
        strategy_id: str,
        short_contract: OptionSnapshot,
        long_contract: OptionSnapshot,
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
        structure: str,
    ) -> TradeIntent:
        conservative_credit = short_contract.bid - long_contract.ask
        width = abs(short_contract.strike - long_contract.strike)
        if conservative_credit <= ZERO:
            raise ValueError("Credit spread must have a positive net credit")
        if conservative_credit >= width:
            raise ValueError(
                "Credit spread price must remain below strike width"
            )
        theta = DefinedRiskOptionFactory._modeled_theta(
            (
                (short_contract, Side.SELL),
                (long_contract, Side.BUY),
            )
        )
        return TradeIntent(
            account_id=account_id,
            strategy_id=strategy_id,
            asset_class=AssetClass.OPTION,
            symbol=short_contract.underlying,
            side=Side.SELL,
            requested_quantity=requested_quantity,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=-conservative_credit,
            risk_budget_fraction=risk_budget_fraction,
            signal_id=signal_id,
            intent_id=signal_id,
            option_legs=(
                short_contract.leg(
                    Side.SELL, conservative_price=short_contract.bid
                ),
                long_contract.leg(
                    Side.BUY, conservative_price=long_contract.ask
                ),
            ),
            explanation=DefinedRiskOptionFactory._explanation(
                structure=structure,
                net_price=-conservative_credit,
                theta=theta,
                feed=short_contract.feed,
                extra={
                    "pricing": "negative_credit_per_alpaca_mleg_convention",
                    "net_credit": conservative_credit,
                    "strike_width": width,
                },
            ),
        )

    @staticmethod
    def _modeled_theta(
        contracts: tuple[tuple[OptionSnapshot, Side], ...]
    ) -> Decimal | None:
        if any(contract.theta is None for contract, _ in contracts):
            return None
        return sum(
            (
                contract.theta
                * Decimal(contract.leg(side).ratio)
                * Decimal(contract.leg(side).multiplier)
                * (Decimal("1") if side is Side.BUY else Decimal("-1"))
                for contract, side in contracts
                if contract.theta is not None
            ),
            start=ZERO,
        )

    @staticmethod
    def _explanation(
        *,
        structure: str,
        net_price: Decimal,
        theta: Decimal | None,
        feed: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "structure": structure,
            "opening_net_price": net_price,
            "modeled_theta_per_day_per_package": theta,
            "theta_attribution": (
                "decision_time_model_only_not_realized_profit"
            ),
            "data_feed": feed,
            "defined_risk": True,
            "automatic_execution": False,
            **extra,
        }


class DefinedRiskOptionSelector:
    """Select a deterministic package from one decision-time option chain."""

    def __init__(
        self,
        policy: OptionExecutionPolicy,
        liquidity_policy: OptionLiquidityPolicy | None = None,
    ) -> None:
        self.policy = policy
        self.liquidity_policy = (
            liquidity_policy or OptionLiquidityPolicy()
        )
        self.factory = DefinedRiskOptionFactory(self.liquidity_policy)

    def build_intent(
        self,
        *,
        account_id: str,
        strategy_id: str,
        underlying: str,
        underlying_price: Decimal,
        direction: str,
        chain: tuple[OptionSnapshot, ...],
        requested_quantity: Decimal,
        risk_budget_fraction: Decimal,
        signal_id: str,
        as_of: date,
    ) -> TradeIntent:
        if direction not in {"bullish", "bearish", "neutral", "hedge"}:
            raise ValueError("Unsupported option signal direction")
        if underlying_price <= ZERO:
            raise ValueError("Underlying price must be positive")
        eligible = tuple(
            contract
            for contract in chain
            if contract.underlying == underlying
            and self.policy.minimum_dte
            <= (contract.expiration - as_of).days
            <= self.policy.maximum_dte
            and self.liquidity_policy.accepts(contract)
            and contract.delta is not None
        )
        if not eligible:
            raise ValueError(
                "No liquid option contracts with Greeks in the DTE window"
            )
        expirations = sorted(
            {
                contract.expiration
                for contract in eligible
            }
        )
        expiration = expirations[0]
        contracts = tuple(
            contract
            for contract in eligible
            if contract.expiration == expiration
        )
        structure = self.policy.structure
        if structure is OptionStructure.BULL_CALL_DEBIT:
            if direction != "bullish":
                raise ValueError("Bull-call spread requires bullish signal")
            long_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.long_delta_target,
            )
            short_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.short_delta_target,
                strike_min=long_call.strike,
                exclude_strike=long_call.strike,
            )
            intent = self.factory.bull_call_debit_spread(
                account_id=account_id,
                strategy_id=strategy_id,
                long_call=long_call,
                short_call=short_call,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        elif structure is OptionStructure.BEAR_PUT_DEBIT:
            if direction != "bearish":
                raise ValueError("Bear-put spread requires bearish signal")
            long_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.long_delta_target,
            )
            short_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.short_delta_target,
                strike_max=long_put.strike,
                exclude_strike=long_put.strike,
            )
            intent = self.factory.bear_put_debit_spread(
                account_id=account_id,
                strategy_id=strategy_id,
                long_put=long_put,
                short_put=short_put,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        elif structure is OptionStructure.BULL_PUT_CREDIT:
            if direction != "bullish":
                raise ValueError("Bull-put spread requires bullish signal")
            short_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.short_delta_target,
                strike_max=underlying_price,
            )
            long_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.wing_delta_target,
                strike_max=short_put.strike,
                exclude_strike=short_put.strike,
            )
            intent = self.factory.bull_put_credit_spread(
                account_id=account_id,
                strategy_id=strategy_id,
                short_put=short_put,
                long_put=long_put,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        elif structure is OptionStructure.BEAR_CALL_CREDIT:
            if direction != "bearish":
                raise ValueError("Bear-call spread requires bearish signal")
            short_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.short_delta_target,
                strike_min=underlying_price,
            )
            long_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.wing_delta_target,
                strike_min=short_call.strike,
                exclude_strike=short_call.strike,
            )
            intent = self.factory.bear_call_credit_spread(
                account_id=account_id,
                strategy_id=strategy_id,
                short_call=short_call,
                long_call=long_call,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        elif structure is OptionStructure.IRON_CONDOR:
            if direction != "neutral":
                raise ValueError("Iron condor requires neutral signal")
            short_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.short_delta_target,
                strike_max=underlying_price,
            )
            long_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.wing_delta_target,
                strike_max=short_put.strike,
                exclude_strike=short_put.strike,
            )
            short_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.short_delta_target,
                strike_min=underlying_price,
            )
            long_call = self._nearest_delta(
                contracts,
                OptionRight.CALL,
                self.policy.wing_delta_target,
                strike_min=short_call.strike,
                exclude_strike=short_call.strike,
            )
            intent = self.factory.iron_condor(
                account_id=account_id,
                strategy_id=strategy_id,
                long_put=long_put,
                short_put=short_put,
                short_call=short_call,
                long_call=long_call,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        elif structure is OptionStructure.PROTECTIVE_PUT:
            if direction != "hedge":
                raise ValueError("Protective put requires hedge direction")
            long_put = self._nearest_delta(
                contracts,
                OptionRight.PUT,
                self.policy.long_delta_target,
                strike_max=underlying_price,
            )
            intent = self.factory.protective_put(
                account_id=account_id,
                strategy_id=strategy_id,
                long_put=long_put,
                requested_quantity=requested_quantity,
                risk_budget_fraction=risk_budget_fraction,
                signal_id=signal_id,
            )
        else:
            raise ValueError(f"Unsupported option structure: {structure}")

        widths = self._strike_widths(intent)
        if any(
            width > self.policy.maximum_strike_width
            for width in widths
        ):
            raise ValueError("Selected option width exceeds policy maximum")
        theta = intent.explanation.get(
            "modeled_theta_per_day_per_package"
        )
        if self.policy.theta_objective == "positive_theta":
            if theta is None:
                raise ValueError(
                    "Positive-theta structure requires decision-time theta"
                )
            if Decimal(str(theta)) <= max(
                ZERO, self.policy.minimum_modeled_theta
            ):
                raise ValueError(
                    "Selected package fails minimum modeled theta"
                )
        return replace(
            intent,
            explanation={
                **intent.explanation,
                "source_strategy_id": (
                    self.policy.source_strategy_id
                ),
                "expiration": expiration.isoformat(),
                "days_to_expiration": (
                    expiration - as_of
                ).days,
                "profit_target_fraction": (
                    self.policy.profit_target_fraction
                ),
                "loss_limit_multiple": (
                    self.policy.loss_limit_multiple
                ),
                "exit_before_expiry_days": (
                    self.policy.exit_before_expiry_days
                ),
                "required_options_trading_level": (
                    self.policy.required_trading_level
                ),
                "maximum_quote_age_seconds": (
                    self.policy.maximum_quote_age_seconds
                ),
                "decision_underlying_price": underlying_price,
            },
        )

    def _nearest_delta(
        self,
        contracts: tuple[OptionSnapshot, ...],
        right: OptionRight,
        target: Decimal,
        *,
        strike_min: Decimal | None = None,
        strike_max: Decimal | None = None,
        exclude_strike: Decimal | None = None,
    ) -> OptionSnapshot:
        candidates = [
            contract
            for contract in contracts
            if contract.right is right
            and contract.delta is not None
            and (
                strike_min is None
                or contract.strike >= strike_min
            )
            and (
                strike_max is None
                or contract.strike <= strike_max
            )
            and (
                exclude_strike is None
                or contract.strike != exclude_strike
            )
        ]
        if not candidates:
            raise ValueError("Option delta/strike selection has no candidate")
        return min(
            candidates,
            key=lambda contract: (
                abs(abs(contract.delta or ZERO) - target),
                contract.relative_spread or Decimal("999"),
                abs(contract.strike),
                contract.symbol,
            ),
        )

    @staticmethod
    def _strike_widths(intent: TradeIntent) -> tuple[Decimal, ...]:
        puts = sorted(
            leg.strike
            for leg in intent.option_legs
            if leg.right is OptionRight.PUT
        )
        calls = sorted(
            leg.strike
            for leg in intent.option_legs
            if leg.right is OptionRight.CALL
        )
        widths: list[Decimal] = []
        if len(puts) == 2:
            widths.append(puts[1] - puts[0])
        if len(calls) == 2:
            widths.append(calls[1] - calls[0])
        return tuple(widths)
