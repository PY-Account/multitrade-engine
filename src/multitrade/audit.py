from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from multitrade.domain import (
    AccountSnapshot,
    RiskDecision,
    TradeIntent,
    ZERO,
)
from multitrade.risk import RiskEngine


ACTIVE_RESERVATION_STATES = ("reserved", "submitted", "open")


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
                """
            )
        finally:
            self._close_if_needed(connection)

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
                WHERE intent_id = ? AND state IN ('reserved', 'submitted')
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
