from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
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
from multitrade.market import timeframe_seconds
from multitrade.options import OptionExecutionPolicy, OptionStructure
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
    asset_class: AssetClass = AssetClass.STOCK
    option_policy: OptionExecutionPolicy | None = None
    timeframe: str | None = None
    minimum_entry_interval_minutes: int = 0

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not Decimal("0") < self.capital_weight <= Decimal("1"):
            raise ValueError("capital_weight must be in (0, 1]")
        maximum_risk_fraction = (
            Decimal("0.10")
            if self.asset_class is AssetClass.OPTION
            else Decimal("0.03")
        )
        if not Decimal("0") < self.risk_fraction <= maximum_risk_fraction:
            raise ValueError("risk_fraction exceeds the asset-class limit")
        if not Decimal("0") <= self.minimum_confidence <= Decimal("1"):
            raise ValueError("minimum_confidence must be in [0, 1]")
        if any(not symbol for symbol in self.symbols):
            raise ValueError("Strategy symbols cannot be empty strings")
        if self.minimum_entry_interval_minutes < 0:
            raise ValueError(
                "minimum_entry_interval_minutes cannot be negative"
            )
        if self.timeframe is not None:
            timeframe_seconds(self.timeframe)
        if self.asset_class is AssetClass.OPTION:
            if self.option_policy is None:
                raise ValueError(
                    "Option allocations require an option policy"
                )
        elif self.option_policy is not None:
            raise ValueError(
                "Only option allocations may define an option policy"
            )

    @property
    def source_strategy_id(self) -> str:
        if self.option_policy is not None:
            return self.option_policy.source_strategy_id
        return self.strategy_id


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
    credential_env_prefix: str = "ALPACA"
    expected_broker_account_id: str = ""

    def __post_init__(self) -> None:
        if not self.account_id or not self.broker:
            raise ValueError("Account identity and broker are required")
        if self.broker != "alpaca":
            raise ValueError("Only Alpaca Paper accounts are implemented")
        if self.environment != "paper":
            raise ValueError("Only Paper account plans are supported")
        if not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,63}", self.credential_env_prefix
        ):
            raise ValueError(
                "credential_env_prefix must contain only uppercase "
                "letters, numbers, and underscores"
            )
        if not self.watchlist:
            raise ValueError("Account watchlist cannot be empty")
        if self.maximum_positions < 1:
            raise ValueError("maximum_positions must be positive")
        if self.maximum_daily_orders < 1:
            raise ValueError("maximum_daily_orders must be positive")
        if self.symbol_cooldown_minutes < 0:
            raise ValueError("symbol_cooldown_minutes cannot be negative")
        for allocation in self.allocations.values():
            if allocation.asset_class not in self.asset_classes:
                raise ValueError(
                    f"{allocation.strategy_id} asset class is not enabled "
                    f"for account {self.account_id}"
                )
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


def apply_strategy_configuration_overrides(
    plans: tuple[AccountPlan, ...],
    overrides: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    strict: bool = True,
) -> tuple[AccountPlan, ...]:
    """Apply audited Paper-only runtime controls to immutable base plans."""

    rows = {
        (str(row["account_id"]), str(row["strategy_id"])): row
        for row in overrides
    }
    known = {
        (plan.account_id, strategy_id)
        for plan in plans
        for strategy_id in plan.allocations
    }
    unknown = set(rows) - known
    if unknown:
        if strict:
            account_id, strategy_id = sorted(unknown)[0]
            raise ValueError(
                "Unknown strategy configuration override: "
                f"{account_id}/{strategy_id}"
            )
        for key in unknown:
            rows.pop(key, None)

    effective: list[AccountPlan] = []
    for plan in plans:
        allocations: dict[str, StrategyAllocation] = {}
        added_symbols: list[str] = []
        for strategy_id, allocation in plan.allocations.items():
            row = rows.get((plan.account_id, strategy_id))
            if row is None:
                allocations[strategy_id] = allocation
                continue
            symbols = tuple(
                dict.fromkeys(
                    str(symbol).strip().upper()
                    for symbol in row.get("symbols", [])
                    if str(symbol).strip()
                )
            )
            updated = replace(
                allocation,
                enabled=bool(row["enabled"]),
                paper_execution_allowed=bool(
                    row["paper_execution_allowed"]
                ),
                symbols=symbols,
            )
            allocations[strategy_id] = updated
            added_symbols.extend(symbols)
        watchlist = tuple(
            dict.fromkeys((*plan.watchlist, *added_symbols))
        )
        effective.append(
            replace(
                plan,
                watchlist=watchlist,
                allocations=allocations,
            )
        )
    return tuple(effective)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal") from exc


