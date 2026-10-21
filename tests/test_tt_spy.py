from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfoNotFoundError

from multitrade.audit import SqliteAuditStore
from multitrade.automation import PaperAutomationService
from multitrade.config import Settings
from multitrade.domain import AccountSnapshot, AssetClass, OptionRight
from multitrade.features import FeatureEngine
from multitrade.option_evidence import evaluate_option_package_path
from multitrade.options import DefinedRiskOptionSelector, OptionSnapshot
from multitrade.portfolio import load_account_plans
from multitrade.risk import RiskEngine, RiskPolicy
from multitrade.strategies import default_equity_strategies, equity_strategy_from_parameters
from multitrade.strategies.base import StrategyContext
from multitrade.strategies.tt_spy import TtSpyPutCreditStrategy
from tests.test_automation import OptionFakeBroker, test_bars
from tests.test_option_evidence import observation, option_bar


NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)  # Monday 11:00 EDT


def plan():
    with patch.dict("os.environ", {"TRADING_ALPACA_OPTIONS_ACCOUNT_UUID": "test-paper"}):
        return load_account_plans(Path("config/tt_spy_paper.json"))[0]


def spy_bars():
    return tuple(replace(bar, symbol="SPY") for bar in test_bars(NOW))


def chain(expiry=None):
    expiry = expiry or (NOW + timedelta(days=45)).date()
    return tuple(
        OptionSnapshot(
            symbol=f"SPY{expiry:%y%m%d}P{int(Decimal(strike) * 1000):08d}",
            underlying="SPY", expiration=expiry, right=OptionRight.PUT,
            strike=Decimal(strike), bid=Decimal(bid), ask=Decimal(ask),
            bid_size=10, ask_size=10, implied_volatility=Decimal("0.20"),
            delta=Decimal(delta), theta=Decimal(theta),
            quote_timestamp=NOW.isoformat(), feed="opra",
        )
        for strike, bid, ask, delta, theta in (
            ("99", "2.00", "2.10", "-0.30", "-0.08"),
            ("94", "0.70", "0.80", "-0.12", "-0.03"),
        )
    )


def intent(contracts=None):
    allocation = plan().allocations["tt_spy_put_credit"]
    return DefinedRiskOptionSelector(allocation.option_policy).build_intent(
        account_id="alpaca-paper-options", strategy_id=allocation.strategy_id,
        underlying="SPY", underlying_price=Decimal("102.50"),
        direction="bullish", chain=contracts if contracts is not None else chain(),
        requested_quantity=Decimal("1000"), risk_budget_fraction=Decimal("0.01"),
        signal_id="tt-test", as_of=NOW.date(),
    )


