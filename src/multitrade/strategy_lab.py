from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Callable, Iterable
from uuid import uuid4

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.backtest import BacktestConfig, StrategyValidator
from multitrade.config import Settings
from multitrade.domain import ZERO
from multitrade.experiments import (
    StrategyExperimentBinding,
    StrategyExperimentProgram,
    load_strategy_experiment_program,
)
from multitrade.health import write_health
from multitrade.market import (
    AlpacaMarketDataClient,
    MarketBar,
    closed_bars,
)
from multitrade.portfolio import (
    AccountPlan,
    StrategyAllocation,
    apply_strategy_configuration_overrides,
    load_account_plans,
)
from multitrade.robustness import TradeSequenceStressTester
from multitrade.strategies import (
    default_equity_strategies,
    equity_strategy_from_parameters,
)
from multitrade.strategies.base import Strategy
from multitrade.trials import (
    StrategyTrialDefinition,
    build_strategy_trial_definition,
)
from multitrade.universe import (
    AssetUniverseProgram,
    load_asset_universe_program,
    recommendations_from_reports,
)


@dataclass(frozen=True, slots=True)
class StrategyLabConfig:
    lookback_days: int = 120
    base_slippage_bps: Decimal = Decimal("10")
    stressed_slippage_bps: Decimal = Decimal("25")
    minimum_covered_symbols: int = 2
    minimum_out_of_sample_trades: int = 30
    minimum_profitable_symbol_fraction: Decimal = Decimal("0.50")
    minimum_profit_factor: Decimal = Decimal("1.10")
    maximum_drawdown: Decimal = Decimal("0.10")
    chronological_folds: int = 3
    trade_sequence_paths: int = 500
    comparison_variants_per_strategy_cycle: int = 1
    comparison_rotation_seconds: int = 21600

    def __post_init__(self) -> None:
        if not 30 <= self.lookback_days <= 365:
            raise ValueError("Strategy Lab lookback must be 30-365 days")
        if (
            self.base_slippage_bps < ZERO
            or self.stressed_slippage_bps < self.base_slippage_bps
        ):
            raise ValueError(
                "Strategy Lab stressed costs cannot be below base costs"
            )
        if self.minimum_covered_symbols < 1:
            raise ValueError("Covered-symbol requirement must be positive")
        if self.minimum_out_of_sample_trades < 1:
            raise ValueError("Trade-count requirement must be positive")
        if not ZERO < self.minimum_profitable_symbol_fraction <= Decimal("1"):
            raise ValueError(
                "Profitable-symbol fraction must be in (0, 1]"
            )
        if self.minimum_profit_factor <= ZERO:
            raise ValueError("Profit-factor threshold must be positive")
        if not ZERO < self.maximum_drawdown <= Decimal("1"):
            raise ValueError("Drawdown threshold must be in (0, 1]")
        if not 2 <= self.chronological_folds <= 6:
            raise ValueError("Chronological folds must be 2-6")
        if not 100 <= self.trade_sequence_paths <= 5000:
            raise ValueError("Trade-sequence paths must be 100-5000")
        if not 1 <= (
            self.comparison_variants_per_strategy_cycle
        ) <= 4:
            raise ValueError(
                "Comparison variants per cycle must be 1-4"
            )
        if not 300 <= self.comparison_rotation_seconds <= 86400:
            raise ValueError(
                "Comparison rotation must be 300-86400 seconds"
            )


