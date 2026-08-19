import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.domain import AccountSnapshot, AssetClass
from multitrade.features import FeatureEngine, FeatureSnapshot, MarketRegime
from multitrade.market import MarketBar
from multitrade.portfolio import (
    SignalAllocator,
    apply_strategy_configuration_overrides,
    load_account_plans,
)
from multitrade.strategies.base import SignalAction, StrategyContext
from multitrade.strategies.equity import (
    BreakoutRetestStrategy,
    ConfirmedBreakoutRetestV3Strategy,
    ConfirmedBreakoutRetestV4Strategy,
    BearishFvgPutDebitStrategy,
    SupportDeltaPutIncomeStrategy,
    SupportDeltaPutIncomeV2Strategy,
    SignalInversionStrategy,
    T3RangeTrendStrategy,
    ZeroDteIronCondorStrategy,
)


def market_bar(
    index: int,
    *,
    open_price: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
    volume: str = "100",
    timeframe: str = "5Min",
) -> MarketBar:
    step = timedelta(days=index) if timeframe == "1Day" else timedelta(
        minutes=index * 5
    )
    return MarketBar(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        timeframe=timeframe,
        timestamp=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        + step,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        trade_count=10,
        vwap=Decimal(close),
        feed="iex",
    )


class StrategyTests(TestCase):
    def bearish_fvg_context(self) -> StrategyContext:
        bars = [
            market_bar(
                index,
                open_price="104",
                high="105",
                low="103",
                close="104",
                volume="1000",
            )
            for index in range(24)
        ]
        bars.extend(
            (
                market_bar(24, open_price="103", high="103", low="101", close="102", volume="1100"),
                market_bar(25, open_price="100", high="100", low="99", close="99.5", volume="1200"),
                market_bar(26, open_price="98", high="98", low="96", close="97", volume="1300"),
                market_bar(27, open_price="97.5", high="98.5", low="96.5", close="97.2", volume="1200"),
                market_bar(28, open_price="97.4", high="98.8", low="96.8", close="97.1", volume="1250"),
                market_bar(29, open_price="97.3", high="99", low="96.7", close="97.0", volume="1250"),
                market_bar(30, open_price="100", high="100.2", low="98", close="98.6", volume="1400"),
            )
        )
        features = FeatureSnapshot(
            symbol="AAPL",
            bar_timestamp=bars[-1].timestamp.isoformat(),
            close=bars[-1].close,
            sma_fast=Decimal("99.20"),
            sma_slow=Decimal("101.00"),
            atr=Decimal("1.50"),
            atr_percent=Decimal("0.015"),
            average_volume=Decimal("1200"),
            relative_volume=Decimal("1.10"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1.00"),
            donchian_high=Decimal("105"),
            donchian_low=Decimal("96"),
            trend_strength=Decimal("-0.012"),
            regime=MarketRegime.TREND_DOWN,
            sample_size=len(bars),
        )
        return StrategyContext(
            account_id="alpaca-paper-options",
            bars=tuple(bars),
            features=features,
            evaluated_at=bars[-1].timestamp,
        )

    def test_bearish_fvg_generates_directional_put_debit_signal(
        self,
    ) -> None:
        signal = BearishFvgPutDebitStrategy().evaluate(
            self.bearish_fvg_context()
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, SignalAction.ENTER_SHORT)
        self.assertIn("bearish_fvg_retest", signal.reason_codes)
        self.assertEqual(
            signal.evidence["execution_vehicle"],
            "bear_put_debit_spread",
        )

    def test_zero_dte_iron_condor_vetoes_directional_fvg(
        self,
    ) -> None:
        prior = [
            market_bar(
                index,
                open_price="100",
                high="101",
                low="99",
                close="100",
                volume="1000",
            )
            for index in range(24)
        ]
        today_start = datetime(
            2026, 1, 6, 14, 30, tzinfo=timezone.utc
        )
        today = [
            MarketBar(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                timeframe="5Min",
                timestamp=today_start + timedelta(minutes=index * 5),
                open=Decimal(open_price),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=Decimal("1000"),
                trade_count=10,
                vwap=Decimal(close),
                feed="iex",
            )
            for index, (open_price, high, low, close) in enumerate(
                (
                    ("100", "100.8", "99.8", "100.2"),
                    ("100.1", "101", "100", "100.8"),
                    ("99", "99", "98", "98.5"),
                    ("98.6", "99", "98.1", "98.4"),
                    ("98.5", "99.2", "98.3", "98.7"),
                    ("99.4", "99.6", "98.4", "98.8"),
                    ("100", "100.1", "98.5", "98.7"),
                )
            )
        ]
        bars = tuple(prior + today)
        features = FeatureSnapshot(
            symbol="AAPL",
            bar_timestamp=bars[-1].timestamp.isoformat(),
            close=bars[-1].close,
            sma_fast=Decimal("99"),
            sma_slow=Decimal("100"),
            atr=Decimal("1"),
            atr_percent=Decimal("0.01"),
            average_volume=Decimal("1000"),
            relative_volume=Decimal("1"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1"),
            donchian_high=Decimal("101"),
            donchian_low=Decimal("98"),
            trend_strength=Decimal("0.001"),
            regime=MarketRegime.RANGE,
            sample_size=len(bars),
        )

        signal = ZeroDteIronCondorStrategy().evaluate(
            StrategyContext(
                account_id="alpaca-paper-options",
                bars=bars,
                features=features,
                evaluated_at=bars[-1].timestamp,
            )
        )

        self.assertIsNone(signal)

    def test_confirmed_retest_v3_requires_five_closes_then_retest(self) -> None:
        history = tuple(
            market_bar(
                index,
                open_price=str(Decimal("97") + Decimal(index) / 20),
                high=str(Decimal("98") + Decimal(index) / 20),
                low=str(Decimal("96.5") + Decimal(index) / 20),
                close=str(Decimal("97.5") + Decimal(index) / 20),
                volume="1000",
            )
            for index in range(30)
        )
        level = max(bar.high for bar in history)
        breakout = market_bar(
            30, open_price=str(level), high="101.5", low="99.3",
            close="101", volume="1400",
        )
        confirmation = tuple(
            market_bar(
                31 + index, open_price="100.8", high="102",
                low="100.2", close="101.2", volume="1000",
            )
            for index in range(5)
        )
        retest = market_bar(
            36, open_price="100.5", high="101.2", low=str(level),
            close="100.8", volume="900",
        )
        bars = history + (breakout,) + confirmation + (retest,)
        features = FeatureSnapshot(
            symbol="AAPL", bar_timestamp=retest.timestamp.isoformat(),
            close=retest.close, sma_fast=Decimal("100.5"),
            sma_slow=Decimal("99"), atr=Decimal("1"),
            atr_percent=Decimal("0.01"), average_volume=Decimal("1000"),
            relative_volume=Decimal("0.9"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1"), donchian_high=Decimal("102"),
            donchian_low=Decimal("96"), trend_strength=Decimal("0.01"),
            regime=MarketRegime.TREND_UP, sample_size=len(bars),
        )
        context = StrategyContext(
            account_id="alpaca-paper", bars=bars, features=features,
            evaluated_at=retest.timestamp + timedelta(minutes=5),
        )

        signal = ConfirmedBreakoutRetestV3Strategy().evaluate(context)

        self.assertIsNotNone(signal)
        self.assertIn("five_bar_acceptance_above_resistance", signal.reason_codes)
        risk = signal.reference_price - signal.stop_price
        reward = signal.target_price - signal.reference_price
        self.assertEqual(reward / risk, Decimal("2"))
        four_bar_context = replace(context, bars=bars[:-2] + (retest,))
        self.assertIsNone(
            ConfirmedBreakoutRetestV3Strategy().evaluate(four_bar_context)
        )

    def test_confirmed_retest_v4_accepts_delayed_daily_retest(self) -> None:
        history = tuple(
            market_bar(
                index,
                open_price=str(Decimal("80") + Decimal(index) / 4),
                high=str(Decimal("81") + Decimal(index) / 4),
                low=str(Decimal("79") + Decimal(index) / 4),
                close=str(Decimal("80.5") + Decimal(index) / 4),
                volume="1000",
                timeframe="1Day",
            )
            for index in range(45)
        )
        level = max(bar.high for bar in history)
        breakout = market_bar(
            45,
            open_price=str(level),
            high=str(level + Decimal("3")),
            low=str(level - Decimal("0.5")),
            close=str(level + Decimal("2")),
            volume="1300",
            timeframe="1Day",
        )
        confirmation = tuple(
            market_bar(
                46 + index,
                open_price=str(level + Decimal("1.0")),
                high=str(level + Decimal("2.0")),
                low=str(level + Decimal("0.4")),
                close=str(level + Decimal("1.2")),
                volume="1050",
                timeframe="1Day",
            )
            for index in range(5)
        )
        drift = tuple(
            market_bar(
                51 + index,
                open_price=str(level + Decimal("1.5")),
                high=str(level + Decimal("2.2")),
                low=str(level + Decimal("0.6")),
                close=str(level + Decimal("1.4")),
                volume="900",
                timeframe="1Day",
            )
            for index in range(6)
        )
        retest = market_bar(
            57,
            open_price=str(level + Decimal("0.15")),
            high=str(level + Decimal("1.0")),
            low=str(level - Decimal("0.2")),
            close=str(level + Decimal("0.65")),
            volume="850",
            timeframe="1Day",
        )
        bars = history + (breakout,) + confirmation + drift + (retest,)
        features = FeatureSnapshot(
            symbol="AAPL",
            bar_timestamp=retest.timestamp.isoformat(),
            close=retest.close,
            sma_fast=level + Decimal("0.4"),
            sma_slow=level - Decimal("2"),
            atr=Decimal("2"),
            atr_percent=Decimal("0.018"),
            average_volume=Decimal("1000"),
            relative_volume=Decimal("0.85"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1"),
            donchian_high=level + Decimal("3"),
            donchian_low=level - Decimal("5"),
            trend_strength=Decimal("0.004"),
            regime=MarketRegime.TREND_UP,
            sample_size=len(bars),
        )
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=bars,
            features=features,
            evaluated_at=retest.timestamp + timedelta(days=1),
        )

        signal = ConfirmedBreakoutRetestV4Strategy().evaluate(context)

        self.assertIsNotNone(signal)
        self.assertIn(
            "delayed_retest_of_breakout_zone",
            signal.reason_codes,
        )
        self.assertEqual(signal.evidence["timeframe"], "1Day")
        risk = signal.reference_price - signal.stop_price
        reward = signal.target_price - signal.reference_price
        self.assertEqual(reward / risk, Decimal("2"))
        self.assertGreaterEqual(risk, features.atr * Decimal("0.75"))

    def test_support_delta_put_signal_requires_bullish_rejection(self) -> None:
        bars = tuple(
            market_bar(
                index,
                open_price="100",
                high="101",
                low="99",
                close="100",
                volume="1000",
            )
            for index in range(41)
        ) + (
            market_bar(
                41,
                open_price="99.2",
                high="101",
                low="99.0",
                close="100.5",
                volume="1200",
            ),
        )
        features = FeatureSnapshot(
            symbol="AAPL",
            bar_timestamp=bars[-1].timestamp.isoformat(),
            close=Decimal("100.5"),
            sma_fast=Decimal("100"),
            sma_slow=Decimal("99.5"),
            atr=Decimal("2"),
            atr_percent=Decimal("0.02"),
            average_volume=Decimal("1000"),
            relative_volume=Decimal("1.2"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1"),
            donchian_high=Decimal("101"),
            donchian_low=Decimal("99"),
            trend_strength=Decimal("0.005"),
            regime=MarketRegime.RANGE,
            sample_size=len(bars),
        )
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=bars,
            features=features,
            evaluated_at=bars[-1].timestamp + timedelta(minutes=5),
        )

        signal = SupportDeltaPutIncomeStrategy().evaluate(context)

        self.assertIsNotNone(signal)
        self.assertIn("bullish_support_rejection", signal.reason_codes)
        self.assertEqual(
            signal.evidence["vehicle_constraint"],
            "bull_put_credit_spread_only",
        )
        downtrend = StrategyContext(
            account_id=context.account_id,
            bars=context.bars,
            features=replace(features, regime=MarketRegime.TREND_DOWN),
            evaluated_at=context.evaluated_at,
        )
        self.assertIsNone(
            SupportDeltaPutIncomeStrategy().evaluate(downtrend)
        )

    def test_put_income_v2_has_new_identity_and_requires_uptrend(self) -> None:
        bars = tuple(
            market_bar(
                index,
                open_price=str(Decimal("99.9") + Decimal(index) / 10),
                high=str(Decimal("100.2") + Decimal(index) / 10),
                low=str(Decimal("99.7") + Decimal(index) / 10),
                close=str(Decimal("100") + Decimal(index) / 10),
                volume="1000",
            )
            for index in range(41)
        ) + (
            market_bar(
                41,
                open_price="103.5",
                high="104.8",
                low="101.8",
                close="104.5",
                volume="1300",
            ),
        )
        features = FeatureSnapshot(
            symbol="AAPL",
            bar_timestamp=bars[-1].timestamp.isoformat(),
            close=Decimal("104.5"),
            sma_fast=Decimal("103.8"),
            sma_slow=Decimal("103"),
            atr=Decimal("2"),
            atr_percent=Decimal("0.019"),
            average_volume=Decimal("1000"),
            relative_volume=Decimal("1.3"),
            return_volatility=Decimal("0.01"),
            relative_volatility=Decimal("1"),
            donchian_high=Decimal("104.2"),
            donchian_low=Decimal("99.7"),
            trend_strength=Decimal("0.008"),
            regime=MarketRegime.TREND_UP,
            sample_size=len(bars),
        )
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=bars,
            features=features,
            evaluated_at=bars[-1].timestamp + timedelta(minutes=5),
        )

        v1 = SupportDeltaPutIncomeStrategy().evaluate(context)
        v2 = SupportDeltaPutIncomeV2Strategy().evaluate(context)

        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertNotEqual(v1.signal_id, v2.signal_id)
        self.assertEqual(v2.strategy_version, "2.0.0")
        self.assertIn("uptrend_regime_required", v2.reason_codes)

    def test_t3_range_adaptation_emits_only_on_dual_filter_transition(
        self,
    ) -> None:
        bars = [market_bar(index) for index in range(90)]
        bars.append(
            market_bar(
                90,
                open_price="100",
                high="105",
                low="99.5",
                close="104",
                volume="500",
            )
        )
        ordered = tuple(bars)
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=ordered,
            features=FeatureEngine().calculate(ordered),
            evaluated_at=ordered[-1].timestamp + timedelta(minutes=5),
        )

        signal = T3RangeTrendStrategy().evaluate(context)

        self.assertIsNotNone(signal)
        self.assertIn("tillson_t3_rising", signal.reason_codes)
        self.assertEqual(
            signal.evidence["source"],
            "youtube_BPFwaD0CgZ8_equity_adaptation",
        )
        self.assertGreater(signal.target_price, signal.reference_price)
        self.assertLess(signal.stop_price, signal.reference_price)

    def test_runtime_strategy_override_is_paper_only_and_expands_watchlist(
        self,
    ) -> None:
        plans = load_account_plans(
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )

        effective = apply_strategy_configuration_overrides(
            plans,
            [
                {
                    "account_id": "alpaca-paper",
                    "strategy_id": "range_mean_reversion",
                    "enabled": True,
                    "paper_execution_allowed": True,
                    "symbols": ["NVDA", "AMD"],
                }
            ],
        )

        allocation = effective[0].allocations["range_mean_reversion"]
        self.assertTrue(allocation.enabled)
        self.assertTrue(allocation.paper_execution_allowed)
        self.assertEqual(allocation.symbols, ("NVDA", "AMD"))
        self.assertIn("NVDA", effective[0].watchlist)
        self.assertEqual(
            plans[0].allocations["range_mean_reversion"].symbols,
            ("SPY", "QQQ", "AAPL", "MSFT"),
        )

    def test_stale_runtime_override_can_be_ignored_for_service_startup(
        self,
    ) -> None:
        plans = load_account_plans(
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )

        effective = apply_strategy_configuration_overrides(
            plans,
            [
                {
                    "account_id": "alpaca-paper",
                    "strategy_id": "breakout_retest_bull_call",
                    "enabled": True,
                    "paper_execution_allowed": True,
                    "symbols": ["SPY"],
                }
            ],
            strict=False,
        )

        self.assertEqual(effective[0].account_id, "alpaca-paper")
        self.assertNotIn(
            "breakout_retest_bull_call", effective[0].allocations
        )

    def test_breakout_retest_signal_is_deterministic_and_explainable(
        self,
    ) -> None:
        bars = [
            market_bar(
                index,
                close=str(
                    Decimal("99.8")
                    + Decimal(index) / Decimal("100")
                ),
            )
            for index in range(38)
        ]
        bars.extend(
            (
                market_bar(
                    38,
                    open_price="100.8",
                    high="103",
                    low="100.5",
                    close="102",
                    volume="200",
                ),
                market_bar(
                    39,
                    open_price="101.5",
                    high="103.5",
                    low="100.9",
                    close="102.5",
                    volume="140",
                ),
            )
        )
        ordered = tuple(bars)
        evaluated_at = ordered[-1].timestamp + timedelta(minutes=5)
        context = StrategyContext(
            account_id="alpaca-paper",
            bars=ordered,
            features=FeatureEngine().calculate(ordered),
            evaluated_at=evaluated_at,
        )
        strategy = BreakoutRetestStrategy()

        first = strategy.evaluate(context)
        second = strategy.evaluate(context)

        self.assertIsNotNone(first)
        self.assertEqual(first.signal_id, second.signal_id)
        self.assertIn(
            "retest_held_resistance", first.reason_codes
        )
        self.assertLess(first.stop_price, first.reference_price)
        self.assertGreater(first.target_price, first.reference_price)

        inverse = SignalInversionStrategy(
            "breakout_retest_inverse", "1.0.0", "breakout_retest"
        ).evaluate(context)
        self.assertIsNotNone(inverse)
        self.assertIs(inverse.action, SignalAction.ENTER_SHORT)
        self.assertNotEqual(first.signal_id, inverse.signal_id)
        self.assertGreater(inverse.stop_price, inverse.reference_price)
        self.assertLess(inverse.target_price, inverse.reference_price)

    def test_signal_allocation_builds_protected_bracket_order(self) -> None:
        config_path = (
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )
        stock_plan, option_plan = load_account_plans(config_path)
        self.assertEqual(stock_plan.asset_classes, (AssetClass.STOCK,))
        self.assertEqual(option_plan.asset_classes, (AssetClass.OPTION,))
        self.assertNotIn(
            "trend_pullback_bull_put_theta",
            stock_plan.allocations,
        )
        allocation = stock_plan.allocations["breakout_retest"]
        option_allocation = option_plan.allocations[
            "trend_pullback_bull_put_theta"
        ]
        self.assertEqual(
            option_allocation.source_strategy_id,
            "trend_pullback",
        )
        self.assertEqual(
            option_allocation.option_policy.required_trading_level, 3
        )
        self.assertTrue(
            option_allocation.paper_execution_allowed
        )
        bars = [
            market_bar(
                index,
                close=str(
                    Decimal("99.8")
                    + Decimal(index) / Decimal("100")
                ),
            )
            for index in range(38)
        ]
        bars.extend(
            (
                market_bar(
                    38,
                    open_price="100.8",
                    high="103",
                    low="100.5",
                    close="102",
                    volume="200",
                ),
                market_bar(
                    39,
                    open_price="101.5",
                    high="103.5",
                    low="100.9",
                    close="102.5",
                    volume="140",
                ),
            )
        )
        ordered = tuple(bars)
        context = StrategyContext(
            account_id=stock_plan.account_id,
            bars=ordered,
            features=FeatureEngine().calculate(ordered),
            evaluated_at=ordered[-1].timestamp + timedelta(minutes=5),
        )
        signal = BreakoutRetestStrategy().evaluate(context)
        snapshot = AccountSnapshot(
            equity=Decimal("10000"),
            start_of_day_equity=Decimal("10000"),
            peak_equity=Decimal("10000"),
        )

        intent = SignalAllocator().allocate(
            signal, allocation, snapshot
        )
        payload = AlpacaPaperBroker.build_order_payload(
            intent, Decimal("2")
        )

        self.assertEqual(intent.risk_budget_fraction, Decimal("0.005"))
        self.assertEqual(payload["order_class"], "bracket")
        self.assertIn("take_profit", payload)
        self.assertIn("stop_loss", payload)

    def test_multiple_accounts_require_unique_credentials_and_pinned_ids(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1]
            / "config"
            / "paper_portfolio.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8"))
        first = payload["accounts"][0]
        first["expected_broker_account_id"] = "broker-account-a"
        second = json.loads(json.dumps(first))
        second["account_id"] = "alpaca-paper-b"
        second["expected_broker_account_id"] = "broker-account-b"
        payload["accounts"] = [first]
        payload["accounts"].append(second)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError, "unique credential_env_prefix"
            ):
                load_account_plans(path)

            second["credential_env_prefix"] = "ALPACA_FUND_B"
            second["expected_broker_account_id"] = ""
            path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError, "expected_broker_account_id"
            ):
                load_account_plans(path)

            second["expected_broker_account_id"] = "broker-account-b"
            path.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            plans = load_account_plans(path)

        self.assertEqual(len(plans), 2)
        self.assertEqual(
            plans[1].credential_env_prefix, "ALPACA_FUND_B"
        )
