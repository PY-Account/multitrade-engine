from decimal import Decimal
from unittest import TestCase

from multitrade.audit import SqliteAuditStore
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OrderType,
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
