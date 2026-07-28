from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.research_validation import (
    PortfolioCorrelationAnalyzer,
    ResearchBacktestConfig,
    ResearchModelBacktester,
)


def daily_rows(
    symbol: str,
    opens: list[Decimal],
    *,
    start: datetime | None = None,
) -> tuple[MarketBar, ...]:
    first = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    return tuple(
        MarketBar(
            symbol=symbol,
            asset_class=AssetClass.STOCK,
            timeframe="1Day",
            timestamp=first + timedelta(days=index),
            open=value,
            high=value * Decimal("1.01"),
            low=value * Decimal("0.99"),
            close=value,
            volume=Decimal("1000000"),
            trade_count=1000,
            vwap=value,
            feed="iex",
        )
        for index, value in enumerate(opens)
    )


class RecordingModel:
    model_id = "recording_model"
    version = "1.0.0"
    minimum_bars = 3

    def __init__(self, exposures: tuple[Decimal, ...]) -> None:
        self.exposures = exposures
        self.calls: list[tuple[str, int]] = []

    def evaluate(self, **kwargs):
        rows = tuple(kwargs["bars"])
        call_index = len(self.calls)
        self.calls.append((rows[-1].timestamp.isoformat(), len(rows)))
        exposure = self.exposures[
            min(call_index, len(self.exposures) - 1)
        ]
        return SimpleNamespace(
            state=SimpleNamespace(value="risk_on"),
            score=Decimal("0.75"),
            target_risk_multiplier=exposure,
        )


class ResearchBacktestTests(TestCase):
    def test_decision_uses_prior_bar_and_return_starts_next_open(
        self,
    ) -> None:
        model = RecordingModel((Decimal("1"),))
        symbol = daily_rows(
            "AAPL",
            [
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                Decimal("110"),
                Decimal("110"),
                Decimal("110"),
            ],
        )
        benchmark = daily_rows("SPY", [Decimal("100")] * 7)
        report = ResearchModelBacktester(
            model,
            config=ResearchBacktestConfig(
                one_way_cost_bps=Decimal("0"),
                minimum_observations=20,
            ),
        ).run(symbol_bars=symbol, benchmark_bars=benchmark)

        first = report.points[0]
        self.assertEqual(
            first.decision_timestamp, symbol[2].timestamp.isoformat()
        )
        self.assertEqual(
            first.execution_timestamp, symbol[3].timestamp.isoformat()
        )
        self.assertEqual(
            first.return_end_timestamp, symbol[4].timestamp.isoformat()
        )
        self.assertEqual(first.asset_return, Decimal("0.10"))
        self.assertEqual(first.net_strategy_return, Decimal("0.10"))
        self.assertEqual(model.calls[0][1], 3)
        self.assertFalse(report.execution_eligible)

    def test_turnover_costs_are_charged_and_leverage_is_clamped(
        self,
    ) -> None:
        model = RecordingModel(
            (
                Decimal("1.50"),
                Decimal("0"),
                Decimal("1"),
            )
        )
        rows = daily_rows("SPY", [Decimal("100")] * 7)
        report = ResearchModelBacktester(
            model,
            config=ResearchBacktestConfig(
                one_way_cost_bps=Decimal("10"),
                minimum_observations=20,
            ),
        ).run(symbol_bars=rows, benchmark_bars=rows)

        self.assertEqual(
            report.points[0].target_exposure, Decimal("1")
        )
        self.assertEqual(
            report.points[0].cost_return, Decimal("0.001")
        )
        self.assertGreater(
            report.metrics.estimated_cost_amount, Decimal("0")
        )
        self.assertLess(
            report.metrics.ending_equity,
            report.metrics.starting_equity,
        )
        self.assertGreater(report.metrics.exposure_changes, 1)
        self.assertEqual(report.promotion_status, "research_only")

    def test_benchmark_is_measured_independently(self) -> None:
        model = RecordingModel((Decimal("0"),))
        symbol = daily_rows("AAPL", [Decimal("100")] * 7)
        benchmark = daily_rows(
            "SPY",
            [
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                Decimal("100"),
                Decimal("102"),
                Decimal("104"),
                Decimal("106"),
            ],
        )
        report = ResearchModelBacktester(
            model,
            config=ResearchBacktestConfig(
                one_way_cost_bps=Decimal("0"),
                minimum_observations=20,
            ),
        ).run(symbol_bars=symbol, benchmark_bars=benchmark)

        self.assertEqual(report.metrics.total_return, Decimal("0"))
        self.assertGreater(
            report.metrics.benchmark_total_return, Decimal("0")
        )
        self.assertLess(
            report.metrics.excess_total_return, Decimal("0")
        )


class PortfolioCorrelationTests(TestCase):
    def test_identical_series_is_reported_as_concentrated(self) -> None:
        values = [
            Decimal("100") + Decimal(index % 7) + Decimal(index)
            for index in range(70)
        ]
        report = PortfolioCorrelationAnalyzer(
            lookback_days=60
        ).analyze(
            account_id="alpaca-paper",
            bars_by_symbol={
                "AAA": daily_rows("AAA", values),
                "BBB": daily_rows("BBB", values),
                "CCC": daily_rows("CCC", values),
            },
            evaluated_at=datetime(
                2026, 7, 29, tzinfo=timezone.utc
            ),
        )

        self.assertEqual(report.state, "concentrated")
        self.assertEqual(
            report.maximum_positive_correlation, Decimal("1")
        )
        self.assertEqual(report.effective_breadth, Decimal("1"))
        self.assertEqual(len(report.high_correlation_pairs), 3)
        self.assertFalse(report.execution_eligible)

    def test_missing_history_fails_to_insufficient_data(self) -> None:
        report = PortfolioCorrelationAnalyzer(
            lookback_days=20
        ).analyze(
            account_id="alpaca-paper",
            bars_by_symbol={
                "AAA": daily_rows("AAA", [Decimal("100")] * 10),
                "BBB": daily_rows("BBB", [Decimal("100")] * 10),
            },
        )
        self.assertEqual(report.state, "insufficient_data")
        self.assertEqual(report.symbols_included, ())
