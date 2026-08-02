from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import product
from typing import Any

from multitrade.domain import ZERO
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan, StrategyAllocation
from multitrade.strategies import equity_strategy_from_parameters
from multitrade.strategy_lab import (
    StrategyLabConfig,
    StrategyLabEvaluator,
    StrategyLabReport,
)
from multitrade.trials import fingerprint, strategy_parameters


_SEARCH_SPACES: dict[str, dict[str, tuple[object, ...]]] = {
    "breakout_retest": {
        "lookback": (15, 20, 30),
        "retest_tolerance": ("0.002", "0.003", "0.004"),
        "volume_multiplier": ("1.15", "1.30"),
        "reward_multiple": ("1.5", "2", "2.5"),
    },
    "trend_pullback": {
        "tolerance": ("0.0025", "0.004", "0.006"),
        "reward_multiple": ("1.5", "2", "2.5"),
    },
    "volatility_contraction": {
        "contraction_ratio": ("0.60", "0.70", "0.80"),
        "volume_multiplier": ("1.10", "1.20", "1.35"),
        "reward_multiple": ("1.75", "2.25", "2.75"),
    },
    "range_mean_reversion": {
        "deviation_multiple": ("1.75", "2", "2.25"),
        "reward_multiple": ("1.25", "1.5", "2"),
    },
    "t3_range_trend": {
        "t3_length": (5, 8, 13),
        "t3_factor": ("0.618", "0.7"),
        "range_period": (14, 20, 30),
        "range_multiplier": ("2", "2.5", "3"),
        "stop_atr_multiple": ("1.2", "1.4", "1.8"),
        "reward_multiple": ("2.5", "3.8", "4.5"),
    },
}


@dataclass(frozen=True, slots=True)
class OptimizationCandidate:
    candidate_id: str
    strategy_id: str
    parameters: dict[str, object]
    development_score: int
    development_gates_passed: int
    development_gates_total: int
    development_metrics: dict[str, Any]
    selected_for_holdout: bool
    holdout_gates_passed: int | None = None
    holdout_gates_total: int | None = None
    holdout_metrics: dict[str, Any] | None = None
    holdout_passed: bool = False
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if self.execution_eligible:
            raise ValueError("Optimization cannot authorize execution")


