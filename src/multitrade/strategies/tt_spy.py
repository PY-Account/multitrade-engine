"""TT-inspired Paper candidate; published ROC is not an engine backtest."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from multitrade.strategies.base import (
    SignalAction, StrategyContext, StrategySignal, create_signal,
)


@dataclass(frozen=True, slots=True)
class TtSpyPutCreditStrategy:
    strategy_id: str = "tt_spy_put_credit"
    version: str = "1.0.0"

    def evaluate(self, context: StrategyContext) -> StrategySignal | None:
        if not context.bars or context.bars[-1].symbol != "SPY":
            return None
        # Fail closed without DST-aware market time; never substitute fixed EST.
        try:
            local = context.evaluated_at.astimezone(ZoneInfo("America/New_York"))
        except ZoneInfoNotFoundError:
            return None
        if local.weekday() != 0 or not time(10) <= local.time() < time(15):
            return None
        reference = context.bars[-1].close
        if reference <= Decimal("0"):
            return None
        # Required signal-envelope prices are not stock orders or option stops.
        return create_signal(
            context=context, strategy_id=self.strategy_id, version=self.version,
            action=SignalAction.ENTER_LONG, confidence=Decimal("0.70"),
            reference_price=reference, stop_price=reference * Decimal("0.99"),
            target_price=reference * Decimal("1.01"),
            reason_codes=("tt_spy_defined_risk_candidate", "monday_entry_window"),
            evidence={
                "execution_vehicle": "bull_put_credit_spread",
                "execution_underlying": "SPY",
                "option_only": True,
                "entry_schedule": "Monday 10:00-15:00 America/New_York",
                "source_url": "https://www.tastylive.com/news-insights/How-to-Use-Static-and-Dynamic-Spreads-to-Trade-SPY-Options",
                "published_annualized_roc_is_not_account_return": True,
                "internal_return_validation": "not_performed",
            },
        )
