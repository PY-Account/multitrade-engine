from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
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

    def __post_init__(self) -> None:
        experiment_ids = {
            experiment.experiment_id
            for experiment in self.experiments_by_strategy.values()
        }
        if len(experiment_ids) != len(
            self.experiments_by_strategy
        ):
            raise ValueError("Experiment IDs must be unique")
        for strategy_id, experiment in (
            self.experiments_by_strategy.items()
        ):
            if strategy_id != experiment.strategy_id:
                raise ValueError(
                    "Experiment strategy map is inconsistent"
                )

    def bind(
        self,
        strategy: Strategy,
        *,
        evaluated_at: datetime,
    ) -> StrategyExperimentBinding:
        experiment = self.experiments_by_strategy.get(
            strategy.strategy_id
        )
        if experiment is None:
            raise ValueError(
                f"No experiment manifest for {strategy.strategy_id}"
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
    experiments: dict[str, StrategyExperiment] = {}
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
        if experiment.strategy_id in experiments:
            raise ValueError(
                "Only one active experiment per strategy is supported"
            )
        experiments[experiment.strategy_id] = experiment
    return StrategyExperimentProgram(experiments)


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
                program.experiments_by_strategy.values(),
                key=lambda item: (
                    item.family_id,
                    item.strategy_id,
                ),
            )
        ],
        "execution_enabled": False,
    }
