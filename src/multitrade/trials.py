from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import ModuleType
from typing import Any, Iterable

from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan, StrategyAllocation
from multitrade.strategies.base import Strategy


def _normalized(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalized(asdict(value))
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _normalized(item)
            for key, item in sorted(
                value.items(), key=lambda row: str(row[0])
            )
        }
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(
        f"Trial evidence cannot normalize {type(value).__name__}"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalized(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _implementation_manifest(
    strategy: Strategy,
) -> tuple[str, str, dict[str, str]]:
    strategy_type = type(strategy)
    module: ModuleType | None = inspect.getmodule(strategy_type)
    module_names = tuple(
        dict.fromkeys(
            (
                (
                    module.__name__
                    if module is not None
                    else strategy_type.__module__
                ),
                "multitrade.features",
                "multitrade.backtest",
                "multitrade.robustness",
                "multitrade.strategies.base",
            )
        )
    )
    manifest: dict[str, str] = {}
    fallback_modules: list[str] = []
    for module_name in module_names:
        target = (
            module
            if module is not None
            and module.__name__ == module_name
            else importlib.import_module(module_name)
        )
        try:
            source = inspect.getsource(target).replace(
                "\r\n", "\n"
            )
        except (OSError, TypeError):
            source = module_name
            fallback_modules.append(module_name)
        manifest[module_name] = hashlib.sha256(
            source.encode("utf-8")
        ).hexdigest()
    source_scope = ",".join(module_names)
    if fallback_modules:
        source_scope += (
            ";identity_fallback=" + ",".join(fallback_modules)
        )
    return (
        fingerprint(manifest),
        source_scope,
        manifest,
    )


def strategy_parameters(strategy: Strategy) -> dict[str, Any]:
    if is_dataclass(strategy):
        return _normalized(asdict(strategy))
    attributes = getattr(strategy, "__dict__", None)
    if not isinstance(attributes, dict):
        raise TypeError(
            "Strategy must be a dataclass or expose public parameters"
        )
    return _normalized(
        {
            name: value
            for name, value in attributes.items()
            if not name.startswith("_")
        }
    )


def _bar_payload(bar: MarketBar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "asset_class": bar.asset_class.value,
        "timeframe": bar.timeframe,
        "timestamp": bar.timestamp,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "trade_count": bar.trade_count,
        "vwap": bar.vwap,
        "feed": bar.feed,
        "adjustment": bar.adjustment,
    }


def _dataset_summary(
    symbols: Iterable[str],
    bars_by_symbol: dict[str, tuple[MarketBar, ...]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        bars = tuple(
            sorted(
                bars_by_symbol.get(symbol, ()),
                key=lambda bar: bar.timestamp,
            )
        )
        rows.append(
            {
                "symbol": symbol,
                "bar_count": len(bars),
                "first_bar": (
                    bars[0].timestamp if bars else None
                ),
                "last_bar": bars[-1].timestamp if bars else None,
                "timeframes": sorted(
                    {bar.timeframe for bar in bars}
                ),
                "feeds": sorted({bar.feed for bar in bars}),
                "adjustments": sorted(
                    {bar.adjustment for bar in bars}
                ),
                "bars_sha256": fingerprint(
                    [_bar_payload(bar) for bar in bars]
                ),
            }
        )
    return {
        "symbols": rows,
        "total_bars": sum(row["bar_count"] for row in rows),
    }


@dataclass(frozen=True, slots=True)
class StrategyTrialDefinition:
    candidate_fingerprint: str
    configuration_fingerprint: str
    dataset_fingerprint: str
    candidate_definition: dict[str, Any]
    configuration: dict[str, Any]
    dataset_summary: dict[str, Any]

    def __post_init__(self) -> None:
        for value in (
            self.candidate_fingerprint,
            self.configuration_fingerprint,
            self.dataset_fingerprint,
        ):
            if (
                len(value) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value
                )
            ):
                raise ValueError(
                    "Trial fingerprints must be lowercase SHA-256"
                )


def build_strategy_trial_definition(
    *,
    strategy: Strategy,
    allocation: StrategyAllocation,
    account_plan: AccountPlan,
    lab_config: Any,
    requested_symbols: tuple[str, ...],
    bars_by_symbol: dict[str, tuple[MarketBar, ...]],
    experiment_binding: Any | None = None,
) -> StrategyTrialDefinition:
    (
        implementation_sha256,
        source_scope,
        implementation_manifest,
    ) = _implementation_manifest(strategy)
    candidate_definition = {
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.version,
        "strategy_type": (
            f"{type(strategy).__module__}."
            f"{type(strategy).__qualname__}"
        ),
        "source_scope": source_scope,
        "implementation_sha256": implementation_sha256,
        "implementation_manifest": implementation_manifest,
        "parameters": strategy_parameters(strategy),
    }
    configuration = {
        "account_id": account_plan.account_id,
        "broker": account_plan.broker,
        "environment": account_plan.environment,
        "timeframe": account_plan.timeframe,
        "requested_symbols": requested_symbols,
        "allocation": allocation,
        "strategy_lab": lab_config,
        "experiment": (
            experiment_binding
            if experiment_binding is not None
            else {"status": "unregistered"}
        ),
    }
    dataset_summary = _dataset_summary(
        requested_symbols, bars_by_symbol
    )
    return StrategyTrialDefinition(
        candidate_fingerprint=fingerprint(candidate_definition),
        configuration_fingerprint=fingerprint(configuration),
        dataset_fingerprint=fingerprint(dataset_summary),
        candidate_definition=_normalized(candidate_definition),
        configuration=_normalized(configuration),
        dataset_summary=_normalized(dataset_summary),
    )
