from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from multitrade.domain import AccountSnapshot, TradeIntent


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    status: str
    raw: dict[str, Any]


class Broker(Protocol):
    def get_account_snapshot(self) -> AccountSnapshot:
        """Return the current broker account state."""

    def submit_order(
        self, intent: TradeIntent, approved_quantity: Decimal
    ) -> BrokerOrder:
        """Submit an already risk-approved order."""
