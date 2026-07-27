from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from multitrade.risk import RiskPolicy


PAPER_URL = "https://paper-api.alpaca.markets"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: str | Path = ".env") -> None:
    """Load a small, predictable subset of dotenv syntax.

    Existing process variables always win. This keeps Docker, CI, and
    production secret injection authoritative while making local use simple.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return

    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            raise ValueError(
                f"{env_path}:{line_number} is not a valid KEY=VALUE line"
            )
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _decimal_env(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number") from exc


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _int_env(
    name: str,
    default: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    alpaca_key_id: str
    alpaca_secret_key: str
    alpaca_base_url: str
    enable_paper_orders: bool
    db_path: Path
    heartbeat_seconds: int
    health_path: Path
    health_max_age_seconds: int
    dashboard_host: str
    dashboard_port: int
    dashboard_username: str
    dashboard_password: str
    risk_policy: RiskPolicy

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        if base_url != PAPER_URL:
            raise ValueError(
                "This MVP is Paper-only; ALPACA_BASE_URL must be "
                f"{PAPER_URL}"
            )

        heartbeat_seconds = _int_env(
            "TRADING_HEARTBEAT_SECONDS", "30", 5
        )
        health_max_age_seconds = _int_env(
            "TRADING_HEALTH_MAX_AGE_SECONDS", "120", 15
        )
        if health_max_age_seconds < heartbeat_seconds * 2:
            raise ValueError(
                "TRADING_HEALTH_MAX_AGE_SECONDS must be at least twice "
                "TRADING_HEARTBEAT_SECONDS"
            )

        return cls(
            alpaca_key_id=os.getenv("ALPACA_API_KEY_ID", "").strip(),
            alpaca_secret_key=os.getenv(
                "ALPACA_API_SECRET_KEY", ""
            ).strip(),
            alpaca_base_url=base_url,
            enable_paper_orders=_bool_env(
                "TRADING_ENABLE_PAPER_ORDERS", False
            ),
            db_path=Path(
                os.getenv("TRADING_DB_PATH", "var/trading.db")
            ),
            heartbeat_seconds=heartbeat_seconds,
            health_path=Path(
                os.getenv("TRADING_HEALTH_PATH", "var/health.json")
            ),
            health_max_age_seconds=health_max_age_seconds,
            dashboard_host=os.getenv(
                "DASHBOARD_HOST", "127.0.0.1"
            ).strip(),
            dashboard_port=_int_env(
                "DASHBOARD_PORT", "8080", 1, 65535
            ),
            dashboard_username=os.getenv(
                "DASHBOARD_USERNAME", ""
            ).strip(),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            risk_policy=RiskPolicy(
                max_per_trade=_decimal_env(
                    "RISK_MAX_PER_TRADE", "0.03"
                ),
                max_total_open=_decimal_env(
                    "RISK_MAX_TOTAL_OPEN", "0.10"
                ),
                max_daily_loss=_decimal_env(
                    "RISK_MAX_DAILY_LOSS", "0.03"
                ),
                max_drawdown=_decimal_env(
                    "RISK_MAX_DRAWDOWN", "0.10"
                ),
                max_notional_per_trade=_decimal_env(
                    "RISK_MAX_NOTIONAL_PER_TRADE", "0.25"
                ),
                stock_stress_move=_decimal_env(
                    "RISK_STOCK_STRESS_MOVE", "0.05"
                ),
                crypto_stress_move=_decimal_env(
                    "RISK_CRYPTO_STRESS_MOVE", "0.10"
                ),
                stock_slippage_bps=_decimal_env(
                    "RISK_STOCK_SLIPPAGE_BPS", "25"
                ),
                crypto_slippage_bps=_decimal_env(
                    "RISK_CRYPTO_SLIPPAGE_BPS", "100"
                ),
                option_slippage_per_package=_decimal_env(
                    "RISK_OPTION_SLIPPAGE_PER_PACKAGE", "5"
                ),
            ),
        )

    def require_alpaca_credentials(self) -> None:
        if not self.alpaca_key_id or not self.alpaca_secret_key:
            raise ValueError(
                "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required"
            )

    def require_dashboard_credentials(self) -> None:
        if not self.dashboard_username or not self.dashboard_password:
            raise ValueError(
                "DASHBOARD_USERNAME and DASHBOARD_PASSWORD are required"
            )
        if ":" in self.dashboard_username:
            raise ValueError("DASHBOARD_USERNAME cannot contain ':'")
        if len(self.dashboard_password) < 16:
            raise ValueError(
                "DASHBOARD_PASSWORD must contain at least 16 characters"
            )
