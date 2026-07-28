from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.strategies.base import SignalAction, StrategySignal


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    strategy_id: str
    enabled: bool
    capital_weight: Decimal
    risk_fraction: Decimal
    minimum_confidence: Decimal
    paper_execution_allowed: bool = False
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not Decimal("0") < self.capital_weight <= Decimal("1"):
            raise ValueError("capital_weight must be in (0, 1]")
        if not Decimal("0") < self.risk_fraction <= Decimal("0.03"):
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if not Decimal("0") <= self.minimum_confidence <= Decimal("1"):
            raise ValueError("minimum_confidence must be in [0, 1]")
        if any(not symbol for symbol in self.symbols):
            raise ValueError("Strategy symbols cannot be empty strings")


@dataclass(frozen=True, slots=True)
class AccountPlan:
    account_id: str
    broker: str
    environment: str
    enabled: bool
    asset_classes: tuple[AssetClass, ...]
    watchlist: tuple[str, ...]
    timeframe: str
    maximum_positions: int
    maximum_daily_orders: int
    symbol_cooldown_minutes: int
    allocations: dict[str, StrategyAllocation]

    def __post_init__(self) -> None:
        if not self.account_id or not self.broker:
            raise ValueError("Account identity and broker are required")
        if self.environment != "paper":
            raise ValueError("Only Paper account plans are supported")
        if not self.watchlist:
            raise ValueError("Account watchlist cannot be empty")
        if self.maximum_positions < 1:
            raise ValueError("maximum_positions must be positive")
        if self.maximum_daily_orders < 1:
            raise ValueError("maximum_daily_orders must be positive")
        if self.symbol_cooldown_minutes < 0:
            raise ValueError("symbol_cooldown_minutes cannot be negative")
        for allocation in self.allocations.values():
            unknown_symbols = set(allocation.symbols) - set(self.watchlist)
            if unknown_symbols:
                raise ValueError(
                    f"{allocation.strategy_id} execution symbols must be "
                    "present in the account watchlist: "
                    + ", ".join(sorted(unknown_symbols))
                )
        enabled_weight = sum(
            (
                allocation.capital_weight
                for allocation in self.allocations.values()
                if allocation.enabled
            ),
            start=Decimal("0"),
        )
        if enabled_weight > Decimal("1"):
            raise ValueError(
                "Enabled strategy capital weights cannot exceed 1.0"
            )


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal") from exc


def load_account_plans(path: str | Path) -> tuple[AccountPlan, ...]:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    rows = payload.get("accounts")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Portfolio config requires a non-empty accounts list")
    plans: list[AccountPlan] = []
    seen_accounts: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each account plan must be an object")
        account_id = str(row.get("account_id", "")).strip()
        if account_id in seen_accounts:
            raise ValueError(f"Duplicate account_id: {account_id}")
        seen_accounts.add(account_id)
        allocations: dict[str, StrategyAllocation] = {}
        for allocation_row in row.get("strategies", []):
            strategy_id = str(
                allocation_row.get("strategy_id", "")
            ).strip()
            if strategy_id in allocations:
                raise ValueError(
                    f"Duplicate strategy allocation: {strategy_id}"
                )
            allocations[strategy_id] = StrategyAllocation(
                strategy_id=strategy_id,
                enabled=bool(allocation_row.get("enabled", False)),
                capital_weight=_decimal(
                    allocation_row.get("capital_weight", "0"),
                    "capital_weight",
                ),
                risk_fraction=_decimal(
                    allocation_row.get("risk_fraction", "0"),
                    "risk_fraction",
                ),
                minimum_confidence=_decimal(
                    allocation_row.get("minimum_confidence", "0"),
                    "minimum_confidence",
                ),
                paper_execution_allowed=bool(
                    allocation_row.get(
                        "paper_execution_allowed", False
                    )
                ),
                symbols=tuple(
                    dict.fromkeys(
                        str(symbol).strip().upper()
                        for symbol in allocation_row.get(
                            "symbols", []
                        )
                        if str(symbol).strip()
                    )
                ),
            )
        plans.append(
            AccountPlan(
                account_id=account_id,
                broker=str(row.get("broker", "")).strip(),
                environment=str(
                    row.get("environment", "")
                ).strip(),
                enabled=bool(row.get("enabled", False)),
                asset_classes=tuple(
                    AssetClass(value)
                    for value in row.get("asset_classes", [])
                ),
                watchlist=tuple(
                    dict.fromkeys(
                        str(symbol).strip().upper()
                        for symbol in row.get("watchlist", [])
                        if str(symbol).strip()
                    )
                ),
                timeframe=str(row.get("timeframe", "5Min")),
                maximum_positions=int(
                    row.get("maximum_positions", 5)
                ),
                maximum_daily_orders=int(
                    row.get("maximum_daily_orders", 8)
                ),
                symbol_cooldown_minutes=int(
                    row.get("symbol_cooldown_minutes", 60)
                ),
                allocations=allocations,
            )
        )
    return tuple(plans)


class SignalAllocator:
    def allocate(
        self,
        signal: StrategySignal,
        allocation: StrategyAllocation,
        snapshot: AccountSnapshot,
    ) -> TradeIntent | None:
        if not allocation.enabled:
            return None
        if signal.confidence < allocation.minimum_confidence:
            return None
        capital = snapshot.equity * allocation.capital_weight
        requested_quantity = (
            capital / signal.reference_price
        ).to_integral_value(rounding=ROUND_DOWN)
        if requested_quantity <= Decimal("0"):
            return None
        side = (
            Side.BUY
            if signal.action is SignalAction.ENTER_LONG
            else Side.SELL
        )
        return TradeIntent(
            strategy_id=signal.strategy_id,
            asset_class=AssetClass.STOCK,
            symbol=signal.symbol,
            side=side,
            requested_quantity=requested_quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            reference_price=signal.reference_price,
            stop_price=signal.stop_price,
            take_profit_price=signal.target_price,
            risk_budget_fraction=allocation.risk_fraction,
            account_id=signal.account_id,
            signal_id=signal.signal_id,
            explanation={
                "strategy_version": signal.strategy_version,
                "reason_codes": signal.reason_codes,
                "evidence": signal.evidence,
                "confidence": signal.confidence,
                "bar_timestamp": signal.bar_timestamp,
            },
            intent_id=signal.signal_id,
        )
