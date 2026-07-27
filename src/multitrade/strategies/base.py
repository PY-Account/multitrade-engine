from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from multitrade.features import FeatureSnapshot
from multitrade.market import MarketBar, timeframe_seconds


class SignalAction(StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"


@dataclass(frozen=True, slots=True)
class StrategySignal:
    signal_id: str
    account_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    action: SignalAction
    bar_timestamp: datetime
    created_at: datetime
    expires_at: datetime
    confidence: Decimal
    reference_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.signal_id or not self.strategy_id or not self.symbol:
            raise ValueError("Signal identity fields are required")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("Signal confidence must be between zero and one")
        if self.bar_timestamp.tzinfo is None:
            raise ValueError("Signal bar timestamp must be timezone-aware")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Signal lifecycle timestamps need time zones")
        if self.expires_at <= self.created_at:
            raise ValueError("Signal expiration must follow creation")
        if self.action is SignalAction.ENTER_LONG:
            if not (
                self.stop_price
                < self.reference_price
                < self.target_price
            ):
                raise ValueError("Long signal prices are inconsistent")
        if self.action is SignalAction.ENTER_SHORT:
            if not (
                self.target_price
                < self.reference_price
                < self.stop_price
            ):
                raise ValueError("Short signal prices are inconsistent")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    account_id: str
    bars: tuple[MarketBar, ...]
    features: FeatureSnapshot
    evaluated_at: datetime


class Strategy(Protocol):
    strategy_id: str
    version: str

    def evaluate(
        self, context: StrategyContext
    ) -> StrategySignal | None:
        """Evaluate closed bars and return at most one opening signal."""


def create_signal(
    *,
    context: StrategyContext,
    strategy_id: str,
    version: str,
    action: SignalAction,
    confidence: Decimal,
    reference_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    reason_codes: tuple[str, ...],
    evidence: dict[str, Any],
    valid_for_bars: int = 2,
) -> StrategySignal:
    latest = context.bars[-1]
    identity = "|".join(
        (
            context.account_id,
            strategy_id,
            version,
            latest.symbol,
            action.value,
            latest.timestamp.isoformat(),
        )
    )
    signal_id = f"mt-{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    created_at = context.evaluated_at.astimezone(timezone.utc)
    expires_at = created_at + timedelta(
        seconds=timeframe_seconds(latest.timeframe) * valid_for_bars
    )
    return StrategySignal(
        signal_id=signal_id,
        account_id=context.account_id,
        strategy_id=strategy_id,
        strategy_version=version,
        symbol=latest.symbol,
        action=action,
        bar_timestamp=latest.timestamp,
        created_at=created_at,
        expires_at=expires_at,
        confidence=confidence,
        reference_price=reference_price,
        stop_price=stop_price,
        target_price=target_price,
        reason_codes=reason_codes,
        evidence=evidence,
    )