def _sample_evenly(rows: list[dict[str, object]], limit: int):
    if len(rows) <= limit:
        return rows
    if limit == 1:
        return [rows[0]]
    indexes = {
        round(index * (len(rows) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [rows[index] for index in sorted(indexes)]


def candidate_parameters(
    strategy_id: str,
    *,
    limit: int,
) -> tuple[dict[str, object], ...]:
    if limit < 1:
        raise ValueError("Candidate limit must be positive")
    space = _SEARCH_SPACES.get(strategy_id)
    if space is None:
        raise ValueError(f"No bounded search space for {strategy_id}")
    names = tuple(space)
    combinations = [
        {
            "strategy_id": strategy_id,
            "version": "1.0.0",
            **dict(zip(names, values)),
        }
        for values in product(*(space[name] for name in names))
    ]
    return tuple(_sample_evenly(combinations, limit))


def _split_dataset(
    bars_by_symbol: dict[str, tuple[MarketBar, ...]],
    *,
    development_fraction: Decimal,
) -> tuple[
    dict[str, tuple[MarketBar, ...]],
    dict[str, tuple[MarketBar, ...]],
]:
    development: dict[str, tuple[MarketBar, ...]] = {}
    holdout: dict[str, tuple[MarketBar, ...]] = {}
    for symbol, source in bars_by_symbol.items():
        rows = tuple(sorted(source, key=lambda bar: bar.timestamp))
        split = int(Decimal(len(rows)) * development_fraction)
        development[symbol] = rows[:split]
        holdout[symbol] = rows[split:]
    return development, holdout


def _metrics(report: StrategyLabReport) -> dict[str, Any]:
    aggregate = report.aggregate_metrics
    return {
        "trade_count": aggregate.get("out_of_sample_trade_count", 0),
        "median_return": aggregate.get(
            "median_out_of_sample_return", ZERO
        ),
        "stressed_return": aggregate.get("median_stressed_return", ZERO),
        "profit_factor": aggregate.get("pooled_profit_factor"),
        "worst_drawdown": aggregate.get("worst_maximum_drawdown", ZERO),
        "profitable_symbol_fraction": aggregate.get(
            "profitable_symbol_fraction", ZERO
        ),
        "chronological_median_return": aggregate.get(
            "chronological_median_fold_return", ZERO
        ),
    }


def _score(report: StrategyLabReport) -> int:
    gates = report.gates
    if not gates:
        return 0
    raw = round(100 * sum(gates.values()) / len(gates))
    if not (
        gates.get("minimum_symbol_coverage")
        and gates.get("minimum_out_of_sample_trades")
        and gates.get("minimum_chronological_trades")
    ):
        return min(raw, 39)
    return raw


class BoundedParameterOptimizer:
    """Nested historical search with an untouched final time segment."""

    def __init__(
        self,
        *,
        account_plan: AccountPlan,
        config: StrategyLabConfig,
        bars_by_symbol: dict[str, tuple[MarketBar, ...]],
        symbols_by_strategy: dict[str, tuple[str, ...]],
        allocations: dict[str, StrategyAllocation],
        workers: int = 2,
        max_candidates: int = 48,
        development_fraction: Decimal = Decimal("0.70"),
    ) -> None:
        if not 1 <= workers <= 8:
            raise ValueError("Optimization workers must be 1-8")
        if not 4 <= max_candidates <= 160:
            raise ValueError("Optimization candidates must be 4-160")
        if not Decimal("0.60") <= development_fraction <= Decimal("0.80"):
            raise ValueError("Development fraction must be 0.60-0.80")
        self.account_plan = account_plan
        self.config = config
        self.bars_by_symbol = bars_by_symbol
        self.symbols_by_strategy = symbols_by_strategy
        self.allocations = allocations
        self.workers = workers
        self.max_candidates = max_candidates
        self.development_fraction = development_fraction

    def run(self) -> dict[str, Any]:
        development, holdout = _split_dataset(
            self.bars_by_symbol,
            development_fraction=self.development_fraction,
        )
        strategy_ids = tuple(
            sorted(set(self.allocations) & set(_SEARCH_SPACES))
        )
        if not strategy_ids:
            return {
                "method": "nested_chronological_grid_search",
                "development_fraction": self.development_fraction,
                "holdout_fraction": (
                    Decimal("1") - self.development_fraction
                ),
                "candidate_count": 0,
                "selected_count": 0,
                "holdout_pass_count": 0,
                "candidates": (),
                "selection_used_holdout": False,
                "automatic_execution_promotion": False,
                "execution_eligible": False,
            }
        per_strategy = max(1, self.max_candidates // len(strategy_ids))
        jobs = tuple(
            (strategy_id, parameters)
            for strategy_id in strategy_ids
            for parameters in candidate_parameters(
                strategy_id, limit=per_strategy
            )
        )
        evaluator = StrategyLabEvaluator(config=self.config)

        def evaluate_development(job):
            strategy_id, parameters = job
            strategy = equity_strategy_from_parameters(parameters)
            report = evaluator.evaluate(
                account_plan=self.account_plan,
                strategy=strategy,
                bars_by_symbol=development,
                symbols=self.symbols_by_strategy[strategy_id],
                allocation=self.allocations[strategy_id],
            )
            return parameters, report

        if self.workers == 1:
            development_results = [
                evaluate_development(job) for job in jobs
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=min(self.workers, len(jobs)),
                thread_name_prefix="parameter-optimization",
            ) as executor:
                development_results = list(
                    executor.map(evaluate_development, jobs)
                )

        ranked_by_strategy: dict[
            str, list[tuple[dict[str, object], StrategyLabReport]]
        ] = {}
        for parameters, report in development_results:
            ranked_by_strategy.setdefault(
                report.strategy_id, []
            ).append((parameters, report))
        selected: dict[str, str] = {}
        holdout_reports: dict[str, StrategyLabReport] = {}
        for strategy_id, rows in ranked_by_strategy.items():
            rows.sort(
                key=lambda item: (
                    -_score(item[1]),
                    -Decimal(
                        str(
                            item[1].aggregate_metrics.get(
                                "pooled_profit_factor"
                            )
                            or ZERO
                        )
                    ),
                    -Decimal(
                        str(
                            item[1].aggregate_metrics.get(
                                "median_stressed_return", ZERO
                            )
                        )
                    ),
                    fingerprint(item[0]),
                )
            )
            parameters, _ = rows[0]
            candidate_id = fingerprint(parameters)[:16]
            selected[strategy_id] = candidate_id
            holdout_reports[strategy_id] = evaluator.evaluate(
                account_plan=self.account_plan,
                strategy=equity_strategy_from_parameters(parameters),
                bars_by_symbol=holdout,
                symbols=self.symbols_by_strategy[strategy_id],
                allocation=self.allocations[strategy_id],
            )

        candidates: list[OptimizationCandidate] = []
        for parameters, report in development_results:
            candidate_id = fingerprint(parameters)[:16]
            is_selected = selected[report.strategy_id] == candidate_id
            holdout_report = (
                holdout_reports[report.strategy_id]
                if is_selected
                else None
            )
            candidates.append(
                OptimizationCandidate(
                    candidate_id=candidate_id,
                    strategy_id=report.strategy_id,
                    parameters=strategy_parameters(
                        equity_strategy_from_parameters(parameters)
                    ),
                    development_score=_score(report),
                    development_gates_passed=sum(report.gates.values()),
                    development_gates_total=len(report.gates),
                    development_metrics=_metrics(report),
                    selected_for_holdout=is_selected,
                    holdout_gates_passed=(
                        sum(holdout_report.gates.values())
                        if holdout_report is not None
                        else None
                    ),
                    holdout_gates_total=(
                        len(holdout_report.gates)
                        if holdout_report is not None
                        else None
                    ),
                    holdout_metrics=(
                        _metrics(holdout_report)
                        if holdout_report is not None
                        else None
                    ),
                    holdout_passed=bool(
                        holdout_report
                        and all(holdout_report.gates.values())
                    ),
                    execution_eligible=False,
                )
            )
        candidates.sort(
            key=lambda item: (
                item.strategy_id,
                not item.selected_for_holdout,
                -item.development_score,
                item.candidate_id,
            )
        )
        return {
            "method": "nested_chronological_grid_search",
            "development_fraction": self.development_fraction,
            "holdout_fraction": Decimal("1") - self.development_fraction,
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "holdout_pass_count": sum(
                item.holdout_passed for item in candidates
            ),
            "candidates": tuple(asdict(item) for item in candidates),
            "selection_used_holdout": False,
            "automatic_execution_promotion": False,
            "execution_eligible": False,
        }
