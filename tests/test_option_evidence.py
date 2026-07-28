from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.domain import AssetClass
from multitrade.market import MarketBar
from multitrade.option_evidence import evaluate_option_package_path
from multitrade.options import AlpacaHistoricalOptionDataClient


def option_bar(
    symbol: str,
    timestamp: str,
    close: str,
) -> MarketBar:
    value = Decimal(close)
    return MarketBar(
        symbol=symbol,
        asset_class=AssetClass.OPTION,
        timeframe="15Min",
        timestamp=datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        ),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal("100"),
        trade_count=10,
        vwap=value,
        feed="indicative",
    )


def observation(
    *,
    intent_id: str = "option-intent",
    opening_net_price: str = "1.20",
    first_side: str = "buy",
    second_side: str = "sell",
) -> dict:
    return {
        "intent_id": intent_id,
        "account_id": "alpaca-paper",
        "strategy_id": "option_breakout",
        "structure": "bull_call_debit_spread",
        "underlying": "AAPL",
        "opening_net_price": opening_net_price,
        "legs": [
            {
                "symbol": "AAPL260918C00150000",
                "side": first_side,
                "ratio": 1,
                "expiration": "2026-09-18",
            },
            {
                "symbol": "AAPL260918C00155000",
                "side": second_side,
                "ratio": 1,
                "expiration": "2026-09-18",
            },
        ],
        "details": {
            "intent_explanation": {
                "expiration": "2026-09-18",
                "profit_target_fraction": "0.50",
                "loss_limit_multiple": "1.50",
                "exit_before_expiry_days": 7,
            }
        },
    }


class HistoricalOptionDataTests(TestCase):
    def test_exact_contract_bars_are_paginated_and_normalized(
        self,
    ) -> None:
        client = AlpacaHistoricalOptionDataClient(
            "paper-key", "paper-secret"
        )
        client._request = Mock(
            side_effect=[
                {
                    "bars": {
                        "AAPL260918C00150000": [
                            {
                                "t": "2026-07-28T14:30:00Z",
                                "o": 2,
                                "h": 2.2,
                                "l": 1.9,
                                "c": 2.1,
                                "v": 100,
                                "n": 12,
                                "vw": 2.05,
                            }
                        ]
                    },
                    "next_page_token": "next",
                },
                {
                    "bars": {
                        "AAPL260918C00150000": [
                            {
                                "t": "2026-07-28T14:45:00Z",
                                "o": 2.1,
                                "h": 2.3,
                                "l": 2,
                                "c": 2.2,
                                "v": 80,
                                "n": 9,
                            }
                        ]
                    },
                    "next_page_token": None,
                },
            ]
        )

        result = client.fetch_bars(
            ("AAPL260918C00150000",),
            "15Min",
            datetime(2026, 7, 28, 14, tzinfo=timezone.utc),
            datetime(2026, 7, 28, 16, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [bar.close for bar in result["AAPL260918C00150000"]],
            [Decimal("2.1"), Decimal("2.2")],
        )
        self.assertTrue(
            all(
                bar.asset_class is AssetClass.OPTION
                for bar in result["AAPL260918C00150000"]
            )
        )
        self.assertEqual(client._request.call_count, 2)


class OptionPackageEvidenceTests(TestCase):
    def test_debit_package_proxy_uses_complete_aligned_structure(
        self,
    ) -> None:
        timestamp = "2026-07-28T15:00:00Z"
        bars = {
            "AAPL260918C00150000": (
                option_bar(
                    "AAPL260918C00150000", timestamp, "3.00"
                ),
            ),
            "AAPL260918C00155000": (
                option_bar(
                    "AAPL260918C00155000", timestamp, "1.20"
                ),
            ),
        }

        report = evaluate_option_package_path(
            observation(),
            bars,
            evaluated_at=datetime(
                2026, 7, 28, 16, tzinfo=timezone.utc
            ),
            timeframe="15Min",
            data_feed="indicative",
            slippage_per_leg=Decimal("0"),
        )

        self.assertEqual(report["aligned_points"], 1)
        self.assertEqual(report["latest_proxy_pnl"], "60.00")
        self.assertEqual(
            report["first_policy_exit_reason"], "profit_target"
        )
        self.assertFalse(report["details"]["realized_pnl_attribution"])

    def test_credit_package_decay_is_positive_without_theta_claim(
        self,
    ) -> None:
        timestamp = "2026-07-28T15:00:00Z"
        candidate = observation(
            opening_net_price="-0.80",
            first_side="sell",
            second_side="buy",
        )
        candidate["structure"] = "bull_put_credit_spread"
        bars = {
            "AAPL260918C00150000": (
                option_bar(
                    "AAPL260918C00150000", timestamp, "0.40"
                ),
            ),
            "AAPL260918C00155000": (
                option_bar(
                    "AAPL260918C00155000", timestamp, "0.10"
                ),
            ),
        }

        report = evaluate_option_package_path(
            candidate,
            bars,
            evaluated_at=datetime(
                2026, 7, 28, 16, tzinfo=timezone.utc
            ),
            timeframe="15Min",
            data_feed="indicative",
            slippage_per_leg=Decimal("0"),
        )

        self.assertEqual(report["latest_proxy_pnl"], "50.00")
        self.assertIn(
            "historical_greeks_not_reconstructed", report["warnings"]
        )

    def test_missing_leg_fails_to_insufficient_path_and_persists(
        self,
    ) -> None:
        candidate = observation()
        report = evaluate_option_package_path(
            candidate,
            {
                "AAPL260918C00150000": (
                    option_bar(
                        "AAPL260918C00150000",
                        "2026-07-28T15:00:00Z",
                        "3.00",
                    ),
                )
            },
            evaluated_at=datetime(
                2026, 7, 28, 16, tzinfo=timezone.utc
            ),
            timeframe="15Min",
            data_feed="indicative",
        )
        self.assertEqual(report["aligned_points"], 0)
        self.assertIsNone(report["latest_proxy_pnl"])
        self.assertIn(
            "one_or_more_legs_have_no_bars", report["warnings"]
        )

        with TemporaryDirectory() as directory:
            path = f"{directory}/evidence.db"
            store = SqliteAuditStore(path)
            store.record_option_package_evidence(report)
            rows = SqliteAuditReader(
                path
            ).recent_option_package_evidence(
                account_id="alpaca-paper"
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["evidence_type"],
            "exact_contract_trade_bar_proxy",
        )
        self.assertFalse(rows[0]["execution_enabled"])
