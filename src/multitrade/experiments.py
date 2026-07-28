from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from multitrade.strategies.base import Strategy
from multitrade.trials import (
    canonical_json,
    fingerprint,
    strategy_parameters,
)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,79}$")


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


@dataclass(frozen=True, slots=True)
class StrategyExperiment:
    experiment_id: str
    family_id: str
    strategy_id: str
    strategy_version: str
    variant_id: str
    registered_at: datetime
    prospective_observation_start: datetime
    review_not_before: datetime
    status: str
    hypothesis: str
    mechanism: str
    primary_metric: str
    minimum_prospective_days: int
    minimum_prospective_trials: int
    final_holdout_status: str
    expected_parameters: dict[str, object]
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "family_id",
            "strategy_id",
            "variant_id",
        ):
            if _IDENTIFIER.fullmatch(
                str(getattr(self, field_name))
            ) is None:
                raise ValueError(
                    f"{field_name} is not a valid identifier"
                )
        if not self.strategy_version:
            raise ValueError("strategy_version is required")
        if (
            self.registered_at.tzinfo is None
            or self.prospective_observation_start.tzinfo is None
            or self.review_not_before.tzinfo is None
        ):
            raise ValueError(
                "Experiment timestamps must be timezone-aware"
            )
        if not (
            self.registered_at
            < self.prospective_observation_start
            < self.review_not_before
        ):
            raise ValueError(
                "Experiment registration, observation, and review "
                "timestamps must be strictly ordered"
            )
        if self.status not in {"frozen_research", "retired"}:
            raise ValueError("Unsupported experiment status")
        if self.final_holdout_status not in {
            "not_reserved",
            "reserved",
        }:
            raise ValueError("Unsupported final holdout status")
        if self.minimum_prospective_days < 1:
            raise ValueError(
                "minimum_prospective_days must be positive"
            )
        if self.minimum_prospective_trials < 1:
            raise ValueError(
                "minimum_prospective_trials must be positive"
            )
        if not self.hypothesis or not self.mechanism:
            raise ValueError(
                "Experiment hypothesis and mechanism are required"
            )
        if not self.primary_metric:
            raise ValueError("primary_metric is required")
        if self.execution_eligible:
            raise ValueError(
                "Experiment manifests cannot authorize execution"
            )

    def payload(self) -> dict[str, object]:
        return asdict(self)

    @property
    def manifest_fingerprint(self) -> str:
        return fingerprint(self.payload())


@dataclass(frozen=True, slots=True)
class StrategyExperimentBinding:
    experiment_id: str
    family_id: str
    variant_id: str
    manifest_fingerprint: str
    evidence_phase: str
    prospective: bool
    registered_before_observation: bool
    parameter_match: bool
    manifest: dict[str, object]
    comparison_variant: bool = False
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if self.evidence_phase not in {
            "pre_observation",
            "prospective_observation",
            "review_due",
        }:
            raise ValueError("Unsupported evidence phase")
        if not self.registered_before_observation:
            raise ValueError(
                "Experiment was not registered before observation"
            )
        if not self.parameter_match:
            raise ValueError(
                "Runtime strategy parameters differ from manifest"
            )
        if self.execution_eligible:
            raise ValueError(
                "Experiment bindings cannot authorize execution"
            )


