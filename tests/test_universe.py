from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan
from multitrade.universe import (
    AssetUniverseEvaluator,
    AssetUniverseProgram,
    CompanySizeEvidence,
    ContinuousAssetUniverseService,
    IndexSnapshot,
    StrategyUniverseAssignment,
    UniversePolicy,
    load_asset_universe_program,
)


def policy() -> UniversePolicy:
    return UniversePolicy(
        policy_id="liquid",
        candidate_source="combined",
        seed_symbols=("GOOD", "PENNY"),
        most_active_limit=20,
        lookback_days=20,
        minimum_price=Decimal("3"),
        minimum_company_size_usd=Decimal("300000000"),
        minimum_average_daily_share_volume=Decimal("500000"),
        minimum_average_daily_dollar_volume=Decimal("10000000"),
        allowed_exchanges=("NASDAQ", "NYSE"),
        required_index_sets=(),
        maximum_company_size_age_days=550,
        maximum_index_snapshot_age_days=45,
        maximum_recommendations=10,
    )


def daily_bars(
    symbol: str, close: Decimal
) -> tuple[MarketBar, ...]:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return tuple(
        MarketBar(
            symbol=symbol,
            asset_class=AssetClass.STOCK,
            timeframe="1Day",
            timestamp=start + timedelta(days=index),
            open=close,
            high=close + Decimal("0.10"),
            low=close - Decimal("0.10"),
            close=close,
            volume=Decimal("1000000"),
            trade_count=10000,
            vwap=close,
            feed="iex",
            adjustment="all",
        )
        for index in range(20)
    )


class FakeBroker:
    request_ids = ("asset-catalog-request",)

    def list_active_stock_assets(self):
        return {
            symbol: {
                "symbol": symbol,
                "status": "active",
                "class": "us_equity",
                "tradable": True,
                "exchange": "NASDAQ",
            }
            for symbol in ("GOOD", "PENNY", "ACTIVE")
        }


class FakeMarketData:
    request_ids = ["bars-request"]

    def fetch_most_active_stocks(self, *, top):
        del top
        return ("ACTIVE",)

    def fetch_stock_bars(
        self,
        symbols,
        timeframe,
        start,
        end,
        *,
        adjustment,
    ):
        del timeframe, start, end, adjustment
        return {
            symbol: daily_bars(
                symbol,
                Decimal("2") if symbol == "PENNY" else Decimal("20"),
            )
            for symbol in symbols
        }


class FakeSec:
    def company_size(self, symbol, price):
        del symbol, price
        return CompanySizeEvidence(
            value_usd=Decimal("1000000000"),
            method="sec_shares_times_price",
            as_of="2026-07-01",
            source_url=(
                "https://data.sec.gov/api/xbrl/companyfacts/"
                "CIK0000000001.json"
            ),
        )


