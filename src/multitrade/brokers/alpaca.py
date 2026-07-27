from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from multitrade.brokers.base import BrokerOrder
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


class AlpacaPaperBroker:
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

    def get_account_snapshot(self) -> AccountSnapshot:
        account = self._request("GET", "/v2/account")
        positions_payload = self._request("GET", "/v2/positions")
        positions: dict[str, Decimal] = {}
        gross_notional = ZERO
        for row in positions_payload:
            quantity = Decimal(row["qty"])
            if row.get("side") == "short":
                quantity = -quantity
            positions[row["symbol"]] = quantity
            gross_notional += abs(Decimal(row.get("market_value", "0")))

        equity = Decimal(account["equity"])
        last_equity = Decimal(account.get("last_equity", account["equity"]))
        return AccountSnapshot(
            equity=equity,
            start_of_day_equity=last_equity,
            peak_equity=max(equity, last_equity),
            gross_notional=gross_notional,
            positions=positions,
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
                "The MVP submits only market or limit opening orders"
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
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise AlpacaError(
                f"Alpaca returned HTTP {exc.code}: {body}"
            ) from exc
        except URLError as exc:
            raise AlpacaError(f"Cannot reach Alpaca: {exc.reason}") from exc
