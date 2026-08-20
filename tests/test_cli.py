import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from multitrade.cli import _run_account_services_cycle, build_parser


@dataclass(frozen=True, slots=True)
class TimestampedResult:
    account_id: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class StubPlan:
    account_id: str


class StubStore:
    def record_event(self, event_type, correlation_id, payload):
        del event_type, correlation_id, payload


class StubService:
    def __init__(self, account_id: str) -> None:
        self.account_plan = StubPlan(account_id)
        self.store = StubStore()

    def run_cycle(self) -> TimestampedResult:
        return TimestampedResult(
            account_id=self.account_plan.account_id,
            evaluated_at=datetime(
                2026, 7, 28, 12, 0, tzinfo=timezone.utc
            ),
        )


class MultiAccountCliTests(TestCase):
    def test_accelerated_validation_accepts_multiple_timeframes(self) -> None:
        args = build_parser().parse_args(
            [
                "accelerated-validation",
                "--timeframes",
                "1Hour,4Hour,1Day",
                "--force-all",
            ]
        )

        self.assertEqual(args.timeframes, "1Hour,4Hour,1Day")
        self.assertTrue(args.force_all)

    def test_admin_agent_command_is_registered(self) -> None:
        args = build_parser().parse_args(["admin-agent"])

        self.assertEqual(args.command, "admin-agent")

    def test_aggregate_health_serializes_cycle_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            health_path = Path(directory) / "health.json"
            result = _run_account_services_cycle(
                (StubService("paper-a"), StubService("paper-b")),
                component="test_component",
                health_path=health_path,
            )
            health = json.loads(
                health_path.read_text(encoding="utf-8")
            )

        self.assertEqual(result["accounts_succeeded"], 2)
        self.assertEqual(
            result["results"][0]["evaluated_at"],
            "2026-07-28T12:00:00+00:00",
        )
        self.assertEqual(health["status"], "ok")