@dataclass(frozen=True, slots=True)
class StrategySymbolResult:
    symbol: str
    bars: int
    first_bar: str
    last_bar: str
    base_validation_passed: bool
    base_gates: dict[str, bool]
    base_metrics: dict[str, Any]
    stressed_metrics: dict[str, Any]
    chronological_passed: bool
    chronological_gates: dict[str, bool]
    chronological_metrics: dict[str, Any]
    chronological_folds: tuple[dict[str, Any], ...]
    chronological_r_multiples: tuple[Decimal, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyLabReport:
    report_id: str
    account_id: str
    strategy_id: str
    strategy_version: str
    timeframe: str
    evaluated_at: datetime
    configuration_enabled: bool
    paper_execution_configured: bool
    symbols_requested: tuple[str, ...]
    symbols_covered: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    symbol_results: tuple[StrategySymbolResult, ...]
    aggregate_metrics: dict[str, Any]
    gates: dict[str, bool]
    warnings: tuple[str, ...]
    readiness_status: str
    trial_definition: StrategyTrialDefinition
    experiment_binding: StrategyExperimentBinding | None
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if not self.report_id or not self.account_id:
            raise ValueError(
                "Strategy Lab report identity is required"
            )
        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "Strategy Lab evaluation time must be timezone-aware"
            )
        if self.execution_eligible:
            raise ValueError(
                "Strategy Lab reports cannot authorize execution"
            )
        if self.readiness_status not in {
            "insufficient_evidence",
            "research_only",
            "extended_paper_observation_candidate",
        }:
            raise ValueError("Unsupported strategy readiness status")


@dataclass(frozen=True, slots=True)
class StrategyLabCycleResult:
    account_id: str
    evaluated_at: datetime
    timeframe: str
    strategies_evaluated: int
    reports_completed: int
    trials_registered: int
    symbols_requested: int
    symbols_with_bars: int
    request_ids: tuple[str, ...]
    execution_enabled: bool = False


