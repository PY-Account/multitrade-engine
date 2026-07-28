from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.backtest import BacktestConfig, StrategyValidator
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan, StrategyAllocation
from multitrade.strategies.base import (
    SignalAction,
    StrategyContext,
    create_signal,
)
from multitrade.strategy_lab import (
    ContinuousStrategyLabService,
    StrategyLabConfig,
    StrategyLabEvaluator,
)
from multitrade.universe import (
    AssetUniverseProgram,
    StrategyUniverseAssignment,
)


@dataclass(frozen=True, slots=True)
class FrequentTestStrategy:
    strategy_id: str = "frequent_test"
    version: str = "1.0.0"

    def evaluate(self, context: StrategyContext):
        if context.bars[-1].timestamp.minute % 15:
            return None
        latest = context.bars[-1]
        return create_signal(
            context=context,
            strategy_id=self.strategy_id,
            version=self.version,
            action=SignalAction.ENTER_LONG,
            confidence=Decimal("0.80"),
            reference_price=latest.close,
            stop_price=Decimal("98"),
            target_price=Decimal("101"),
            reason_codes=("test_pattern",),
            evidence={"bar": latest.timestamp},
        )


def intraday_bars(
    symbol: str, count: int = 420
) -> tuple[MarketBar, ...]:
    start = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
    timestamps = []
    session_start = start
    while len(timestamps) < count:
        if session_start.weekday() < 5:
            timestamps.extend(
                session_start + timedelta(minutes=5 * index)
                for index in range(78)
            )
        session_start += timedelta(days=1)
    return tuple(
        MarketBar(
            symbol=symbol,
            asset_class=AssetClass.STOCK,
            timeframe="5Min",
            timestamp=timestamp,
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99.5"),
            close=Decimal("100"),
            volume=Decimal("100000"),
            trade_count=500,
            vwap=Decimal("100"),
            feed="iex",
        )
        for timestamp in timestamps[:count]
    )


def account_plan(
    *,
    paper_execution_allowed: bool = False,
) -> AccountPlan:
    return AccountPlan(
        account_id="alpaca-paper",
        broker="alpaca",
        environment="paper",
        enabled=True,
        asset_classes=(AssetClass.STOCK,),
        watchlist=("SPY", "QQQ"),
        timeframe="5Min",
        maximum_positions=2,
        maximum_daily_orders=6,
        symbol_cooldown_minutes=60,
        allocations={
            "frequent_test": StrategyAllocation(
                strategy_id="frequent_test",
                enabled=True,
                capital_weight=Decimal("0.20"),
                risk_fraction=Decimal("0.005"),
                minimum_confidence=Decimal("0.60"),
                paper_execution_allowed=paper_execution_allowed,
            )
        },
    )


class FakeMarketData:
    def __init__(self) -> None:
        self.request_ids = ["strategy-lab-request"]
        self.adjustment = None

    def fetch_stock_bars(
        self,
        symbols,
        timeframe,
        start,
        end,
        *,
        adjustment,
    ):
        del timeframe, start, end
        self.adjustment = adjustment
        return {
            symbol: intraday_bars(symbol)
            for symbol in symbols
        }


