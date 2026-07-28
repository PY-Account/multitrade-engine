import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditStore
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan, StrategyAllocation
from multitrade.trials import build_strategy_trial_definition


@dataclass(frozen=True, slots=True)
class ParameterStrategy:
    strategy_id: str = "parameter_test"
    version: str = "1.0.0"
    threshold: Decimal = Decimal("1.10")

    def evaluate(self, context):
        del context
        return None


def bar(*, close: Decimal = Decimal("100")) -> MarketBar:
    return MarketBar(
        symbol="SPY",
        asset_class=AssetClass.STOCK,
        timeframe="5Min",
        timestamp=datetime(
            2026, 7, 28, 14, 30, tzinfo=timezone.utc
        ),
        open=Decimal("100"),
        high=max(Decimal("101"), close),
        low=Decimal("99"),
        close=close,
        volume=Decimal("100000"),
        trade_count=500,
        vwap=Decimal("100"),
        feed="iex",
    )


def plan() -> AccountPlan:
    allocation = StrategyAllocation(
        strategy_id="parameter_test",
        enabled=True,
        capital_weight=Decimal("0.20"),
        risk_fraction=Decimal("0.005"),
        minimum_confidence=Decimal("0.60"),
    )
    return AccountPlan(
        account_id="alpaca-paper",
        broker="alpaca",
        environment="paper",
        enabled=True,
        asset_classes=(AssetClass.STOCK,),
        watchlist=("SPY",),
        timeframe="5Min",
        maximum_positions=2,
        maximum_daily_orders=6,
        symbol_cooldown_minutes=60,
        allocations={"parameter_test": allocation},
    )


class StrategyTrialDefinitionTests(TestCase):
    def definition(
        self,
        *,
        strategy: ParameterStrategy | None = None,
        lab_config: dict | None = None,
        market_bar: MarketBar | None = None,
    ):
        account_plan = plan()
        return build_strategy_trial_definition(
            strategy=strategy or ParameterStrategy(),
            allocation=account_plan.allocations["parameter_test"],
            account_plan=account_plan,
            lab_config=lab_config or {"folds": 3},
            requested_symbols=("SPY",),
            bars_by_symbol={"SPY": (market_bar or bar(),)},
        )

    def test_fingerprints_separate_candidate_config_and_data(self) -> None:
        baseline = self.definition()
        changed_candidate = self.definition(
            strategy=replace(
                ParameterStrategy(),
                threshold=Decimal("1.20"),
            )
        )
        changed_configuration = self.definition(
            lab_config={"folds": 4}
        )
        changed_dataset = self.definition(
            market_bar=bar(close=Decimal("100.5"))
        )

        self.assertNotEqual(
            baseline.candidate_fingerprint,
            changed_candidate.candidate_fingerprint,
        )
        self.assertEqual(
            baseline.configuration_fingerprint,
            changed_candidate.configuration_fingerprint,
        )
        self.assertEqual(
            baseline.dataset_fingerprint,
            changed_candidate.dataset_fingerprint,
        )
        self.assertNotEqual(
            baseline.configuration_fingerprint,
            changed_configuration.configuration_fingerprint,
        )
        self.assertNotEqual(
            baseline.dataset_fingerprint,
            changed_dataset.dataset_fingerprint,
        )

    def test_existing_strategy_lab_database_adds_registry(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE strategy_lab_reports (
                        report_id TEXT PRIMARY KEY,
                        account_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        strategy_version TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        evaluated_at TEXT NOT NULL,
                        configuration_enabled INTEGER NOT NULL,
                        paper_execution_configured INTEGER NOT NULL,
                        symbols_requested_json TEXT NOT NULL,
                        symbols_covered_json TEXT NOT NULL,
                        missing_symbols_json TEXT NOT NULL,
                        symbol_results_json TEXT NOT NULL,
                        aggregate_metrics_json TEXT NOT NULL,
                        gates_json TEXT NOT NULL,
                        warnings_json TEXT NOT NULL,
                        readiness_status TEXT NOT NULL,
                        execution_eligible INTEGER NOT NULL
                    )
                    """
                )
                connection.commit()

            SqliteAuditStore(db_path).close()

            with closing(sqlite3.connect(db_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                triggers = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'trigger'
                        """
                    )
                }
            self.assertIn("strategy_model_trials", tables)
            self.assertIn(
                "strategy_experiment_manifests", tables
            )
            self.assertIn(
                "strategy_model_trials_no_update", triggers
            )
            self.assertIn(
                "strategy_experiment_manifests_no_update",
                triggers,
            )
            self.assertIn(
                "strategy_experiment_manifests_no_delete",
                triggers,
            )
            self.assertIn(
                "strategy_lab_reports_with_trial_no_delete",
                triggers,
            )
