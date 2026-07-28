import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.config import Settings
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.portfolio import AccountPlan
from multitrade.research import (
    EVIDENCE_REGISTRY,
    ContinuousResearchService,
    EvidenceWeightedMarketModel,
    ResearchState,
    load_research_program,
)


def daily_bars(
    *,
    symbol: str,
    closes: list[Decimal],
    end: datetime,
) -> tuple[MarketBar, ...]:
    start = end - timedelta(days=len(closes))
    return tuple(
        MarketBar(
            symbol=symbol,
            asset_class=AssetClass.STOCK,
            timeframe="1Day",
            timestamp=start + timedelta(days=index),
            open=close,
            high=close * Decimal("1.005"),
            low=close * Decimal("0.995"),
            close=close,
            volume=Decimal("1000000"),
            trade_count=1000,
            vwap=close,
            feed="iex",
        )
        for index, close in enumerate(closes)
    )


def rising_closes(count: int = 300) -> list[Decimal]:
    return [
        Decimal("100") + Decimal(index) * Decimal("0.25")
        for index in range(count)
    ]


class FakeDailyMarketData:
    def __init__(
        self, rows: dict[str, tuple[MarketBar, ...]]
    ) -> None:
        self.rows = rows
        self.request_ids = ["research-request"]

    def fetch_stock_bars(
        self, symbols, timeframe, start, end, *, adjustment="raw"
    ):
        del start, end
        if timeframe != "1Day":
            raise AssertionError("Research must use closed daily bars")
        if adjustment != "all":
            raise AssertionError("Research bars must be fully adjusted")
        return {
            symbol: tuple(
                replace(bar, adjustment=adjustment)
                for bar in self.rows.get(symbol, ())
            )
            for symbol in symbols
        }


