import base64
import json
import threading
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from multitrade.audit import SqliteAuditStore
from multitrade.dashboard import DashboardData, create_dashboard_server
from multitrade.health import write_health


class DashboardTests(TestCase):
    def _fixture(
        self, directory: str
    ) -> tuple[DashboardData, Path, Path]:
        db_path = Path(directory) / "trading.db"
        health_path = Path(directory) / "health.json"
        store = SqliteAuditStore(db_path)
        store.record_event(
            "account_heartbeat",
            "alpaca-paper",
            {
                "equity": Decimal("100000"),
                "start_of_day_equity": Decimal("100000"),
                "gross_notional": Decimal("2500"),
                "positions": {"AAPL": Decimal("5")},
                "reserved_active_risk": Decimal("0"),
            },
        )
        write_health(health_path, "ok", {"environment": "paper"})
        return (
            DashboardData(
                db_path=db_path,
                health_path=health_path,
                health_max_age_seconds=120,
                max_total_open=Decimal("0.10"),
            ),
            db_path,
            health_path,
        )

    def test_overview_reports_paper_account_and_health(self) -> None:
        with TemporaryDirectory() as directory:
            service, _, _ = self._fixture(directory)

            result = service.overview()

            self.assertEqual(result["environment"], "paper")
            self.assertTrue(result["engine"]["healthy"])
            self.assertEqual(result["storage"]["status"], "ok")
            self.assertEqual(result["account"]["equity"], "100000")
            self.assertEqual(result["risk"]["active_amount"], "0")
            self.assertEqual(len(result["events"]), 1)

    def test_http_dashboard_requires_authentication(self) -> None:
        with TemporaryDirectory() as directory:
            service, _, _ = self._fixture(directory)
            server = create_dashboard_server(
                "127.0.0.1",
                0,
                service,
                username="operator",
                password="a-long-test-password",
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            port = server.server_address[1]
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://127.0.0.1:{port}/", timeout=3)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()

                token = base64.b64encode(
                    b"operator:a-long-test-password"
                ).decode("ascii")
                request = Request(
                    f"http://127.0.0.1:{port}/api/overview",
                    headers={"Authorization": f"Basic {token}"},
                )
                with urlopen(request, timeout=3) as response:
                    result = json.loads(response.read())

                self.assertEqual(response.status, 200)
                self.assertEqual(result["account"]["equity"], "100000")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_health_endpoint_contains_no_account_data(self) -> None:
        with TemporaryDirectory() as directory:
            service, _, _ = self._fixture(directory)
            server = create_dashboard_server(
                "127.0.0.1",
                0,
                service,
                username="operator",
                password="a-long-test-password",
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            port = server.server_address[1]
            try:
                with urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=3
                ) as response:
                    result = json.loads(response.read())
                self.assertEqual(result, {"status": "ok"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
