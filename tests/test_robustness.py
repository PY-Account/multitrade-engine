from decimal import Decimal
from unittest import TestCase

from multitrade.robustness import TradeSequenceStressTester


class TradeSequenceStressTests(TestCase):
    def test_bootstrap_is_deterministic_and_non_executable(self) -> None:
        tester = TradeSequenceStressTester()
        sample = (
            Decimal("1"),
            Decimal("0.5"),
            Decimal("-0.4"),
        ) * 10

        first = tester.evaluate(
            sample,
            risk_fraction=Decimal("0.005"),
            seed_material="fixed-strategy-evidence",
            paths=200,
        )
        second = tester.evaluate(
            sample,
            risk_fraction=Decimal("0.005"),
            seed_material="fixed-strategy-evidence",
            paths=200,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.sample_trade_count, 30)
        self.assertEqual(first.simulated_paths, 200)
        self.assertFalse(first.execution_eligible)

    def test_small_or_adverse_samples_fail_closed(self) -> None:
        tester = TradeSequenceStressTester()
        small = tester.evaluate(
            (Decimal("1"),) * 5,
            risk_fraction=Decimal("0.005"),
            seed_material="small",
            paths=100,
        )
        adverse = tester.evaluate(
            (Decimal("-1"),) * 30,
            risk_fraction=Decimal("0.005"),
            seed_material="adverse",
            paths=100,
        )

        self.assertFalse(small.gates["minimum_trade_sample"])
        self.assertFalse(small.passed)
        self.assertGreater(
            adverse.ninety_fifth_percentile_drawdown,
            adverse.drawdown_limit,
        )
        self.assertFalse(adverse.passed)