def _decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def _average(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(values)
    if not rows:
        return ZERO
    return sum(rows, start=ZERO) / Decimal(len(rows))


def _median(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(values)
    if not rows:
        return ZERO
    return Decimal(str(median(rows)))


def _metrics_payload(metrics: Any) -> dict[str, Any]:
    return asdict(metrics)


class StrategyLabEvaluator:
    """Adversarial, execution-isolated validation for intraday models."""

    def __init__(
        self,
        *,
        config: StrategyLabConfig | None = None,
    ) -> None:
        self.config = config or StrategyLabConfig()

    def evaluate(
        self,
        *,
        account_plan: AccountPlan,
        strategy: Strategy,
        bars_by_symbol: dict[str, tuple[MarketBar, ...]],
        symbols: Iterable[str] | None = None,
        allocation: StrategyAllocation | None = None,
        experiment_binding: StrategyExperimentBinding | None = None,
        evaluated_at: datetime | None = None,
    ) -> StrategyLabReport:
        evaluated_at = evaluated_at or datetime.now(timezone.utc)
        allocation = (
            allocation
            or account_plan.allocations[strategy.strategy_id]
        )
        results: list[StrategySymbolResult] = []
        missing: list[str] = []
        requested_symbols = tuple(
            dict.fromkeys(symbols or account_plan.watchlist)
        )
        if not requested_symbols:
            raise ValueError("Strategy Lab requires assigned symbols")
        trial_definition = build_strategy_trial_definition(
            strategy=strategy,
            allocation=allocation,
            account_plan=account_plan,
            lab_config=self.config,
            experiment_binding=experiment_binding,
            requested_symbols=requested_symbols,
            bars_by_symbol=bars_by_symbol,
        )

        for symbol in requested_symbols:
            bars = tuple(
                sorted(
                    bars_by_symbol.get(symbol, ()),
                    key=lambda item: item.timestamp,
                )
            )
            try:
                base_validator = StrategyValidator(
                    strategy,
                    config=BacktestConfig(
                        risk_fraction=allocation.risk_fraction,
                        capital_weight=allocation.capital_weight,
                        slippage_bps=self.config.base_slippage_bps,
                    ),
                )
                base = base_validator.validate(bars)
                stressed = StrategyValidator(
                    strategy,
                    config=BacktestConfig(
                        risk_fraction=allocation.risk_fraction,
                        capital_weight=allocation.capital_weight,
                        slippage_bps=self.config.stressed_slippage_bps,
                    ),
                ).validate(bars)
                chronological = base_validator.chronological_stability(
                    bars,
                    folds=self.config.chronological_folds,
                    drawdown_limit=self.config.maximum_drawdown,
                )
            except ValueError:
                missing.append(symbol)
                continue

            symbol_warnings = list(base.warnings)
            if not stressed.passed:
                symbol_warnings.append(
                    "stressed_cost_validation_failed"
                )
            symbol_warnings.extend(chronological.warnings)
            warnings = tuple(dict.fromkeys(symbol_warnings))
            results.append(
                StrategySymbolResult(
                    symbol=symbol,
                    bars=len(bars),
                    first_bar=bars[0].timestamp.isoformat(),
                    last_bar=bars[-1].timestamp.isoformat(),
                    base_validation_passed=base.passed,
                    base_gates=base.gates,
                    base_metrics=_metrics_payload(
                        base.out_of_sample.metrics
                    ),
                    stressed_metrics=_metrics_payload(
                        stressed.out_of_sample.metrics
                    ),
                    chronological_passed=chronological.passed,
                    chronological_gates=chronological.gates,
                    chronological_metrics={
                        "folds_requested": (
                            chronological.folds_requested
                        ),
                        "folds_completed": (
                            chronological.folds_completed
                        ),
                        "total_trade_count": (
                            chronological.total_trade_count
                        ),
                        "profitable_fold_fraction": (
                            chronological.profitable_fold_fraction
                        ),
                        "passed_fold_fraction": (
                            chronological.passed_fold_fraction
                        ),
                        "median_fold_return": (
                            chronological.median_fold_return
                        ),
                        "worst_fold_drawdown": (
                            chronological.worst_fold_drawdown
                        ),
                        "pooled_profit_factor": (
                            chronological.pooled_profit_factor
                        ),
                    },
                    chronological_folds=tuple(
                        asdict(fold) for fold in chronological.folds
                    ),
                    chronological_r_multiples=(
                        chronological.trade_r_multiples
                    ),
                    warnings=warnings,
                )
            )

        aggregate = self._aggregate(results)
        chronological_r_multiples = tuple(
            value
            for result in results
            for value in result.chronological_r_multiples
        )
        stress = TradeSequenceStressTester().evaluate(
            chronological_r_multiples,
            risk_fraction=allocation.risk_fraction,
            seed_material="|".join(
                (
                    account_plan.account_id,
                    strategy.strategy_id,
                    strategy.version,
                    ",".join(requested_symbols),
                    ",".join(
                        format(value, "f")
                        for value in chronological_r_multiples
                    ),
                )
            ),
            paths=self.config.trade_sequence_paths,
            drawdown_limit=self.config.maximum_drawdown,
        )
        aggregate["trade_sequence_stress"] = asdict(stress)
        required_coverage = min(
            self.config.minimum_covered_symbols,
            len(requested_symbols),
        )
        covered_count = len(results)
        trade_count = int(aggregate["out_of_sample_trade_count"])
        profitable_fraction = _decimal(
            aggregate["profitable_symbol_fraction"]
        )
        pooled_profit_factor = aggregate["pooled_profit_factor"]
        gates = {
            "minimum_symbol_coverage": covered_count >= required_coverage,
            "minimum_out_of_sample_trades": (
                trade_count >= self.config.minimum_out_of_sample_trades
            ),
            "positive_median_out_of_sample_return": (
                _decimal(aggregate["median_out_of_sample_return"]) > ZERO
            ),
            "profitable_across_symbols": (
                profitable_fraction
                >= self.config.minimum_profitable_symbol_fraction
            ),
            "pooled_profit_factor": (
                pooled_profit_factor is not None
                and _decimal(pooled_profit_factor)
                >= self.config.minimum_profit_factor
            ),
            "maximum_drawdown": (
                _decimal(aggregate["worst_maximum_drawdown"])
                <= self.config.maximum_drawdown
            ),
            "positive_after_stressed_costs": (
                _decimal(aggregate["median_stressed_return"]) > ZERO
            ),
            "majority_symbol_validations_pass": (
                _decimal(aggregate["passed_symbol_fraction"])
                >= Decimal("0.50")
            ),
            "chronological_fold_coverage": (
                covered_count > 0
                and int(aggregate["chronological_fold_count"])
                == covered_count * self.config.chronological_folds
            ),
            "minimum_chronological_trades": (
                int(aggregate["chronological_trade_count"])
                >= self.config.minimum_out_of_sample_trades
            ),
            "profitable_chronological_folds": (
                _decimal(
                    aggregate[
                        "chronological_profitable_fold_fraction"
                    ]
                )
                >= Decimal("0.50")
            ),
            "majority_chronological_fold_validations_pass": (
                _decimal(
                    aggregate[
                        "chronological_passed_fold_fraction"
                    ]
                )
                >= Decimal("0.50")
            ),
            "positive_median_chronological_return": (
                _decimal(
                    aggregate["chronological_median_fold_return"]
                )
                > ZERO
            ),
            "chronological_maximum_drawdown": (
                _decimal(
                    aggregate["chronological_worst_fold_drawdown"]
                )
                <= self.config.maximum_drawdown
            ),
            "chronological_pooled_profit_factor": (
                aggregate["chronological_pooled_profit_factor"]
                is not None
                and _decimal(
                    aggregate[
                        "chronological_pooled_profit_factor"
                    ]
                )
                >= self.config.minimum_profit_factor
            ),
            **{
                f"trade_sequence_{name}": passed
                for name, passed in stress.gates.items()
            },
        }
        evidence_gates = (
            gates["minimum_symbol_coverage"],
            gates["minimum_out_of_sample_trades"],
        )
        if not all(evidence_gates):
            readiness = "insufficient_evidence"
        elif all(gates.values()):
            readiness = "extended_paper_observation_candidate"
        else:
            readiness = "research_only"
        warnings: list[str] = []
        if missing:
            warnings.append("symbols_missing_validation_history")
        if not all(gates.values()):
            warnings.append("strategy_not_ready_for_extended_paper")
        if (
            experiment_binding is not None
            and experiment_binding.comparison_variant
        ):
            if (
                readiness
                == "extended_paper_observation_candidate"
            ):
                readiness = "research_only"
            warnings.append(
                "comparison_variant_requires_family_review"
            )
        if not stress.passed:
            warnings.append("trade_sequence_stress_failed")
        if allocation.paper_execution_allowed:
            warnings.append(
                "configuration_permission_does_not_override_lab_readiness"
            )

        return StrategyLabReport(
            report_id=str(uuid4()),
            account_id=account_plan.account_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            timeframe=account_plan.timeframe,
            evaluated_at=evaluated_at,
            configuration_enabled=allocation.enabled,
            paper_execution_configured=(
                allocation.paper_execution_allowed
            ),
            symbols_requested=requested_symbols,
            symbols_covered=tuple(item.symbol for item in results),
            missing_symbols=tuple(missing),
            symbol_results=tuple(results),
            aggregate_metrics=aggregate,
            gates=gates,
            warnings=tuple(warnings),
            readiness_status=readiness,
            trial_definition=trial_definition,
            experiment_binding=experiment_binding,
            execution_eligible=False,
        )

    @staticmethod
    def _aggregate(
        results: Iterable[StrategySymbolResult],
    ) -> dict[str, Any]:
        rows = tuple(results)
        if not rows:
            return {
                "symbols_covered": 0,
                "out_of_sample_trade_count": 0,
                "median_out_of_sample_return": ZERO,
                "average_out_of_sample_return": ZERO,
                "median_stressed_return": ZERO,
                "profitable_symbol_fraction": ZERO,
                "passed_symbol_fraction": ZERO,
                "pooled_profit_factor": None,
                "worst_maximum_drawdown": ZERO,
                "average_r_multiple": ZERO,
                "chronological_fold_count": 0,
                "chronological_trade_count": 0,
                "chronological_profitable_fold_fraction": ZERO,
                "chronological_passed_fold_fraction": ZERO,
                "chronological_median_fold_return": ZERO,
                "chronological_worst_fold_drawdown": ZERO,
                "chronological_pooled_profit_factor": None,
            }
        base_returns = tuple(
            _decimal(item.base_metrics["total_return"]) for item in rows
        )
        stressed_returns = tuple(
            _decimal(item.stressed_metrics["total_return"]) for item in rows
        )
        gross_profit = sum(
            (_decimal(item.base_metrics["gross_profit"]) for item in rows),
            start=ZERO,
        )
        gross_loss = sum(
            (_decimal(item.base_metrics["gross_loss"]) for item in rows),
            start=ZERO,
        )
        chronological_folds = tuple(
            fold
            for item in rows
            for fold in item.chronological_folds
        )
        chronological_returns = tuple(
            _decimal(fold["metrics"]["total_return"])
            for fold in chronological_folds
        )
        chronological_gross_profit = sum(
            (
                _decimal(fold["metrics"]["gross_profit"])
                for fold in chronological_folds
            ),
            start=ZERO,
        )
        chronological_gross_loss = sum(
            (
                _decimal(fold["metrics"]["gross_loss"])
                for fold in chronological_folds
            ),
            start=ZERO,
        )
        return {
            "symbols_covered": len(rows),
            "out_of_sample_trade_count": sum(
                int(item.base_metrics["trade_count"]) for item in rows
            ),
            "median_out_of_sample_return": _median(base_returns),
            "average_out_of_sample_return": _average(base_returns),
            "median_stressed_return": _median(stressed_returns),
            "profitable_symbol_fraction": (
                Decimal(
                    sum(value > ZERO for value in base_returns)
                )
                / Decimal(len(rows))
            ),
            "passed_symbol_fraction": (
                Decimal(
                    sum(item.base_validation_passed for item in rows)
                )
                / Decimal(len(rows))
            ),
            "pooled_profit_factor": (
                gross_profit / gross_loss if gross_loss > ZERO else None
            ),
            "worst_maximum_drawdown": max(
                _decimal(item.base_metrics["maximum_drawdown"])
                for item in rows
            ),
            "average_r_multiple": _average(
                _decimal(item.base_metrics["average_r_multiple"])
                for item in rows
            ),
            "chronological_fold_count": len(chronological_folds),
            "chronological_trade_count": sum(
                int(fold["metrics"]["trade_count"])
                for fold in chronological_folds
            ),
            "chronological_profitable_fold_fraction": (
                Decimal(
                    sum(
                        value > ZERO
                        for value in chronological_returns
                    )
                )
                / Decimal(len(chronological_folds))
                if chronological_folds
                else ZERO
            ),
            "chronological_passed_fold_fraction": (
                Decimal(
                    sum(
                        bool(fold["passed"])
                        for fold in chronological_folds
                    )
                )
                / Decimal(len(chronological_folds))
                if chronological_folds
                else ZERO
            ),
            "chronological_median_fold_return": _median(
                chronological_returns
            ),
            "chronological_worst_fold_drawdown": (
                max(
                    _decimal(
                        fold["metrics"]["maximum_drawdown"]
                    )
                    for fold in chronological_folds
                )
                if chronological_folds
                else ZERO
            ),
            "chronological_pooled_profit_factor": (
                chronological_gross_profit
                / chronological_gross_loss
                if chronological_gross_loss > ZERO
                else None
            ),
        }


class ContinuousStrategyLabService:
    """Fetches raw intraday data and persists non-executable model reviews."""

    def __init__(
        self,
        *,
        account_plan: AccountPlan,
        strategies: dict[str, Strategy],
        market_data: AlpacaMarketDataClient,
        store: SqliteAuditStore,
        health_path: str,
        config: StrategyLabConfig | None = None,
        universe_program: AssetUniverseProgram | None = None,
        experiment_program: StrategyExperimentProgram | None = None,
        comparison_strategy_factory: (
            Callable[[dict[str, object]], Strategy] | None
        ) = None,
        evaluation_workers: int = 1,
    ) -> None:
        if not 1 <= evaluation_workers <= 8:
            raise ValueError("Strategy Lab evaluation workers must be 1-8")
        self.base_account_plan = account_plan
        self.account_plan = account_plan
        self.strategies = strategies
        self.market_data = market_data
        self.store = store
        self.health_path = health_path
        self.config = config or StrategyLabConfig()
        self.universe_program = universe_program
        self.experiment_program = experiment_program
        self.comparison_strategy_factory = (
            comparison_strategy_factory
            or equity_strategy_from_parameters
        )
        self.evaluation_workers = evaluation_workers
        self.last_reports: tuple[StrategyLabReport, ...] = ()
        self.comparison_strategies: dict[str, Strategy] = {}
        self.strategy_allocations: dict[str, StrategyAllocation] = {}
        self._configure_strategy_allocations()
        unknown = set(self.strategy_allocations) - set(strategies)
        if unknown:
            raise ValueError(
                "Unknown strategy allocations: "
                + ", ".join(sorted(unknown))
            )
        if self.experiment_program is not None:
            missing_experiments = (
                set(self.strategy_allocations)
                - set(
                    self.experiment_program.experiments_by_strategy
                )
            )
            if missing_experiments:
                raise ValueError(
                    "Missing strategy experiment manifests: "
                    + ", ".join(sorted(missing_experiments))
                )
            for experiment_id, experiment in (
                self.experiment_program
                .comparison_experiments_by_id.items()
            ):
                if experiment.strategy_id not in (
                    self.strategy_allocations
                ):
                    raise ValueError(
                        "Comparison experiment has no account "
                        f"allocation: {experiment.strategy_id}"
                    )
                candidate = self.comparison_strategy_factory(
                    experiment.expected_parameters
                )
                self.experiment_program.bind(
                    candidate,
                    evaluated_at=datetime.now(timezone.utc),
                    experiment_id=experiment_id,
                )
                self.comparison_strategies[
                    experiment_id
                ] = candidate

    @classmethod
    def from_settings(
        cls, settings: Settings
    ) -> "ContinuousStrategyLabService":
        plans = tuple(
            plan
            for plan in load_account_plans(
                settings.portfolio_config_path
            )
            if plan.enabled
        )
        if len(plans) != 1:
            raise ValueError(
                "ContinuousStrategyLabService.from_settings requires "
                "exactly one enabled Paper account"
            )
        return cls.from_account_plan(settings, plans[0])

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
        evaluation_workers: int = 1,
    ) -> "ContinuousStrategyLabService":
        key_id, secret_key, _ = settings.alpaca_credentials_for(
            account_plan.credential_env_prefix
        )
        return cls(
            account_plan=account_plan,
            strategies=default_equity_strategies(),
            market_data=AlpacaMarketDataClient(
                key_id,
                secret_key,
                feed=settings.market_data_feed,
            ),
            store=store or SqliteAuditStore(settings.db_path),
            health_path=str(settings.strategy_lab_health_path),
            universe_program=load_asset_universe_program(
                settings.asset_universe_config_path
            ),
            experiment_program=load_strategy_experiment_program(
                settings.strategy_experiment_program_path
            ),
            config=StrategyLabConfig(
                lookback_days=settings.strategy_lab_lookback_days,
                base_slippage_bps=settings.strategy_lab_base_cost_bps,
                stressed_slippage_bps=(
                    settings.strategy_lab_stressed_cost_bps
                ),
                chronological_folds=(
                    settings.strategy_lab_chronological_folds
                ),
                trade_sequence_paths=(
                    settings.strategy_lab_trade_sequence_paths
                ),
                comparison_variants_per_strategy_cycle=(
                    settings.strategy_lab_comparison_variants
                ),
                comparison_rotation_seconds=(
                    settings.strategy_lab_cycle_seconds
                ),
            ),
            evaluation_workers=evaluation_workers,
        )

    def _configure_strategy_allocations(self) -> None:
        self.strategy_allocations = {}
        for allocation in self.account_plan.allocations.values():
            source_id = allocation.source_strategy_id
            existing = self.strategy_allocations.get(source_id)
            if (
                existing is None
                or allocation.strategy_id == source_id
            ):
                self.strategy_allocations[source_id] = allocation

    def _refresh_account_plan(self) -> None:
        overrides = self.store.strategy_configuration_overrides(
            self.base_account_plan.account_id
        )
        self.account_plan = apply_strategy_configuration_overrides(
            (self.base_account_plan,), overrides
        )[0]
        self._configure_strategy_allocations()

    def run_cycle(
        self,
        *,
        now: datetime | None = None,
        persist_reports: bool = True,
    ) -> StrategyLabCycleResult:
        self._refresh_account_plan()
        evaluated_at = now or datetime.now(timezone.utc)
        start = evaluated_at - timedelta(days=self.config.lookback_days)
        recommendations = (
            recommendations_from_reports(
                SqliteAuditReader(self.store.path)
            )
            if (
                self.universe_program is not None
                and self.store.path != ":memory:"
            )
            else {}
        )
        symbols_by_strategy: dict[str, tuple[str, ...]] = {}
        for strategy_id, allocation in self.strategy_allocations.items():
            execution_symbols = tuple(
                dict.fromkeys(
                    symbol
                    for configured in (
                        self.account_plan.allocations.values()
                    )
                    if configured.source_strategy_id == strategy_id
                    for symbol in configured.symbols
                )
            )
            research_symbols = (
                self.universe_program.assigned_symbols(
                    strategy_id,
                    account_watchlist=self.account_plan.watchlist,
                    recommendations_by_policy=recommendations,
                )
                if self.universe_program is not None
                else self.account_plan.watchlist
            )
            symbols_by_strategy[strategy_id] = tuple(
                dict.fromkeys(
                    (
                        *research_symbols,
                        *allocation.symbols,
                        *execution_symbols,
                    )
                )
            )
        requested_symbols = tuple(
            dict.fromkeys(
                symbol
                for rows in symbols_by_strategy.values()
                for symbol in rows
            )
        )
        fetched = self.market_data.fetch_stock_bars(
            requested_symbols,
            self.account_plan.timeframe,
            start,
            evaluated_at,
            adjustment="raw",
        )
        usable = {
            symbol: closed_bars(rows, now=evaluated_at)
            for symbol, rows in fetched.items()
        }
        self.store.record_market_bars(
            bar for rows in usable.values() for bar in rows
        )
        evaluator = StrategyLabEvaluator(config=self.config)
        evaluation_jobs: list[
            tuple[
                Strategy,
                StrategyAllocation,
                tuple[str, ...],
                StrategyExperimentBinding | None,
            ]
        ] = []
        for strategy_id in self.strategy_allocations:
            strategy = self.strategies[strategy_id]
            experiment_binding = (
                self.experiment_program.bind(
                    strategy,
                    evaluated_at=evaluated_at,
                )
                if self.experiment_program is not None
                else None
            )
            evaluation_jobs.append(
                (
                    strategy,
                    self.strategy_allocations[strategy_id],
                    symbols_by_strategy[strategy_id],
                    experiment_binding,
                )
            )
        baseline_count = len(evaluation_jobs)
        comparisons_by_strategy: dict[
            str, list[tuple[str, Strategy]]
        ] = {}
        for experiment_id, strategy in self.comparison_strategies.items():
            comparisons_by_strategy.setdefault(
                strategy.strategy_id, []
            ).append((experiment_id, strategy))
        rotation_slot = int(
            evaluated_at.timestamp()
            // self.config.comparison_rotation_seconds
        )
        selected_comparisons: list[tuple[str, Strategy]] = []
        for strategy_id in sorted(comparisons_by_strategy):
            candidates = sorted(
                comparisons_by_strategy[strategy_id],
                key=lambda item: item[0],
            )
            start_index = rotation_slot % len(candidates)
            selected_comparisons.extend(
                candidates[
                    (
                        start_index + offset
                    ) % len(candidates)
                ]
                for offset in range(
                    min(
                        self.config
                        .comparison_variants_per_strategy_cycle,
                        len(candidates),
                    )
                )
            )
        for experiment_id, strategy in selected_comparisons:
            experiment_binding = self.experiment_program.bind(
                strategy,
                evaluated_at=evaluated_at,
                experiment_id=experiment_id,
            )
            evaluation_jobs.append(
                (
                    strategy,
                    self.strategy_allocations[strategy.strategy_id],
                    symbols_by_strategy[strategy.strategy_id],
                    experiment_binding,
                )
            )
        comparison_count = len(evaluation_jobs) - baseline_count

        def evaluate_job(
            job: tuple[
                Strategy,
                StrategyAllocation,
                tuple[str, ...],
                StrategyExperimentBinding | None,
            ],
        ) -> StrategyLabReport:
            strategy, allocation, symbols, binding = job
            return evaluator.evaluate(
                account_plan=self.account_plan,
                strategy=strategy,
                bars_by_symbol=usable,
                symbols=symbols,
                allocation=allocation,
                experiment_binding=binding,
                evaluated_at=evaluated_at,
            )

        if not evaluation_jobs:
            reports = []
        elif self.evaluation_workers == 1:
            reports = [evaluate_job(job) for job in evaluation_jobs]
        else:
            with ThreadPoolExecutor(
                max_workers=min(
                    self.evaluation_workers, len(evaluation_jobs)
                ),
                thread_name_prefix="strategy-lab",
            ) as executor:
                reports = list(executor.map(evaluate_job, evaluation_jobs))
        self.last_reports = tuple(reports)
        if persist_reports:
            for report in reports:
                self.store.record_strategy_lab_report(report)
            self.store.record_event(
                "strategy_lab_cycle_completed",
                self.account_plan.account_id,
                {
                    "timeframe": self.account_plan.timeframe,
                    "strategies_evaluated": len(reports),
                    "baseline_strategies_evaluated": baseline_count,
                    "comparison_variants_evaluated": comparison_count,
                    "immutable_trials_registered": len(reports),
                    "symbols_requested": len(requested_symbols),
                    "symbols_with_bars": sum(
                        bool(rows) for rows in usable.values()
                    ),
                    "execution_enabled": False,
                },
            )
        health_details = {
            "account_id": self.account_plan.account_id,
            "timeframe": self.account_plan.timeframe,
            "strategies_evaluated": len(reports),
            "baseline_strategies_evaluated": baseline_count,
            "comparison_variants_evaluated": comparison_count,
            "immutable_trials_registered": len(reports),
            "symbols_requested": len(requested_symbols),
            "symbols_with_bars": sum(bool(rows) for rows in usable.values()),
            "execution_enabled": False,
        }
        if persist_reports:
            write_health(self.health_path, "ok", health_details)
        return StrategyLabCycleResult(
            account_id=self.account_plan.account_id,
            evaluated_at=evaluated_at,
            timeframe=self.account_plan.timeframe,
            strategies_evaluated=len(reports),
            reports_completed=len(reports),
            trials_registered=len(reports) if persist_reports else 0,
            symbols_requested=len(requested_symbols),
            symbols_with_bars=sum(bool(rows) for rows in usable.values()),
            request_ids=tuple(self.market_data.request_ids),
            execution_enabled=False,
        )