class ResearchModelTests(TestCase):
    def test_positive_trend_is_observation_only(self) -> None:
        now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
        rows = daily_bars(symbol="SPY", closes=rising_closes(), end=now)
        decision = EvidenceWeightedMarketModel().evaluate(
            account_id="alpaca-paper",
            symbol="SPY",
            bars=rows,
            benchmark_bars=rows,
            evaluated_at=now,
        )
        self.assertEqual(decision.state, ResearchState.RISK_ON)
        self.assertGreater(decision.score, Decimal("0.25"))
        self.assertGreater(decision.target_risk_multiplier, Decimal("0"))
        self.assertLessEqual(
            decision.target_risk_multiplier, Decimal("1")
        )
        self.assertFalse(decision.execution_eligible)

    def test_insufficient_history_fails_closed(self) -> None:
        now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
        rows = daily_bars(
            symbol="SPY", closes=rising_closes(252), end=now
        )
        decision = EvidenceWeightedMarketModel().evaluate(
            account_id="alpaca-paper",
            symbol="SPY",
            bars=rows,
            benchmark_bars=rows,
            evaluated_at=now,
        )
        self.assertEqual(
            decision.state, ResearchState.INSUFFICIENT_DATA
        )
        self.assertEqual(decision.target_risk_multiplier, Decimal("0"))

    def test_panic_state_forces_zero_risk_multiplier(self) -> None:
        now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
        closes = rising_closes(260)
        last = closes[-1]
        for index in range(40):
            last *= Decimal("0.995") if index % 2 else Decimal("0.90")
            closes.append(last)
        rows = daily_bars(symbol="SPY", closes=closes, end=now)
        decision = EvidenceWeightedMarketModel().evaluate(
            account_id="alpaca-paper",
            symbol="SPY",
            bars=rows,
            benchmark_bars=rows,
            evaluated_at=now,
        )
        self.assertEqual(decision.state, ResearchState.RISK_OFF)
        self.assertEqual(decision.target_risk_multiplier, Decimal("0"))
        self.assertIn("panic_state_guard", decision.reason_codes)

    def test_public_thesis_is_never_execution_candidate(self) -> None:
        record = next(
            item
            for item in EVIDENCE_REGISTRY
            if item.evidence_id
            == "aschenbrenner_public_thesis_2024"
        )
        self.assertFalse(record.execution_candidate)
        self.assertTrue(
            any(
                "not a published trading strategy" in caveat
                for caveat in record.caveats
            )
        )

    def test_theme_config_rejects_paper_execution(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "research.json"
            path.write_text(
                json.dumps(
                    {
                        "benchmark": "SPY",
                        "universe": ["SPY"],
                        "themes": [
                            {
                                "theme_id": "unsafe",
                                "title": "Unsafe",
                                "source_url": "https://example.test",
                                "attribution_notice": "test",
                                "paper_execution_allowed": True,
                                "pillars": [
                                    {
                                        "pillar_id": "market",
                                        "symbols": ["SPY"],
                                        "weight": "1",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, "cannot enable Paper execution"
            ):
                load_research_program(path)

    def test_service_excludes_open_daily_bar_and_persists_decisions(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
            symbols = ("SPY", "QQQ", "SMH", "XLU")
            rows = {
                symbol: daily_bars(
                    symbol=symbol, closes=rising_closes(), end=now
                )
                for symbol in symbols
            }
            future = MarketBar(
                symbol="SPY",
                asset_class=AssetClass.STOCK,
                timeframe="1Day",
                timestamp=now,
                open=Decimal("1000"),
                high=Decimal("1001"),
                low=Decimal("999"),
                close=Decimal("1000"),
                volume=Decimal("1000000"),
                trade_count=100,
                vwap=Decimal("1000"),
                feed="iex",
            )
            rows["SPY"] = (*rows["SPY"], future)
            program_path = (
                Path(__file__).parents[1]
                / "config"
                / "research_program.json"
            )
            settings = replace(
                Settings.from_env(),
                db_path=Path(directory) / "trading.db",
                research_health_path=Path(directory)
                / "research-health.json",
                research_program_path=program_path,
            )
            service = ContinuousResearchService(
                settings=settings,
                market_data=FakeDailyMarketData(rows),
                store=SqliteAuditStore(settings.db_path),
                account_plan=AccountPlan(
                    account_id="alpaca-paper",
                    broker="alpaca",
                    environment="paper",
                    enabled=True,
                    asset_classes=(AssetClass.STOCK,),
                    watchlist=("SPY",),
                    timeframe="5Min",
                    maximum_positions=1,
                    maximum_daily_orders=1,
                    symbol_cooldown_minutes=60,
                    allocations={},
                ),
                program=load_research_program(program_path),
            )
            result = service.run_cycle(now=now)
            decisions = SqliteAuditReader(
                settings.db_path
            ).recent_research_decisions()
            research_backtests = SqliteAuditReader(
                settings.db_path
            ).recent_research_backtests()
            portfolio_risk = SqliteAuditReader(
                settings.db_path
            ).recent_portfolio_risk_reports()
            stored_daily_bars = SqliteAuditReader(
                settings.db_path
            ).market_bars("SPY", "1Day")
            spy = next(
                item
                for item in decisions
                if item["model_id"]
                == "evidence_weighted_market_model"
                and item["symbol"] == "SPY"
            )
            self.assertEqual(result.decisions_recorded, 4)
            self.assertNotEqual(spy["bar_timestamp"], now.isoformat())
            self.assertTrue(
                any(
                    item["model_id"] == "public_thesis_proxy"
                    for item in decisions
                )
            )
            self.assertTrue(
                all(not item["execution_eligible"] for item in decisions)
            )
            self.assertEqual(result.validation_reports_recorded, 4)
            self.assertEqual(len(research_backtests), 4)
            self.assertTrue(
                all(
                    not item["execution_eligible"]
                    for item in research_backtests
                )
            )
            self.assertEqual(len(portfolio_risk), 1)
            self.assertFalse(portfolio_risk[0]["execution_eligible"])
            self.assertTrue(stored_daily_bars)
            self.assertTrue(
                all(
                    item["adjustment"] == "all"
                    for item in stored_daily_bars
                )
            )
