from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from multitrade.domain import ZERO


def _decimal(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def _supported_weak_bucket(
    attribution: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a repeatable weak segment, never a one-trade anecdote."""

    total = int(attribution.get("overall", {}).get("trade_count", 0))
    minimum = max(5, (total + 9) // 10)
    candidates: list[dict[str, Any]] = []
    for dimension, key in (
        ("market_regime", "by_regime"),
        ("entry_hour_new_york", "by_entry_hour_new_york"),
        ("symbol", "by_symbol"),
    ):
        for bucket in attribution.get(key, []):
            if (
                int(bucket.get("trade_count", 0)) >= minimum
                and _decimal(bucket.get("net_profit")) < ZERO
            ):
                candidates.append({"dimension": dimension, **bucket})
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: (
            _decimal(row.get("net_profit")),
            -int(row.get("trade_count", 0)),
            str(row.get("dimension")),
            str(row.get("bucket")),
        ),
    )


def build_research_decision(
    *,
    candidate_id: str,
    family_id: str,
    classification: str,
    failed_gates: Iterable[str],
    research_score: int,
    attribution: dict[str, Any],
    family_rank: int,
    family_candidate_count: int,
) -> dict[str, Any]:
    """Translate fixed-candidate evidence into a non-executable next step."""

    overall = attribution.get("overall", {})
    trades = int(overall.get("trade_count", 0))
    gross = _decimal(overall.get("gross_before_costs"))
    costs = _decimal(overall.get("transaction_costs"))
    net = _decimal(overall.get("net_profit"))
    profit_factor = overall.get("profit_factor")
    weak_bucket = _supported_weak_bucket(attribution)

    if trades == 0:
        action = "expand_evidence_before_redesign"
        rationale = (
            "No out-of-sample trades exist, so profitability and failure "
            "mechanism cannot be estimated."
        )
        hypothesis = (
            "Increase eligible history or universe coverage without changing "
            "the frozen signal definition."
        )
    elif gross <= ZERO:
        action = "redesign_or_retire_mechanism"
        rationale = (
            "The candidate loses before modeled trading costs; cheaper "
            "execution alone cannot repair the observed signal."
        )
        hypothesis = (
            "A materially different, preregistered signal mechanism is "
            "required; threshold tuning of this sample is not justified."
        )
    elif net <= ZERO:
        action = "test_cost_and_turnover_reduction"
        rationale = (
            "The raw signal is positive, but modeled costs erase the edge."
        )
        hypothesis = (
            "A preregistered lower-turnover or more selective V2 may preserve "
            "gross expectancy after adverse costs."
        )
    elif profit_factor is None or _decimal(profit_factor) < Decimal("1.10"):
        action = "preregister_selectivity_v2"
        rationale = (
            "Net profit is positive, but the loss distribution leaves the "
            "profit factor below the research gate."
        )
        hypothesis = (
            "A single predeclared selectivity rule may remove a repeatable "
            "loss segment while retaining enough independent trades."
        )
    elif classification in {
        "prospective_observation_candidate",
        "family_review_candidate",
    }:
        action = "reserve_untouched_holdout"
        rationale = (
            "Historical gates passed; further tuning would contaminate the "
            "evidence now needed for confirmation."
        )
        hypothesis = (
            "The frozen candidate should retain positive expectancy on an "
            "untouched holdout and later prospective Paper observations."
        )
    else:
        action = "continue_bounded_research"
        rationale = (
            "Some economic evidence is positive, but one or more robustness "
            "gates still fail."
        )
        hypothesis = (
            "One preregistered change tied to the diagnosed failure may "
            "improve robustness on untouched data."
        )

    if weak_bucket is not None and action in {
        "preregister_selectivity_v2",
        "continue_bounded_research",
        "test_cost_and_turnover_reduction",
    }:
        hypothesis += (
            f" The development sample identifies {weak_bucket['dimension']} "
            f"'{weak_bucket['bucket']}' as a supported loss segment; this is "
            "a hypothesis for a new candidate, not permission to delete "
            "trades retrospectively."
        )

    return {
        "candidate_id": candidate_id,
        "family_id": family_id,
        "family_rank": family_rank,
        "family_candidate_count": family_candidate_count,
        "research_score": research_score,
        "recommended_action": action,
        "rationale": rationale,
        "preregistered_hypothesis": hypothesis,
        "supported_weak_bucket": weak_bucket,
        "failed_gates": tuple(failed_gates),
        "development_data_status": "inspected_not_holdout",
        "required_next_evidence": (
            "new_untouched_chronological_holdout",
            "adverse_cost_retest",
            "cross_symbol_breadth",
            "prospective_paper_observation_after_historical_pass",
        ),
        "automatic_parameter_change": False,
        "execution_eligible": False,
    }
