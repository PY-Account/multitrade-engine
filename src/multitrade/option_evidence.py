from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.config import Settings
from multitrade.domain import ZERO
from multitrade.market import MarketBar
from multitrade.options import (
    AlpacaHistoricalOptionDataClient,
    OptionDataError,
)
from multitrade.portfolio import AccountPlan


@dataclass(frozen=True, slots=True)
class OptionEvidenceConfig:
    timeframe: str = "15Min"
    maximum_observations: int = 100
    slippage_per_leg: Decimal = Decimal("0.05")
    maximum_path_points: int = 500

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_observations <= 500:
            raise ValueError(
                "maximum_observations must be between 1 and 500"
            )
        if self.slippage_per_leg < ZERO:
            raise ValueError("Option evidence slippage cannot be negative")
        if not 20 <= self.maximum_path_points <= 2000:
            raise ValueError(
                "maximum_path_points must be between 20 and 2000"
            )


@dataclass(frozen=True, slots=True)
class OptionEvidenceCycleResult:
    account_id: str
    observations_considered: int
    packages_evaluated: int
    packages_with_aligned_bars: int
    packages_failed: int
    bars_ingested: int
    request_ids: tuple[str, ...]
    execution_enabled: bool = False


def evaluate_option_package_path(
    observation: dict[str, Any],
    bars_by_symbol: dict[str, tuple[MarketBar, ...]],
    *,
    evaluated_at: datetime,
    timeframe: str,
    data_feed: str,
    slippage_per_leg: Decimal = Decimal("0.05"),
    quantity: Decimal = Decimal("1"),
    maximum_path_points: int = 500,
) -> dict[str, Any]:
    """Evaluate one frozen package with exact-contract trade-bar proxies."""
    if evaluated_at.tzinfo is None:
        raise ValueError("Option evidence time must be timezone-aware")
    if slippage_per_leg < ZERO:
        raise ValueError("Option evidence slippage cannot be negative")
    if quantity <= ZERO:
        raise ValueError("Option evidence quantity must be positive")
    legs = tuple(observation.get("legs") or ())
    if not legs:
        raise ValueError("Option observation does not contain legs")
    opening_text = observation.get("opening_net_price")
    if opening_text is None:
        raise ValueError("Option observation has no opening net price")
    opening_net_price = Decimal(str(opening_text))
    symbols = tuple(str(leg["symbol"]) for leg in legs)
    if len(set(symbols)) != len(symbols):
        raise ValueError("Option evidence requires unique leg symbols")

    bar_maps = {
        symbol: {
            bar.timestamp.astimezone(timezone.utc).isoformat(): bar
            for bar in bars_by_symbol.get(symbol, ())
        }
        for symbol in symbols
    }
    timestamp_sets = tuple(set(rows) for rows in bar_maps.values())
    union_timestamps = (
        set().union(*timestamp_sets) if timestamp_sets else set()
    )
    aligned_timestamps = (
        set.intersection(*timestamp_sets) if timestamp_sets else set()
    )
    warnings: list[str] = [
        "trade_bar_proxy_not_executable_quote",
        "historical_greeks_not_reconstructed",
        "decision_limit_used_as_opening_basis",
    ]
    missing_symbols = tuple(
        symbol for symbol, rows in bar_maps.items() if not rows
    )
    if missing_symbols:
        warnings.append("one_or_more_legs_have_no_bars")
    if aligned_timestamps != union_timestamps:
        warnings.append("leg_timestamps_not_fully_aligned")

    total_ratios = sum(
        (Decimal(str(leg.get("ratio", 1))) for leg in legs),
        start=ZERO,
    )
    points: list[dict[str, str]] = []
    for timestamp in sorted(aligned_timestamps):
        package_mark = ZERO
        for leg in legs:
            sign = (
                Decimal("1")
                if str(leg["side"]) == "buy"
                else Decimal("-1")
            )
            ratio = Decimal(str(leg.get("ratio", 1)))
            package_mark += (
                sign
                * bar_maps[str(leg["symbol"])][timestamp].close
                * ratio
            )
        conservative_liquidation_mark = (
            package_mark - slippage_per_leg * total_ratios
        )
        proxy_pnl = (
            conservative_liquidation_mark - opening_net_price
        ) * Decimal("100") * quantity
        points.append(
            {
                "timestamp": timestamp,
                "package_mark": format(package_mark, "f"),
                "conservative_liquidation_mark": format(
                    conservative_liquidation_mark, "f"
                ),
                "proxy_pnl": format(proxy_pnl, "f"),
            }
        )

    coverage = (
        Decimal(len(aligned_timestamps))
        / Decimal(len(union_timestamps))
        if union_timestamps
        else ZERO
    )
    pnl_values = tuple(
        Decimal(point["proxy_pnl"]) for point in points
    )
    explanation = (
        observation.get("details", {}).get("intent_explanation", {})
    )
    premium_basis = (
        abs(opening_net_price) * Decimal("100") * quantity
    )
    profit_target = Decimal(
        str(explanation.get("profit_target_fraction", "0.50"))
    )
    loss_multiple = Decimal(
        str(explanation.get("loss_limit_multiple", "1.50"))
    )
    exit_days = int(
        explanation.get("exit_before_expiry_days", 7)
    )
    expiration_text = explanation.get("expiration")
    if expiration_text is None:
        expiration_text = legs[0].get("expiration")
    expiration = (
        datetime.fromisoformat(str(expiration_text)).date()
        if expiration_text
        else None
    )
    first_exit: dict[str, str] | None = None
    for point in points:
        timestamp = datetime.fromisoformat(point["timestamp"])
        pnl = Decimal(point["proxy_pnl"])
        reason: str | None = None
        if expiration is not None and (
            expiration - timestamp.date()
        ).days <= exit_days:
            reason = "expiration_window"
        elif pnl >= premium_basis * profit_target:
            reason = "profit_target"
        elif pnl <= -(premium_basis * loss_multiple):
            reason = "loss_limit"
        if reason is not None:
            first_exit = {
                "reason": reason,
                "timestamp": point["timestamp"],
                "proxy_pnl": point["proxy_pnl"],
            }
            break

    retained_path = (
        points[-maximum_path_points:]
        if len(points) > maximum_path_points
        else points
    )
    if len(retained_path) < len(points):
        warnings.append("display_path_truncated")
    return {
        "intent_id": observation["intent_id"],
        "account_id": observation["account_id"],
        "strategy_id": observation["strategy_id"],
        "structure": (
            observation.get("structure") or "unspecified_option"
        ),
        "underlying": observation["underlying"],
        "timeframe": timeframe,
        "data_feed": data_feed,
        "evidence_type": "exact_contract_trade_bar_proxy",
        "price_basis": (
            "decision_limit_plus_configured_per_leg_slippage"
        ),
        "evaluated_at": evaluated_at.astimezone(
            timezone.utc
        ).isoformat(),
        "data_start": (
            points[0]["timestamp"] if points else None
        ),
        "data_end": points[-1]["timestamp"] if points else None,
        "aligned_points": len(points),
        "union_points": len(union_timestamps),
        "coverage_fraction": format(coverage, ".6f"),
        "latest_proxy_pnl": (
            format(pnl_values[-1], "f") if pnl_values else None
        ),
        "maximum_favorable_excursion": (
            format(max(pnl_values), "f") if pnl_values else None
        ),
        "maximum_adverse_excursion": (
            format(min(pnl_values), "f") if pnl_values else None
        ),
        "time_underwater_fraction": (
            format(
                Decimal(sum(value < ZERO for value in pnl_values))
                / Decimal(len(pnl_values)),
                ".6f",
            )
            if pnl_values
            else None
        ),
        "first_policy_exit_reason": (
            first_exit["reason"] if first_exit else None
        ),
        "first_policy_exit_at": (
            first_exit["timestamp"] if first_exit else None
        ),
        "first_policy_exit_proxy_pnl": (
            first_exit["proxy_pnl"] if first_exit else None
        ),
        "warnings": tuple(warnings),
        "path": retained_path,
        "details": {
            "contracts": symbols,
            "missing_contracts": missing_symbols,
            "quantity": format(quantity, "f"),
            "quantity_basis": observation.get(
                "quantity_basis", "normalized_one_package"
            ),
            "opening_net_price": format(opening_net_price, "f"),
            "slippage_per_leg_price_points": format(
                slippage_per_leg, "f"
            ),
            "historical_greeks_available": False,
            "realized_pnl_attribution": False,
            "execution_enabled": False,
        },
    }