@dataclass(frozen=True, slots=True)
class StrategyExperimentProgram:
    experiments_by_strategy: dict[str, StrategyExperiment]
    comparison_experiments_by_id: dict[
        str, StrategyExperiment
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        all_experiments = tuple(
            self.experiments_by_strategy.values()
        ) + tuple(self.comparison_experiments_by_id.values())
        experiment_ids = {
            experiment.experiment_id
            for experiment in all_experiments
        }
        if len(experiment_ids) != len(all_experiments):
            raise ValueError("Experiment IDs must be unique")
        for strategy_id, experiment in (
            self.experiments_by_strategy.items()
        ):
            if strategy_id != experiment.strategy_id:
                raise ValueError(
                    "Experiment strategy map is inconsistent"
                )
        for experiment_id, experiment in (
            self.comparison_experiments_by_id.items()
        ):
            if experiment_id != experiment.experiment_id:
                raise ValueError(
                    "Comparison experiment map is inconsistent"
                )
        parameter_identities: set[tuple[str, str]] = set()
        variants: set[tuple[str, str]] = set()
        for experiment in all_experiments:
            baseline = self.experiments_by_strategy.get(
                experiment.strategy_id
            )
            if baseline is None:
                raise ValueError(
                    "Comparison experiment has no baseline strategy"
                )
            if baseline.family_id != experiment.family_id:
                raise ValueError(
                    "Experiment family differs from its baseline"
                )
            variant_identity = (
                experiment.strategy_id,
                experiment.variant_id,
            )
            if variant_identity in variants:
                raise ValueError(
                    "Variant IDs must be unique per strategy"
                )
            variants.add(variant_identity)
            parameter_identity = (
                experiment.strategy_id,
                fingerprint(experiment.expected_parameters),
            )
            if parameter_identity in parameter_identities:
                raise ValueError(
                    "Experiment parameters must be unique per strategy"
                )
            parameter_identities.add(parameter_identity)

    @property
    def all_experiments(self) -> tuple[StrategyExperiment, ...]:
        return tuple(
            self.experiments_by_strategy.values()
        ) + tuple(self.comparison_experiments_by_id.values())

    def experiments_for_strategy(
        self, strategy_id: str
    ) -> tuple[StrategyExperiment, ...]:
        baseline = self.experiments_by_strategy.get(strategy_id)
        if baseline is None:
            return ()
        comparisons = tuple(
            experiment
            for experiment in (
                self.comparison_experiments_by_id.values()
            )
            if experiment.strategy_id == strategy_id
        )
        return (baseline,) + comparisons

    def bind(
        self,
        strategy: Strategy,
        *,
        evaluated_at: datetime,
        experiment_id: str | None = None,
    ) -> StrategyExperimentBinding:
        experiment = self.experiments_by_strategy.get(
            strategy.strategy_id
        )
        if experiment_id is not None:
            comparison = (
                self.comparison_experiments_by_id.get(
                    experiment_id
                )
            )
            if comparison is not None:
                experiment = comparison
            elif (
                experiment is None
                or experiment.experiment_id != experiment_id
            ):
                experiment = None
        if experiment is None:
            raise ValueError(
                "No experiment manifest for "
                f"{strategy.strategy_id}"
            )
        if experiment.strategy_id != strategy.strategy_id:
            raise ValueError(
                "Experiment strategy differs from runtime strategy"
            )
        if experiment.status != "frozen_research":
            raise ValueError(
                f"Experiment {experiment.experiment_id} is not active"
            )
        if strategy.version != experiment.strategy_version:
            raise ValueError(
                "Runtime strategy version differs from experiment"
            )
        parameters_match = (
            strategy_parameters(strategy)
            == experiment.expected_parameters
        )
        if not parameters_match:
            raise ValueError(
                "Runtime strategy parameters differ from experiment"
            )
        registered_before = (
            experiment.registered_at
            < experiment.prospective_observation_start
        )
        if evaluated_at < experiment.prospective_observation_start:
            evidence_phase = "pre_observation"
        elif evaluated_at < experiment.review_not_before:
            evidence_phase = "prospective_observation"
        else:
            evidence_phase = "review_due"
        return StrategyExperimentBinding(
            experiment_id=experiment.experiment_id,
            family_id=experiment.family_id,
            variant_id=experiment.variant_id,
            manifest_fingerprint=(
                experiment.manifest_fingerprint
            ),
            evidence_phase=evidence_phase,
            prospective=(
                evaluated_at
                >= experiment.prospective_observation_start
            ),
            registered_before_observation=registered_before,
            parameter_match=parameters_match,
            manifest=experiment.payload(),
            comparison_variant=(
                experiment.experiment_id
                in self.comparison_experiments_by_id
            ),
            execution_eligible=False,
        )


def load_strategy_experiment_program(
    path: str | Path,
) -> StrategyExperimentProgram:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    rows = payload.get("experiments")
    if not isinstance(rows, list) or not rows:
        raise ValueError(
            "Experiment program requires a non-empty experiments list"
        )
    primary_ids_payload = payload.get(
        "primary_experiment_ids", {}
    )
    if not isinstance(primary_ids_payload, dict):
        raise ValueError(
            "primary_experiment_ids must be an object"
        )
    declared_primary_ids = {
        str(strategy_id): str(experiment_id)
        for strategy_id, experiment_id in (
            primary_ids_payload.items()
        )
    }
    experiments: list[StrategyExperiment] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every experiment must be an object")
        experiment = StrategyExperiment(
            experiment_id=str(row.get("experiment_id", "")),
            family_id=str(row.get("family_id", "")),
            strategy_id=str(row.get("strategy_id", "")),
            strategy_version=str(
                row.get("strategy_version", "")
            ),
            variant_id=str(row.get("variant_id", "")),
            registered_at=_timestamp(
                str(row.get("registered_at", "")),
                "registered_at",
            ),
            prospective_observation_start=_timestamp(
                str(
                    row.get(
                        "prospective_observation_start", ""
                    )
                ),
                "prospective_observation_start",
            ),
            review_not_before=_timestamp(
                str(row.get("review_not_before", "")),
                "review_not_before",
            ),
            status=str(row.get("status", "")),
            hypothesis=str(row.get("hypothesis", "")).strip(),
            mechanism=str(row.get("mechanism", "")).strip(),
            primary_metric=str(
                row.get("primary_metric", "")
            ).strip(),
            minimum_prospective_days=int(
                row.get("minimum_prospective_days", 0)
            ),
            minimum_prospective_trials=int(
                row.get("minimum_prospective_trials", 0)
            ),
            final_holdout_status=str(
                row.get("final_holdout_status", "")
            ),
            expected_parameters=dict(
                row.get("expected_parameters", {})
            ),
            execution_eligible=bool(
                row.get("execution_eligible", False)
            ),
        )
        experiments.append(experiment)
    grouped: dict[str, list[StrategyExperiment]] = {}
    for experiment in experiments:
        grouped.setdefault(experiment.strategy_id, []).append(
            experiment
        )
    unknown_primary_strategies = (
        set(declared_primary_ids) - set(grouped)
    )
    if unknown_primary_strategies:
        raise ValueError(
            "Primary experiment references unknown strategies"
        )
    baselines: dict[str, StrategyExperiment] = {}
    comparisons: dict[str, StrategyExperiment] = {}
    for strategy_id, candidates in grouped.items():
        declared_id = declared_primary_ids.get(strategy_id)
        if declared_id is None:
            if len(candidates) != 1:
                raise ValueError(
                    "Multiple strategy experiments require an "
                    "explicit primary_experiment_ids entry"
                )
            baseline = candidates[0]
        else:
            matches = tuple(
                candidate
                for candidate in candidates
                if candidate.experiment_id == declared_id
            )
            if len(matches) != 1:
                raise ValueError(
                    "Primary experiment ID is missing or ambiguous"
                )
            baseline = matches[0]
        baselines[strategy_id] = baseline
        for candidate in candidates:
            if candidate.experiment_id != baseline.experiment_id:
                comparisons[candidate.experiment_id] = candidate
    return StrategyExperimentProgram(
        baselines,
        comparisons,
    )


def experiment_program_payload(
    program: StrategyExperimentProgram,
) -> dict[str, object]:
    return {
        "experiments": [
            {
                **json.loads(
                    canonical_json(experiment.payload())
                ),
                "manifest_fingerprint": (
                    experiment.manifest_fingerprint
                ),
            }
            for experiment in sorted(
                program.all_experiments,
                key=lambda item: (
                    item.family_id,
                    item.strategy_id,
                ),
            )
        ],
        "execution_enabled": False,
    }
