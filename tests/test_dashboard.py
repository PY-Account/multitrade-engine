import base64
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from multitrade.audit import SqliteAuditStore
from multitrade.dashboard import DashboardData, create_dashboard_server
from multitrade.experiments import (
    load_strategy_experiment_program,
)
from multitrade.health import write_health
from multitrade.universe import load_asset_universe_program


class DashboardTests(TestCase):
    def _fixture(
        self, directory: str
    ) -> tuple[DashboardData, Path, Path]:
        db_path = Path(directory) / "trading.db"
        health_path = Path(directory) / "health.json"
        store = SqliteAuditStore(db_path)
        observed_at = datetime.now(timezone.utc)
        store.record_broker_state(
            "alpaca-paper",
            observed_at,
            {
                "broker": "alpaca",
                "environment": "paper",
                "observed_at": observed_at,
                "account": {
                    "status": "active",
                    "currency": "USD",
                    "equity": Decimal("100000"),
                    "last_equity": Decimal("99500"),
                    "cash": Decimal("50000"),
                    "buying_power": Decimal("200000"),
                    "long_market_value": Decimal("2500"),
                    "short_market_value": Decimal("0"),
                    "maintenance_margin": Decimal("750"),
                    "gross_notional": Decimal("2500"),
                    "daytrade_count": 1,
                    "pattern_day_trader": False,
                    "trading_blocked": False,
                    "transfers_blocked": False,
                    "account_blocked": False,
                    "trade_suspended_by_user": False,
                    "shorting_enabled": True,
                },
                "market": {
                    "timestamp": observed_at.isoformat(),
                    "is_open": True,
                    "next_open": observed_at.isoformat(),
                    "next_close": observed_at.isoformat(),
                },
                "positions": [
                    {
                        "symbol": "AAPL",
                        "asset_class": "stock",
                        "side": "long",
                        "quantity": Decimal("5"),
                        "market_value": Decimal("2500"),
                        "cost_basis": Decimal("2400"),
                        "average_entry_price": Decimal("480"),
                        "current_price": Decimal("500"),
                        "unrealized_pl": Decimal("100"),
                        "unrealized_pl_percent": Decimal("0.0416667"),
                    }
                ],
                "open_orders": [],
                "request_ids": ["test-request-id"],
            },
            {
                "equity": Decimal("100000"),
                "positions_count": 1,
                "open_orders_count": 0,
            },
        )
        write_health(health_path, "ok", {"environment": "paper"})
        return (
            DashboardData(
                db_path=db_path,
                health_path=health_path,
                health_max_age_seconds=120,
                max_total_open=Decimal("0.10"),
                max_per_trade=Decimal("0.03"),
                asset_universe_program=load_asset_universe_program(
                    Path(__file__).parents[1]
                    / "config"
                    / "asset_universe.json"
                ),
                strategy_experiment_program=(
                    load_strategy_experiment_program(
                        Path(__file__).parents[1]
                        / "config"
                        / "strategy_experiments.json"
                    )
                ),
                release_version="0.7.1",
                build_commit="a" * 40,
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
            self.assertEqual(result["release"]["version"], "0.7.1")
            self.assertEqual(result["release"]["short_commit"], "aaaaaaaa")
            self.assertEqual(result["release"]["commit"], "a" * 40)
            self.assertEqual(result["storage"]["status"], "ok")
            self.assertFalse(
                result["operating_mode"]["paper_execution_enabled"]
            )
            self.assertEqual(result["account"]["equity"], "100000")
            self.assertTrue(result["market"]["is_open"])
            self.assertEqual(len(result["positions"]), 1)
            self.assertEqual(result["connection"]["request_ids"], [
                "test-request-id"
            ])
            self.assertEqual(result["risk"]["active_amount"], "0")
            self.assertEqual(
                result["risk"]["per_trade_capacity_amount"], "3000.00"
            )
            self.assertEqual(len(result["events"]), 1)
            self.assertFalse(result["research"]["execution_enabled"])
            self.assertGreater(len(result["evidence_catalog"]), 0)
            self.assertEqual(result["research_backtests"], [])
            self.assertEqual(result["portfolio_risk_reports"], [])
            self.assertEqual(result["strategy_lab_reports"], [])
            self.assertEqual(result["strategy_model_trials"], [])
            self.assertEqual(
                len(
                    result["strategy_experiments"][
                        "configuration"
                    ]["experiments"]
                ),
                4,
            )
            self.assertEqual(
                result["strategy_experiments"]["summaries"],
                [],
            )
            self.assertFalse(
                result["strategy_experiments"][
                    "execution_enabled"
                ]
            )
            self.assertEqual(result["asset_universe_reports"], [])
            self.assertFalse(
                result["asset_universe"]["execution_enabled"]
            )
            self.assertEqual(
                result["asset_universe"]["configuration"]["policies"][
                    0
                ]["minimum_price"],
                "3",
            )
            self.assertFalse(
                result["strategy_lab"]["execution_enabled"]
            )

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

                root_request = Request(
                    f"http://127.0.0.1:{port}/",
                    headers={"Authorization": f"Basic {token}"},
                )
                with urlopen(root_request, timeout=3) as root_response:
                    html = root_response.read().decode("utf-8")
                    policy = root_response.headers[
                        "Content-Security-Policy"
                    ]

                self.assertIn("Open positions", html)
                self.assertIn('id="release-version"', html)
                self.assertIn(
                    "Deployed Git revision", html
                )
                self.assertIn('id="preferences-dialog"', html)
                self.assertIn('id="preference-theme"', html)
                self.assertIn('id="preference-date-format"', html)
                self.assertIn('Browser location', html)
                self.assertIn('value="light"', html)
                self.assertIn('value="dark"', html)
                self.assertIn('hourCycle: "h23"', html)
                self.assertIn('Asia/Jerusalem', html)
                self.assertIn('data-primary-tab="account"', html)
                self.assertIn(
                    'data-primary-tab="strategy-lab"', html
                )
                self.assertIn(
                    'data-primary-tab="asset-universe"', html
                )
                self.assertIn(
                    'data-primary-tab="allocation"', html
                )
                self.assertIn('id="account-select"', html)
                self.assertIn("Continuous Strategy Lab", html)
                self.assertIn("Chronological stability", html)
                self.assertIn("Trade-sequence stress", html)
                self.assertIn(
                    "Immutable model-trial registry", html
                )
                self.assertIn(
                    "Preregistered strategy experiments", html
                )
                self.assertIn("Trial Registry", html)
                self.assertIn(
                    'data-secondary="robustness"', html
                )
                self.assertIn(
                    "Evidence-gated asset recommendations", html
                )
                self.assertIn("Strategy symbol assignments", html)
                self.assertIn("Account strategy allocation", html)
                self.assertIn('Strategy bots', html)
                self.assertIn('Evidence-weighted market model', html)
                self.assertIn('Research evidence registry', html)
                self.assertIn('Research model validation', html)
                self.assertIn('Universe correlation and concentration', html)
                self.assertNotIn("{{NONCE}}", html)
                self.assertIn("script-src 'nonce-", policy)
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
