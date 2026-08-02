import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from multitrade.experiments import (
    StrategyExperimentProgram,
    experiment_program_payload,
    load_strategy_experiment_program,
)
from multitrade.strategies import (
    default_equity_strategies,
    equity_strategy_from_parameters,
)
from multitrade.strategies.equity import BreakoutRetestStrategy


class StrategyExperimentProgramTests(TestCase):
    @staticmethod
    def program():
        return load_strategy_experiment_program(
            Path(__file__).parents[1]
            / "config"
            / "strategy_experiments.json"
        )

    def test_repository_manifests_match_runtime_strategies(
        self,
    ) -> None:
        program = self.program()
        strategies = default_equity_strategies()

        self.assertEqual(
            set(program.experiments_by_strategy),
            set(strategies),
        )
        self.assertEqual(
            len(program.comparison_experiments_by_id),
            16,
        )
        self.assertEqual(len(program.all_experiments), 24)
        self.assertEqual(
            len(
                {
                    experiment.family_id
                    for experiment in (
                        program.experiments_by_strategy.values()
                    )
                }
            ),
            7,
        )
        for strategy in strategies.values():
            binding = program.bind(
                strategy,
                evaluated_at=datetime(
                    2026, 8, 4, tzinfo=timezone.utc
                ),
            )
            self.assertEqual(
                binding.evidence_phase,
                "prospective_observation",
            )
            self.assertTrue(binding.prospective)
            self.assertFalse(binding.execution_eligible)
            self.assertEqual(
                len(binding.manifest_fingerprint), 64
            )
        family_counts = {}
        for experiment in program.all_experiments:
            family_counts[experiment.family_id] = (
                family_counts.get(experiment.family_id, 0) + 1
            )
            candidate = equity_strategy_from_parameters(
                experiment.expected_parameters
            )
            binding = program.bind(
                candidate,
                evaluated_at=datetime(
                    2026, 8, 4, tzinfo=timezone.utc
                ),
                experiment_id=(
                    None
                    if experiment.experiment_id
                    == program.experiments_by_strategy[
                        experiment.strategy_id
                    ].experiment_id
                    else experiment.experiment_id
                ),
            )
            self.assertEqual(
                binding.variant_id,
                experiment.variant_id,
            )
        self.assertEqual(
            family_counts,
            {
                "intraday_breakout_continuation": 6,
                "intraday_trend_continuation": 3,
                "intraday_range_reversion": 3,
                "dual_filter_trend_continuation": 3,
                "mathematical_chart_pattern_confluence": 3,
                "defined_risk_put_income": 3,
                "defined_risk_put_income_v2": 3,
            },
        )

    def test_evidence_phase_and_parameter_freeze_fail_closed(
        self,
    ) -> None:
        program = self.program()
        strategy = BreakoutRetestStrategy()

        pre = program.bind(
            strategy,
            evaluated_at=datetime(
                2026, 7, 28, 12, tzinfo=timezone.utc
            ),
        )
        due = program.bind(
            strategy,
            evaluated_at=datetime(
                2026, 8, 20, tzinfo=timezone.utc
            ),
        )

        self.assertEqual(pre.evidence_phase, "pre_observation")
        self.assertFalse(pre.prospective)
        self.assertEqual(due.evidence_phase, "review_due")
        with self.assertRaisesRegex(
            ValueError, "parameters differ"
        ):
            program.bind(
                replace(strategy, lookback=21),
                evaluated_at=datetime(
                    2026, 7, 30, tzinfo=timezone.utc
                ),
            )

    def test_dashboard_payload_is_json_serializable(self) -> None:
        payload = experiment_program_payload(self.program())

        encoded = json.dumps(payload, sort_keys=True)

        self.assertIn(
            "breakout_retest_baseline_2026q3", encoded
        )
        self.assertEqual(len(payload["experiments"]), 24)
        self.assertFalse(payload["execution_enabled"])
        self.assertTrue(
            all(
                item["final_holdout_status"] == "not_reserved"
                for item in payload["experiments"]
            )
        )

    def test_duplicate_candidate_parameters_fail_closed(
        self,
    ) -> None:
        baseline = self.program().experiments_by_strategy[
            "breakout_retest"
        ]
        duplicate = replace(
            baseline,
            experiment_id="breakout_retest_duplicate_2026q3",
            variant_id="duplicate_v1",
        )

        with self.assertRaisesRegex(
            ValueError, "parameters must be unique"
        ):
            StrategyExperimentProgram(
                {"breakout_retest": baseline},
                {duplicate.experiment_id: duplicate},
            )

    def test_candidate_constructor_requires_exact_fields(
        self,
    ) -> None:
        parameters = dict(
            self.program().experiments_by_strategy[
                "breakout_retest"
            ].expected_parameters
        )
        parameters.pop("lookback")

        with self.assertRaisesRegex(
            ValueError, "exactly match"
        ):
            equity_strategy_from_parameters(parameters)
