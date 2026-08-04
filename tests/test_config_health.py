import os
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from multitrade import __version__
from multitrade.config import Settings, load_env_file
from multitrade.health import check_health, write_health


class EnvironmentFileTests(TestCase):
    def test_runtime_version_matches_package_metadata(self) -> None:
        project = Path(__file__).parents[1]
        with (project / "pyproject.toml").open("rb") as handle:
            package_version = tomllib.load(handle)["project"]["version"]

        self.assertEqual(__version__, package_version)

    def test_analyst_api_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertIs(settings.analyst_api_enabled, False)
        self.assertEqual(settings.analyst_api_token, "")

    def test_env_file_loads_values_without_overriding_process_env(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "FROM_FILE=paper\n"
                "EXISTING_VALUE=must-not-win\n"
                "QUOTED_VALUE=\"quoted\"\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"EXISTING_VALUE": "process-wins"},
                clear=True,
            ):
                load_env_file(env_path)
                self.assertEqual(os.environ["FROM_FILE"], "paper")
                self.assertEqual(
                    os.environ["EXISTING_VALUE"], "process-wins"
                )
                self.assertEqual(os.environ["QUOTED_VALUE"], "quoted")

    def test_health_age_must_cover_two_heartbeat_intervals(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRADING_HEARTBEAT_SECONDS": "30",
                "TRADING_HEALTH_MAX_AGE_SECONDS": "59",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at least twice"):
                Settings.from_env()

    def test_dashboard_password_requires_minimum_length(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DASHBOARD_USERNAME": "operator",
                "DASHBOARD_PASSWORD": "too-short",
            },
            clear=True,
        ):
            settings = Settings.from_env()
            with self.assertRaisesRegex(ValueError, "at least 16"):
                settings.require_dashboard_credentials()

    def test_enabled_analyst_api_requires_unique_strong_token(self) -> None:
        base = {
            "DASHBOARD_USERNAME": "operator",
            "DASHBOARD_PASSWORD": (
                "dashboard-password-long-1234567890"
            ),
            "ANALYST_API_ENABLED": "true",
        }
        with patch.dict(
            os.environ,
            {**base, "ANALYST_API_TOKEN": "too-short"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at least 32"):
                Settings.from_env().require_dashboard_credentials()

        with patch.dict(
            os.environ,
            {
                **base,
                "ANALYST_API_TOKEN": (
                    "dashboard-password-long-1234567890"
                ),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must differ"):
                Settings.from_env().require_dashboard_credentials()

        with patch.dict(
            os.environ,
            {
                **base,
                "ANALYST_API_TOKEN": (
                    "unique-analyst-token-12345678901234567890"
                ),
            },
            clear=True,
        ):
            Settings.from_env().require_dashboard_credentials()

    def test_firm_dimension_limit_cannot_exceed_total(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RISK_FIRM_MAX_TOTAL_OPEN": "0.05",
                "RISK_FIRM_MAX_SYMBOL_OPEN": "0.06",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError, "cannot exceed total-open"
            ):
                Settings.from_env()

    def test_custom_alpaca_credentials_are_resolved_by_prefix(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "FUND_A_API_KEY_ID": "paper-key-a",
                "FUND_A_API_SECRET_KEY": "paper-secret-a",
                "FUND_A_BASE_URL": (
                    "https://paper-api.alpaca.markets"
                ),
            },
            clear=True,
        ):
            credentials = Settings.from_env().alpaca_credentials_for(
                "FUND_A"
            )

        self.assertEqual(
            credentials,
            (
                "paper-key-a",
                "paper-secret-a",
                "https://paper-api.alpaca.markets",
            ),
        )

    def test_custom_alpaca_credentials_refuse_live_endpoint(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "FUND_A_API_KEY_ID": "key",
                "FUND_A_API_SECRET_KEY": "secret",
                "FUND_A_BASE_URL": "https://api.alpaca.markets",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError, "paper-api.alpaca.markets"
            ):
                Settings.from_env().alpaca_credentials_for("FUND_A")


class HealthFileTests(TestCase):
    def test_fresh_successful_heartbeat_is_healthy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            health_path = Path(temporary_directory) / "health.json"
            payload = write_health(health_path, "ok", {"environment": "paper"})
            now = datetime.fromisoformat(payload["updated_at"])

            healthy, result = check_health(
                health_path, max_age_seconds=120, now=now
            )

            self.assertTrue(healthy)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                result["details"]["environment"], "paper"
            )

    def test_stale_heartbeat_is_unhealthy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            health_path = Path(temporary_directory) / "health.json"
            payload = write_health(health_path, "ok")
            updated_at = datetime.fromisoformat(payload["updated_at"])

            healthy, result = check_health(
                health_path,
                max_age_seconds=120,
                now=updated_at + timedelta(seconds=121),
            )

            self.assertFalse(healthy)
            self.assertEqual(result["reason"], "heartbeat_is_stale")

    def test_failed_heartbeat_is_unhealthy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            health_path = Path(temporary_directory) / "health.json"
            write_health(health_path, "error", {"error_type": "TimeoutError"})

            healthy, result = check_health(
                health_path,
                max_age_seconds=120,
                now=datetime.now(timezone.utc),
            )

            self.assertFalse(healthy)
            self.assertEqual(result["reason"], "latest_heartbeat_failed")
