from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.brokers.base import (
    BrokerAccount,
    BrokerMarketClock,
    BrokerOpenOrder,
    BrokerPosition,
    BrokerReconciliation,
)
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.risk import RiskEngine


def account() -> BrokerAccount:
    return BrokerAccount(
        status="active",
        currency="USD",
        equity=Decimal("10000"),
        last_equity=Decimal("10000"),
        cash=Decimal("10000"),
        buying_power=Decimal("20000"),
        long_market_value=Decimal("0"),
        short_market_value=Decimal("0"),
        maintenance_margin=Decimal("0"),
        gross_notional=Decimal("0"),
        daytrade_count=0,
        pattern_day_trader=False,
        trading_blocked=False,
        transfers_blocked=False,
        account_blocked=False,
        trade_suspended_by_user=False,
        shorting_enabled=True,
    )


def order(status: str) -> BrokerOpenOrder:
    return BrokerOpenOrder(
        broker_order_id="broker-order-1",
        client_order_id="intent-1",
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side="buy",
        order_type="market",
        order_class="bracket",
        status=status,
        quantity=Decimal("10"),
        filled_quantity=Decimal("10") if status == "filled" else Decimal("0"),
        limit_price=None,
        stop_price=None,
        submitted_at="2026-07-28T14:00:00+00:00",
        legs_count=2,
        filled_average_price=(
            Decimal("100") if status == "filled" else None
        ),
    )


def reconciliation(
    current_order: BrokerOpenOrder,
    *,
    position_open: bool,
) -> BrokerReconciliation:
    observed_at = datetime(2026, 7, 28, 14, 5, tzinfo=timezone.utc)
    positions = (
        (
            BrokerPosition(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                side="long",
                quantity=Decimal("10"),
                market_value=Decimal("1000"),
                cost_basis=Decimal("1000"),
                average_entry_price=Decimal("100"),
                current_price=Decimal("100"),
                unrealized_pl=Decimal("0"),
                unrealized_pl_percent=Decimal("0"),
            ),
        )
        if position_open
        else ()
    )
    return BrokerReconciliation(
        broker="alpaca",
        environment="paper",
        observed_at=observed_at,
        account=account(),
        market=BrokerMarketClock(
            timestamp=observed_at.isoformat(),
            is_open=True,
            next_open=observed_at.isoformat(),
            next_close=observed_at.isoformat(),
        ),
        positions=positions,
        open_orders=(),
        recent_orders=(current_order,),
    )


class OrderLifecycleTests(TestCase):
    def test_peak_equity_is_persisted_for_drawdown_guard(self) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteAuditStore(Path(directory) / "audit.db")
            base = AccountSnapshot(
                equity=Decimal("10000"),
                start_of_day_equity=Decimal("10000"),
                peak_equity=Decimal("10000"),
            )
            first = store.apply_account_equity_state(
                "alpaca-paper",
                base,
                datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
            )
            high = store.apply_account_equity_state(
                "alpaca-paper",
                AccountSnapshot(
                    equity=Decimal("11000"),
                    start_of_day_equity=Decimal("10000"),
                    peak_equity=Decimal("11000"),
                ),
                datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
            )
            pullback = store.apply_account_equity_state(
                "alpaca-paper",
                base,
                datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(first.peak_equity, Decimal("10000"))
            self.assertEqual(high.peak_equity, Decimal("11000"))
            self.assertEqual(pullback.peak_equity, Decimal("11000"))

    def test_filled_entry_keeps_risk_until_position_disappears(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "audit.db"
            store = SqliteAuditStore(db_path)
            reader = SqliteAuditReader(db_path)
            intent = TradeIntent(
                intent_id="intent-1",
                strategy_id="breakout_retest",
                asset_class=AssetClass.STOCK,
                symbol="AAPL",
                side=Side.BUY,
                requested_quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                reference_price=Decimal("100"),
                stop_price=Decimal("95"),
            )
            decision = store.evaluate_and_reserve(
                RiskEngine(),
                intent,
                AccountSnapshot(
                    equity=Decimal("10000"),
                    start_of_day_equity=Decimal("10000"),
                    peak_equity=Decimal("10000"),
                ),
            )
            self.assertTrue(decision.approved)
            store.mark_submitted("intent-1", "broker-order-1")

            store.record_order_reconciliation(
                "alpaca-paper",
                reconciliation(order("filled"), position_open=True),
            )
            self.assertGreater(store.active_risk(), Decimal("0"))
            self.assertIn("open", reader.reservation_summary())

            store.record_order_reconciliation(
                "alpaca-paper",
                reconciliation(order("filled"), position_open=False),
            )
            self.assertGreater(store.active_risk(), Decimal("0"))
            self.assertIn("closing_pending", reader.reservation_summary())

            store.record_order_reconciliation(
                "alpaca-paper",
                reconciliation(order("filled"), position_open=False),
            )
            self.assertEqual(store.active_risk(), Decimal("0"))
            self.assertIn("released", reader.reservation_summary())

            store.record_order_reconciliation(
                "alpaca-paper",
                reconciliation(order("filled"), position_open=True),
            )
            self.assertEqual(store.active_risk(), Decimal("0"))

    def test_canceled_opening_order_releases_risk(self) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteAuditStore(Path(directory) / "audit.db")
            intent = TradeIntent(
                intent_id="intent-1",
                strategy_id="breakout_retest",
                asset_class=AssetClass.STOCK,
                symbol="AAPL",
                side=Side.BUY,
                requested_quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                reference_price=Decimal("100"),
                stop_price=Decimal("95"),
            )
            store.evaluate_and_reserve(
                RiskEngine(),
                intent,
                AccountSnapshot(
                    equity=Decimal("10000"),
                    start_of_day_equity=Decimal("10000"),
                    peak_equity=Decimal("10000"),
                ),
            )
            store.mark_submitted("intent-1", "broker-order-1")

            store.record_order_reconciliation(
                "alpaca-paper",
                reconciliation(order("canceled"), position_open=False),
            )

            self.assertEqual(store.active_risk(), Decimal("0"))