class ContinuousOptionEvidenceService:
    """Maintains forward evidence for frozen option packages; no orders."""

    def __init__(
        self,
        *,
        account_plan: AccountPlan,
        option_data: AlpacaHistoricalOptionDataClient,
        store: SqliteAuditStore,
        config: OptionEvidenceConfig | None = None,
    ) -> None:
        self.account_plan = account_plan
        self.option_data = option_data
        self.store = store
        self.config = config or OptionEvidenceConfig()

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
    ) -> "ContinuousOptionEvidenceService":
        key_id, secret_key, _ = settings.alpaca_credentials_for(
            account_plan.credential_env_prefix
        )
        return cls(
            account_plan=account_plan,
            option_data=AlpacaHistoricalOptionDataClient(
                key_id,
                secret_key,
                feed=settings.option_data_feed,
            ),
            store=store or SqliteAuditStore(settings.db_path),
            config=OptionEvidenceConfig(
                timeframe=settings.option_evidence_timeframe,
                maximum_observations=(
                    settings.option_evidence_maximum_observations
                ),
                slippage_per_leg=(
                    settings.option_evidence_slippage_per_leg
                ),
            ),
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> OptionEvidenceCycleResult:
        evaluated_at = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        reader = SqliteAuditReader(self.store.path)
        observation_rows = reader.recent_option_observations(
            self.config.maximum_observations,
            account_id=self.account_plan.account_id,
        )
        trade_rows = tuple(
            row
            for row in reader.recent_trade_records(200)
            if row["account_id"] == self.account_plan.account_id
            and row["asset_class"] == "option"
        )
        observed_intents = {
            row["intent_id"]
            for row in observation_rows
            if row.get("intent_id")
        }
        legacy_observations = [
            {
                "signal_id": row["signal_id"],
                "intent_id": row["intent_id"],
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "structure": row["structure"],
                "underlying": row["symbol"],
                "status": "legacy_trade_record",
                "data_feed": row["explanation"].get("data_feed"),
                "opening_net_price": row["opening_net_price"],
                "requested_quantity": row["requested_quantity"],
                "estimated_risk_per_package": row[
                    "explanation"
                ].get("estimated_risk_per_package"),
                "modeled_delta": None,
                "modeled_gamma": None,
                "modeled_theta_per_day": row[
                    "modeled_theta_per_day"
                ],
                "modeled_vega": None,
                "legs": row["option_legs"],
                "details": {
                    "intent_explanation": row["explanation"],
                    "migrated_from_trade_ledger": True,
                },
                "decision_at": row["created_at"],
                "updated_at": row["updated_at"],
                "execution_proof": False,
            }
            for row in trade_rows
            if row["intent_id"] not in observed_intents
            and row.get("opening_net_price") is not None
            and row.get("option_legs")
        ]
        observations = tuple(
            sorted(
                (*observation_rows, *legacy_observations),
                key=lambda row: str(row["decision_at"]),
                reverse=True,
            )[: self.config.maximum_observations]
        )
        observations = tuple(
            row
            for row in observations
            if row.get("intent_id")
            and row.get("opening_net_price") is not None
            and row.get("legs")
        )
        trades = {
            row["intent_id"]: row
            for row in trade_rows
        }
        packages_evaluated = 0
        packages_with_bars = 0
        packages_failed = 0
        bars_ingested = 0
        request_ids: list[str] = []
        for observation in observations:
            try:
                decision_at = datetime.fromisoformat(
                    str(observation["decision_at"]).replace(
                        "Z", "+00:00"
                    )
                ).astimezone(timezone.utc)
                trade = trades.get(observation["intent_id"])
                end = evaluated_at
                if trade is not None and trade.get("closed_at"):
                    end = min(
                        end,
                        datetime.fromisoformat(
                            str(trade["closed_at"]).replace(
                                "Z", "+00:00"
                            )
                        ).astimezone(timezone.utc),
                    )
                if end <= decision_at:
                    end = decision_at + timedelta(minutes=1)
                symbols = tuple(
                    str(leg["symbol"])
                    for leg in observation["legs"]
                )
                bars = self.option_data.fetch_bars(
                    symbols,
                    self.config.timeframe,
                    decision_at,
                    end,
                )
                request_ids.extend(self.option_data.request_ids)
                bars_ingested += self.store.record_market_bars(
                    bar for rows in bars.values() for bar in rows
                )
                quantity = Decimal("1")
                quantity_basis = "normalized_one_package"
                if trade is not None:
                    approved = Decimal(
                        str(trade.get("approved_quantity") or "0")
                    )
                    if approved > ZERO:
                        quantity = approved
                        quantity_basis = "risk_approved_quantity"
                enriched = {
                    **observation,
                    "quantity_basis": quantity_basis,
                }
                report = evaluate_option_package_path(
                    enriched,
                    bars,
                    evaluated_at=evaluated_at,
                    timeframe=self.config.timeframe,
                    data_feed=self.option_data.feed,
                    slippage_per_leg=self.config.slippage_per_leg,
                    quantity=quantity,
                    maximum_path_points=(
                        self.config.maximum_path_points
                    ),
                )
                report["details"] = {
                    **report["details"],
                    "trade_state": (
                        trade.get("state")
                        if trade is not None
                        else None
                    ),
                    "actual_realized_pnl": (
                        trade.get("realized_pnl")
                        if trade is not None
                        else None
                    ),
                    "actual_realized_pnl_is_separate": True,
                }
                self.store.record_option_package_evidence(report)
                packages_evaluated += 1
                packages_with_bars += int(
                    report["aligned_points"] > 0
                )
            except (ArithmeticError, OptionDataError, ValueError) as exc:
                packages_failed += 1
                self.store.record_event(
                    "option_evidence_package_failed",
                    str(observation["intent_id"]),
                    {
                        "account_id": self.account_plan.account_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "execution_enabled": False,
                    },
                )
        return OptionEvidenceCycleResult(
            account_id=self.account_plan.account_id,
            observations_considered=len(observations),
            packages_evaluated=packages_evaluated,
            packages_with_aligned_bars=packages_with_bars,
            packages_failed=packages_failed,
            bars_ingested=bars_ingested,
            request_ids=tuple(request_ids),
            execution_enabled=False,
        )
