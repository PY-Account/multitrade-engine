from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from multitrade.domain import (
    AccountSnapshot,
    RiskDecision,
    TradeIntent,
    ZERO,
)
from multitrade.risk import RiskEngine

if TYPE_CHECKING:
    from multitrade.brokers.base import BrokerReconciliation
    from multitrade.engine import EngineResult
    from multitrade.market import MarketBar
    from multitrade.research import MarketModelDecision
    from multitrade.research_validation import (
        PortfolioRiskReport,
        ResearchBacktestReport,
    )
    from multitrade.strategy_lab import StrategyLabReport
    from multitrade.strategies.base import StrategySignal
    from multitrade.universe import AssetUniverseReport


ACTIVE_RESERVATION_STATES = (
    "reserved",
    "submitted",
    "open",
    "closing_pending",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
    )


class SqliteAuditStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = self._new_connection()
        self._initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=10, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._memory_connection or self._new_connection()

    def _close_if_needed(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS risk_reservations (
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

                CREATE INDEX IF NOT EXISTS idx_risk_reservation_state
                ON risk_reservations(state);

                CREATE TABLE IF NOT EXISTS latest_broker_state (
                    connection_id TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_bars (
                    asset_class TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    adjustment TEXT NOT NULL DEFAULT 'raw',
                    bar_timestamp TEXT NOT NULL,
                    open_price TEXT NOT NULL,
                    high_price TEXT NOT NULL,
                    low_price TEXT NOT NULL,
                    close_price TEXT NOT NULL,
                    volume TEXT NOT NULL,
                    trade_count INTEGER NOT NULL,
                    vwap TEXT,
                    ingested_at TEXT NOT NULL,
                    PRIMARY KEY (
                        asset_class, symbol, timeframe, feed, bar_timestamp
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_time
                ON market_bars(symbol, timeframe, bar_timestamp DESC);

                CREATE TABLE IF NOT EXISTS strategy_signals (
                    signal_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    bar_timestamp TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    reference_price TEXT NOT NULL,
                    stop_price TEXT NOT NULL,
                    target_price TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    status_details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_signals_recent
                ON strategy_signals(created_at DESC);

                CREATE TABLE IF NOT EXISTS strategy_runtime (
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state TEXT NOT NULL,
                    bar_timestamp TEXT,
                    details_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, strategy_id, symbol)
                );

                CREATE TABLE IF NOT EXISTS trade_records (
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

                CREATE INDEX IF NOT EXISTS idx_trade_records_recent
                ON trade_records(created_at DESC);

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    trades_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS broker_order_snapshots (
                    broker_order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    order_class TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    filled_quantity TEXT NOT NULL,
                    filled_average_price TEXT,
                    submitted_at TEXT,
                    filled_at TEXT,
                    canceled_at TEXT,
                    expired_at TEXT,
                    has_active_legs INTEGER NOT NULL,
                    exit_leg_type TEXT,
                    exit_filled_average_price TEXT,
                    exit_filled_at TEXT,
                    observed_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_broker_orders_client
                ON broker_order_snapshots(client_order_id);

                CREATE INDEX IF NOT EXISTS idx_broker_orders_recent
                ON broker_order_snapshots(observed_at DESC);

                CREATE TABLE IF NOT EXISTS strategy_validations (
                    validation_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    gates_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    in_sample_run_id TEXT NOT NULL,
                    out_of_sample_run_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_validation_recent
                ON strategy_validations(completed_at DESC);

                CREATE TABLE IF NOT EXISTS account_equity_state (
                    account_id TEXT PRIMARY KEY,
                    trading_day TEXT NOT NULL,
                    start_of_day_equity TEXT NOT NULL,
                    peak_equity TEXT NOT NULL,
                    latest_equity TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_decisions (
                    decision_key TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    bar_timestamp TEXT,
                    evaluated_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    score TEXT NOT NULL,
                    target_risk_multiplier TEXT NOT NULL,
                    execution_eligible INTEGER NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    components_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_research_decisions_recent
                ON research_decisions(evaluated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_research_decisions_symbol
                ON research_decisions(model_id, symbol, evaluated_at DESC);

                CREATE TABLE IF NOT EXISTS research_backtest_reports (
                    report_id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    benchmark TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    gates_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    promotion_status TEXT NOT NULL,
                    execution_eligible INTEGER NOT NULL,
                    points_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_research_backtest_recent
                ON research_backtest_reports(completed_at DESC);

                CREATE TABLE IF NOT EXISTS portfolio_risk_reports (
                    report_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    lookback_days INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    symbols_requested_json TEXT NOT NULL,
                    symbols_included_json TEXT NOT NULL,
                    missing_symbols_json TEXT NOT NULL,
                    average_positive_correlation TEXT NOT NULL,
                    maximum_positive_correlation TEXT NOT NULL,
                    effective_breadth TEXT NOT NULL,
                    high_correlation_pairs_json TEXT NOT NULL,
                    all_pairs_json TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    execution_eligible INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_risk_recent
                ON portfolio_risk_reports(evaluated_at DESC);

                CREATE TABLE IF NOT EXISTS strategy_lab_reports (
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
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_lab_recent
                ON strategy_lab_reports(evaluated_at DESC);

                CREATE TABLE IF NOT EXISTS asset_universe_reports (
                    report_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    candidates_requested_json TEXT NOT NULL,
                    recommendations_json TEXT NOT NULL,
                    evaluations_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    execution_eligible INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_asset_universe_recent
                ON asset_universe_reports(evaluated_at DESC, policy_id);
                """
            )
            self._ensure_column(
                connection,
                "market_bars",
                "adjustment",
                "TEXT NOT NULL DEFAULT 'raw'",
            )
            self._ensure_column(
                connection, "trade_records", "entry_price", "TEXT"
            )
            self._ensure_column(
                connection, "trade_records", "exit_price", "TEXT"
            )
            self._ensure_column(
                connection, "trade_records", "realized_pnl", "TEXT"
            )
            self._ensure_column(
                connection, "trade_records", "exit_reason", "TEXT"
            )
            self._ensure_column(
                connection, "trade_records", "closed_at", "TEXT"
            )
            self._ensure_column(
                connection,
                "broker_order_snapshots",
                "exit_leg_type",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "broker_order_snapshots",
                "exit_filled_average_price",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "broker_order_snapshots",
                "exit_filled_at",
                "TEXT",
            )
        finally:
            self._close_if_needed(connection)

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                f"{column} {declaration}"
            )

    def evaluate_and_reserve(
        self,
        risk_engine: RiskEngine,
        intent: TradeIntent,
        snapshot: AccountSnapshot,
    ) -> RiskDecision:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT state FROM risk_reservations WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
            if duplicate is not None:
                decision = RiskDecision(
                    approved=False,
                    intent_id=intent.intent_id,
                    reason=f"duplicate_intent_{duplicate['state']}",
                    projected_active_risk=self._active_risk(connection),
                )
                self._insert_event(
                    connection,
                    "risk_rejected",
                    intent.intent_id,
                    {"intent": asdict(intent), "decision": asdict(decision)},
                )
                connection.execute("COMMIT")
                return decision

            active_risk = self._active_risk(connection)
            decision = risk_engine.evaluate(
                intent, replace(snapshot, active_risk=active_risk)
            )
            event_type = (
                "risk_approved" if decision.approved else "risk_rejected"
            )
            self._insert_event(
                connection,
                event_type,
                intent.intent_id,
                {"intent": asdict(intent), "decision": asdict(decision)},
            )
            if decision.approved:
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO risk_reservations (
                        intent_id, strategy_id, symbol, risk_amount,
                        quantity, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?)
                    """,
                    (
                        intent.intent_id,
                        intent.strategy_id,
                        intent.symbol,
                        format(decision.reserved_risk, "f"),
                        format(decision.approved_quantity, "f"),
                        now,
                        now,
                    ),
                )
            connection.execute("COMMIT")
            return decision
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def mark_submitted(
        self, intent_id: str, broker_order_id: str
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE risk_reservations
                SET state = 'submitted', broker_order_id = ?, updated_at = ?
                WHERE intent_id = ? AND state = 'reserved'
                """,
                (
                    broker_order_id,
                    datetime.now(timezone.utc).isoformat(),
                    intent_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "Reservation was not found or was not reservable"
                )
            self._insert_event(
                connection,
                "order_submitted",
                intent_id,
                {"broker_order_id": broker_order_id},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def release(self, intent_id: str, reason: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE risk_reservations
                SET state = 'released', updated_at = ?
                WHERE intent_id = ?
                  AND state IN (
                      'reserved', 'submitted', 'open', 'closing_pending'
                  )
                """,
                (datetime.now(timezone.utc).isoformat(), intent_id),
            )
            self._insert_event(
                connection,
                "risk_released",
                intent_id,
                {"reason": reason},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_order_reconciliation(
        self,
        account_id: str,
        reconciliation: "BrokerReconciliation",
    ) -> None:
        """Persist broker order state and release risk only on safe evidence.

        A filled entry remains risk-active while the reconciled symbol has a
        position. A previously-open reservation is released only after that
        position disappears, or when the opening order is terminal without a
        fill. Unknown/missing broker rows never cause a release.
        """
        terminal_without_position = {
            "canceled",
            "expired",
            "rejected",
            "replaced",
            "done_for_day",
        }
        active_states = {
            "accepted",
            "new",
            "pending_new",
            "partially_filled",
            "held",
            "pending_cancel",
            "pending_replace",
            "accepted_for_bidding",
            "stopped",
            "suspended",
            "calculated",
        }
        position_symbols = {
            position.symbol for position in reconciliation.positions
        }
        observed_at = reconciliation.observed_at.isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for order in reconciliation.recent_orders:
                connection.execute(
                    """
                    INSERT INTO broker_order_snapshots (
                        broker_order_id, account_id, client_order_id,
                        symbol, asset_class, side, order_type, order_class,
                        status, quantity, filled_quantity,
                        filled_average_price, submitted_at, filled_at,
                        canceled_at, expired_at, has_active_legs,
                        exit_leg_type, exit_filled_average_price,
                        exit_filled_at, observed_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(broker_order_id) DO UPDATE SET
                        status = excluded.status,
                        filled_quantity = excluded.filled_quantity,
                        filled_average_price =
                            excluded.filled_average_price,
                        filled_at = excluded.filled_at,
                        canceled_at = excluded.canceled_at,
                        expired_at = excluded.expired_at,
                        has_active_legs = excluded.has_active_legs,
                        exit_leg_type = excluded.exit_leg_type,
                        exit_filled_average_price =
                            excluded.exit_filled_average_price,
                        exit_filled_at = excluded.exit_filled_at,
                        observed_at = excluded.observed_at,
                        raw_json = excluded.raw_json
                    """,
                    (
                        order.broker_order_id,
                        account_id,
                        order.client_order_id,
                        order.symbol,
                        order.asset_class.value,
                        order.side,
                        order.order_type,
                        order.order_class,
                        order.status,
                        format(order.quantity, "f"),
                        format(order.filled_quantity, "f"),
                        (
                            format(order.filled_average_price, "f")
                            if order.filled_average_price is not None
                            else None
                        ),
                        order.submitted_at,
                        order.filled_at,
                        order.canceled_at,
                        order.expired_at,
                        int(order.has_active_legs),
                        order.exit_leg_type,
                        (
                            format(
                                order.exit_filled_average_price, "f"
                            )
                            if order.exit_filled_average_price is not None
                            else None
                        ),
                        order.exit_filled_at,
                        observed_at,
                        _json(asdict(order)),
                    ),
                )
                if not order.client_order_id:
                    continue
                reservation = connection.execute(
                    """
                    SELECT state, symbol FROM risk_reservations
                    WHERE intent_id = ?
                    """,
                    (order.client_order_id,),
                ).fetchone()
                if reservation is None:
                    continue

                previous_state = reservation["state"]
                if previous_state == "released":
                    continue
                symbol = reservation["symbol"]
                has_position = symbol in position_symbols
                is_active = (
                    order.status in active_states or order.has_active_legs
                )
                reservation_state = previous_state
                trade_state: str | None = None
                release_reason: str | None = None
                if has_position or is_active:
                    reservation_state = "open"
                    trade_state = (
                        "position_open"
                        if has_position
                        else f"broker_{order.status}"
                    )
                elif order.status in terminal_without_position:
                    reservation_state = "released"
                    trade_state = order.status
                    release_reason = f"broker_order_{order.status}"
                elif (
                    order.status == "filled"
                    and previous_state == "closing_pending"
                ):
                    reservation_state = "released"
                    trade_state = "position_closed"
                    release_reason = "reconciled_position_closed"
                elif (
                    order.status == "filled"
                    and previous_state == "open"
                ):
                    reservation_state = "closing_pending"
                    trade_state = "position_close_pending"
                elif order.status == "filled":
                    trade_state = "broker_filled"

                if reservation_state != previous_state:
                    connection.execute(
                        """
                        UPDATE risk_reservations
                        SET state = ?, updated_at = ?
                        WHERE intent_id = ?
                        """,
                        (
                            reservation_state,
                            observed_at,
                            order.client_order_id,
                        ),
                    )
                    self._insert_event(
                        connection,
                        (
                            "risk_released"
                            if reservation_state == "released"
                            else "order_lifecycle_changed"
                        ),
                        order.client_order_id,
                        {
                            "previous_state": previous_state,
                            "state": reservation_state,
                            "broker_order_id": order.broker_order_id,
                            "broker_status": order.status,
                            "reason": release_reason,
                        },
                    )
                if trade_state is not None:
                    trade = connection.execute(
                        """
                        SELECT side, approved_quantity, entry_price, state
                        FROM trade_records WHERE intent_id = ?
                        """,
                        (order.client_order_id,),
                    ).fetchone()
                    entry_price = (
                        order.filled_average_price
                        if order.filled_average_price is not None
                        else (
                            Decimal(trade["entry_price"])
                            if trade is not None
                            and trade["entry_price"] is not None
                            else None
                        )
                    )
                    exit_price = (
                        order.exit_filled_average_price
                        if trade_state == "position_closed"
                        else None
                    )
                    realized_pnl: Decimal | None = None
                    if (
                        trade is not None
                        and entry_price is not None
                        and exit_price is not None
                    ):
                        quantity = Decimal(
                            trade["approved_quantity"]
                        )
                        direction = (
                            Decimal("1")
                            if trade["side"] == "buy"
                            else Decimal("-1")
                        )
                        realized_pnl = (
                            (exit_price - entry_price)
                            * quantity
                            * direction
                        )
                    exit_reason = (
                        (
                            "stop_loss"
                            if "stop" in order.exit_leg_type
                            else "take_profit"
                            if order.exit_leg_type == "limit"
                            else "broker_exit_fill"
                        )
                        if trade_state == "position_closed"
                        else None
                    )
                    connection.execute(
                        """
                        UPDATE trade_records
                        SET state = ?, broker_order_id = ?,
                            entry_price = COALESCE(?, entry_price),
                            exit_price = COALESCE(?, exit_price),
                            realized_pnl = COALESCE(?, realized_pnl),
                            exit_reason = COALESCE(?, exit_reason),
                            closed_at = COALESCE(?, closed_at),
                            updated_at = ?
                        WHERE intent_id = ?
                        """,
                        (
                            trade_state,
                            order.broker_order_id,
                            (
                                format(entry_price, "f")
                                if entry_price is not None
                                else None
                            ),
                            (
                                format(exit_price, "f")
                                if exit_price is not None
                                else None
                            ),
                            (
                                format(realized_pnl, "f")
                                if realized_pnl is not None
                                else None
                            ),
                            exit_reason,
                            (
                                order.exit_filled_at or observed_at
                                if trade_state == "position_closed"
                                else None
                            ),
                            observed_at,
                            order.client_order_id,
                        ),
                    )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def submitted_orders_since(
        self, account_id: str, since: datetime
    ) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM trade_records
                WHERE account_id = ?
                  AND broker_order_id IS NOT NULL
                  AND created_at >= ?
                """,
                (account_id, since.isoformat()),
            ).fetchone()
            return int(row["count"])
        finally:
            self._close_if_needed(connection)

    def apply_account_equity_state(
        self,
        account_id: str,
        snapshot: AccountSnapshot,
        observed_at: datetime,
    ) -> AccountSnapshot:
        trading_day = observed_at.astimezone(timezone.utc).date().isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT trading_day, start_of_day_equity, peak_equity
                FROM account_equity_state WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            if row is None or row["trading_day"] != trading_day:
                start_equity = snapshot.start_of_day_equity
                peak_equity = max(start_equity, snapshot.equity)
            else:
                start_equity = Decimal(row["start_of_day_equity"])
                peak_equity = max(
                    Decimal(row["peak_equity"]), snapshot.equity
                )
            connection.execute(
                """
                INSERT INTO account_equity_state (
                    account_id, trading_day, start_of_day_equity,
                    peak_equity, latest_equity, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    trading_day = excluded.trading_day,
                    start_of_day_equity =
                        excluded.start_of_day_equity,
                    peak_equity = excluded.peak_equity,
                    latest_equity = excluded.latest_equity,
                    observed_at = excluded.observed_at
                """,
                (
                    account_id,
                    trading_day,
                    format(start_equity, "f"),
                    format(peak_equity, "f"),
                    format(snapshot.equity, "f"),
                    observed_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")
            return replace(
                snapshot,
                start_of_day_equity=start_equity,
                peak_equity=peak_equity,
            )
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def active_reservation_identity(
        self,
    ) -> tuple[set[str], set[str]]:
        connection = self._connect()
        try:
            placeholders = ",".join(
                "?" for _ in ACTIVE_RESERVATION_STATES
            )
            rows = connection.execute(
                f"""
                SELECT intent_id, symbol FROM risk_reservations
                WHERE state IN ({placeholders})
                """,
                ACTIVE_RESERVATION_STATES,
            ).fetchall()
            return (
                {row["intent_id"] for row in rows},
                {row["symbol"] for row in rows},
            )
        finally:
            self._close_if_needed(connection)

    def last_submitted_at(
        self, account_id: str, symbol: str
    ) -> datetime | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT created_at
                FROM trade_records
                WHERE account_id = ? AND symbol = ?
                  AND broker_order_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (account_id, symbol),
            ).fetchone()
            return (
                datetime.fromisoformat(row["created_at"])
                if row is not None
                else None
            )
        finally:
            self._close_if_needed(connection)

    def record_event(
        self, event_type: str, correlation_id: str, payload: Any
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_event(
                connection, event_type, correlation_id, payload
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_broker_state(
        self,
        connection_id: str,
        observed_at: datetime,
        payload: Any,
        summary: Any,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO latest_broker_state (
                    connection_id, observed_at, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    observed_at = excluded.observed_at,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    connection_id,
                    observed_at.isoformat(),
                    _json(payload),
                    updated_at,
                ),
            )
            self._insert_event(
                connection,
                "broker_reconciled",
                connection_id,
                summary,
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_market_bars(
        self, bars: Iterable["MarketBar"]
    ) -> int:
        materialized = tuple(bars)
        if not materialized:
            return 0
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            ingested_at = datetime.now(timezone.utc).isoformat()
            connection.executemany(
                """
                INSERT INTO market_bars (
                    asset_class, symbol, timeframe, feed, adjustment,
                    bar_timestamp,
                    open_price, high_price, low_price, close_price,
                    volume, trade_count, vwap, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_class, symbol, timeframe, feed, bar_timestamp
                ) DO UPDATE SET
                    open_price = excluded.open_price,
                    high_price = excluded.high_price,
                    low_price = excluded.low_price,
                    close_price = excluded.close_price,
                    volume = excluded.volume,
                    trade_count = excluded.trade_count,
                    vwap = excluded.vwap,
                    adjustment = excluded.adjustment,
                    ingested_at = excluded.ingested_at
                """,
                [
                    (
                        bar.asset_class.value,
                        bar.symbol,
                        bar.timeframe,
                        bar.feed,
                        bar.adjustment,
                        bar.timestamp.isoformat(),
                        format(bar.open, "f"),
                        format(bar.high, "f"),
                        format(bar.low, "f"),
                        format(bar.close, "f"),
                        format(bar.volume, "f"),
                        bar.trade_count,
                        (
                            format(bar.vwap, "f")
                            if bar.vwap is not None
                            else None
                        ),
                        ingested_at,
                    )
                    for bar in materialized
                ],
            )
            connection.execute("COMMIT")
            return len(materialized)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_signal(self, signal: "StrategySignal") -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO strategy_signals (
                    signal_id, account_id, strategy_id, strategy_version,
                    symbol, action, bar_timestamp, confidence,
                    reference_price, stop_price, target_price,
                    reason_codes_json, evidence_json, status,
                    status_details_json, created_at, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'generated', '{}', ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.account_id,
                    signal.strategy_id,
                    signal.strategy_version,
                    signal.symbol,
                    signal.action.value,
                    signal.bar_timestamp.isoformat(),
                    format(signal.confidence, "f"),
                    format(signal.reference_price, "f"),
                    format(signal.stop_price, "f"),
                    format(signal.target_price, "f"),
                    _json(signal.reason_codes),
                    _json(signal.evidence),
                    signal.created_at.isoformat(),
                    signal.expires_at.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._insert_event(
                    connection,
                    "strategy_signal_generated",
                    signal.signal_id,
                    {
                        "account_id": signal.account_id,
                        "strategy_id": signal.strategy_id,
                        "strategy_version": signal.strategy_version,
                        "symbol": signal.symbol,
                        "action": signal.action,
                        "confidence": signal.confidence,
                        "reference_price": signal.reference_price,
                        "stop_price": signal.stop_price,
                        "target_price": signal.target_price,
                        "reason_codes": signal.reason_codes,
                    },
                )
            connection.execute("COMMIT")
            return inserted
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def update_signal_status(
        self,
        signal_id: str,
        status: str,
        details: Any | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE strategy_signals
                SET status = ?, status_details_json = ?, updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    status,
                    _json(details or {}),
                    datetime.now(timezone.utc).isoformat(),
                    signal_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Strategy signal was not found")
            self._insert_event(
                connection,
                "strategy_signal_status",
                signal_id,
                {"status": status, "details": details or {}},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_strategy_runtime(
        self,
        account_id: str,
        strategy_id: str,
        symbol: str,
        state: str,
        bar_timestamp: str | None,
        details: Any,
        evaluated_at: datetime,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO strategy_runtime (
                    account_id, strategy_id, symbol, state,
                    bar_timestamp, details_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, strategy_id, symbol) DO UPDATE SET
                    state = excluded.state,
                    bar_timestamp = excluded.bar_timestamp,
                    details_json = excluded.details_json,
                    evaluated_at = excluded.evaluated_at
                """,
                (
                    account_id,
                    strategy_id,
                    symbol,
                    state,
                    bar_timestamp,
                    _json(details),
                    evaluated_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_trade_result(
        self,
        signal: "StrategySignal",
        intent: TradeIntent,
        result: "EngineResult",
    ) -> None:
        state = (
            "rejected"
            if not result.decision.approved
            else "dry_run"
            if result.dry_run
            else "submitted"
        )
        broker_order_id = (
            result.order.broker_order_id if result.order else None
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO trade_records (
                    signal_id, intent_id, account_id, strategy_id,
                    symbol, side, state, requested_quantity,
                    approved_quantity, reserved_risk, reference_price,
                    stop_price, target_price, broker_order_id,
                    explanation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    state = excluded.state,
                    approved_quantity = excluded.approved_quantity,
                    reserved_risk = excluded.reserved_risk,
                    broker_order_id = excluded.broker_order_id,
                    updated_at = excluded.updated_at
                """,
                (
                    signal.signal_id,
                    intent.intent_id,
                    intent.account_id,
                    intent.strategy_id,
                    intent.symbol,
                    intent.side.value,
                    state,
                    format(intent.requested_quantity, "f"),
                    format(result.decision.approved_quantity, "f"),
                    format(result.decision.reserved_risk, "f"),
                    (
                        format(intent.reference_price, "f")
                        if intent.reference_price is not None
                        else None
                    ),
                    (
                        format(intent.stop_price, "f")
                        if intent.stop_price is not None
                        else None
                    ),
                    (
                        format(intent.take_profit_price, "f")
                        if intent.take_profit_price is not None
                        else None
                    ),
                    broker_order_id,
                    _json(intent.explanation),
                    signal.created_at.isoformat(),
                    now,
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_backtest(
        self,
        *,
        run_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        timeframe: str,
        started_at: datetime,
        completed_at: datetime,
        config: Any,
        metrics: Any,
        trades: Any,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id, strategy_id, strategy_version, symbol,
                    timeframe, started_at, completed_at, config_json,
                    metrics_json, trades_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    strategy_id,
                    strategy_version,
                    symbol,
                    timeframe,
                    started_at.isoformat(),
                    completed_at.isoformat(),
                    _json(config),
                    _json(metrics),
                    _json(trades),
                ),
            )
            self._insert_event(
                connection,
                "backtest_completed",
                run_id,
                {
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "metrics": metrics,
                },
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_validation(
        self,
        *,
        validation_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        timeframe: str,
        passed: bool,
        gates: Any,
        warnings: Any,
        in_sample_run_id: str,
        out_of_sample_run_id: str,
        completed_at: datetime,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO strategy_validations (
                    validation_id, strategy_id, strategy_version,
                    symbol, timeframe, passed, gates_json,
                    warnings_json, in_sample_run_id,
                    out_of_sample_run_id, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id,
                    strategy_id,
                    strategy_version,
                    symbol,
                    timeframe,
                    int(passed),
                    _json(gates),
                    _json(warnings),
                    in_sample_run_id,
                    out_of_sample_run_id,
                    completed_at.isoformat(),
                ),
            )
            self._insert_event(
                connection,
                "strategy_validation_completed",
                validation_id,
                {
                    "strategy_id": strategy_id,
                    "strategy_version": strategy_version,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "passed": passed,
                    "gates": gates,
                    "warnings": warnings,
                    "in_sample_run_id": in_sample_run_id,
                    "out_of_sample_run_id": out_of_sample_run_id,
                },
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._close_if_needed(connection)

    def record_research_decision(
        self, decision: "MarketModelDecision"
    ) -> None:
        bar_identity = decision.bar_timestamp or decision.evaluated_at
        decision_key = (
            f"{decision.account_id}:{decision.model_id}:"
            f"{decision.symbol}:{bar_identity}"
        )
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO research_decisions (
                    decision_key, model_id, model_version, account_id,
                    symbol, bar_timestamp, evaluated_at, state, score,
                    target_risk_multiplier, execution_eligible,
                    reason_codes_json, evidence_ids_json, components_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_key) DO UPDATE SET
                    evaluated_at = excluded.evaluated_at,
                    state = excluded.state,
                    score = excluded.score,
                    target_risk_multiplier =
                        excluded.target_risk_multiplier,
                    execution_eligible = excluded.execution_eligible,
                    reason_codes_json = excluded.reason_codes_json,
                    evidence_ids_json = excluded.evidence_ids_json,
                    components_json = excluded.components_json
                """,
                (
                    decision_key,
                    decision.model_id,
                    decision.model_version,
                    decision.account_id,
                    decision.symbol,
                    decision.bar_timestamp,
                    decision.evaluated_at,
                    decision.state.value,
                    format(decision.score, "f"),
                    format(decision.target_risk_multiplier, "f"),
                    int(decision.execution_eligible),
                    _json(decision.reason_codes),
                    _json(decision.evidence_ids),
                    _json(decision.components),
                ),
            )
        finally:
            self._close_if_needed(connection)

    def record_research_backtest(
        self, report: "ResearchBacktestReport"
    ) -> None:
        sample_step = max(1, len(report.points) // 120)
        sampled_points = list(report.points[::sample_step])
        if (
            report.points
            and sampled_points[-1] is not report.points[-1]
        ):
            sampled_points.append(report.points[-1])
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO research_backtest_reports (
                    report_id, model_id, model_version, symbol, benchmark,
                    timeframe, started_at, completed_at, config_json,
                    metrics_json, gates_json, warnings_json,
                    promotion_status, execution_eligible, points_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    completed_at = excluded.completed_at,
                    config_json = excluded.config_json,
                    metrics_json = excluded.metrics_json,
                    gates_json = excluded.gates_json,
                    warnings_json = excluded.warnings_json,
                    promotion_status = excluded.promotion_status,
                    execution_eligible = excluded.execution_eligible,
                    points_json = excluded.points_json
                """,
                (
                    report.report_id,
                    report.model_id,
                    report.model_version,
                    report.symbol,
                    report.benchmark,
                    report.timeframe,
                    report.started_at,
                    report.completed_at,
                    _json(asdict(report.config)),
                    _json(asdict(report.metrics)),
                    _json(report.gates),
                    _json(report.warnings),
                    report.promotion_status,
                    int(report.execution_eligible),
                    _json(
                        [asdict(point) for point in sampled_points]
                    ),
                ),
            )
        finally:
            self._close_if_needed(connection)

    def record_portfolio_risk_report(
        self, report: "PortfolioRiskReport"
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO portfolio_risk_reports (
                    report_id, account_id, evaluated_at, lookback_days,
                    state, symbols_requested_json, symbols_included_json,
                    missing_symbols_json, average_positive_correlation,
                    maximum_positive_correlation, effective_breadth,
                    high_correlation_pairs_json, all_pairs_json,
                    reason_codes_json, execution_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    evaluated_at = excluded.evaluated_at,
                    state = excluded.state,
                    symbols_requested_json =
                        excluded.symbols_requested_json,
                    symbols_included_json =
                        excluded.symbols_included_json,
                    missing_symbols_json = excluded.missing_symbols_json,
                    average_positive_correlation =
                        excluded.average_positive_correlation,
                    maximum_positive_correlation =
                        excluded.maximum_positive_correlation,
                    effective_breadth = excluded.effective_breadth,
                    high_correlation_pairs_json =
                        excluded.high_correlation_pairs_json,
                    all_pairs_json = excluded.all_pairs_json,
                    reason_codes_json = excluded.reason_codes_json,
                    execution_eligible = excluded.execution_eligible
                """,
                (
                    report.report_id,
                    report.account_id,
                    report.evaluated_at,
                    report.lookback_days,
                    report.state,
                    _json(report.symbols_requested),
                    _json(report.symbols_included),
                    _json(report.missing_symbols),
                    format(report.average_positive_correlation, "f"),
                    format(report.maximum_positive_correlation, "f"),
                    format(report.effective_breadth, "f"),
                    _json(
                        [
                            asdict(pair)
                            for pair in report.high_correlation_pairs
                        ]
                    ),
                    _json(
                        [asdict(pair) for pair in report.all_pairs]
                    ),
                    _json(report.reason_codes),
                    int(report.execution_eligible),
                ),
            )
        finally:
            self._close_if_needed(connection)

    def record_strategy_lab_report(
        self, report: "StrategyLabReport"
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO strategy_lab_reports (
                    report_id, account_id, strategy_id, strategy_version,
                    timeframe, evaluated_at, configuration_enabled,
                    paper_execution_configured, symbols_requested_json,
                    symbols_covered_json, missing_symbols_json,
                    symbol_results_json, aggregate_metrics_json,
                    gates_json, warnings_json, readiness_status,
                    execution_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    evaluated_at = excluded.evaluated_at,
                    symbol_results_json = excluded.symbol_results_json,
                    aggregate_metrics_json =
                        excluded.aggregate_metrics_json,
                    gates_json = excluded.gates_json,
                    warnings_json = excluded.warnings_json,
                    readiness_status = excluded.readiness_status,
                    execution_eligible = excluded.execution_eligible
                """,
                (
                    report.report_id,
                    report.account_id,
                    report.strategy_id,
                    report.strategy_version,
                    report.timeframe,
                    report.evaluated_at.isoformat(),
                    int(report.configuration_enabled),
                    int(report.paper_execution_configured),
                    _json(report.symbols_requested),
                    _json(report.symbols_covered),
                    _json(report.missing_symbols),
                    _json(
                        [asdict(item) for item in report.symbol_results]
                    ),
                    _json(report.aggregate_metrics),
                    _json(report.gates),
                    _json(report.warnings),
                    report.readiness_status,
                    int(report.execution_eligible),
                ),
            )
        finally:
            self._close_if_needed(connection)

    def record_asset_universe_report(
        self, report: "AssetUniverseReport"
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO asset_universe_reports (
                    report_id, account_id, policy_id, evaluated_at,
                    candidates_requested_json, recommendations_json,
                    evaluations_json, warnings_json, execution_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    evaluated_at = excluded.evaluated_at,
                    candidates_requested_json =
                        excluded.candidates_requested_json,
                    recommendations_json =
                        excluded.recommendations_json,
                    evaluations_json = excluded.evaluations_json,
                    warnings_json = excluded.warnings_json,
                    execution_eligible = excluded.execution_eligible
                """,
                (
                    report.report_id,
                    report.account_id,
                    report.policy_id,
                    report.evaluated_at.isoformat(),
                    _json(report.candidates_requested),
                    _json(report.recommendations),
                    _json(
                        [
                            asdict(item)
                            for item in report.evaluations
                        ]
                    ),
                    _json(report.warnings),
                    int(report.execution_eligible),
                ),
            )
        finally:
            self._close_if_needed(connection)

    def active_risk(self) -> Decimal:
        connection = self._connect()
        try:
            return self._active_risk(connection)
        finally:
            self._close_if_needed(connection)

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT occurred_at, event_type, correlation_id, payload_json
                FROM audit_events ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    "occurred_at": row["occurred_at"],
                    "event_type": row["event_type"],
                    "correlation_id": row["correlation_id"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]
        finally:
            self._close_if_needed(connection)

    @staticmethod
    def _active_risk(connection: sqlite3.Connection) -> Decimal:
        placeholders = ",".join("?" for _ in ACTIVE_RESERVATION_STATES)
        rows = connection.execute(
            f"""
            SELECT risk_amount FROM risk_reservations
            WHERE state IN ({placeholders})
            """,
            ACTIVE_RESERVATION_STATES,
        ).fetchall()
        return sum(
            (Decimal(row["risk_amount"]) for row in rows),
            start=ZERO,
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event_type: str,
        correlation_id: str,
        payload: Any,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                occurred_at, event_type, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                correlation_id,
                _json(payload),
            ),
        )


class SqliteAuditReader:
    """Strictly read-only access for monitoring and reporting processes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise FileNotFoundError(f"Audit database not found: {self.path}")
        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro",
            timeout=5,
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def active_risk(self) -> Decimal:
        with closing(self._connect()) as connection:
            return SqliteAuditStore._active_risk(connection)

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, event_type, correlation_id, payload_json
                FROM audit_events ORDER BY id DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "occurred_at": row["occurred_at"],
                "event_type": row["event_type"],
                "correlation_id": row["correlation_id"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def latest_event(self, event_type: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT occurred_at, event_type, correlation_id, payload_json
                FROM audit_events
                WHERE event_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (event_type,),
            ).fetchone()
        if row is None:
            return None
        return {
            "occurred_at": row["occurred_at"],
            "event_type": row["event_type"],
            "correlation_id": row["correlation_id"],
            "payload": json.loads(row["payload_json"]),
        }

    def reservation_summary(self) -> dict[str, dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT state, risk_amount
                FROM risk_reservations
                ORDER BY state
                """
            ).fetchall()

        summary: dict[str, dict[str, Any]] = {}
        for row in rows:
            state = row["state"]
            entry = summary.setdefault(
                state, {"count": 0, "risk_amount": ZERO}
            )
            entry["count"] += 1
            entry["risk_amount"] += Decimal(row["risk_amount"])
        return {
            state: {
                "count": entry["count"],
                "risk_amount": format(entry["risk_amount"], "f"),
            }
            for state, entry in summary.items()
        }

    def latest_broker_state(
        self, connection_id: str
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT connection_id, observed_at, payload_json, updated_at
                FROM latest_broker_state
                WHERE connection_id = ?
                """,
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "connection_id": row["connection_id"],
            "observed_at": row["observed_at"],
            "updated_at": row["updated_at"],
            "payload": json.loads(row["payload_json"]),
        }

    def recent_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT signal_id, account_id, strategy_id,
                       strategy_version, symbol, action, bar_timestamp,
                       confidence, reference_price, stop_price,
                       target_price, reason_codes_json, evidence_json,
                       status, status_details_json, created_at,
                       expires_at, updated_at
                FROM strategy_signals
                ORDER BY created_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "signal_id": row["signal_id"],
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "symbol": row["symbol"],
                "action": row["action"],
                "bar_timestamp": row["bar_timestamp"],
                "confidence": row["confidence"],
                "reference_price": row["reference_price"],
                "stop_price": row["stop_price"],
                "target_price": row["target_price"],
                "reason_codes": json.loads(row["reason_codes_json"]),
                "evidence": json.loads(row["evidence_json"]),
                "status": row["status"],
                "status_details": json.loads(
                    row["status_details_json"]
                ),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def strategy_runtime(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT account_id, strategy_id, symbol, state,
                       bar_timestamp, details_json, evaluated_at
                FROM strategy_runtime
                ORDER BY strategy_id, symbol
                """
            ).fetchall()
        return [
            {
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "state": row["state"],
                "bar_timestamp": row["bar_timestamp"],
                "details": json.loads(row["details_json"]),
                "evaluated_at": row["evaluated_at"],
            }
            for row in rows
        ]

    def recent_trade_records(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT signal_id, intent_id, account_id, strategy_id,
                       symbol, side, state, requested_quantity,
                       approved_quantity, reserved_risk, reference_price,
                       stop_price, target_price, broker_order_id,
                       entry_price, exit_price, realized_pnl,
                       exit_reason, closed_at, explanation_json,
                       created_at, updated_at
                FROM trade_records
                ORDER BY created_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "signal_id": row["signal_id"],
                "intent_id": row["intent_id"],
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "side": row["side"],
                "state": row["state"],
                "requested_quantity": row["requested_quantity"],
                "approved_quantity": row["approved_quantity"],
                "reserved_risk": row["reserved_risk"],
                "reference_price": row["reference_price"],
                "stop_price": row["stop_price"],
                "target_price": row["target_price"],
                "broker_order_id": row["broker_order_id"],
                "entry_price": row["entry_price"],
                "exit_price": row["exit_price"],
                "realized_pnl": row["realized_pnl"],
                "exit_reason": row["exit_reason"],
                "closed_at": row["closed_at"],
                "explanation": json.loads(row["explanation_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def recent_backtests(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT run_id, strategy_id, strategy_version, symbol,
                       timeframe, started_at, completed_at,
                       config_json, metrics_json
                FROM backtest_runs
                ORDER BY completed_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "config": json.loads(row["config_json"]),
                "metrics": json.loads(row["metrics_json"]),
            }
            for row in rows
        ]

    def recent_validations(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT validation_id, strategy_id, strategy_version,
                       symbol, timeframe, passed, gates_json,
                       warnings_json, in_sample_run_id,
                       out_of_sample_run_id, completed_at
                FROM strategy_validations
                ORDER BY completed_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "validation_id": row["validation_id"],
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "passed": bool(row["passed"]),
                "gates": json.loads(row["gates_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "in_sample_run_id": row["in_sample_run_id"],
                "out_of_sample_run_id": row["out_of_sample_run_id"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        ]

    def recent_research_decisions(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT model_id, model_version, account_id, symbol,
                       bar_timestamp, evaluated_at, state, score,
                       target_risk_multiplier, execution_eligible,
                       reason_codes_json, evidence_ids_json,
                       components_json
                FROM research_decisions
                ORDER BY evaluated_at DESC, model_id, symbol
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "model_id": row["model_id"],
                "model_version": row["model_version"],
                "account_id": row["account_id"],
                "symbol": row["symbol"],
                "bar_timestamp": row["bar_timestamp"],
                "evaluated_at": row["evaluated_at"],
                "state": row["state"],
                "score": row["score"],
                "target_risk_multiplier": row[
                    "target_risk_multiplier"
                ],
                "execution_eligible": bool(
                    row["execution_eligible"]
                ),
                "reason_codes": json.loads(
                    row["reason_codes_json"]
                ),
                "evidence_ids": json.loads(
                    row["evidence_ids_json"]
                ),
                "components": json.loads(row["components_json"]),
            }
            for row in rows
        ]

    def recent_research_backtests(
        self, limit: int = 50
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT report_id, model_id, model_version, symbol,
                       benchmark, timeframe, started_at, completed_at,
                       config_json, metrics_json, gates_json,
                       warnings_json, promotion_status,
                       execution_eligible
                FROM research_backtest_reports
                ORDER BY completed_at DESC, symbol
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "report_id": row["report_id"],
                "model_id": row["model_id"],
                "model_version": row["model_version"],
                "symbol": row["symbol"],
                "benchmark": row["benchmark"],
                "timeframe": row["timeframe"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "config": json.loads(row["config_json"]),
                "metrics": json.loads(row["metrics_json"]),
                "gates": json.loads(row["gates_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "promotion_status": row["promotion_status"],
                "execution_eligible": bool(
                    row["execution_eligible"]
                ),
            }
            for row in rows
        ]

    def recent_portfolio_risk_reports(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT report_id, account_id, evaluated_at,
                       lookback_days, state, symbols_requested_json,
                       symbols_included_json, missing_symbols_json,
                       average_positive_correlation,
                       maximum_positive_correlation,
                       effective_breadth,
                       high_correlation_pairs_json, all_pairs_json,
                       reason_codes_json, execution_eligible
                FROM portfolio_risk_reports
                ORDER BY evaluated_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "report_id": row["report_id"],
                "account_id": row["account_id"],
                "evaluated_at": row["evaluated_at"],
                "lookback_days": row["lookback_days"],
                "state": row["state"],
                "symbols_requested": json.loads(
                    row["symbols_requested_json"]
                ),
                "symbols_included": json.loads(
                    row["symbols_included_json"]
                ),
                "missing_symbols": json.loads(
                    row["missing_symbols_json"]
                ),
                "average_positive_correlation": row[
                    "average_positive_correlation"
                ],
                "maximum_positive_correlation": row[
                    "maximum_positive_correlation"
                ],
                "effective_breadth": row["effective_breadth"],
                "high_correlation_pairs": json.loads(
                    row["high_correlation_pairs_json"]
                ),
                "all_pairs": json.loads(row["all_pairs_json"]),
                "reason_codes": json.loads(
                    row["reason_codes_json"]
                ),
                "execution_eligible": bool(
                    row["execution_eligible"]
                ),
            }
            for row in rows
        ]

    def recent_strategy_lab_reports(
        self, limit: int = 30
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT report_id, account_id, strategy_id,
                       strategy_version, timeframe, evaluated_at,
                       configuration_enabled,
                       paper_execution_configured,
                       symbols_requested_json, symbols_covered_json,
                       missing_symbols_json, symbol_results_json,
                       aggregate_metrics_json, gates_json,
                       warnings_json, readiness_status,
                       execution_eligible
                FROM strategy_lab_reports
                ORDER BY evaluated_at DESC, strategy_id
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "report_id": row["report_id"],
                "account_id": row["account_id"],
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "timeframe": row["timeframe"],
                "evaluated_at": row["evaluated_at"],
                "configuration_enabled": bool(
                    row["configuration_enabled"]
                ),
                "paper_execution_configured": bool(
                    row["paper_execution_configured"]
                ),
                "symbols_requested": json.loads(
                    row["symbols_requested_json"]
                ),
                "symbols_covered": json.loads(
                    row["symbols_covered_json"]
                ),
                "missing_symbols": json.loads(
                    row["missing_symbols_json"]
                ),
                "symbol_results": json.loads(
                    row["symbol_results_json"]
                ),
                "aggregate_metrics": json.loads(
                    row["aggregate_metrics_json"]
                ),
                "gates": json.loads(row["gates_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "readiness_status": row["readiness_status"],
                "execution_eligible": bool(
                    row["execution_eligible"]
                ),
            }
            for row in rows
        ]

    def recent_asset_universe_reports(
        self, limit: int = 30
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT report_id, account_id, policy_id, evaluated_at,
                       candidates_requested_json,
                       recommendations_json, evaluations_json,
                       warnings_json, execution_eligible
                FROM asset_universe_reports
                ORDER BY evaluated_at DESC, policy_id
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "report_id": row["report_id"],
                "account_id": row["account_id"],
                "policy_id": row["policy_id"],
                "evaluated_at": row["evaluated_at"],
                "candidates_requested": json.loads(
                    row["candidates_requested_json"]
                ),
                "recommendations": json.loads(
                    row["recommendations_json"]
                ),
                "evaluations": json.loads(
                    row["evaluations_json"]
                ),
                "warnings": json.loads(row["warnings_json"]),
                "execution_eligible": bool(
                    row["execution_eligible"]
                ),
            }
            for row in rows
        ]

    def market_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = max(10, min(limit, 1000))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, feed, adjustment, bar_timestamp,
                       open_price, high_price, low_price, close_price,
                       volume, trade_count, vwap
                FROM market_bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY bar_timestamp DESC LIMIT ?
                """,
                (symbol, timeframe, safe_limit),
            ).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "feed": row["feed"],
                "adjustment": row["adjustment"],
                "timestamp": row["bar_timestamp"],
                "open": row["open_price"],
                "high": row["high_price"],
                "low": row["low_price"],
                "close": row["close_price"],
                "volume": row["volume"],
                "trade_count": row["trade_count"],
                "vwap": row["vwap"],
            }
            for row in reversed(rows)
        ]
