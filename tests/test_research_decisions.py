from decimal import Decimal
from unittest import TestCase

from multitrade.research_decisions import build_research_decision


class ResearchDecisionTests(TestCase):
    def decision(self, attribution):
        return build_research_decision(
            candidate_id="candidate",
            family_id="family",
            classification="continue_research",
            failed_gates=("pooled_profit_factor",),
            research_score=61,
            attribution=attribution,
            family_rank=1,
            family_candidate_count=3,
        )

    def test_negative_gross_requires_mechanism_redesign(self):
        result = self.decision(
            {
                "overall": {
                    "trade_count": 20,
                    "gross_before_costs": Decimal("-10"),
                    "transaction_costs": Decimal("2"),
                    "net_profit": Decimal("-12"),
                    "profit_factor": Decimal("0.8"),
                }
            }
        )

        self.assertEqual(
            result["recommended_action"],
            "redesign_or_retire_mechanism",
        )
        self.assertFalse(result["automatic_parameter_change"])
        self.assertFalse(result["execution_eligible"])

    def test_positive_gross_erased_by_costs_targets_turnover(self):
        result = self.decision(
            {
                "overall": {
                    "trade_count": 20,
                    "gross_before_costs": Decimal("8"),
                    "transaction_costs": Decimal("12"),
                    "net_profit": Decimal("-4"),
                    "profit_factor": Decimal("0.9"),
                }
            }
        )

        self.assertEqual(
            result["recommended_action"],
            "test_cost_and_turnover_reduction",
        )

    def test_weak_bucket_requires_repeatable_sample(self):
        result = self.decision(
            {
                "overall": {
                    "trade_count": 40,
                    "gross_before_costs": Decimal("10"),
                    "transaction_costs": Decimal("2"),
                    "net_profit": Decimal("8"),
                    "profit_factor": Decimal("1.05"),
                },
                "by_regime": [
                    {
                        "bucket": "range",
                        "trade_count": 8,
                        "net_profit": Decimal("-6"),
                    }
                ],
                "by_entry_hour_new_york": [
                    {
                        "bucket": "15",
                        "trade_count": 2,
                        "net_profit": Decimal("-20"),
                    }
                ],
            }
        )

        self.assertEqual(
            result["recommended_action"],
            "preregister_selectivity_v2",
        )
        self.assertEqual(
            result["supported_weak_bucket"]["dimension"],
            "market_regime",
        )
        self.assertNotEqual(
            result["supported_weak_bucket"]["bucket"], "15"
        )
