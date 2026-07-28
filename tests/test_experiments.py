import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from multitrade.experiments import (
    experiment_program_payload,
    load_strategy_experiment_program,
)
from multitrade.strategies import default_equity_strategies
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
            len(
                {
                    experiment.family_id
                    for experiment in (
                        program.experiments_by_strategy.values()
                    )
                }
            ),
            3,
        )
        for strategy in strategies.values():
            binding = program.bind(
                strategy,
                evaluated_at=datetime(
                    2026, 7, 30, tzinfo=timezone.utc
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
        self.assertFalse(payload["execution_enabled"])
        self.assertTrue(
            all(
                item["final_holdout_status"] == "not_reserved"
                for item in payload["experiments"]
            )
        )
