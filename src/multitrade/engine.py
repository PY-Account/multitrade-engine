from __future__ import annotations

from dataclasses import dataclass

from multitrade.audit import SqliteAuditStore
from multitrade.brokers.base import Broker, BrokerOrder
from multitrade.domain import AccountSnapshot, RiskDecision, TradeIntent
from multitrade.risk import RiskEngine


@dataclass(frozen=True, slots=True)
class EngineResult:
    decision: RiskDecision
    order: BrokerOrder | None = None
    dry_run: bool = False


class TradingEngine:
    def __init__(
        self,
        broker: Broker,
        risk_engine: RiskEngine,
        audit_store: SqliteAuditStore,
        enable_order_submission: bool = False,
    ) -> None:
        self.broker = broker
        self.risk_engine = risk_engine
        self.audit_store = audit_store
        self.enable_order_submission = enable_order_submission

    def process(
        self,
        intent: TradeIntent,
        snapshot: AccountSnapshot | None = None,
        *,
        allow_submission: bool = True,
    ) -> EngineResult:
        account_snapshot = snapshot or self.broker.get_account_snapshot()
        decision = self.audit_store.evaluate_and_reserve(
            self.risk_engine, intent, account_snapshot
        )
        if not decision.approved:
            return EngineResult(decision=decision)

        if not self.enable_order_submission or not allow_submission:
            disabled_reason = (
                "paper_order_submission_disabled"
                if not self.enable_order_submission
                else "strategy_paper_execution_not_approved"
            )
            self.audit_store.record_event(
                "dry_run",
                intent.intent_id,
                {
                    "approved_quantity": format(
                        decision.approved_quantity, "f"
                    ),
                    "reason": disabled_reason,
                },
            )
            self.audit_store.release(intent.intent_id, disabled_reason)
            return EngineResult(decision=decision, dry_run=True)

        try:
            order = self.broker.submit_order(
                intent, decision.approved_quantity
            )
            self.audit_store.mark_submitted(
                intent.intent_id, order.broker_order_id
            )
            return EngineResult(decision=decision, order=order)
        except Exception as exc:
            try:
                self.audit_store.record_event(
                    "order_submission_failed",
                    intent.intent_id,
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
            finally:
                self.audit_store.release(
                    intent.intent_id, "broker_submission_failed"
                )
            raise
