from dataclasses import replace
from decimal import Decimal
from unittest import TestCase

from multitrade.parameter_optimization import (
    BoundedParameterOptimizer,
    candidate_parameters,
)
from multitrade.portfolio import StrategyAllocation
from multitrade.strategy_lab import StrategyLabConfig
from tests.test_strategy_lab import account_plan, intraday_bars


class ParameterOptimizationTests(TestCase):
    def test_search_space_is_bounded_unique_and_deterministic(self):
        first = candidate_parameters("breakout_retest", limit=7)
        second = candidate_parameters("breakout_retest", limit=7)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 7)
        self.assertEqual(
            len({tuple(sorted(item.items())) for item in first}), 7
        )
        self.assertTrue(
            all(item["strategy_id"] == "breakout_retest" for item in first)
        )

    def test_optimizer_selects_on_development_then_tests_holdout(self):
        allocation = StrategyAllocation(
            strategy_id="breakout_retest",
            enabled=True,
            capital_weight=Decimal("0.20"),
            risk_fraction=Decimal("0.005"),
            minimum_confidence=Decimal("0.60"),
            paper_execution_allowed=False,
        )
        plan = replace(
            account_plan(),
            allocations={"breakout_retest": allocation},
        )
        result = BoundedParameterOptimizer(
            account_plan=plan,
            config=StrategyLabConfig(
                base_slippage_bps=Decimal("0"),
                stressed_slippage_bps=Decimal("10"),
                minimum_out_of_sample_trades=1,
                trade_sequence_paths=100,
            ),
            bars_by_symbol={
                "SPY": intraday_bars("SPY", 600),
                "QQQ": intraday_bars("QQQ", 600),
            },
            symbols_by_strategy={
                "breakout_retest": ("SPY", "QQQ")
            },
            allocations={"breakout_retest": allocation},
            workers=2,
            max_candidates=4,
        ).run()

        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["selected_count"], 1)
        self.assertFalse(result["selection_used_holdout"])
        self.assertFalse(result["automatic_execution_promotion"])
        self.assertFalse(result["execution_eligible"])
        selected = tuple(
            item
            for item in result["candidates"]
            if item["selected_for_holdout"]
        )
        self.assertEqual(len(selected), 1)
        self.assertIsNotNone(selected[0]["holdout_metrics"])
        self.assertTrue(
            all(not item["execution_eligible"] for item in result["candidates"])
        )
