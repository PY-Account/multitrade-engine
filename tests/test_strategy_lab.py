import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.accelerated_validation import (
    AcceleratedValidationService,
)
from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.backtest import BacktestConfig, StrategyValidator
from multitrade.domain import AssetClass
from multitrade.experiments import (
    StrategyExperiment,
    StrategyExperimentProgram,
)
from multitrade.market import MarketBar
from multitrade.portfolio import (
    AccountPlan,
    StrategyAllocation,
    load_account_plans,
)
from multitrade.options import OptionExecutionPolicy, OptionStructure
from multitrade.strategies import default_equity_strategies
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
from multitrade.trials import strategy_parameters
from multitrade.universe import (
    AssetUniverseProgram,
    StrategyUniverseAssignment,
)


@dataclass(frozen=True, slots=True)
class FrequentTestStrategy:
    strategy_id: str = "frequent_test"
    version: str = "1.0.0"
    interval_minutes: int = 15

    def evaluate(self, context: StrategyContext):
        if (
            context.bars[-1].timestamp.minute
            % self.interval_minutes
        ):
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


def experiment_program(
    *,
    with_comparison: bool = False,
) -> StrategyExperimentProgram:
    experiment = StrategyExperiment(
        experiment_id="frequent_test_baseline_2026q3",
        family_id="test_continuation",
        strategy_id="frequent_test",
        strategy_version="1.0.0",
        variant_id="baseline_v1",
        registered_at=datetime(
            2026, 7, 27, tzinfo=timezone.utc
        ),
        prospective_observation_start=datetime(
            2026, 7, 29, tzinfo=timezone.utc
        ),
        review_not_before=datetime(
            2026, 8, 19, tzinfo=timezone.utc
        ),
        status="frozen_research",
        hypothesis="The test continuation has positive expectancy.",
        mechanism="The synthetic pattern repeats every 15 minutes.",
        primary_metric="chronological_median_fold_return",
        minimum_prospective_days=1,
        minimum_prospective_trials=1,
        final_holdout_status="not_reserved",
        expected_parameters=strategy_parameters(
            FrequentTestStrategy()
        ),
    )
    comparisons = {}
    if with_comparison:
        for label, interval in (("fast", 10), ("slow", 20)):
            comparison = replace(
                experiment,
                experiment_id=(
                    f"frequent_test_{label}_2026q3"
                ),
                variant_id=f"{label}_v1",
                expected_parameters=strategy_parameters(
                    FrequentTestStrategy(
                        interval_minutes=interval
                    )
                ),
            )
            comparisons[comparison.experiment_id] = comparison
    return StrategyExperimentProgram(
        {"frequent_test": experiment},
        comparisons,
    )


class FakeMarketData:
    def __init__(self) -> None:
        self.request_ids = ["strategy-lab-request"]
        self.adjustment = None
        self.fetch_calls = []

    def fetch_stock_bars(
        self,
        symbols,
        timeframe,
        start,
        end,
        *,
        adjustment,
    ):
        del start, end
        self.adjustment = adjustment
        self.fetch_calls.append((tuple(symbols), timeframe))
        return {
            symbol: tuple(
                replace(bar, timeframe=timeframe)
                for bar in intraday_bars(symbol)
            )
            for symbol in symbols
        }


class ChunkLimitedMarketData(FakeMarketData):
    def fetch_stock_bars(
        self,
        symbols,
        timeframe,
        start,
        end,
        *,
        adjustment,
    ):
        if len(symbols) > 50:
            raise AssertionError("Strategy Lab did not chunk symbol fetches")
        return super().fetch_stock_bars(
            symbols,
            timeframe,
            start,
            end,
            adjustment=adjustment,
        )


