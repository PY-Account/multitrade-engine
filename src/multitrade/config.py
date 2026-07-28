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
    emergency_stop: bool
    db_path: Path
    heartbeat_seconds: int
    health_path: Path
    health_max_age_seconds: int
    automation_enabled: bool
    strategy_cycle_seconds: int
    strategy_health_path: Path
    strategy_health_max_age_seconds: int
    portfolio_config_path: Path
    market_data_feed: str
    option_data_feed: str
    market_lookback_days: int
    market_max_bar_age_seconds: int
    research_cycle_seconds: int
    research_health_path: Path
    research_health_max_age_seconds: int
    research_program_path: Path
    research_lookback_days: int
    strategy_lab_cycle_seconds: int
    strategy_lab_health_path: Path
    strategy_lab_health_max_age_seconds: int
    strategy_lab_lookback_days: int
    strategy_lab_base_cost_bps: Decimal
    strategy_lab_stressed_cost_bps: Decimal
    strategy_lab_chronological_folds: int
    strategy_lab_trade_sequence_paths: int
    asset_universe_cycle_seconds: int
    asset_universe_health_path: Path
    asset_universe_health_max_age_seconds: int
    asset_universe_config_path: Path
    sec_user_agent: str
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
                "This release is Paper-only; ALPACA_BASE_URL must be "
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
        strategy_cycle_seconds = _int_env(
            "TRADING_STRATEGY_CYCLE_SECONDS", "300", 60
        )
        strategy_health_max_age_seconds = _int_env(
            "TRADING_STRATEGY_HEALTH_MAX_AGE_SECONDS", "900", 180
        )
        if strategy_health_max_age_seconds < strategy_cycle_seconds * 2:
            raise ValueError(
                "TRADING_STRATEGY_HEALTH_MAX_AGE_SECONDS must be at "
                "least twice TRADING_STRATEGY_CYCLE_SECONDS"
            )
        research_cycle_seconds = _int_env(
            "TRADING_RESEARCH_CYCLE_SECONDS", "3600", 300, 86400
        )
        research_health_max_age_seconds = _int_env(
            "TRADING_RESEARCH_HEALTH_MAX_AGE_SECONDS",
            "10800",
            900,
            259200,
        )
        if research_health_max_age_seconds < research_cycle_seconds * 2:
            raise ValueError(
                "TRADING_RESEARCH_HEALTH_MAX_AGE_SECONDS must be at "
                "least twice TRADING_RESEARCH_CYCLE_SECONDS"
            )
        strategy_lab_cycle_seconds = _int_env(
            "TRADING_STRATEGY_LAB_CYCLE_SECONDS",
            "21600",
            3600,
            604800,
        )
        strategy_lab_health_max_age_seconds = _int_env(
            "TRADING_STRATEGY_LAB_HEALTH_MAX_AGE_SECONDS",
            "64800",
            7200,
            1209600,
        )
        if (
            strategy_lab_health_max_age_seconds
            < strategy_lab_cycle_seconds * 2
        ):
            raise ValueError(
                "TRADING_STRATEGY_LAB_HEALTH_MAX_AGE_SECONDS must be "
                "at least twice TRADING_STRATEGY_LAB_CYCLE_SECONDS"
            )
        strategy_lab_base_cost_bps = _decimal_env(
            "TRADING_STRATEGY_LAB_BASE_COST_BPS", "10"
        )
        strategy_lab_stressed_cost_bps = _decimal_env(
            "TRADING_STRATEGY_LAB_STRESSED_COST_BPS", "25"
        )
        if (
            strategy_lab_base_cost_bps < Decimal("0")
            or strategy_lab_stressed_cost_bps
            < strategy_lab_base_cost_bps
        ):
            raise ValueError(
                "Strategy Lab costs must be non-negative and stressed "
                "costs cannot be below base costs"
            )
        asset_universe_cycle_seconds = _int_env(
            "TRADING_ASSET_UNIVERSE_CYCLE_SECONDS",
            "86400",
            3600,
            604800,
        )
        asset_universe_health_max_age_seconds = _int_env(
            "TRADING_ASSET_UNIVERSE_HEALTH_MAX_AGE_SECONDS",
            "259200",
            7200,
            1209600,
        )
        if (
            asset_universe_health_max_age_seconds
            < asset_universe_cycle_seconds * 2
        ):
            raise ValueError(
                "TRADING_ASSET_UNIVERSE_HEALTH_MAX_AGE_SECONDS must be "
                "at least twice TRADING_ASSET_UNIVERSE_CYCLE_SECONDS"
            )
        market_data_feed = os.getenv(
            "TRADING_MARKET_DATA_FEED", "iex"
        ).strip().lower()
        if market_data_feed not in {"iex", "sip"}:
            raise ValueError(
                "TRADING_MARKET_DATA_FEED must be iex or sip"
            )
        option_data_feed = os.getenv(
            "TRADING_OPTION_DATA_FEED", "indicative"
        ).strip().lower()
        if option_data_feed not in {"indicative", "opra"}:
            raise ValueError(
                "TRADING_OPTION_DATA_FEED must be indicative or opra"
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
            emergency_stop=_bool_env(
                "TRADING_EMERGENCY_STOP", False
            ),
            db_path=Path(
                os.getenv("TRADING_DB_PATH", "var/trading.db")
            ),
            heartbeat_seconds=heartbeat_seconds,
            health_path=Path(
                os.getenv("TRADING_HEALTH_PATH", "var/health.json")
            ),
            health_max_age_seconds=health_max_age_seconds,
            automation_enabled=_bool_env(
                "TRADING_AUTOMATION_ENABLED", False
            ),
            strategy_cycle_seconds=strategy_cycle_seconds,
            strategy_health_path=Path(
                os.getenv(
                    "TRADING_STRATEGY_HEALTH_PATH",
                    "var/strategy-health.json",
                )
            ),
            strategy_health_max_age_seconds=(
                strategy_health_max_age_seconds
            ),
            portfolio_config_path=Path(
                os.getenv(
                    "TRADING_PORTFOLIO_CONFIG",
                    "config/paper_portfolio.json",
                )
            ),
            market_data_feed=market_data_feed,
            option_data_feed=option_data_feed,
            market_lookback_days=_int_env(
                "TRADING_MARKET_LOOKBACK_DAYS", "10", 2, 90
            ),
            market_max_bar_age_seconds=_int_env(
                "TRADING_MARKET_MAX_BAR_AGE_SECONDS", "900", 60, 86400
            ),
            research_cycle_seconds=research_cycle_seconds,
            research_health_path=Path(
                os.getenv(
                    "TRADING_RESEARCH_HEALTH_PATH",
                    "var/research-health.json",
                )
            ),
            research_health_max_age_seconds=(
                research_health_max_age_seconds
            ),
            research_program_path=Path(
                os.getenv(
                    "TRADING_RESEARCH_PROGRAM",
                    "config/research_program.json",
                )
            ),
            research_lookback_days=_int_env(
                "TRADING_RESEARCH_LOOKBACK_DAYS", "1500", 400, 1500
            ),
            strategy_lab_cycle_seconds=strategy_lab_cycle_seconds,
            strategy_lab_health_path=Path(
                os.getenv(
                    "TRADING_STRATEGY_LAB_HEALTH_PATH",
                    "var/strategy-lab-health.json",
                )
            ),
            strategy_lab_health_max_age_seconds=(
                strategy_lab_health_max_age_seconds
            ),
            strategy_lab_lookback_days=_int_env(
                "TRADING_STRATEGY_LAB_LOOKBACK_DAYS",
                "120",
                30,
                365,
            ),
            strategy_lab_base_cost_bps=strategy_lab_base_cost_bps,
            strategy_lab_stressed_cost_bps=(
                strategy_lab_stressed_cost_bps
            ),
            strategy_lab_chronological_folds=_int_env(
                "TRADING_STRATEGY_LAB_CHRONOLOGICAL_FOLDS",
                "3",
                2,
                6,
            ),
            strategy_lab_trade_sequence_paths=_int_env(
                "TRADING_STRATEGY_LAB_TRADE_SEQUENCE_PATHS",
                "500",
                100,
                5000,
            ),
            asset_universe_cycle_seconds=(
                asset_universe_cycle_seconds
            ),
            asset_universe_health_path=Path(
                os.getenv(
                    "TRADING_ASSET_UNIVERSE_HEALTH_PATH",
                    "var/asset-universe-health.json",
                )
            ),
            asset_universe_health_max_age_seconds=(
                asset_universe_health_max_age_seconds
            ),
            asset_universe_config_path=Path(
                os.getenv(
                    "TRADING_ASSET_UNIVERSE_CONFIG",
                    "config/asset_universe.json",
                )
            ),
            sec_user_agent=os.getenv(
                "TRADING_SEC_USER_AGENT", ""
            ).strip(),
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

    @property
    def paper_execution_enabled(self) -> bool:
        return (
            self.automation_enabled
            and self.enable_paper_orders
            and not self.emergency_stop
        )
