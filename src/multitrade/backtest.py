from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Iterable
from uuid import uuid4

from multitrade.domain import ZERO
from multitrade.features import FeatureEngine
from multitrade.market import MarketBar, timeframe_seconds
from multitrade.strategies.base import (
    SignalAction,
    Strategy,
    StrategyContext,
    StrategySignal,
)


BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("100000")
    risk_fraction: Decimal = Decimal("0.005")
    capital_weight: Decimal = Decimal("0.20")
    slippage_bps: Decimal = Decimal("10")
    commission_per_share: Decimal = ZERO
    maximum_holding_bars: int = 78

    def __post_init__(self) -> None:
        if self.initial_equity <= ZERO:
            raise ValueError("Backtest initial equity must be positive")
        if not ZERO < self.risk_fraction <= Decimal("0.03"):
            raise ValueError("Backtest risk fraction must be in (0, 0.03]")
        if not ZERO < self.capital_weight <= Decimal("1"):
            raise ValueError("Backtest capital weight must be in (0, 1]")
        if self.slippage_bps < ZERO or self.commission_per_share < ZERO:
            raise ValueError("Backtest costs cannot be negative")
        if self.maximum_holding_bars < 1:
            raise ValueError("maximum_holding_bars must be positive")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_id: str
    strategy_id: str
    symbol: str
    side: str
    signal_timestamp: str
    entry_timestamp: str
    exit_timestamp: str
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    initial_risk: Decimal
    pnl: Decimal
    r_multiple: Decimal
    exit_reason: str
    holding_bars: int


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    starting_equity: Decimal
    ending_equity: Decimal
    net_profit: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    trade_count: int
    winners: int
    losers: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    average_r_multiple: Decimal
    exposure_percent: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    started_at: datetime
    completed_at: datetime
    config: BacktestConfig
    metrics: BacktestMetrics
    trades: tuple[BacktestTrade, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    strategy_id: str
    strategy_version: str
    in_sample: BacktestResult
    out_of_sample: BacktestResult
    passed: bool
    gates: dict[str, bool]
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _OpenPosition:
    signal: StrategySignal
    entry_timestamp: datetime
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    initial_risk: Decimal
    entry_index: int


class Backtester:
    def __init__(
        self,
        strategy: Strategy,
        *,
        feature_engine: FeatureEngine | None = None,
        config: BacktestConfig | None = None,
    ) -> None:
        self.strategy = strategy
        self.feature_engine = feature_engine or FeatureEngine()
        self.config = config or BacktestConfig()

    def run(
        self,
        bars: Iterable[MarketBar],
        *,
        signal_start_at: datetime | None = None,
    ) -> BacktestResult:
        started_at = datetime.now(timezone.utc)
        ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
        if len(ordered) < self.feature_engine.minimum_bars + 2:
            raise ValueError("Not enough bars for a reproducible backtest")
        symbols = {bar.symbol for bar in ordered}
        timeframes = {bar.timeframe for bar in ordered}
        feeds = {bar.feed for bar in ordered}
        if len(symbols) != 1 or len(timeframes) != 1 or len(feeds) != 1:
            raise ValueError(
                "Backtest bars must share symbol, timeframe, and feed"
            )

        equity = self.config.initial_equity
        equity_peak = equity
        maximum_drawdown = ZERO
        equity_curve = [equity]
        trades: list[BacktestTrade] = []
        open_position: _OpenPosition | None = None
        exposed_bars = 0

        for index in range(
            self.feature_engine.minimum_bars - 1, len(ordered)
        ):
            current = ordered[index]
            if open_position is not None:
                exposed_bars += 1
                exit_result = self._position_exit(
                    open_position, current, index
                )
                if exit_result is not None:
                    exit_price, exit_reason = exit_result
                    trade = self._close_trade(
                        open_position,
                        current,
                        exit_price,
                        exit_reason,
                        index,
                    )
                    trades.append(trade)
                    equity += trade.pnl
                    equity_peak = max(equity_peak, equity)
                    drawdown = (
                        (equity_peak - equity) / equity_peak
                        if equity_peak > ZERO
                        else ZERO
                    )
                    maximum_drawdown = max(maximum_drawdown, drawdown)
                    equity_curve.append(equity)
                    open_position = None
                continue

            if index + 1 >= len(ordered):
                break
            if (
                signal_start_at is not None
                and current.timestamp < signal_start_at
            ):
                continue
            history = ordered[: index + 1]
            features = self.feature_engine.calculate(history)
            evaluated_at = current.timestamp + timedelta(
                seconds=timeframe_seconds(current.timeframe)
            )
            signal = self.strategy.evaluate(
                StrategyContext(
                    account_id="backtest",
                    bars=history,
                    features=features,
                    evaluated_at=evaluated_at,
                )
            )
            if signal is None:
                continue
            next_bar = ordered[index + 1]
            open_position = self._open_position(
                signal, next_bar, index + 1, equity
            )

        if open_position is not None:
            final_bar = ordered[-1]
            exit_price = self._exit_with_slippage(
                final_bar.close, open_position.signal.action
            )
            trade = self._close_trade(
                open_position,
                final_bar,
                exit_price,
                "end_of_data",
                len(ordered) - 1,
            )
            trades.append(trade)
            equity += trade.pnl
            equity_peak = max(equity_peak, equity)
            maximum_drawdown = max(
                maximum_drawdown,
                (
                    (equity_peak - equity) / equity_peak
                    if equity_peak > ZERO
                    else ZERO
                ),
            )
            equity_curve.append(equity)

        metrics = self._metrics(
            trades,
            equity,
            maximum_drawdown,
            exposed_bars,
            len(ordered),
        )
        warnings: list[str] = []
        if metrics.trade_count < 30:
            warnings.append("fewer_than_30_trades")
        if metrics.profit_factor is None:
            warnings.append("profit_factor_undefined")
        if len(ordered) < 500:
            warnings.append("limited_market_sample")
        completed_at = datetime.now(timezone.utc)
        return BacktestResult(
            run_id=str(uuid4()),
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            symbol=ordered[0].symbol,
            timeframe=ordered[0].timeframe,
            started_at=started_at,
            completed_at=completed_at,
            config=self.config,
            metrics=metrics,
            trades=tuple(trades),
            warnings=tuple(warnings),
        )

    def _open_position(
        self,
        signal: StrategySignal,
        entry_bar: MarketBar,
        entry_index: int,
        equity: Decimal,
    ) -> _OpenPosition | None:
        if signal.action is SignalAction.ENTER_LONG:
            entry_price = entry_bar.open * (
                Decimal("1") + self.config.slippage_bps / BPS
            )
            risk_per_share = entry_price - signal.stop_price
            if (
                risk_per_share <= ZERO
                or entry_price >= signal.target_price
            ):
                return None
        else:
            entry_price = entry_bar.open * (
                Decimal("1") - self.config.slippage_bps / BPS
            )
            risk_per_share = signal.stop_price - entry_price
            if (
                risk_per_share <= ZERO
                or entry_price <= signal.target_price
            ):
                return None
        risk_quantity = (
            equity * self.config.risk_fraction / risk_per_share
        ).to_integral_value(rounding=ROUND_DOWN)
        capital_quantity = (
            equity * self.config.capital_weight / entry_price
        ).to_integral_value(rounding=ROUND_DOWN)
        quantity = min(risk_quantity, capital_quantity)
        if quantity <= ZERO:
            return None
        return _OpenPosition(
            signal=signal,
            entry_timestamp=entry_bar.timestamp,
            entry_price=entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            quantity=quantity,
            initial_risk=risk_per_share * quantity,
            entry_index=entry_index,
        )

    def _position_exit(
        self,
        position: _OpenPosition,
        bar: MarketBar,
        index: int,
    ) -> tuple[Decimal, str] | None:
        is_long = position.signal.action is SignalAction.ENTER_LONG
        if is_long:
            stop_hit = bar.low <= position.stop_price
            target_hit = bar.high >= position.target_price
        else:
            stop_hit = bar.high >= position.stop_price
            target_hit = bar.low <= position.target_price
        if stop_hit:
            return (
                self._exit_with_slippage(
                    position.stop_price, position.signal.action
                ),
                (
                    "stop_before_target_same_bar"
                    if target_hit
                    else "stop_loss"
                ),
            )
        if target_hit:
            return (
                self._exit_with_slippage(
                    position.target_price, position.signal.action
                ),
                "take_profit",
            )
        if (
            index - position.entry_index + 1
            >= self.config.maximum_holding_bars
        ):
            return (
                self._exit_with_slippage(
                    bar.close, position.signal.action
                ),
                "maximum_holding_period",
            )
        return None

    def _exit_with_slippage(
        self, price: Decimal, action: SignalAction
    ) -> Decimal:
        if action is SignalAction.ENTER_LONG:
            return price * (
                Decimal("1") - self.config.slippage_bps / BPS
            )
        return price * (
            Decimal("1") + self.config.slippage_bps / BPS
        )

    def _close_trade(
        self,
        position: _OpenPosition,
        exit_bar: MarketBar,
        exit_price: Decimal,
        exit_reason: str,
        exit_index: int,
    ) -> BacktestTrade:
        if position.signal.action is SignalAction.ENTER_LONG:
            gross_pnl = (
                exit_price - position.entry_price
            ) * position.quantity
            side = "long"
        else:
            gross_pnl = (
                position.entry_price - exit_price
            ) * position.quantity
            side = "short"
        costs = (
            self.config.commission_per_share
            * position.quantity
            * Decimal("2")
        )
        pnl = gross_pnl - costs
        r_multiple = (
            pnl / position.initial_risk
            if position.initial_risk > ZERO
            else ZERO
        )
        return BacktestTrade(
            signal_id=position.signal.signal_id,
            strategy_id=position.signal.strategy_id,
            symbol=position.signal.symbol,
            side=side,
            signal_timestamp=position.signal.bar_timestamp.isoformat(),
            entry_timestamp=position.entry_timestamp.isoformat(),
            exit_timestamp=exit_bar.timestamp.isoformat(),
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            quantity=position.quantity,
            initial_risk=position.initial_risk,
            pnl=pnl,
            r_multiple=r_multiple,
            exit_reason=exit_reason,
            holding_bars=exit_index - position.entry_index + 1,
        )

    def _metrics(
        self,
        trades: list[BacktestTrade],
        ending_equity: Decimal,
        maximum_drawdown: Decimal,
        exposed_bars: int,
        total_bars: int,
    ) -> BacktestMetrics:
        winners = tuple(trade for trade in trades if trade.pnl > ZERO)
        losers = tuple(trade for trade in trades if trade.pnl < ZERO)
        gross_profit = sum(
            (trade.pnl for trade in winners), start=ZERO
        )
        gross_loss = abs(
            sum((trade.pnl for trade in losers), start=ZERO)
        )
        trade_count = len(trades)
        net_profit = ending_equity - self.config.initial_equity
        return BacktestMetrics(
            starting_equity=self.config.initial_equity,
            ending_equity=ending_equity,
            net_profit=net_profit,
            total_return=net_profit / self.config.initial_equity,
            maximum_drawdown=maximum_drawdown,
            trade_count=trade_count,
            winners=len(winners),
            losers=len(losers),
            win_rate=(
                Decimal(len(winners)) / Decimal(trade_count)
                if trade_count
                else ZERO
            ),
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            profit_factor=(
                gross_profit / gross_loss if gross_loss > ZERO else None
            ),
            expectancy=(
                sum((trade.pnl for trade in trades), start=ZERO)
                / Decimal(trade_count)
                if trade_count
                else ZERO
            ),
            average_r_multiple=(
                sum(
                    (trade.r_multiple for trade in trades), start=ZERO
                )
                / Decimal(trade_count)
                if trade_count
                else ZERO
            ),
            exposure_percent=(
                Decimal(exposed_bars) / Decimal(total_bars)
                * Decimal("100")
                if total_bars
                else ZERO
            ),
        )


class StrategyValidator:
    def __init__(
        self,
        strategy: Strategy,
        *,
        config: BacktestConfig | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()

    def validate(
        self,
        bars: Iterable[MarketBar],
        *,
        split_fraction: Decimal = Decimal("0.60"),
    ) -> ValidationReport:
        ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
        if not Decimal("0.50") <= split_fraction <= Decimal("0.80"):
            raise ValueError("Validation split must be between 0.50 and 0.80")
        split_index = int(
            Decimal(len(ordered)) * split_fraction
        )
        minimum = FeatureEngine().minimum_bars + 2
        if split_index < minimum or len(ordered) - split_index < minimum:
            raise ValueError(
                "Not enough bars for in-sample and out-of-sample tests"
            )
        warmup_start = max(0, split_index - FeatureEngine().minimum_bars)
        in_sample = Backtester(
            self.strategy, config=self.config
        ).run(ordered[:split_index])
        out_of_sample = Backtester(
            self.strategy, config=self.config
        ).run(
            ordered[warmup_start:],
            signal_start_at=ordered[split_index].timestamp,
        )
        metrics = out_of_sample.metrics
        gates = {
            "minimum_trade_count": metrics.trade_count >= 20,
            "positive_net_profit": metrics.net_profit > ZERO,
            "maximum_drawdown": (
                metrics.maximum_drawdown <= Decimal("0.10")
            ),
            "profit_factor": (
                metrics.profit_factor is not None
                and metrics.profit_factor >= Decimal("1.10")
            ),
            "positive_expectancy": metrics.expectancy > ZERO,
        }
        warnings = list(out_of_sample.warnings)
        if not all(gates.values()):
            warnings.append("strategy_not_validated")
        return ValidationReport(
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            in_sample=in_sample,
            out_of_sample=out_of_sample,
            passed=all(gates.values()),
            gates=gates,
            warnings=tuple(dict.fromkeys(warnings)),
        )


def result_payload(result: BacktestResult) -> dict:
    return asdict(result)
