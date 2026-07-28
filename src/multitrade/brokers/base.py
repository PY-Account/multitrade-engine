from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from multitrade.domain import AccountSnapshot, AssetClass, TradeIntent


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    status: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    status: str
    currency: str
    equity: Decimal
    last_equity: Decimal
    cash: Decimal
    buying_power: Decimal
    long_market_value: Decimal
    short_market_value: Decimal
    maintenance_margin: Decimal
    gross_notional: Decimal
    daytrade_count: int
    pattern_day_trader: bool
    trading_blocked: bool
    transfers_blocked: bool
    account_blocked: bool
    trade_suspended_by_user: bool
    shorting_enabled: bool
    options_buying_power: Decimal = Decimal("0")
    options_approved_level: int = 0
    options_trading_level: int = 0


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    asset_class: AssetClass
    side: str
    quantity: Decimal
    market_value: Decimal
    cost_basis: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    unrealized_pl: Decimal
    unrealized_pl_percent: Decimal


@dataclass(frozen=True, slots=True)
class BrokerOpenOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    asset_class: AssetClass
    side: str
    order_type: str
    order_class: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    submitted_at: str
    legs_count: int
    filled_average_price: Decimal | None = None
    filled_at: str = ""
    canceled_at: str = ""
    expired_at: str = ""
    has_active_legs: bool = False
    exit_leg_type: str = ""
    exit_filled_average_price: Decimal | None = None
    exit_filled_at: str = ""


@dataclass(frozen=True, slots=True)
class BrokerMarketClock:
    timestamp: str
    is_open: bool
    next_open: str
    next_close: str


@dataclass(frozen=True, slots=True)
class BrokerReconciliation:
    broker: str
    environment: str
    observed_at: datetime
    account: BrokerAccount
    market: BrokerMarketClock
    positions: tuple[BrokerPosition, ...]
    open_orders: tuple[BrokerOpenOrder, ...]
    recent_orders: tuple[BrokerOpenOrder, ...] = ()
    request_ids: tuple[str, ...] = ()

    def account_snapshot(self) -> AccountSnapshot:
        signed_positions = {
            position.symbol: (
                -position.quantity
                if position.side == "short"
                else position.quantity
            )
            for position in self.positions
        }
        return AccountSnapshot(
            equity=self.account.equity,
            start_of_day_equity=self.account.last_equity,
            peak_equity=max(
                self.account.equity, self.account.last_equity
            ),
            gross_notional=self.account.gross_notional,
            positions=signed_positions,
        )


class Broker(Protocol):
    def reconcile(self) -> BrokerReconciliation:
        """Return normalized read-only broker state."""

    def get_account_snapshot(self) -> AccountSnapshot:
        """Return the current broker account state."""

    def submit_order(
        self, intent: TradeIntent, approved_quantity: Decimal
    ) -> BrokerOrder:
        """Submit an already risk-approved order."""
