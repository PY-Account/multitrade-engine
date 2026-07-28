from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
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
    BrokerOrder,
)
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OptionLeg,
    OptionRight,
    OrderType,
    Side,
    TimeInForce,
    TradeIntent,
)
from multitrade.engine import EngineResult
from multitrade.risk import FirmRiskPolicy, RiskEngine
from multitrade.strategies.base import SignalAction, StrategySignal


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
    def test_existing_database_adds_account_and_option_ledger_columns(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE risk_reservations (
                        intent_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        risk_amount TEXT NOT NULL,
                        quantity TEXT NOT NULL,
                        state TEXT NOT NULL,
                        broker_order_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE trade_records (
                        signal_id TEXT PRIMARY KEY,
                        intent_id TEXT NOT NULL UNIQUE,
                        account_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        state TEXT NOT NULL,
                        requested_quantity TEXT NOT NULL,
                        approved_quantity TEXT NOT NULL,
                        reserved_risk TEXT NOT NULL,
                        reference_price TEXT,
                        stop_price TEXT,
                        target_price TEXT,
                        broker_order_id TEXT,
                        entry_price TEXT,
                        exit_price TEXT,
                        realized_pnl TEXT,
                        exit_reason TEXT,
                        closed_at TEXT,
                        explanation_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
            connection.close()

            SqliteAuditStore(db_path).close()

            with sqlite3.connect(db_path) as connection:
                risk_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(risk_reservations)"
                    )
                }
                trade_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(trade_records)"
                    )
                }
                exit_table = connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'trade_exit_orders'
                    """
                ).fetchone()
            connection.close()

            self.assertTrue(
                {
                    "account_id",
                    "asset_class",
                    "instruments_json",
                    "risk_group",
                }.issubset(risk_columns)
            )
            self.assertTrue(
                {
                    "asset_class",
                    "structure",
                    "option_legs_json",
                    "opening_net_price",
                    "modeled_theta_per_day",
                }.issubset(trade_columns)
            )
            self.assertIsNotNone(exit_table)

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

    def test_risk_reservations_are_scoped_per_account(self) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteAuditStore(Path(directory) / "audit.db")
            snapshot = AccountSnapshot(
                equity=Decimal("10000"),
                start_of_day_equity=Decimal("10000"),
                peak_equity=Decimal("10000"),
            )
            for account_id, intent_id in (
                ("paper-a", "intent-a"),
                ("paper-b", "intent-b"),
            ):
                decision = store.evaluate_and_reserve(
                    RiskEngine(),
                    TradeIntent(
                        account_id=account_id,
                        intent_id=intent_id,
                        strategy_id="breakout_retest",
                        asset_class=AssetClass.STOCK,
                        symbol="AAPL",
                        side=Side.BUY,
                        requested_quantity=Decimal("1"),
                        order_type=OrderType.MARKET,
                        time_in_force=TimeInForce.DAY,
                        reference_price=Decimal("100"),
                        stop_price=Decimal("95"),
                    ),
                    snapshot,
                )
                self.assertTrue(decision.approved)

            self.assertEqual(
                store.active_risk(),
                store.active_risk("paper-a")
                + store.active_risk("paper-b"),
            )
            self.assertGreater(
                store.active_risk("paper-a"), Decimal("0")
            )

    def test_option_exit_fill_records_realized_package_pnl(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "audit.db"
            store = SqliteAuditStore(db_path)
            reader = SqliteAuditReader(db_path)
            expiration = date(2026, 9, 18)
            opening_legs = (
                OptionLeg(
                    symbol="AAPL260918P00150000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.PUT,
                    strike=Decimal("150"),
                    side=Side.SELL,
                    ratio=1,
                    mark_price=Decimal("1.20"),
                    theta=Decimal("-0.08"),
                ),
                OptionLeg(
                    symbol="AAPL260918P00145000",
                    underlying="AAPL",
                    expiration=expiration,
                    right=OptionRight.PUT,
                    strike=Decimal("145"),
                    side=Side.BUY,
                    ratio=1,
                    mark_price=Decimal("0.40"),
                    theta=Decimal("-0.02"),
                ),
            )
            intent = TradeIntent(
                account_id="alpaca-paper",
                intent_id="option-entry-1",
                signal_id="option-entry-1",
                strategy_id="trend_pullback_bull_put_theta",
                asset_class=AssetClass.OPTION,
                symbol="AAPL",
                side=Side.SELL,
                requested_quantity=Decimal("1"),
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY,
                limit_price=Decimal("-0.80"),
                option_legs=opening_legs,
                explanation={
                    "structure": "bull_put_credit_spread",
                    "opening_net_price": Decimal("-0.80"),
                    "modeled_theta_per_day_per_package": Decimal("6"),
                    "expiration": expiration.isoformat(),
                },
            )
            decision = store.evaluate_and_reserve(
                RiskEngine(),
                intent,
                AccountSnapshot(
                    equity=Decimal("100000"),
                    start_of_day_equity=Decimal("100000"),
                    peak_equity=Decimal("100000"),
                ),
            )
            self.assertTrue(decision.approved)
            store.mark_submitted(
                intent.intent_id, "option-entry-order"
            )
            now = datetime(
                2026, 7, 28, 14, 0, tzinfo=timezone.utc
            )
            signal = StrategySignal(
                signal_id="option-entry-1",
                account_id="alpaca-paper",
                strategy_id=intent.strategy_id,
                strategy_version="1.0.0+option",
                symbol="AAPL",
                action=SignalAction.ENTER_LONG,
                bar_timestamp=now - timedelta(minutes=5),
                created_at=now,
                expires_at=now + timedelta(minutes=10),
                confidence=Decimal("0.70"),
                reference_price=Decimal("155"),
                stop_price=Decimal("150"),
                target_price=Decimal("165"),
                reason_codes=("test",),
                evidence={},
            )
            store.record_trade_result(
                signal,
                intent,
                EngineResult(
                    decision=decision,
                    order=BrokerOrder(
                        broker_order_id="option-entry-order",
                        status="accepted",
                        raw={},
                    ),
                ),
            )

            closing_legs = tuple(
                OptionLeg(
                    symbol=leg.symbol,
                    underlying=leg.underlying,
                    expiration=leg.expiration,
                    right=leg.right,
                    strike=leg.strike,
                    side=(
                        Side.BUY
                        if leg.side is Side.SELL
                        else Side.SELL
                    ),
                    ratio=leg.ratio,
                    mark_price=Decimal("0.30"),
                )
                for leg in opening_legs
            )
            exit_intent = TradeIntent(
                account_id="alpaca-paper",
                parent_intent_id=intent.intent_id,
                intent_id="option-exit-1",
                strategy_id=intent.strategy_id,
                asset_class=AssetClass.OPTION,
                symbol="AAPL",
                side=Side.BUY,
                requested_quantity=Decimal("1"),
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY,
                limit_price=Decimal("0.30"),
                option_legs=closing_legs,
                reduce_only=True,
            )
            store.record_exit_submitted(
                exit_intent, "option-exit-order"
            )
            exit_order = BrokerOpenOrder(
                broker_order_id="option-exit-order",
                client_order_id="option-exit-1",
                symbol="MULTI-LEG",
                asset_class=AssetClass.OPTION,
                side="",
                order_type="limit",
                order_class="mleg",
                status="filled",
                quantity=Decimal("1"),
                filled_quantity=Decimal("1"),
                limit_price=Decimal("0.30"),
                stop_price=None,
                submitted_at=now.isoformat(),
                legs_count=2,
                filled_average_price=Decimal("0.30"),
                filled_at=(
                    now + timedelta(minutes=1)
                ).isoformat(),
            )
            store.record_order_reconciliation(
                "alpaca-paper",
                BrokerReconciliation(
                    broker="alpaca",
                    environment="paper",
                    observed_at=now + timedelta(minutes=1),
                    account=account(),
                    market=BrokerMarketClock(
                        timestamp=now.isoformat(),
                        is_open=True,
                        next_open=now.isoformat(),
                        next_close=now.isoformat(),
                    ),
                    positions=(),
                    open_orders=(),
                    recent_orders=(exit_order,),
                ),
            )

            trade = reader.recent_trade_records()[0]
            statistics = reader.strategy_performance(
                "alpaca-paper"
            )[0]
            self.assertEqual(trade["state"], "position_closed")
            self.assertEqual(trade["realized_pnl"], "50.00")
            self.assertEqual(
                statistics["option_realized_pnl"], "50.00"
            )
            self.assertEqual(
                statistics[
                    "positive_theta_trade_realized_pnl"
                ],
                "50.00",
            )
            self.assertEqual(statistics["closed_trade_count"], 1)
            self.assertEqual(
                statistics["theta_attribution"],
                "decision_time_model_not_realized_profit",
            )

    def test_firm_symbol_limit_is_atomic_across_accounts(self) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteAuditStore(Path(directory) / "audit.db")
            now = datetime.now(timezone.utc)
            snapshot = AccountSnapshot(
                equity=Decimal("100000"),
                start_of_day_equity=Decimal("100000"),
                peak_equity=Decimal("100000"),
            )
            store.apply_account_equity_state(
                "paper-a", snapshot, now
            )
            store.apply_account_equity_state(
                "paper-b", snapshot, now
            )
            policy = FirmRiskPolicy()

            decisions = []
            for index, account_id in enumerate(
                ("paper-a", "paper-b", "paper-b", "paper-a"),
                start=1,
            ):
                decisions.append(
                    store.evaluate_and_reserve(
                        RiskEngine(),
                        TradeIntent(
                            account_id=account_id,
                            intent_id=f"firm-symbol-{index}",
                            strategy_id=f"strategy-{index}",
                            asset_class=AssetClass.STOCK,
                            symbol="AAPL",
                            side=Side.BUY,
                            requested_quantity=Decimal("10000"),
                            order_type=OrderType.MARKET,
                            time_in_force=TimeInForce.DAY,
                            reference_price=Decimal("100"),
                            stop_price=Decimal("90"),
                        ),
                        snapshot,
                        firm_policy=policy,
                    )
                )

            self.assertTrue(all(row.approved for row in decisions[:3]))
            self.assertLess(
                decisions[2].approved_quantity,
                decisions[1].approved_quantity,
            )
            self.assertFalse(decisions[3].approved)
            self.assertEqual(
                decisions[3].reason,
                "firm_symbol_risk_budget_exhausted",
            )
            self.assertLessEqual(
                store.active_risk(), Decimal("6000")
            )

    def test_firm_total_limit_caps_cross_account_quantity(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.db"
            store = SqliteAuditStore(path)
            now = datetime.now(timezone.utc)
            snapshot = AccountSnapshot(
                equity=Decimal("100000"),
                start_of_day_equity=Decimal("100000"),
                peak_equity=Decimal("100000"),
            )
            for account_id in ("paper-a", "paper-b"):
                store.apply_account_equity_state(
                    account_id, snapshot, now
                )
            policy = FirmRiskPolicy(
                max_total_open=Decimal("0.03"),
                max_symbol_open=Decimal("0.03"),
                max_strategy_open=Decimal("0.03"),
            )

            decisions = []
            for index, (account_id, symbol) in enumerate(
                (
                    ("paper-a", "AAPL"),
                    ("paper-b", "MSFT"),
                    ("paper-a", "NVDA"),
                    ("paper-b", "AMZN"),
                ),
                start=1,
            ):
                decisions.append(
                    store.evaluate_and_reserve(
                        RiskEngine(),
                        TradeIntent(
                            account_id=account_id,
                            intent_id=f"firm-total-{index}",
                            strategy_id=f"strategy-{index}",
                            asset_class=AssetClass.STOCK,
                            symbol=symbol,
                            side=Side.BUY,
                            requested_quantity=Decimal("10000"),
                            order_type=OrderType.MARKET,
                            time_in_force=TimeInForce.DAY,
                            reference_price=Decimal("100"),
                            stop_price=Decimal("90"),
                        ),
                        snapshot,
                        firm_policy=policy,
                    )
                )

            self.assertTrue(all(row.approved for row in decisions[:3]))
            self.assertIn(
                "firm_total", decisions[2].reason
            )
            self.assertFalse(decisions[3].approved)
            self.assertEqual(
                decisions[3].reason,
                "firm_total_risk_budget_exhausted",
            )
            summary = SqliteAuditReader(path).firm_risk_summary(
                policy
            )
            self.assertLessEqual(
                Decimal(summary["active_risk"]),
                Decimal(summary["total_capacity"]),
            )

    def test_option_wrappers_share_source_strategy_firm_limit(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            store = SqliteAuditStore(Path(directory) / "audit.db")
            now = datetime.now(timezone.utc)
            snapshot = AccountSnapshot(
                equity=Decimal("100000"),
                start_of_day_equity=Decimal("100000"),
                peak_equity=Decimal("100000"),
            )
            for account_id in ("paper-a", "paper-b"):
                store.apply_account_equity_state(
                    account_id, snapshot, now
                )
            policy = FirmRiskPolicy(
                max_total_open=Decimal("0.10"),
                max_symbol_open=Decimal("0.10"),
                max_strategy_open=Decimal("0.03"),
            )
            decisions = []
            for index, (account_id, symbol) in enumerate(
                (
                    ("paper-a", "AAPL"),
                    ("paper-b", "MSFT"),
                    ("paper-a", "NVDA"),
                    ("paper-b", "AMZN"),
                ),
                start=1,
            ):
                decisions.append(
                    store.evaluate_and_reserve(
                        RiskEngine(),
                        TradeIntent(
                            account_id=account_id,
                            intent_id=f"firm-source-{index}",
                            strategy_id=f"wrapper-{index}",
                            asset_class=AssetClass.STOCK,
                            symbol=symbol,
                            side=Side.BUY,
                            requested_quantity=Decimal("10000"),
                            order_type=OrderType.MARKET,
                            time_in_force=TimeInForce.DAY,
                            reference_price=Decimal("100"),
                            stop_price=Decimal("90"),
                            explanation={
                                "source_strategy_id": "breakout_retest"
                            },
                        ),
                        snapshot,
                        firm_policy=policy,
                    )
                )

            self.assertTrue(all(row.approved for row in decisions[:3]))
            self.assertIn(
                "firm_strategy", decisions[2].reason
            )
            self.assertFalse(decisions[3].approved)
            self.assertEqual(
                decisions[3].reason,
                "firm_strategy_risk_budget_exhausted",
            )
