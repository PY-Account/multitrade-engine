from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import uuid4

from multitrade.audit import SqliteAuditStore
from multitrade.config import Settings
from multitrade.portfolio import AccountPlan
from multitrade.strategy_lab import (
    ContinuousStrategyLabService,
    StrategyLabReport,
)


@dataclass(frozen=True, slots=True)
class AcceleratedScorecard:
    run_id: str
    account_id: str
    candidate_id: str
    family_id: str
    strategy_id: str
    strategy_version: str
    variant_id: str
    comparison_variant: bool
    research_score: int
    score_components: dict[str, int]
    classification: str
    readiness_status: str
    gates_passed: int
    gates_total: int
    failed_gates: tuple[str, ...]
    failure_explanations: tuple[str, ...]
    symbols_requested: tuple[str, ...]
    symbols_covered: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    metrics: dict[str, Any]
    candidate_fingerprint: str
    dataset_fingerprint: str
    evidence_kind: str = "historical_accelerated_screening"
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.research_score <= 100:
            raise ValueError("Research score must be in [0, 100]")
        if self.execution_eligible:
            raise ValueError(
                "Accelerated validation cannot authorize execution"
            )


@dataclass(frozen=True, slots=True)
class AcceleratedValidationRun:
    run_id: str
    account_id: str
    evaluated_at: datetime
    timeframe: str
    duration_seconds: Decimal
    symbols_requested: int
    symbols_with_bars: int
    baseline_candidates: int
    comparison_candidates: int
    dataset_fingerprints: tuple[str, ...]
    scorecards: tuple[AcceleratedScorecard, ...]
    summary: dict[str, Any]
    request_ids: tuple[str, ...]
    evidence_kind: str = "historical_accelerated_screening"
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if not self.run_id or not self.account_id:
            raise ValueError("Accelerated validation identity is required")
        if self.evaluated_at.tzinfo is None:
            raise ValueError(
                "Accelerated validation time must be timezone-aware"
            )
        if self.execution_eligible:
            raise ValueError(
                "Accelerated validation cannot authorize execution"
            )


_FAILURE_EXPLANATIONS = {
    "minimum_symbol_coverage": (
        "Too few assigned symbols had enough usable historical bars."
    ),
    "minimum_out_of_sample_trades": (
        "The out-of-sample trade count is below the minimum evidence gate."
    ),
    "positive_median_out_of_sample_return": (
        "The median out-of-sample return is not positive."
    ),
    "profitable_across_symbols": (
        "The result is not profitable across enough different symbols."
    ),
    "pooled_profit_factor": (
        "The pooled gross-profit to gross-loss ratio is below the gate."
    ),
    "maximum_drawdown": (
        "At least one symbol exceeds the permitted out-of-sample drawdown."
    ),
    "positive_after_stressed_costs": (
        "Conservative slippage and cost assumptions remove the positive return."
    ),
    "majority_symbol_validations_pass": (
        "Fewer than half of the symbol-level validations pass."
    ),
    "chronological_fold_coverage": (
        "Not all required non-overlapping chronological folds completed."
    ),
    "minimum_chronological_trades": (
        "The walk-forward folds contain too few trades."
    ),
    "profitable_chronological_folds": (
        "Fewer than half of the chronological folds are profitable."
    ),
    "majority_chronological_fold_validations_pass": (
        "Fewer than half of the chronological fold validations pass."
    ),
    "positive_median_chronological_return": (
        "The median return across later chronological windows is not positive."
    ),
    "chronological_maximum_drawdown": (
        "A chronological fold exceeds the permitted drawdown."
    ),
    "chronological_pooled_profit_factor": (
        "The walk-forward pooled profit factor is below the gate."
    ),
    "trade_sequence_minimum_trade_sample": (
        "The trade sample is too small for reliable sequence stress."
    ),
    "trade_sequence_fifth_percentile_loss_within_limit": (
        "The adverse fifth-percentile simulated return breaches the limit."
    ),
    "trade_sequence_tail_drawdown_within_limit": (
        "The simulated tail drawdown exceeds the limit."
    ),
    "trade_sequence_drawdown_limit_probability": (
        "Too many simulated trade sequences breach the drawdown limit."
    ),
}