class AssetUniverseTests(TestCase):
    def test_price_liquidity_size_and_tradability_gate_recommendations(
        self,
    ) -> None:
        evaluated_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
        report = AssetUniverseEvaluator().evaluate(
            account_id="alpaca-paper",
            policy=policy(),
            evaluated_at=evaluated_at,
            candidate_sources={
                "GOOD": ("configured_seed",),
                "PENNY": ("configured_seed",),
            },
            bars_by_symbol={
                "GOOD": daily_bars("GOOD", Decimal("20")),
                "PENNY": daily_bars("PENNY", Decimal("2")),
            },
            assets_by_symbol=FakeBroker().list_active_stock_assets(),
            size_evidence={
                symbol: FakeSec().company_size(
                    symbol, Decimal("20")
                )
                for symbol in ("GOOD", "PENNY")
            },
            index_snapshots={},
        )

        self.assertEqual(report.recommendations, ("GOOD",))
        penny = next(
            item
            for item in report.evaluations
            if item.symbol == "PENNY"
        )
        self.assertFalse(penny.gates["minimum_price"])
        self.assertFalse(report.execution_eligible)

    def test_cycle_combines_manual_and_alpaca_candidates_and_persists(
        self,
    ) -> None:
        assignment = StrategyUniverseAssignment(
            strategy_id="breakout_retest",
            selection_mode="combined",
            policy_id="liquid",
            manual_symbols=("GOOD",),
            maximum_symbols=10,
        )
        program = AssetUniverseProgram(
            policies={"liquid": policy()},
            strategy_assignments={
                "breakout_retest": assignment
            },
            index_snapshots={},
            asset_references={},
        )
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            health_path = Path(directory) / "universe-health.json"
            service = ContinuousAssetUniverseService(
                account_plan=AccountPlan(
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
                    allocations={},
                ),
                program=program,
                broker=FakeBroker(),
                market_data=FakeMarketData(),
                store=SqliteAuditStore(db_path),
                health_path=health_path,
                sec_client=FakeSec(),
            )

            result = service.run_cycle(
                now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            )
            reports = SqliteAuditReader(
                db_path
            ).recent_asset_universe_reports()

            self.assertEqual(result.policies_evaluated, 1)
            self.assertIn("ACTIVE", reports[0]["candidates_requested"])
            self.assertIn("GOOD", reports[0]["recommendations"])
            self.assertTrue(health_path.is_file())

    def test_cycle_adds_required_index_snapshot_symbols_as_candidates(
        self,
    ) -> None:
        indexed_policy = replace(
            policy(),
            candidate_source="manual",
            seed_symbols=(),
            required_index_sets=("sp500",),
            maximum_recommendations=503,
        )
        program = AssetUniverseProgram(
            policies={"sp500": indexed_policy},
            strategy_assignments={},
            index_snapshots={
                "sp500": IndexSnapshot(
                    index_id="sp500",
                    label="S&P 500",
                    as_of="2026-07-28",
                    source_url=(
                        "https://raw.githubusercontent.com/datasets/"
                        "s-and-p-500-companies/main/data/"
                        "constituents.csv"
                    ),
                    symbols=("GOOD", "ACTIVE"),
                )
            },
            asset_references={},
        )
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.db"
            service = ContinuousAssetUniverseService(
                account_plan=AccountPlan(
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
                    allocations={},
                ),
                program=program,
                broker=FakeBroker(),
                market_data=FakeMarketData(),
                store=SqliteAuditStore(db_path),
                health_path=Path(directory) / "universe-health.json",
                sec_client=FakeSec(),
            )

            service.run_cycle(
                now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            )
            reports = SqliteAuditReader(
                db_path
            ).recent_asset_universe_reports()

            self.assertIn("GOOD", reports[0]["candidates_requested"])
            self.assertIn("ACTIVE", reports[0]["candidates_requested"])
            self.assertEqual(
                reports[0]["evaluations"][0]["sources"],
                ["index_snapshot:sp500"],
            )

    def test_index_membership_requires_a_fresh_sourced_snapshot(
        self,
    ) -> None:
        evaluated_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
        restricted = replace(
            policy(),
            required_index_sets=("nasdaq100",),
        )
        report = AssetUniverseEvaluator().evaluate(
            account_id="alpaca-paper",
            policy=restricted,
            evaluated_at=evaluated_at,
            candidate_sources={"GOOD": ("configured_seed",)},
            bars_by_symbol={
                "GOOD": daily_bars("GOOD", Decimal("20"))
            },
            assets_by_symbol=FakeBroker().list_active_stock_assets(),
            size_evidence={
                "GOOD": FakeSec().company_size(
                    "GOOD", Decimal("20")
                )
            },
            index_snapshots={
                "nasdaq100": IndexSnapshot(
                    index_id="nasdaq100",
                    label="Nasdaq-100",
                    as_of="2025-01-01",
                    source_url=(
                        "https://indexes.nasdaqomx.com/"
                        "Index/Overview/NDX"
                    ),
                    symbols=("GOOD",),
                )
            },
        )

        self.assertEqual(report.recommendations, ())
        self.assertTrue(
            report.evaluations[0].gates[
                "required_index_membership"
            ]
        )
        self.assertFalse(
            report.evaluations[0].gates[
                "index_snapshot_fresh"
            ]
        )

    def test_repository_config_assigns_each_strategy_for_research(
        self,
    ) -> None:
        program = load_asset_universe_program(
            Path(__file__).parents[1]
            / "config"
            / "asset_universe.json"
        )

        symbols = program.assigned_symbols(
            "breakout_retest",
            account_watchlist=("SPY",),
            recommendations_by_policy={
                "liquid_us_breakout_candidates": ("ACTIVE",)
            },
        )

        self.assertIn("AMD", symbols)
        self.assertIn("ACTIVE", symbols)
        self.assertNotEqual(symbols, ("SPY",))
