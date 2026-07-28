from datetime import date
from decimal import Decimal
from unittest import TestCase

from multitrade.audit import SqliteAuditStore
from multitrade.brokers.base import BrokerOrder
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OptionLeg,
    OptionRight,
    OrderType,
    RiskDecision,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.engine import TradingEngine
from multitrade.risk import RiskEngine


class FakeBroker:
    def __init__(self) -> None:
        self.submissions = 0

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity=Decimal("100000"),
            start_of_day_equity=Decimal("100000"),
            peak_equity=Decimal("100000"),
        )

    def submit_order(self, intent, approved_quantity):
        self.submissions += 1
        raise AssertionError("Dry-run must not submit")


class AcceptingBroker(FakeBroker):
    def submit_order(self, intent, approved_quantity):
        del intent, approved_quantity
        self.submissions += 1
        return BrokerOrder(
            broker_order_id="closing-order",
            status="accepted",
            raw={},
        )


class ExitAudit:
    def __init__(self) -> None:
        self.exit_submitted = False

    def evaluate_and_reserve(self, risk_engine, intent, snapshot):
        del risk_engine, snapshot
        return RiskDecision(
            approved=True,
            intent_id=intent.intent_id,
            reason="reduce_only_approved",
            approved_quantity=Decimal("1"),
            risk_per_unit=Decimal("0"),
            reserved_risk=Decimal("0"),
            projected_active_risk=Decimal("0"),
        )

    def record_exit_submitted(self, intent, broker_order_id):
        del intent, broker_order_id
        self.exit_submitted = True


def make_intent() -> TradeIntent:
    return TradeIntent(
        strategy_id="engine-test",
        asset_class=AssetClass.STOCK,
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        reference_price=Decimal("200"),
        stop_price=Decimal("190"),
        limit_price=Decimal("200"),
    )


class TradingEngineTests(TestCase):
    def test_reduce_only_protection_can_run_during_entry_stop(
        self,
    ) -> None:
        broker = AcceptingBroker()
        audit = ExitAudit()
        engine = TradingEngine(
            broker=broker,
            risk_engine=RiskEngine(),
            audit_store=audit,
            enable_order_submission=False,
            enable_reduce_only_submission=True,
        )
        expiration = date(2027, 1, 15)
        intent = TradeIntent(
            strategy_id="option-exit",
            asset_class=AssetClass.OPTION,
            symbol="AAPL",
            side=Side.SELL,
            requested_quantity=Decimal("1"),
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            reference_price=Decimal("1"),
            limit_price=Decimal("-0.50"),
            option_legs=(
                OptionLeg(
                    symbol="AAPL270115C00100000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.CALL,
                    strike=Decimal("100"),
                    side=Side.SELL,
                    ratio=1,
                    mark_price=Decimal("1"),
                ),
            ),
            reduce_only=True,
            parent_intent_id="opening-intent",
        )

        result = engine.process(intent)

        self.assertFalse(result.dry_run)
        self.assertEqual(broker.submissions, 1)
        self.assertTrue(audit.exit_submitted)

    def test_dry_run_releases_reservation_and_never_submits(self) -> None:
        broker = FakeBroker()
        store = SqliteAuditStore(":memory:")
        engine = TradingEngine(
            broker=broker,
            risk_engine=RiskEngine(),
            audit_store=store,
            enable_order_submission=False,
        )

        result = engine.process(make_intent())

        self.assertTrue(result.decision.approved)
        self.assertTrue(result.dry_run)
        self.assertEqual(broker.submissions, 0)
        self.assertEqual(store.active_risk(), Decimal("0"))
        event_types = {
            event["event_type"] for event in store.recent_events()
        }
        self.assertIn("risk_approved", event_types)
        self.assertIn("dry_run", event_types)
        self.assertIn("risk_released", event_types)

    def test_duplicate_active_intent_is_rejected(self) -> None:
        store = SqliteAuditStore(":memory:")
        intent = make_intent()
        risk_engine = RiskEngine()
        snapshot = FakeBroker().get_account_snapshot()

        first = store.evaluate_and_reserve(
            risk_engine, intent, snapshot
        )
        second = store.evaluate_and_reserve(
            risk_engine, intent, snapshot
        )

        self.assertTrue(first.approved)
        self.assertFalse(second.approved)
        self.assertEqual(second.reason, "duplicate_intent_reserved")

    def test_strategy_approval_is_required_even_when_global_gate_is_on(
        self,
    ) -> None:
        broker = FakeBroker()
        store = SqliteAuditStore(":memory:")
        engine = TradingEngine(
            broker=broker,
            risk_engine=RiskEngine(),
            audit_store=store,
            enable_order_submission=True,
        )

        result = engine.process(
            make_intent(), allow_submission=False
        )

        self.assertTrue(result.decision.approved)
        self.assertTrue(result.dry_run)
        self.assertEqual(broker.submissions, 0)
        dry_run = next(
            event
            for event in store.recent_events()
            if event["event_type"] == "dry_run"
        )
        self.assertEqual(
            dry_run["payload"]["reason"],
            "strategy_paper_execution_not_approved",
        )