class StrategyLabTests(TestCase):
    def test_long_horizon_research_lookback_is_supported(self) -> None:
        self.assertEqual(
            StrategyLabConfig(lookback_days=1825).lookback_days,
            1825,
        )

    def test_execution_vehicles_collapse_to_unique_source_models(
        self,
    ) -> None:
        plan = load_account_plans(
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )[0]
        with TemporaryDirectory() as directory:
            service = ContinuousStrategyLabService(
                account_plan=plan,
                strategies=default_equity_strategies(),
                market_data=FakeMarketData(),
                store=SqliteAuditStore(
                    Path(directory) / "trading.db"
                ),
                health_path=str(
                    Path(directory) / "strategy-lab-health.json"
                ),
            )

        self.assertEqual(
            set(service.strategy_allocations),
            set(default_equity_strategies()),
        )
        self.assertEqual(
            service.strategy_allocations[
                "breakout_retest"
            ].strategy_id,
            "breakout_retest",
        )

    def test_market_data_requests_are_chunked_for_large_universe(
        self,
    ) -> None:
        source_plan = load_account_plans(
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )[0]
        plan = replace(
            source_plan,
            watchlist=tuple(f"SYM{index:03d}" for index in range(181)),
            allocations={
                "breakout_retest": replace(
                    source_plan.allocations["breakout_retest"],
                    symbols=tuple(
                        f"SYM{index:03d}" for index in range(181)
                    ),
                    timeframe="4Hour",
                )
            },
        )
        market_data = ChunkLimitedMarketData()
        with TemporaryDirectory() as directory:
            service = ContinuousStrategyLabService(
                account_plan=plan,
                strategies=default_equity_strategies(),
                market_data=market_data,
                store=SqliteAuditStore(
                    Path(directory) / "trading.db"
                ),
                health_path=str(
                    Path(directory) / "strategy-lab-health.json"
                ),
            )
            service._market_data_chunk_pause_seconds = 0
            service.run_cycle(
                now=intraday_bars("SPY")[-1].timestamp
            )

        self.assertEqual(len(market_data.fetch_calls), 4)
        self.assertLessEqual(
            max(len(symbols) for symbols, _ in market_data.fetch_calls),
            50,
        )

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
        diagnostics = report.aggregate_metrics[
            "diagnostic_attribution"
        ]
        self.assertEqual(
            diagnostics["overall"]["trade_count"],
            report.aggregate_metrics[
                "out_of_sample_trade_count"
            ],
        )
        self.assertEqual(len(diagnostics["by_symbol"]), 2)
        self.assertIn(
            diagnostics["primary_diagnosis"],
            {
                "negative_before_modeled_costs",
                "gross_edge_erased_by_modeled_costs",
                "positive_net_but_insufficient_profit_factor",
                "positive_diagnostic_sample_requires_validation",
            },
        )
        self.assertFalse(diagnostics["execution_eligible"])
        self.assertIn(
            "chronological_fold_coverage",
            report.gates,
        )

    def test_option_allocation_research_stress_is_capped_to_stock_limit(
        self,
    ) -> None:
        allocation = StrategyAllocation(
            strategy_id="frequent_test_bull_put",
            enabled=True,
            capital_weight=Decimal("0.10"),
            risk_fraction=Decimal("0.10"),
            minimum_confidence=Decimal("0.60"),
            paper_execution_allowed=True,
            asset_class=AssetClass.OPTION,
            option_policy=OptionExecutionPolicy(
                structure=OptionStructure.BULL_PUT_CREDIT,
                source_strategy_id="frequent_test",
            ),
        )
        plan = AccountPlan(
            account_id="alpaca-paper",
            broker="alpaca",
            environment="paper",
            enabled=True,
            asset_classes=(AssetClass.OPTION,),
            watchlist=("SPY", "QQQ"),
            timeframe="5Min",
            maximum_positions=2,
            maximum_daily_orders=6,
            symbol_cooldown_minutes=60,
            allocations={allocation.strategy_id: allocation},
        )

        report = StrategyLabEvaluator(
            config=StrategyLabConfig(
                minimum_covered_symbols=1,
                minimum_out_of_sample_trades=1,
                trade_sequence_paths=100,
            )
        ).evaluate(
            account_plan=plan,
            strategy=FrequentTestStrategy(),
            allocation=allocation,
            symbols=("SPY",),
            bars_by_symbol={"SPY": intraday_bars("SPY", 420)},
            experiment_binding=None,
            evaluated_at=datetime(
                2026, 1, 10, 12, 0, tzinfo=timezone.utc
            ),
        )

        self.assertEqual(
            report.aggregate_metrics["trade_sequence_stress"][
                "risk_fraction"
            ],
            Decimal("0.03"),
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
            self.assertEqual(result.trials_registered, 1)
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
            trials = SqliteAuditReader(
                db_path
            ).recent_strategy_model_trials()
            self.assertEqual(len(trials), 1)
            self.assertTrue(trials[0]["integrity_valid"])
            self.assertFalse(trials[0]["execution_eligible"])
            self.assertEqual(
                len(trials[0]["candidate_fingerprint"]),
                64,
            )
            self.assertTrue(health_path.is_file())

    def test_trial_chain_is_append_only_and_tamper_evident(
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
                config=StrategyLabConfig(
                    base_slippage_bps=Decimal("0"),
                    stressed_slippage_bps=Decimal("10"),
                    minimum_out_of_sample_trades=20,
                ),
            )

            service.run_cycle()
            service.run_cycle()
            trials = SqliteAuditReader(
                db_path
            ).recent_strategy_model_trials()

            self.assertEqual(len(trials), 2)
            latest, previous = trials
            self.assertEqual(
                latest["previous_trial_hash"],
                previous["trial_hash"],
            )
            self.assertEqual(
                latest["candidate_fingerprint"],
                previous["candidate_fingerprint"],
            )
            self.assertEqual(
                latest["configuration_fingerprint"],
                previous["configuration_fingerprint"],
            )
            self.assertEqual(
                latest["dataset_fingerprint"],
                previous["dataset_fingerprint"],
            )
            self.assertTrue(
                all(trial["integrity_valid"] for trial in trials)
            )

            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        UPDATE strategy_model_trials
                        SET readiness_status = 'changed'
                        WHERE trial_id = ?
                        """,
                        (latest["trial_id"],),
                    )
            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        DELETE FROM strategy_model_trials
                        WHERE trial_id = ?
                        """,
                        (latest["trial_id"],),
                    )
            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        UPDATE strategy_lab_reports
                        SET readiness_status = 'changed'
                        WHERE report_id = ?
                        """,
                        (latest["report_id"],),
                    )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "DROP TRIGGER strategy_model_trials_no_update"
                )
                connection.execute(
                    """
                    UPDATE strategy_model_trials
                    SET candidate_definition_json = '{}'
                    WHERE trial_id = ?
                    """,
                    (latest["trial_id"],),
                )
                connection.commit()
            tampered = SqliteAuditReader(
                db_path
            ).recent_strategy_model_trials()
            self.assertFalse(tampered[0]["self_hash_valid"])
            self.assertFalse(tampered[0]["integrity_valid"])

    def test_preregistered_experiment_is_linked_and_immutable(
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
                experiment_program=experiment_program(),
                config=StrategyLabConfig(
                    base_slippage_bps=Decimal("0"),
                    stressed_slippage_bps=Decimal("10"),
                    minimum_out_of_sample_trades=20,
                ),
            )

            service.run_cycle(
                now=datetime(
                    2026, 7, 30, 12, tzinfo=timezone.utc
                )
            )
            reader = SqliteAuditReader(db_path)
            trial = reader.recent_strategy_model_trials()[0]
            summary = reader.strategy_experiment_summaries()[0]
            experiment = trial["configuration"]["experiment"]

            self.assertEqual(
                experiment["experiment_id"],
                "frequent_test_baseline_2026q3",
            )
            self.assertEqual(
                experiment["evidence_phase"],
                "prospective_observation",
            )
            self.assertTrue(experiment["prospective"])
            self.assertEqual(summary["trial_count"], 1)
            self.assertEqual(
                summary["prospective_trial_count"], 1
            )
            self.assertEqual(
                summary["prospective_days_observed"], 1
            )
            self.assertEqual(
                summary["family_candidate_count"], 1
            )
            self.assertEqual(
                summary["distinct_dataset_count"], 1
            )
            self.assertTrue(
                summary["prospective_requirements_met"]
            )
            self.assertTrue(
                summary["manifest_integrity_valid"]
            )
            self.assertFalse(summary["execution_eligible"])

            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        UPDATE strategy_experiment_manifests
                        SET status = 'retired'
                        WHERE experiment_id = ?
                        """,
                        ("frequent_test_baseline_2026q3",),
                    )
            with closing(sqlite3.connect(db_path)) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        DELETE FROM strategy_experiment_manifests
                        WHERE experiment_id = ?
                        """,
                        ("frequent_test_baseline_2026q3",),
                    )

    def test_comparison_variant_uses_same_research_symbols(
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
                experiment_program=experiment_program(
                    with_comparison=True
                ),
                comparison_strategy_factory=lambda parameters: (
                    FrequentTestStrategy(
                        interval_minutes=int(
                            parameters["interval_minutes"]
                        )
                    )
                ),
                universe_program=AssetUniverseProgram(
                    policies={},
                    strategy_assignments={
                        "frequent_test": StrategyUniverseAssignment(
                            strategy_id="frequent_test",
                            selection_mode="manual",
                            policy_id=None,
                            manual_symbols=(
                                "AAPL",
                                "AMD",
                                "NVDA",
                            ),
                            maximum_symbols=10,
                        )
                    },
                    index_snapshots={},
                    asset_references={},
                ),
                config=StrategyLabConfig(
                    base_slippage_bps=Decimal("0"),
                    stressed_slippage_bps=Decimal("10"),
                    minimum_out_of_sample_trades=20,
                    comparison_variants_per_strategy_cycle=1,
                ),
            )

            result = service.run_cycle(
                now=datetime(
                    2026, 7, 30, 12, tzinfo=timezone.utc
                )
            )
            second_result = service.run_cycle(
                now=datetime(
                    2026, 7, 30, 18, tzinfo=timezone.utc
                )
            )
            reader = SqliteAuditReader(db_path)
            reports = reader.recent_strategy_lab_reports()
            trials = reader.recent_strategy_model_trials()
            summaries = {
                item["variant_id"]: item
                for item in (
                    reader.strategy_experiment_summaries()
                )
            }

            self.assertEqual(result.strategies_evaluated, 2)
            self.assertEqual(result.reports_completed, 2)
            self.assertEqual(
                second_result.strategies_evaluated, 2
            )
            self.assertEqual(len(reports), 4)
            trials_by_time = {}
            for trial in trials:
                trials_by_time.setdefault(
                    trial["evaluated_at"], []
                ).append(trial)
            self.assertEqual(len(trials_by_time), 2)
            self.assertTrue(
                all(
                    len(rows) == 2
                    and len(
                        {
                            row["dataset_fingerprint"]
                            for row in rows
                        }
                    )
                    == 1
                    for rows in trials_by_time.values()
                )
            )
            self.assertEqual(
                len(
                    {
                        trial["candidate_fingerprint"]
                        for trial in trials
                    }
                ),
                3,
            )
            comparison_reports = [
                report
                for report in reports
                if report["experiment"].get(
                    "comparison_variant"
                )
            ]
            self.assertEqual(len(comparison_reports), 2)
            self.assertTrue(
                all(
                    report["readiness_status"]
                    != "extended_paper_observation_candidate"
                    for report in comparison_reports
                )
            )
            self.assertTrue(
                all(
                    "comparison_variant_requires_family_review"
                    in report["warnings"]
                    for report in comparison_reports
                )
            )
            self.assertEqual(
                summaries["baseline_v1"][
                    "latest_symbols_requested"
                ],
                ["AAPL", "AMD", "NVDA"],
            )
            self.assertEqual(
                summaries["fast_v1"][
                    "latest_symbols_requested"
                ],
                ["AAPL", "AMD", "NVDA"],
            )
            self.assertEqual(
                summaries["fast_v1"][
                    "family_candidate_count"
                ],
                3,
            )
            self.assertFalse(
                summaries["fast_v1"]["execution_eligible"]
            )
            self.assertEqual(
                summaries["slow_v1"][
                    "latest_symbols_requested"
                ],
                ["AAPL", "AMD", "NVDA"],
            )

    def test_accelerated_cycle_scores_all_candidates_without_trials(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            store = SqliteAuditStore(db_path)
            service = ContinuousStrategyLabService(
                account_plan=account_plan(),
                strategies={
                    "frequent_test": FrequentTestStrategy()
                },
                market_data=FakeMarketData(),
                store=store,
                health_path=str(
                    Path(directory) / "strategy-lab-health.json"
                ),
                experiment_program=experiment_program(
                    with_comparison=True
                ),
                comparison_strategy_factory=lambda parameters: (
                    FrequentTestStrategy(
                        interval_minutes=int(
                            parameters["interval_minutes"]
                        )
                    )
                ),
                config=StrategyLabConfig(
                    base_slippage_bps=Decimal("0"),
                    stressed_slippage_bps=Decimal("10"),
                    minimum_out_of_sample_trades=20,
                    comparison_variants_per_strategy_cycle=4,
                ),
                evaluation_workers=2,
            )

            run = AcceleratedValidationService(
                lab_service=service,
                store=store,
            ).run(
                now=datetime(
                    2026, 7, 30, 12, tzinfo=timezone.utc
                )
            )
            reader = SqliteAuditReader(db_path)
            stored_runs = (
                reader.recent_accelerated_validation_runs()
            )

            self.assertEqual(len(run.scorecards), 3)
            self.assertEqual(run.baseline_candidates, 1)
            self.assertEqual(run.comparison_candidates, 2)
            self.assertEqual(
                {item.variant_id for item in run.scorecards},
                {"baseline_v1", "fast_v1", "slow_v1"},
            )
            self.assertTrue(
                all(
                    0 <= item.research_score <= 100
                    and not item.execution_eligible
                    and not item.metrics["research_decision"][
                        "execution_eligible"
                    ]
                    and not item.metrics["research_decision"][
                        "automatic_parameter_change"
                    ]
                    for item in run.scorecards
                )
            )
            self.assertEqual(len(run.summary["research_shortlist"]), 1)
            self.assertFalse(
                run.summary["automatic_parameter_changes"]
            )
            self.assertEqual(
                run.summary["selection_mode"],
                "incremental_research",
            )
            self.assertEqual(
                run.summary["selected_strategy_ids"],
                ("frequent_test",),
            )
            self.assertEqual(
                len(run.dataset_fingerprints), 1
            )
            self.assertEqual(
                reader.recent_strategy_lab_reports(), []
            )
            self.assertEqual(
                reader.recent_strategy_model_trials(), []
            )
            self.assertEqual(len(stored_runs), 1)
            self.assertFalse(
                stored_runs[0]["execution_eligible"]
            )
            self.assertTrue(
                all(
                    "research_decision" in item["metrics"]
                    for item in stored_runs[0]["scorecards"]
                )
            )
            self.assertFalse(
                stored_runs[0]["summary"][
                    "prospective_trial_count_incremented"
                ]
            )
            event = reader.recent_events()[0]
            self.assertEqual(
                event["event_type"],
                "accelerated_validation_completed",
            )
            self.assertFalse(
                event["payload"][
                    "prospective_trial_count_incremented"
                ]
            )
            self.assertFalse(
                Path(
                    directory
                ).joinpath(
                    "strategy-lab-health.json"
                ).exists()
            )

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

    def test_continuous_cycle_uses_allocation_specific_timeframes(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            market_data = FakeMarketData()
            plan = AccountPlan(
                account_id="alpaca-paper",
                broker="alpaca",
                environment="paper",
                enabled=True,
                asset_classes=(AssetClass.STOCK,),
                watchlist=("AAPL", "MSFT"),
                timeframe="5Min",
                maximum_positions=4,
                maximum_daily_orders=6,
                symbol_cooldown_minutes=60,
                allocations={
                    "daily_test": StrategyAllocation(
                        strategy_id="daily_test",
                        enabled=True,
                        capital_weight=Decimal("0.10"),
                        risk_fraction=Decimal("0.003"),
                        minimum_confidence=Decimal("0.60"),
                        timeframe="1Day",
                    ),
                    "swing_test": StrategyAllocation(
                        strategy_id="swing_test",
                        enabled=True,
                        capital_weight=Decimal("0.10"),
                        risk_fraction=Decimal("0.003"),
                        minimum_confidence=Decimal("0.60"),
                        timeframe="4Hour",
                    ),
                },
            )
            service = ContinuousStrategyLabService(
                account_plan=plan,
                strategies={
                    "daily_test": FrequentTestStrategy(
                        strategy_id="daily_test"
                    ),
                    "swing_test": FrequentTestStrategy(
                        strategy_id="swing_test"
                    ),
                },
                market_data=market_data,
                store=SqliteAuditStore(db_path),
                health_path=str(
                    Path(directory) / "strategy-lab-health.json"
                ),
                config=StrategyLabConfig(minimum_out_of_sample_trades=5),
            )

            result = service.run_cycle(
                now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            )
            reports = SqliteAuditReader(
                db_path
            ).recent_strategy_lab_reports(limit=10)

            self.assertEqual(
                {call[1] for call in market_data.fetch_calls},
                {"1Day", "4Hour"},
            )
            self.assertEqual(result.timeframe, "1Day,4Hour")
            self.assertEqual(
                {report["timeframe"] for report in reports},
                {"1Day", "4Hour"},
            )
