import base64
import json
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from multitrade.audit import SqliteAuditStore
from multitrade.dashboard import DashboardData, create_dashboard_server
from multitrade.domain import AssetClass
from multitrade.experiments import (
    load_strategy_experiment_program,
)
from multitrade.health import write_health
from multitrade.portfolio import AccountPlan, StrategyAllocation
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
            self.assertEqual(
                result["accelerated_validation_runs"], []
            )
            self.assertEqual(result["strategy_model_trials"], [])
            self.assertEqual(result["strategy_performance"], [])
            self.assertEqual(
                len(
                    result["strategy_experiments"][
                        "configuration"
                    ]["experiments"]
                ),
                    18,
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

    def test_overview_exposes_isolated_views_for_each_account(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            service, db_path, _ = self._fixture(directory)
            observed_at = datetime.now(timezone.utc)
            SqliteAuditStore(db_path).record_broker_state(
                "paper-b",
                observed_at,
                {
                    "broker": "alpaca",
                    "environment": "paper",
                    "account": {
                        "status": "active",
                        "currency": "USD",
                        "equity": Decimal("25000"),
                        "buying_power": Decimal("50000"),
                        "cash": Decimal("25000"),
                        "maintenance_margin": Decimal("0"),
                        "gross_notional": Decimal("0"),
                    },
                    "market": {"is_open": False},
                    "positions": [],
                    "open_orders": [],
                    "request_ids": ["paper-b-request"],
                },
                {"equity": Decimal("25000")},
            )

            def plan(
                account_id: str, prefix: str
            ) -> AccountPlan:
                return AccountPlan(
                    account_id=account_id,
                    broker="alpaca",
                    environment="paper",
                    enabled=True,
                    asset_classes=(AssetClass.STOCK,),
                    watchlist=("AAPL",),
                    timeframe="5Min",
                    maximum_positions=4,
                    maximum_daily_orders=6,
                    symbol_cooldown_minutes=60,
                    allocations={},
                    credential_env_prefix=prefix,
                    expected_broker_account_id=(
                        f"broker-{account_id}"
                    ),
                )

            service.account_plans = (
                plan("alpaca-paper", "ALPACA"),
                plan("paper-b", "ALPACA_FUND_B"),
            )
            result = service.overview()

            self.assertEqual(
                set(result["account_views"]),
                {"alpaca-paper", "paper-b"},
            )
            self.assertEqual(
                result["account_views"]["paper-b"]["account"][
                    "equity"
                ],
                "25000",
            )
            self.assertEqual(
                result["account_views"]["paper-b"]["risk"][
                    "per_trade_capacity_amount"
                ],
                "750.00",
            )
            self.assertEqual(
                result["account_views"]["paper-b"]["positions"], []
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
                with urlopen(
                    f"http://127.0.0.1:{port}/", timeout=3
                ) as login_response:
                    login_html = login_response.read().decode("utf-8")
                self.assertIn("Secure operator sign in", login_html)

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
                self.assertIn(
                    "Account-scoped strategy performance", html
                )
                self.assertIn(
                    'id="strategy-performance"', html
                )
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
                self.assertIn('data-primary-tab="management"', html)
                self.assertIn('id="strategy-controls"', html)
                self.assertIn("Trading and research glossary", html)
                self.assertIn("Awaiting registration", html)
                self.assertNotIn("{{CSRF_TOKEN}}", html)
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
                self.assertIn(
                    "Accelerated candidate screening", html
                )
                self.assertIn(
                    'id="accelerated-scorecards"', html
                )
                self.assertIn(
                    'id="accelerated-diagnostics"', html
                )
                self.assertIn(
                    'id="accelerated-research-decisions"', html
                )
                self.assertIn(
                    'id="accelerated-optimization"', html
                )
                self.assertIn(
                    "Trade attribution diagnostics", html
                )
                self.assertIn("Research decision queue", html)
                self.assertIn("Nested parameter optimization", html)
                self.assertIn(
                    "prospective trial count was not incremented",
                    html,
                )
                self.assertIn("Chronological stability", html)
                self.assertIn("Trade-sequence stress", html)
                self.assertIn(
                    "Immutable model-trial registry", html
                )
                self.assertIn(
                    "Frozen strategy-family comparison", html
                )
                self.assertIn("Family Comparison", html)
                self.assertIn(
                    "Observed / frozen candidates", html
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

    def test_form_login_creates_secure_session_and_logout_revokes_it(
        self,
    ) -> None:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                del args, kwargs
                return None

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
            opener = build_opener(NoRedirect())
            try:
                with urlopen(
                    f"http://127.0.0.1:{port}/login", timeout=3
                ) as response:
                    login_html = response.read().decode("utf-8")
                    policy = response.headers["Content-Security-Policy"]
                csrf = re.search(
                    r'name="csrf_token" value="([^"]+)"', login_html
                ).group(1)
                self.assertIn("form-action 'self'", policy)

                body = (
                    f"csrf_token={csrf}&username=operator&"
                    "password=a-long-test-password"
                ).encode("ascii")
                request = Request(
                    f"http://127.0.0.1:{port}/login",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": (
                            "application/x-www-form-urlencoded"
                        )
                    },
                )
                with self.assertRaises(HTTPError) as redirect:
                    opener.open(request, timeout=3)
                self.assertEqual(redirect.exception.code, 303)
                cookie = redirect.exception.headers["Set-Cookie"]
                redirect.exception.close()
                self.assertIn("Secure", cookie)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=Strict", cookie)
                session_cookie = cookie.split(";", 1)[0]

                root = Request(
                    f"http://127.0.0.1:{port}/",
                    headers={"Cookie": session_cookie},
                )
                with urlopen(root, timeout=3) as response:
                    dashboard_html = response.read().decode("utf-8")
                self.assertIn("Broker monitoring", dashboard_html)
                logout_csrf = re.search(
                    r'name="csrf-token" content="([^"]+)"',
                    dashboard_html,
                ).group(1)
                logout_body = f"csrf_token={logout_csrf}".encode("ascii")
                logout = Request(
                    f"http://127.0.0.1:{port}/logout",
                    data=logout_body,
                    method="POST",
                    headers={
                        "Cookie": session_cookie,
                        "Content-Type": (
                            "application/x-www-form-urlencoded"
                        ),
                    },
                )
                with self.assertRaises(HTTPError) as redirect:
                    opener.open(logout, timeout=3)
                self.assertEqual(redirect.exception.code, 303)
                self.assertIn(
                    "Max-Age=0",
                    redirect.exception.headers["Set-Cookie"],
                )
                redirect.exception.close()

                with urlopen(root, timeout=3) as response:
                    signed_out_html = response.read().decode("utf-8")
                self.assertIn("Secure operator sign in", signed_out_html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_authenticated_csrf_protected_paper_configuration_update(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            service, _, _ = self._fixture(directory)
            service.account_plans = (
                AccountPlan(
                    account_id="alpaca-paper",
                    broker="alpaca",
                    environment="paper",
                    enabled=True,
                    asset_classes=(AssetClass.STOCK,),
                    watchlist=("AAPL",),
                    timeframe="5Min",
                    maximum_positions=4,
                    maximum_daily_orders=6,
                    symbol_cooldown_minutes=60,
                    allocations={
                        "breakout_retest": StrategyAllocation(
                            strategy_id="breakout_retest",
                            enabled=False,
                            capital_weight=Decimal("0.20"),
                            risk_fraction=Decimal("0.005"),
                            minimum_confidence=Decimal("0.60"),
                        )
                    },
                ),
            )
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
            token = base64.b64encode(
                b"operator:a-long-test-password"
            ).decode("ascii")
            authorization = f"Basic {token}"
            try:
                root = Request(
                    f"http://127.0.0.1:{port}/",
                    headers={"Authorization": authorization},
                )
                with urlopen(root, timeout=3) as response:
                    html = response.read().decode("utf-8")
                csrf = re.search(
                    r'name="csrf-token" content="([^"]+)"', html
                ).group(1)
                payload = json.dumps(
                    {
                        "account_id": "alpaca-paper",
                        "strategy_id": "breakout_retest",
                        "enabled": True,
                        "paper_execution_allowed": True,
                        "symbols": ["NVDA", "AMD"],
                        "expected_revision": 0,
                        "confirmation": "APPLY PAPER CONFIG",
                    }
                ).encode("utf-8")

                missing_csrf = Request(
                    f"http://127.0.0.1:{port}/api/config/strategy",
                    data=payload,
                    method="POST",
                    headers={
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                    },
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(missing_csrf, timeout=3)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()

                update = Request(
                    f"http://127.0.0.1:{port}/api/config/strategy",
                    data=payload,
                    method="POST",
                    headers={
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf,
                    },
                )
                with urlopen(update, timeout=3) as response:
                    result = json.loads(response.read())
                self.assertEqual(result["configuration"]["revision"], 1)

                overview = service.overview()
                allocation = overview["configured_accounts"][0][
                    "allocations"
                ][0]
                self.assertTrue(allocation["enabled"])
                self.assertTrue(
                    allocation["paper_execution_allowed"]
                )
                self.assertEqual(allocation["symbols"], ["NVDA", "AMD"])
                self.assertIn(
                    "NVDA",
                    overview["configured_accounts"][0]["watchlist"],
                )
                self.assertEqual(
                    overview["events"][0]["event_type"],
                    "strategy_configuration_changed",
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_analyst_gateway_is_read_only_redacted_and_audited(self) -> None:
        with TemporaryDirectory() as directory:
            service, _, _ = self._fixture(directory)
            service.account_plans = (
                AccountPlan(
                    account_id="alpaca-paper",
                    broker="alpaca",
                    environment="paper",
                    enabled=True,
                    asset_classes=(AssetClass.STOCK,),
                    watchlist=("AAPL",),
                    timeframe="5Min",
                    maximum_positions=4,
                    maximum_daily_orders=6,
                    symbol_cooldown_minutes=60,
                    allocations={},
                    credential_env_prefix="ALPACA_SECRET_PREFIX",
                    expected_broker_account_id="broker-account-private",
                ),
            )
            analyst_token = "analyst-test-token-unique-1234567890"
            server = create_dashboard_server(
                "127.0.0.1",
                0,
                service,
                username="operator",
                password="a-long-test-password",
                analyst_api_enabled=True,
                analyst_api_token=analyst_token,
                analyst_requests_per_minute=2,
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            port = server.server_address[1]
            try:
                unauthorized = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/snapshot",
                    headers={"Authorization": "Bearer wrong"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(unauthorized, timeout=3)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()

                headers = {"Authorization": f"Bearer {analyst_token}"}
                snapshot_request = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/snapshot",
                    headers=headers,
                )
                with urlopen(snapshot_request, timeout=3) as response:
                    snapshot = json.loads(response.read())
                    self.assertEqual(
                        response.headers["Cache-Control"], "no-store"
                    )

                encoded = json.dumps(snapshot)
                self.assertEqual(snapshot["schema_version"], "analyst.v1")
                self.assertIn("accelerated_validation_runs", snapshot)
                self.assertNotIn("ALPACA_SECRET_PREFIX", encoded)
                self.assertNotIn("test-request-id", encoded)
                self.assertNotIn(analyst_token, encoded)

                post = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/snapshot",
                    data=b"{}",
                    method="POST",
                    headers=headers,
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(post, timeout=3)
                self.assertEqual(error.exception.code, 405)
                error.exception.close()

                health = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/health",
                    headers=headers,
                )
                with urlopen(health, timeout=3) as response:
                    self.assertEqual(response.status, 200)

                limited = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/trades",
                    headers=headers,
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(limited, timeout=3)
                self.assertEqual(error.exception.code, 429)
                error.exception.close()

                events = service.overview()["events"]
                analyst_reads = [
                    event
                    for event in events
                    if event["event_type"] == "analyst_api_read"
                ]
                self.assertEqual(len(analyst_reads), 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_disabled_analyst_gateway_is_not_discoverable(self) -> None:
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
                request = Request(
                    f"http://127.0.0.1:{port}/api/analyst/v1/health",
                    headers={"Authorization": "Bearer any-token"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
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