class StrategyLabTests(TestCase):
    def test_chronological_folds_are_non_overlapping(self) -> None:
        report = StrategyValidator(
            FrequentTestStrategy(),
            config=BacktestConfig(
                slippage_bps=Decimal("0"),
                risk_fraction=Decimal("0.005"),
                capital_weight=Decimal("0.20"),
            ),
        ).chronological_stability(
            intraday_bars("SPY"),
            folds=3,
        )

        self.assertEqual(report.folds_completed, 3)
        self.assertEqual(
            report.total_trade_count,
            len(report.trade_r_multiples),
        )
        for previous, current in zip(
            report.folds, report.folds[1:]
        ):
            self.assertLess(previous.test_end, current.test_start)

    def test_cross_symbol_and_stressed_costs_are_required(self) -> None:
        report = StrategyLabEvaluator(
            config=StrategyLabConfig(
                base_slippage_bps=Decimal("0"),
                stressed_slippage_bps=Decimal("10"),
                minimum_out_of_sample_trades=20,
            )
        ).evaluate(
            account_plan=account_plan(
                paper_execution_allowed=True
            ),
            strategy=FrequentTestStrategy(),
            bars_by_symbol={
                "SPY": intraday_bars("SPY"),
                "QQQ": intraday_bars("QQQ"),
            },
        )

        self.assertEqual(report.symbols_covered, ("SPY", "QQQ"))
        self.assertGreater(
            report.aggregate_metrics["out_of_sample_trade_count"],
            20,
        )
        self.assertTrue(
            report.gates["positive_after_stressed_costs"]
        )
        self.assertEqual(
            report.aggregate_metrics["chronological_fold_count"],
            6,
        )
        self.assertEqual(
            report.aggregate_metrics[
                "trade_sequence_stress"
            ]["simulated_paths"],
            500,
        )
        self.assertIn(
            "chronological_fold_coverage",
            report.gates,
        )
        self.assertFalse(report.execution_eligible)
        self.assertIn(
            "configuration_permission_does_not_override_lab_readiness",
            report.warnings,
        )

    def test_missing_symbol_history_fails_closed(self) -> None:
        report = StrategyLabEvaluator().evaluate(
            account_plan=account_plan(),
            strategy=FrequentTestStrategy(),
            bars_by_symbol={"SPY": intraday_bars("SPY")},
        )

        self.assertEqual(report.readiness_status, "insufficient_evidence")
        self.assertEqual(report.missing_symbols, ("QQQ",))
        self.assertFalse(report.gates["minimum_symbol_coverage"])
        self.assertFalse(report.execution_eligible)

    def test_continuous_cycle_persists_read_only_report(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            health_path = Path(directory) / "strategy-lab-health.json"
            market_data = FakeMarketData()
            service = ContinuousStrategyLabService(
                account_plan=account_plan(),
                strategies={"frequent_test": FrequentTestStrategy()},
                market_data=market_data,
                store=SqliteAuditStore(db_path),
                health_path=str(health_path),
                config=StrategyLabConfig(
                    base_slippage_bps=Decimal("0"),
                    stressed_slippage_bps=Decimal("10"),
                    minimum_out_of_sample_trades=20,
                ),
            )

            result = service.run_cycle(
                now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            )
            reports = SqliteAuditReader(
                db_path
            ).recent_strategy_lab_reports()

            self.assertEqual(result.reports_completed, 1)
            self.assertEqual(market_data.adjustment, "raw")
            self.assertEqual(len(reports), 1)
            self.assertFalse(reports[0]["execution_eligible"])
            self.assertEqual(
                len(
                    reports[0]["symbol_results"][0][
                        "chronological_folds"
                    ]
                ),
                3,
            )
            self.assertTrue(health_path.is_file())

    def test_continuous_cycle_uses_strategy_specific_research_symbols(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            service = ContinuousStrategyLabService(
                account_plan=account_plan(),
                strategies={
                    "frequent_test": FrequentTestStrategy()
                },
                market_data=FakeMarketData(),
                store=SqliteAuditStore(db_path),
                health_path=str(
                    Path(directory) / "strategy-lab-health.json"
                ),
                universe_program=AssetUniverseProgram(
                    policies={},
                    strategy_assignments={
                        "frequent_test": StrategyUniverseAssignment(
                            strategy_id="frequent_test",
                            selection_mode="manual",
                            policy_id=None,
                            manual_symbols=("AAPL", "AMD"),
                            maximum_symbols=10,
                        )
                    },
                    index_snapshots={},
                    asset_references={},
                ),
            )

            service.run_cycle(
                now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            )
            report = SqliteAuditReader(
                db_path
            ).recent_strategy_lab_reports()[0]

            self.assertEqual(
                report["symbols_requested"], ["AAPL", "AMD"]
            )
