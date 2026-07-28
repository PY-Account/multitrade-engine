from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from multitrade.domain import ZERO


@dataclass(frozen=True, slots=True)
class TradeSequenceStressReport:
    sample_trade_count: int
    simulated_paths: int
    risk_fraction: Decimal
    drawdown_limit: Decimal
    fifth_percentile_return: Decimal
    median_return: Decimal
    ninety_fifth_percentile_drawdown: Decimal
    drawdown_limit_probability: Decimal
    gates: dict[str, bool]
    passed: bool
    seed_fingerprint: str
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if self.execution_eligible:
            raise ValueError(
                "Trade-sequence stress cannot authorize execution"
            )


def _quantile(
    ordered: tuple[Decimal, ...], fraction: Decimal
) -> Decimal:
    if not ordered:
        return ZERO
    index = int(
        (Decimal(len(ordered) - 1) * fraction).to_integral_value()
    )
    return ordered[max(0, min(index, len(ordered) - 1))]


class TradeSequenceStressTester:
    """Deterministic bootstrap of observed out-of-sample R-multiples."""

    def evaluate(
        self,
        r_multiples: Iterable[Decimal],
        *,
        risk_fraction: Decimal,
        seed_material: str,
        paths: int = 500,
        drawdown_limit: Decimal = Decimal("0.10"),
        minimum_sample: int = 20,
    ) -> TradeSequenceStressReport:
        sample = tuple(Decimal(value) for value in r_multiples)
        if not ZERO < risk_fraction <= Decimal("0.03"):
            raise ValueError("Stress risk_fraction must be in (0, 0.03]")
        if not 100 <= paths <= 5000:
            raise ValueError("Stress paths must be 100-5000")
        if not ZERO < drawdown_limit <= Decimal("0.50"):
            raise ValueError("Stress drawdown limit must be in (0, 0.50]")
        if minimum_sample < 5:
            raise ValueError("Stress minimum sample must be at least 5")

        seed_bytes = hashlib.sha256(
            seed_material.encode("utf-8")
        ).digest()
        seed_fingerprint = seed_bytes.hex()[:16]
        rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
        returns: list[Decimal] = []
        drawdowns: list[Decimal] = []
        if sample:
            for _ in range(paths):
                equity = Decimal("1")
                peak = equity
                maximum_drawdown = ZERO
                for _ in range(len(sample)):
                    r_multiple = sample[rng.randrange(len(sample))]
                    equity *= max(
                        ZERO,
                        Decimal("1") + risk_fraction * r_multiple,
                    )
                    peak = max(peak, equity)
                    if peak > ZERO:
                        maximum_drawdown = max(
                            maximum_drawdown,
                            (peak - equity) / peak,
                        )
                returns.append(equity - Decimal("1"))
                drawdowns.append(maximum_drawdown)
        ordered_returns = tuple(sorted(returns))
        ordered_drawdowns = tuple(sorted(drawdowns))
        fifth_return = _quantile(
            ordered_returns, Decimal("0.05")
        )
        median_return = _quantile(
            ordered_returns, Decimal("0.50")
        )
        tail_drawdown = _quantile(
            ordered_drawdowns, Decimal("0.95")
        )
        drawdown_probability = (
            Decimal(
                sum(
                    value >= drawdown_limit
                    for value in ordered_drawdowns
                )
            )
            / Decimal(len(ordered_drawdowns))
            if ordered_drawdowns
            else Decimal("1")
        )
        gates = {
            "minimum_trade_sample": len(sample) >= minimum_sample,
            "fifth_percentile_loss_within_limit": (
                fifth_return >= -drawdown_limit
            ),
            "tail_drawdown_within_limit": (
                tail_drawdown <= drawdown_limit
            ),
            "drawdown_limit_probability": (
                drawdown_probability <= Decimal("0.10")
            ),
        }
        return TradeSequenceStressReport(
            sample_trade_count=len(sample),
            simulated_paths=paths,
            risk_fraction=risk_fraction,
            drawdown_limit=drawdown_limit,
            fifth_percentile_return=fifth_return,
            median_return=median_return,
            ninety_fifth_percentile_drawdown=tail_drawdown,
            drawdown_limit_probability=drawdown_probability,
            gates=gates,
            passed=all(gates.values()),
            seed_fingerprint=seed_fingerprint,
            execution_eligible=False,
        )