def _env_string(value: Any) -> str:
    candidate = str(value or "").strip()
    match = re.fullmatch(r"\$\{([A-Z][A-Z0-9_]{0,127})\}", candidate)
    if not match:
        return candidate
    return os.getenv(match.group(1), "").strip()


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
            asset_class = AssetClass(
                allocation_row.get("asset_class", "stock")
            )
            option_payload = allocation_row.get("option_policy")
            option_policy = None
            if option_payload is not None:
                if not isinstance(option_payload, dict):
                    raise ValueError("option_policy must be an object")
                option_policy = OptionExecutionPolicy(
                    structure=OptionStructure(
                        option_payload.get("structure", "")
                    ),
                    source_strategy_id=str(
                        option_payload.get(
                            "source_strategy_id", ""
                        )
                    ).strip(),
                    minimum_dte=int(
                        option_payload.get("minimum_dte", 30)
                    ),
                    maximum_dte=int(
                        option_payload.get("maximum_dte", 60)
                    ),
                    long_delta_target=_decimal(
                        option_payload.get(
                            "long_delta_target", "0.55"
                        ),
                        "long_delta_target",
                    ),
                    short_delta_target=_decimal(
                        option_payload.get(
                            "short_delta_target", "0.30"
                        ),
                        "short_delta_target",
                    ),
                    maximum_short_delta=_decimal(
                        option_payload.get("maximum_short_delta", "0.35"),
                        "maximum_short_delta",
                    ),
                    wing_delta_target=_decimal(
                        option_payload.get(
                            "wing_delta_target", "0.10"
                        ),
                        "wing_delta_target",
                    ),
                    target_strike_width=(
                        _decimal(
                            option_payload.get("target_strike_width"),
                            "target_strike_width",
                        )
                        if "target_strike_width" in option_payload
                        else None
                    ),
                    maximum_strike_width=_decimal(
                        option_payload.get(
                            "maximum_strike_width", "10"
                        ),
                        "maximum_strike_width",
                    ),
                    minimum_modeled_theta=_decimal(
                        option_payload.get(
                            "minimum_modeled_theta", "0"
                        ),
                        "minimum_modeled_theta",
                    ),
                    minimum_credit_to_risk=_decimal(
                        option_payload.get("minimum_credit_to_risk", "0"),
                        "minimum_credit_to_risk",
                    ),
                    profit_target_fraction=_decimal(
                        option_payload.get(
                            "profit_target_fraction", "0.50"
                        ),
                        "profit_target_fraction",
                    ),
                    loss_limit_multiple=_decimal(
                        option_payload.get(
                            "loss_limit_multiple", "1.50"
                        ),
                        "loss_limit_multiple",
                    ),
                    exit_before_expiry_days=int(
                        option_payload.get(
                            "exit_before_expiry_days", 7
                        )
                    ),
                    maximum_holding_minutes=(
                        int(option_payload["maximum_holding_minutes"])
                        if option_payload.get("maximum_holding_minutes") is not None
                        else None
                    ),
                    maximum_quote_age_seconds=int(
                        option_payload.get(
                            "maximum_quote_age_seconds", 120
                        )
                    ),
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
                timeframe=(
                    str(allocation_row["timeframe"]).strip()
                    if allocation_row.get("timeframe") is not None
                    else None
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
                asset_class=asset_class,
                option_policy=option_policy,
                minimum_entry_interval_minutes=int(
                    allocation_row.get(
                        "minimum_entry_interval_minutes", 0
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
                credential_env_prefix=str(
                    row.get("credential_env_prefix", "ALPACA")
                ).strip(),
                expected_broker_account_id=_env_string(
                    row.get("expected_broker_account_id", "")
                ),
            )
        )
    enabled_plans = tuple(plan for plan in plans if plan.enabled)
    credential_prefixes = [
        plan.credential_env_prefix for plan in enabled_plans
    ]
    if len(set(credential_prefixes)) != len(credential_prefixes):
        raise ValueError(
            "Enabled accounts must use unique credential_env_prefix values"
        )
    if len(enabled_plans) > 1:
        missing_identities = [
            plan.account_id
            for plan in enabled_plans
            if not plan.expected_broker_account_id
        ]
        if missing_identities:
            raise ValueError(
                "Multiple enabled accounts require "
                "expected_broker_account_id for: "
                + ", ".join(sorted(missing_identities))
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
