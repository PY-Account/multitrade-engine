from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase

from multitrade.backtest import BacktestConfig, Backtester
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.strategies.base import (
    SignalAction,
    StrategyContext,
    create_signal,
)


@dataclass(frozen=True, slots=True)
class OneSignalStrategy:
    strategy_id: str = "one_signal"
    version: str = "1.0.0"

    def evaluate(self, context: StrategyContext):
        if len(context.bars) != 31:
            return None
        latest = context.bars[-1]
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=Decimal("0.8"),
            reference_price=latest.close,
            stop_price=Decimal("98"),
            target_price=Decimal("104"),
            reason_codes=("test_setup",),
            evidence={"closed_bar": latest.timestamp},
        )


def bars(*, ambiguous_exit: bool = False) -> tuple[MarketBar, ...]:
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    result = []
    for index in range(40):
        high = Decimal("101")
        low = Decimal("99")
        if index == 32:
            high = Decimal("105")
            low = Decimal("97") if ambiguous_exit else Decimal("100")
        result.append(
            MarketBar(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                timeframe="5Min",
                timestamp=start + timedelta(minutes=5 * index),
                open=Decimal("100"),
                high=high,
                low=low,
                close=Decimal("100"),
                volume=Decimal("1000"),
                trade_count=100,
                vwap=Decimal("100"),
                feed="iex",
            )
        )
    return tuple(result)


class BacktestTests(TestCase):
    def test_signal_enters_on_next_bar_and_takes_profit(self) -> None:
        result = Backtester(
            OneSignalStrategy(),
            config=BacktestConfig(slippage_bps=Decimal("0")),
        ).run(bars())

        self.assertEqual(result.metrics.trade_count, 1)
        self.assertEqual(result.trades[0].entry_timestamp, bars()[31].timestamp.isoformat())
        self.assertEqual(result.trades[0].exit_reason, "take_profit")
        self.assertGreater(result.trades[0].pnl, Decimal("0"))

    def test_ambiguous_bar_resolves_to_stop_conservatively(self) -> None:
        result = Backtester(
            OneSignalStrategy(),
            config=BacktestConfig(slippage_bps=Decimal("0")),
        ).run(bars(ambiguous_exit=True))

        self.assertEqual(
            result.trades[0].exit_reason,
            "stop_before_target_same_bar",
        )
        self.assertLess(result.trades[0].pnl, Decimal("0"))