_SCORE_GROUPS = {
    "evidence": (
        20,
        (
            "minimum_symbol_coverage",
            "minimum_out_of_sample_trades",
            "minimum_chronological_trades",
        ),
    ),
    "return_after_costs": (
        25,
        (
            "positive_median_out_of_sample_return",
            "pooled_profit_factor",
            "positive_after_stressed_costs",
            "positive_median_chronological_return",
            "chronological_pooled_profit_factor",
        ),
    ),
    "breadth_and_stability": (
        20,
        (
            "profitable_across_symbols",
            "majority_symbol_validations_pass",
            "profitable_chronological_folds",
            "majority_chronological_fold_validations_pass",
            "chronological_fold_coverage",
        ),
    ),
    "drawdown": (
        15,
        (
            "maximum_drawdown",
            "chronological_maximum_drawdown",
        ),
    ),
    "sequence_stress": (
        20,
        (
            "trade_sequence_minimum_trade_sample",
            "trade_sequence_fifth_percentile_loss_within_limit",
            "trade_sequence_tail_drawdown_within_limit",
            "trade_sequence_drawdown_limit_probability",
        ),
    ),
}


def _score_components(gates: dict[str, bool]) -> dict[str, int]:
    components: dict[str, int] = {}
    for name, (weight, members) in _SCORE_GROUPS.items():
        passed = sum(bool(gates.get(member)) for member in members)
        components[name] = round(weight * passed / len(members))
    return components


def scorecard_from_report(
    report: StrategyLabReport, *, run_id: str
) -> AcceleratedScorecard:
    binding = report.experiment_binding
    failed_gates = tuple(
        name for name, passed in report.gates.items() if not passed
    )
    components = _score_components(report.gates)
    score = sum(components.values())
    evidence_failed = any(
        not report.gates.get(name, False)
        for name in (
            "minimum_symbol_coverage",
            "minimum_out_of_sample_trades",
            "minimum_chronological_trades",
        )
    )
    if evidence_failed:
        score = min(score, 39)
        classification = "insufficient_evidence"
    elif not failed_gates and binding is not None and binding.comparison_variant:
        classification = "family_review_candidate"
    elif not failed_gates:
        classification = "prospective_observation_candidate"
    elif score >= 70:
        classification = "continue_research"
    else:
        classification = "rejected_by_research_gates"

    metrics = report.aggregate_metrics
    summarized_metrics = {
        "out_of_sample_trade_count": metrics.get(
            "out_of_sample_trade_count", 0
        ),
        "median_out_of_sample_return": metrics.get(
            "median_out_of_sample_return", Decimal("0")
        ),
        "median_stressed_return": metrics.get(
            "median_stressed_return", Decimal("0")
        ),
        "pooled_profit_factor": metrics.get("pooled_profit_factor"),
        "worst_maximum_drawdown": metrics.get(
            "worst_maximum_drawdown", Decimal("0")
        ),
        "chronological_fold_count": metrics.get(
            "chronological_fold_count", 0
        ),
        "chronological_trade_count": metrics.get(
            "chronological_trade_count", 0
        ),
        "chronological_profitable_fold_fraction": metrics.get(
            "chronological_profitable_fold_fraction", Decimal("0")
        ),
        "chronological_median_fold_return": metrics.get(
            "chronological_median_fold_return", Decimal("0")
        ),
        "chronological_worst_fold_drawdown": metrics.get(
            "chronological_worst_fold_drawdown", Decimal("0")
        ),
        "trade_sequence_stress": metrics.get(
            "trade_sequence_stress", {}
        ),
    }
    definition = report.trial_definition
    return AcceleratedScorecard(
        run_id=run_id,
        account_id=report.account_id,
        candidate_id=(
            binding.experiment_id
            if binding is not None
            else f"{report.strategy_id}:{report.strategy_version}"
        ),
        family_id=(
            binding.family_id if binding is not None else report.strategy_id
        ),
        strategy_id=report.strategy_id,
        strategy_version=report.strategy_version,
        variant_id=(
            binding.variant_id if binding is not None else "baseline"
        ),
        comparison_variant=bool(
            binding and binding.comparison_variant
        ),
        research_score=score,
        score_components=components,
        classification=classification,
        readiness_status=report.readiness_status,
        gates_passed=len(report.gates) - len(failed_gates),
        gates_total=len(report.gates),
        failed_gates=failed_gates,
        failure_explanations=tuple(
            _FAILURE_EXPLANATIONS.get(
                gate,
                f"The {gate.replace('_', ' ')} gate did not pass.",
            )
            for gate in failed_gates
        ),
        symbols_requested=report.symbols_requested,
        symbols_covered=report.symbols_covered,
        missing_symbols=report.missing_symbols,
        metrics=summarized_metrics,
        candidate_fingerprint=definition.candidate_fingerprint,
        dataset_fingerprint=definition.dataset_fingerprint,
        execution_eligible=False,
    )


