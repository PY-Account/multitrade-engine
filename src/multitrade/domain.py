from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Mapping
from uuid import uuid4


ZERO = Decimal("0")


class AssetClass(StrEnum):
    STOCK = "stock"
    OPTION = "option"
    CRYPTO = "crypto"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True, slots=True)
class OptionLeg:
    symbol: str
    underlying: str
    expiration: date
    right: OptionRight
    strike: Decimal
    side: Side
    ratio: int
    mark_price: Decimal
    multiplier: int = 100

    def __post_init__(self) -> None:
        if not self.symbol or not self.underlying:
            raise ValueError("Option symbol and underlying are required")
        if self.strike <= ZERO:
            raise ValueError("Option strike must be positive")
        if self.ratio <= 0 or self.multiplier <= 0:
            raise ValueError("Option ratio and multiplier must be positive")
        if self.mark_price < ZERO:
            raise ValueError("Option mark price cannot be negative")


@dataclass(frozen=True, slots=True)
class TradeIntent:
    strategy_id: str
    asset_class: AssetClass
    symbol: str
    side: Side
    requested_quantity: Decimal
    order_type: OrderType
    time_in_force: TimeInForce
    reference_price: Decimal | None = None
    stop_price: Decimal | None = None
    limit_price: Decimal | None = None
    option_legs: tuple[OptionLeg, ...] = ()
    reduce_only: bool = False
    intent_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.symbol:
            raise ValueError("strategy_id and symbol are required")
        if self.requested_quantity <= ZERO:
            raise ValueError("requested_quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        if self.asset_class is AssetClass.OPTION and not self.option_legs:
            raise ValueError("Option intents require at least one option leg")
        if self.asset_class is not AssetClass.OPTION and self.option_legs:
            raise ValueError("Only option intents may contain option legs")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    equity: Decimal
    start_of_day_equity: Decimal
    peak_equity: Decimal
    active_risk: Decimal = ZERO
    gross_notional: Decimal = ZERO
    positions: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.equity <= ZERO:
            raise ValueError("Account equity must be positive")
        if self.start_of_day_equity <= ZERO or self.peak_equity <= ZERO:
            raise ValueError("Reference equity values must be positive")
        if self.active_risk < ZERO:
            raise ValueError("active_risk cannot be negative")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    intent_id: str
    reason: str
    approved_quantity: Decimal = ZERO
    risk_per_unit: Decimal = ZERO
    reserved_risk: Decimal = ZERO
    projected_active_risk: Decimal = ZERO
