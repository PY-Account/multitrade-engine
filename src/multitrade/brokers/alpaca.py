from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from multitrade.brokers.base import (
    BrokerAccount,
    BrokerMarketClock,
    BrokerOpenOrder,
    BrokerOrder,
    BrokerPosition,
    BrokerReconciliation,
)
from multitrade.config import PAPER_URL
from multitrade.domain import (
    AccountSnapshot,
    AssetClass,
    OrderType,
    Side,
    TradeIntent,
    ZERO,
)


class AlpacaError(RuntimeError):
    pass


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _decimal(value: Any, default: Any = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(str(default))
    return Decimal(str(value))


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _asset_class(value: Any) -> AssetClass:
    normalized = str(value or "").lower()
    mapping = {
        "us_equity": AssetClass.STOCK,
        "stock": AssetClass.STOCK,
        "us_option": AssetClass.OPTION,
        "option": AssetClass.OPTION,
        "crypto": AssetClass.CRYPTO,
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise AlpacaError(
            f"Unsupported Alpaca asset class: {normalized or 'missing'}"
        ) from exc


class AlpacaPaperBroker:
    _ACTIVE_ORDER_STATES = frozenset(
        {
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
    )

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        base_url: str = PAPER_URL,
        timeout_seconds: int = 15,
    ) -> None:
        if base_url.rstrip("/") != PAPER_URL:
            raise ValueError("AlpacaPaperBroker refuses non-Paper endpoints")
        if not key_id or not secret_key:
            raise ValueError("Alpaca Paper credentials are required")
        self.base_url = PAPER_URL
        self.timeout_seconds = timeout_seconds
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self._request_ids: list[str] = []

    def reconcile(self) -> BrokerReconciliation:
        self._request_ids = []
        account = self._request("GET", "/v2/account")
        positions_payload = self._request("GET", "/v2/positions")
        orders_payload = self._request(
            "GET",
            "/v2/orders",
            query={
                "status": "all",
                "limit": "500",
                "nested": "true",
                "direction": "desc",
            },
        )
        clock_payload = self._request("GET", "/v2/clock")

        if not isinstance(account, dict):
            raise AlpacaError("Alpaca account response was not an object")
        if not isinstance(positions_payload, list):
            raise AlpacaError("Alpaca positions response was not a list")
        if not isinstance(orders_payload, list):
            raise AlpacaError("Alpaca orders response was not a list")
        if not isinstance(clock_payload, dict):
            raise AlpacaError("Alpaca clock response was not an object")

        positions = tuple(
            self._parse_position(row) for row in positions_payload
        )
        gross_notional = sum(
            (abs(position.market_value) for position in positions),
            start=ZERO,
        )
        normalized_account = BrokerAccount(
            status=str(account.get("status", "unknown")).lower(),
            currency=str(account.get("currency", "USD")).upper(),
            equity=_decimal(account.get("equity")),
            last_equity=_decimal(
                account.get("last_equity"), account.get("equity", "0")
            ),
            cash=_decimal(account.get("cash")),
            buying_power=_decimal(account.get("buying_power")),
            long_market_value=_decimal(
                account.get("long_market_value")
            ),
            short_market_value=_decimal(
                account.get("short_market_value")
            ),
            maintenance_margin=_decimal(
                account.get("maintenance_margin")
            ),
            gross_notional=gross_notional,
            daytrade_count=int(account.get("daytrade_count") or 0),
            pattern_day_trader=bool(
                account.get("pattern_day_trader", False)
            ),
            trading_blocked=bool(
                account.get("trading_blocked", False)
            ),
            transfers_blocked=bool(
                account.get("transfers_blocked", False)
            ),
            account_blocked=bool(
                account.get("account_blocked", False)
            ),
            trade_suspended_by_user=bool(
                account.get("trade_suspended_by_user", False)
            ),
            shorting_enabled=bool(
                account.get("shorting_enabled", False)
            ),
        )
        market = BrokerMarketClock(
            timestamp=str(clock_payload.get("timestamp", "")),
            is_open=bool(clock_payload.get("is_open", False)),
            next_open=str(clock_payload.get("next_open", "")),
            next_close=str(clock_payload.get("next_close", "")),
        )
        recent_orders = tuple(
            self._parse_open_order(row) for row in orders_payload
        )
        open_orders = tuple(
            order
            for order in recent_orders
            if order.status in self._ACTIVE_ORDER_STATES
            or order.has_active_legs
        )
        return BrokerReconciliation(
            broker="alpaca",
            environment="paper",
            observed_at=datetime.now(timezone.utc),
            account=normalized_account,
            market=market,
            positions=positions,
            open_orders=open_orders,
            recent_orders=recent_orders,
            request_ids=tuple(self._request_ids),
        )

    def get_account_snapshot(self) -> AccountSnapshot:
        return self.reconcile().account_snapshot()

    @staticmethod
    def _parse_position(row: dict[str, Any]) -> BrokerPosition:
        return BrokerPosition(
            symbol=str(row["symbol"]),
            asset_class=_asset_class(row.get("asset_class")),
            side=str(row.get("side", "long")).lower(),
            quantity=abs(_decimal(row.get("qty"))),
            market_value=_decimal(row.get("market_value")),
            cost_basis=_decimal(row.get("cost_basis")),
            average_entry_price=_decimal(
                row.get("avg_entry_price")
            ),
            current_price=_decimal(row.get("current_price")),
            unrealized_pl=_decimal(row.get("unrealized_pl")),
            unrealized_pl_percent=_decimal(
                row.get("unrealized_plpc")
            ),
        )

    @staticmethod
    def _parse_open_order(row: dict[str, Any]) -> BrokerOpenOrder:
        legs = row.get("legs") or []
        order_class = str(
            row.get("order_class", "simple")
        ).lower()
        filled_exit_leg = next(
            (
                leg
                for leg in legs
                if order_class == "bracket"
                and str(leg.get("status", "")).lower() == "filled"
            ),
            None,
        )
        asset_class = row.get("asset_class")
        if not asset_class and legs:
            asset_class = legs[0].get("asset_class")
        symbol = row.get("symbol")
        if not symbol and len(legs) == 1:
            symbol = legs[0].get("symbol")
        if not symbol:
            symbol = "MULTI-LEG"
        return BrokerOpenOrder(
            broker_order_id=str(row.get("id", "")),
            client_order_id=str(row.get("client_order_id", "")),
            symbol=str(symbol),
            asset_class=_asset_class(asset_class),
            side=str(row.get("side", "")).lower(),
            order_type=str(row.get("type", "")).lower(),
            order_class=order_class,
            status=str(row.get("status", "unknown")).lower(),
            quantity=abs(_decimal(row.get("qty"))),
            filled_quantity=abs(_decimal(row.get("filled_qty"))),
            limit_price=_optional_decimal(row.get("limit_price")),
            stop_price=_optional_decimal(row.get("stop_price")),
            submitted_at=str(row.get("submitted_at", "")),
            legs_count=len(legs),
            filled_average_price=_optional_decimal(
                row.get("filled_avg_price")
            ),
            filled_at=str(row.get("filled_at") or ""),
            canceled_at=str(row.get("canceled_at") or ""),
            expired_at=str(row.get("expired_at") or ""),
            has_active_legs=any(
                str(leg.get("status", "")).lower()
                in AlpacaPaperBroker._ACTIVE_ORDER_STATES
                for leg in legs
            ),
            exit_leg_type=(
                str(filled_exit_leg.get("type") or "").lower()
                if filled_exit_leg is not None
                else ""
            ),
            exit_filled_average_price=(
                _optional_decimal(
                    filled_exit_leg.get("filled_avg_price")
                )
                if filled_exit_leg is not None
                else None
            ),
            exit_filled_at=(
                str(filled_exit_leg.get("filled_at") or "")
                if filled_exit_leg is not None
                else ""
            ),
        )

    def submit_order(
        self, intent: TradeIntent, approved_quantity: Decimal
    ) -> BrokerOrder:
        payload = self.build_order_payload(intent, approved_quantity)
        response = self._request("POST", "/v2/orders", payload)
        return BrokerOrder(
            broker_order_id=str(response["id"]),
            status=str(response.get("status", "accepted")),
            raw=response,
        )

    @staticmethod
    def build_order_payload(
        intent: TradeIntent, approved_quantity: Decimal
    ) -> dict[str, Any]:
        if intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            raise ValueError(
                "This release submits only market or limit opening orders"
            )
        payload: dict[str, Any] = {
            "qty": _decimal_text(approved_quantity),
            "type": intent.order_type.value,
            "time_in_force": intent.time_in_force.value,
            "client_order_id": intent.intent_id,
        }
        if intent.limit_price is not None:
            payload["limit_price"] = _decimal_text(intent.limit_price)

        if intent.asset_class is AssetClass.OPTION:
            if intent.time_in_force.value != "day":
                raise ValueError(
                    "Alpaca option orders must use day time-in-force"
                )
            if len(intent.option_legs) == 1:
                leg = intent.option_legs[0]
                payload["symbol"] = leg.symbol
                payload["side"] = leg.side.value
                return payload
            payload["order_class"] = "mleg"
            payload["legs"] = [
                {
                    "symbol": leg.symbol,
                    "ratio_qty": str(leg.ratio),
                    "side": leg.side.value,
                    "position_intent": (
                        "buy_to_open"
                        if leg.side is Side.BUY
                        else "sell_to_open"
                    ),
                }
                for leg in intent.option_legs
            ]
            return payload

        payload["symbol"] = intent.symbol
        payload["side"] = intent.side.value
        if (
            intent.asset_class is AssetClass.STOCK
            and intent.stop_price is not None
            and intent.take_profit_price is not None
        ):
            payload["order_class"] = "bracket"
            payload["take_profit"] = {
                "limit_price": _decimal_text(
                    intent.take_profit_price
                )
            }
            payload["stop_loss"] = {
                "stop_price": _decimal_text(intent.stop_price)
            }
        return payload

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = None
        headers = dict(self._headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                request_id = response.headers.get("X-Request-ID")
                if request_id:
                    self._request_ids.append(request_id)
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as exc:
            request_id = (
                exc.headers.get("X-Request-ID")
                if exc.headers is not None
                else None
            )
            if request_id:
                self._request_ids.append(request_id)
            body = exc.read().decode("utf-8", errors="replace")
            request_context = (
                f" request_id={request_id}" if request_id else ""
            )
            raise AlpacaError(
                f"Alpaca returned HTTP {exc.code}:{request_context} "
                f"{body[:1000]}"
            ) from exc
        except URLError as exc:
            raise AlpacaError(f"Cannot reach Alpaca: {exc.reason}") from exc