class AcceleratedValidationService:
    """One-shot, all-candidate historical screening without trial inflation."""

    def __init__(
        self,
        *,
        lab_service: ContinuousStrategyLabService,
        store: SqliteAuditStore,
    ) -> None:
        self.lab_service = lab_service
        self.store = store

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
        workers: int = 2,
    ) -> "AcceleratedValidationService":
        shared_store = store or SqliteAuditStore(settings.db_path)
        lab_service = ContinuousStrategyLabService.from_account_plan(
            settings,
            account_plan,
            store=shared_store,
            evaluation_workers=workers,
        )
        lab_service.config = replace(
            lab_service.config,
            comparison_variants_per_strategy_cycle=4,
        )
        return cls(lab_service=lab_service, store=shared_store)

    def run(
        self, *, now: datetime | None = None
    ) -> AcceleratedValidationRun:
        evaluated_at = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        started = perf_counter()
        cycle = self.lab_service.run_cycle(
            now=evaluated_at,
            persist_reports=False,
        )
        duration = Decimal(str(round(perf_counter() - started, 3)))
        run_id = str(uuid4())
        scorecards = tuple(
            scorecard_from_report(report, run_id=run_id)
            for report in self.lab_service.last_reports
        )
        classifications = Counter(
            item.classification for item in scorecards
        )
        baseline = tuple(
            item for item in scorecards if not item.comparison_variant
        )
        summary = {
            "candidate_count": len(scorecards),
            "classification_counts": dict(sorted(classifications.items())),
            "highest_scoring_baseline": (
                max(
                    baseline,
                    key=lambda item: (
                        item.research_score,
                        item.strategy_id,
                    ),
                ).strategy_id
                if baseline
                else None
            ),
            "highest_baseline_score": (
                max(item.research_score for item in baseline)
                if baseline
                else None
            ),
            "all_candidates_evaluated_same_cycle": True,
            "prospective_trial_count_incremented": False,
            "execution_enabled": False,
        }
        run = AcceleratedValidationRun(
            run_id=run_id,
            account_id=cycle.account_id,
            evaluated_at=evaluated_at,
            timeframe=cycle.timeframe,
            duration_seconds=duration,
            symbols_requested=cycle.symbols_requested,
            symbols_with_bars=cycle.symbols_with_bars,
            baseline_candidates=len(baseline),
            comparison_candidates=(
                len(scorecards) - len(baseline)
            ),
            dataset_fingerprints=tuple(
                sorted(
                    {
                        item.dataset_fingerprint
                        for item in scorecards
                    }
                )
            ),
            scorecards=scorecards,
            summary=summary,
            request_ids=cycle.request_ids,
            execution_eligible=False,
        )
        self.store.record_accelerated_validation_run(run)
        return run


def accelerated_validation_payload(
    run: AcceleratedValidationRun,
) -> dict[str, Any]:
    return asdict(run)
