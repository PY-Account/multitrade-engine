from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from multitrade.backtest import BacktestTrade
from multitrade.domain import ZERO


def _average(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(values)
    if not rows:
        return ZERO
    return sum(rows, start=ZERO) / Decimal(len(rows))


def _trade_summary(
    trades: Iterable[BacktestTrade],
) -> dict[str, Any]:
    rows = tuple(trades)
    winners = tuple(row for row in rows if row.pnl > ZERO)
    losers = tuple(row for row in rows if row.pnl < ZERO)
    net_profit = sum((row.pnl for row in rows), start=ZERO)
    gross_before_costs = sum(
        (row.gross_pnl for row in rows), start=ZERO
    )
    transaction_costs = sum(
        (row.transaction_costs for row in rows), start=ZERO
    )
    gross_wins = sum((row.pnl for row in winners), start=ZERO)
    gross_losses = abs(
        sum((row.pnl for row in losers), start=ZERO)
    )
    absolute_gross_movement = sum(
        (abs(row.gross_pnl) for row in rows), start=ZERO
    )
    return {
        "trade_count": len(rows),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": (
            Decimal(len(winners)) / Decimal(len(rows))
            if rows
            else ZERO
        ),
        "gross_before_costs": gross_before_costs,
        "transaction_costs": transaction_costs,
        "net_profit": net_profit,
        "profit_factor": (
            gross_wins / gross_losses
            if gross_losses > ZERO
            else None
        ),
        "average_r_multiple": _average(
            row.r_multiple for row in rows
        ),
        "average_holding_bars": _average(
            Decimal(row.holding_bars) for row in rows
        ),
        "average_mfe": _average(
            row.maximum_favorable_excursion for row in rows
        ),
        "average_mae": _average(
            row.maximum_adverse_excursion for row in rows
        ),
        "cost_to_absolute_gross_movement": (
            transaction_costs / absolute_gross_movement
            if absolute_gross_movement > ZERO
            else None
        ),
    }


def _grouped(
    trades: tuple[BacktestTrade, ...],
    key,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[str(key(trade) or "unknown")].append(trade)
    return [
        {
            "bucket": name,
            **_trade_summary(rows),
        }
        for name, rows in sorted(grouped.items())
    ]


def _extreme(
    rows: list[dict[str, Any]],
    *,
    reverse: bool,
) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            Decimal(str(row["net_profit"])),
            row["bucket"],
        ),
        reverse=reverse,
    )[0]


def build_trade_attribution(
    trades: Iterable[BacktestTrade],
) -> dict[str, Any]:
    """Aggregate additive, decision-time backtest diagnostics."""

    rows = tuple(trades)
    overall = _trade_summary(rows)
    by_symbol = _grouped(rows, lambda row: row.symbol)
    by_regime = _grouped(rows, lambda row: row.entry_regime)
    by_entry_hour = _grouped(
        rows, lambda row: row.entry_hour_new_york
    )
    by_exit_reason = _grouped(rows, lambda row: row.exit_reason)
    by_reason_set = _grouped(
        rows,
        lambda row: " + ".join(row.reason_codes) or "unlabeled",
    )
    gross = Decimal(str(overall["gross_before_costs"]))
    net = Decimal(str(overall["net_profit"]))
    if not rows:
        diagnosis = "no_out_of_sample_trades"
    elif gross <= ZERO:
        diagnosis = "negative_before_modeled_costs"
    elif net <= ZERO:
        diagnosis = "gross_edge_erased_by_modeled_costs"
    elif (
        overall["profit_factor"] is None
        or Decimal(str(overall["profit_factor"])) < Decimal("1.10")
    ):
        diagnosis = "positive_net_but_insufficient_profit_factor"
    else:
        diagnosis = "positive_diagnostic_sample_requires_validation"

    return {
        "overall": overall,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
        "by_entry_hour_new_york": by_entry_hour,
        "by_exit_reason": by_exit_reason,
        "by_reason_set": by_reason_set,
        "strongest_symbol": _extreme(by_symbol, reverse=True),
        "weakest_symbol": _extreme(by_symbol, reverse=False),
        "strongest_regime": _extreme(by_regime, reverse=True),
        "weakest_regime": _extreme(by_regime, reverse=False),
        "strongest_entry_hour_new_york": _extreme(
            by_entry_hour, reverse=True
        ),
        "weakest_entry_hour_new_york": _extreme(
            by_entry_hour, reverse=False
        ),
        "largest_loss_exit_reason": _extreme(
            by_exit_reason, reverse=False
        ),
        "primary_diagnosis": diagnosis,
        "execution_eligible": False,
    }