class TtSpyTests(TestCase):
    def test_registry_roundtrip_and_spy_only_schedule(self):
        strategy = default_equity_strategies()["tt_spy_put_credit"]
        self.assertEqual(equity_strategy_from_parameters(asdict(strategy)), strategy)
        bars = spy_bars()
        context = StrategyContext("paper", bars, FeatureEngine().calculate(bars), NOW)
        self.assertIsNotNone(strategy.evaluate(context))
        self.assertIsNone(strategy.evaluate(replace(context, evaluated_at=NOW + timedelta(days=1))))
        self.assertIsNone(strategy.evaluate(replace(context, evaluated_at=NOW.replace(hour=13))))
        self.assertIsNone(strategy.evaluate(replace(context, bars=test_bars(NOW))))

    def test_winter_schedule_uses_est_not_edt(self):
        bars = spy_bars()
        strategy = TtSpyPutCreditStrategy()
        context = StrategyContext("paper", bars, FeatureEngine().calculate(bars),
                                  datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))
        self.assertIsNone(strategy.evaluate(context))  # 09:30 EST
        self.assertIsNotNone(strategy.evaluate(replace(context, evaluated_at=context.evaluated_at + timedelta(hours=1))))
        with patch("multitrade.strategies.tt_spy.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            self.assertIsNone(strategy.evaluate(context))

    def test_target_expiry_and_exact_width(self):
        package = intent(chain((NOW + timedelta(days=40)).date()) + chain())
        self.assertEqual(package.explanation["days_to_expiration"], 45)
        self.assertEqual(package.explanation["exit_mode"], "expiration_only")
        self.assertEqual(sorted(leg.strike for leg in package.option_legs), [Decimal("94"), Decimal("99")])
        self.assertEqual(RiskEngine().estimate_risk_per_unit(package), Decimal("385"))

    def test_missing_exact_hedge_is_rejected(self):
        contracts = chain()
        wrong_hedge = replace(contracts[1], strike=Decimal("95"), symbol="SPY261203P00095000")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            intent((contracts[0], wrong_hedge))
        with self.assertRaises(ValueError):
            intent((contracts[0],))

    def test_profile_is_frozen_and_submission_requires_opt_in(self):
        allocation = plan().allocations["tt_spy_put_credit"]
        self.assertFalse(allocation.paper_execution_allowed)
        for changes in (
            {"asset_class": AssetClass.STOCK, "option_policy": None},
            {"symbols": ("QQQ",)}, {"risk_fraction": Decimal("0.02")},
            {"minimum_entry_interval_minutes": 0},
            {"option_policy": replace(allocation.option_policy, exit_mode="profit_loss_or_expiration")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(allocation, **changes)

    def test_policy_rejects_invalid_new_selection_fields(self):
        policy = plan().allocations["tt_spy_put_credit"].option_policy
        for changes in ({"target_dte": 55}, {"target_strike_width": None},
                        {"exit_mode": "unknown"}, {"maximum_holding_minutes": 60}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(policy, **changes)

    def test_risk_caps_survive_looser_server_settings_and_wrapper_ids(self):
        engine = RiskEngine(RiskPolicy(max_option_per_trade=Decimal("0.30"),
                                       max_total_open=Decimal("0.50"), max_drawdown=Decimal("0.30")))
        package = replace(intent(), strategy_id="tt-wrapper", risk_budget_fraction=Decimal("0.30"))
        snapshot = AccountSnapshot(Decimal("100000"), Decimal("100000"), Decimal("100000"))
        decision = engine.evaluate(package, snapshot)
        self.assertTrue(decision.approved)
        self.assertLessEqual(decision.reserved_risk, Decimal("1000"))
        self.assertFalse(engine.evaluate(package, replace(snapshot, active_risk=Decimal("10000"))).approved)
        self.assertEqual(engine.evaluate(package, replace(snapshot, equity=Decimal("90000"),
                                                         start_of_day_equity=Decimal("90000"))).reason,
                         "drawdown_kill_switch")

    def test_replay_does_not_invent_profit_or_loss_exits(self):
        candidate = observation()
        candidate["details"]["intent_explanation"].update(
            exit_mode="expiration_only", exit_before_expiry_days=21)
        for prices in (("4", "1"), ("0.1", "3")):
            bars = {
                "AAPL260918C00150000": (option_bar("AAPL260918C00150000", "2026-07-28T15:00:00Z", prices[0]),),
                "AAPL260918C00155000": (option_bar("AAPL260918C00155000", "2026-07-28T15:00:00Z", prices[1]),),
            }
            report = evaluate_option_package_path(candidate, bars, evaluated_at=NOW,
                                                  timeframe="15Min", data_feed="opra", slippage_per_leg=Decimal("0"))
            self.assertIsNone(report["first_policy_exit_reason"])
        expiry_time = "2026-08-28T15:00:00Z"
        bars = {symbol: (replace(items[0], timestamp=datetime.fromisoformat(expiry_time)),)
                for symbol, items in bars.items()}
        report = evaluate_option_package_path(candidate, bars, evaluated_at=NOW,
                                              timeframe="15Min", data_feed="opra", slippage_per_leg=Decimal("0"))
        self.assertEqual(report["first_policy_exit_reason"], "expiration_window")

    def test_paper_cycle_selects_package_and_default_does_not_submit(self):
        for approval in (False, True):
            with self.subTest(approval=approval), TemporaryDirectory() as directory:
                account = plan()
                allocation = replace(account.allocations["tt_spy_put_credit"], paper_execution_allowed=approval)
                account = replace(account, allocations={allocation.strategy_id: allocation})
                settings = replace(Settings.from_env(), automation_enabled=True, enable_paper_orders=True,
                                   emergency_stop=False, db_path=Path(directory) / "paper.db",
                                   strategy_health_path=Path(directory) / "health.json")
                broker = OptionFakeBroker(NOW)
                broker.reconcile = Mock(return_value=replace(broker.reconcile(),
                    account=replace(broker.reconcile().account, equity=Decimal("100000"),
                                    last_equity=Decimal("100000"), cash=Decimal("100000"),
                                    buying_power=Decimal("100000"), options_buying_power=Decimal("100000"),
                                    broker_account_id="test-paper")))
                data = Mock()
                data.request_ids = []
                data.fetch_stock_bars.return_value = {"SPY": spy_bars()}
                option_data = Mock(feed="opra")
                option_data.fetch_chain.return_value = chain()
                service = PaperAutomationService(settings=settings, broker=broker, market_data=data,
                    store=SqliteAuditStore(settings.db_path), account_plan=account, option_data=option_data)
                result = service.run_cycle(now=NOW)
                self.assertEqual(result.orders_submitted, int(approval), result)
                self.assertEqual(broker.submit_calls, int(approval))
                if approval:
                    self.assertLessEqual(broker.submitted[0][1] * Decimal("385"), Decimal("1000"))
                    service.run_cycle(now=NOW)
                    self.assertEqual(broker.submit_calls, 1)

    def test_runtime_expiration_only_closes_both_legs_at_21_dte(self):
        package = intent()
        legs = [{**asdict(leg), "expiration": leg.expiration.isoformat()}
                for leg in package.option_legs]
        # Exercise the actual managed-exit path, including values which would
        # trigger legacy profit/loss exits well before 21 DTE.
        for held_days, opening_price, expected in ((0, "-10", 0), (0, "-0.01", 0), (24, "-1.20", 1)):
            with self.subTest(held_days=held_days, opening_price=opening_price), TemporaryDirectory() as directory:
                checked_at = NOW + timedelta(days=held_days)
                settings = replace(Settings.from_env(), enable_paper_orders=True,
                                   db_path=Path(directory) / "paper.db")
                broker = OptionFakeBroker(checked_at)
                option_data = Mock(feed="opra")
                option_data.fetch_chain.return_value = tuple(
                    replace(contract, quote_timestamp=checked_at.isoformat()) for contract in chain())
                service = PaperAutomationService(settings=settings, broker=broker, market_data=Mock(),
                    store=SqliteAuditStore(settings.db_path), account_plan=plan(), option_data=option_data)
                service.store = Mock()
                service.store.open_option_trades.return_value = [{
                    "intent_id": package.intent_id, "strategy_id": package.strategy_id,
                    "symbol": "SPY", "state": "position_open", "explanation": package.explanation,
                    "option_legs": legs, "approved_quantity": "1", "opening_net_price": opening_price,
                    "created_at": NOW.isoformat(),
                }]
                service.trading_engine = Mock()
                service.trading_engine.process.return_value = Mock(
                    decision=Mock(approved=True), dry_run=False)
                snapshot = AccountSnapshot(Decimal("100000"), Decimal("100000"), Decimal("100000"))
                count = service._manage_option_positions(broker.reconcile(), snapshot, checked_at)
                self.assertEqual(count, expected)
                if expected:
                    closing = service.trading_engine.process.call_args.args[0]
                    self.assertTrue(closing.reduce_only)
                    self.assertEqual(len(closing.option_legs), 2)
                    self.assertEqual(closing.explanation["exit_reason"], "expiration_window")
                else:
                    service.trading_engine.process.assert_not_called()
                self.assertFalse(any(call.args[0] == "option_exit_evaluation_failed"
                                     for call in service.store.record_event.call_args_list))
