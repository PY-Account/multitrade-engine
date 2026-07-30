from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from statistics import median
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
    maximum_history_bars: int = 120
    regular_session_only: bool = True
    flatten_at_session_end: bool = True

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
        if self.maximum_history_bars < 40:
            raise ValueError("maximum_history_bars must be at least 40")


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
    gross_pnl: Decimal
    transaction_costs: Decimal
    pnl: Decimal
    r_multiple: Decimal
    exit_reason: str
    holding_bars: int
    entry_regime: str
    entry_hour_new_york: str
    reason_codes: tuple[str, ...]
    maximum_favorable_excursion: Decimal
    maximum_adverse_excursion: Decimal


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


@dataclass(frozen=True, slots=True)
class ChronologicalFoldResult:
    fold_number: int
    test_start: str
    test_end: str
    bar_count: int
    metrics: BacktestMetrics
    r_multiples: tuple[Decimal, ...]
    gates: dict[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class ChronologicalStabilityReport:
    strategy_id: str
    strategy_version: str
    folds_requested: int
    folds_completed: int
    folds: tuple[ChronologicalFoldResult, ...]
    trade_r_multiples: tuple[Decimal, ...]
    total_trade_count: int
    profitable_fold_fraction: Decimal
    passed_fold_fraction: Decimal
    median_fold_return: Decimal
    worst_fold_drawdown: Decimal
    pooled_profit_factor: Decimal | None
    gates: dict[str, bool]
    passed: bool
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _OpenPosition:
    signal: StrategySignal
    entry_timestamp: datetime
    raw_entry_price: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    initial_risk: Decimal
    entry_index: int
    entry_regime: str
    maximum_favorable_excursion: Decimal = ZERO
    maximum_adverse_excursion: Decimal = ZERO


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
        if (
            self.config.regular_session_only
            and ordered
            and not ordered[0].timeframe.endswith(("Day", "D"))
        ):
            ordered = tuple(
                bar for bar in ordered if _is_regular_session_bar(bar)
            )
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
                if (
                    exit_result is None
                    and self.config.flatten_at_session_end
                    and _is_last_bar_of_session(ordered, index)
                ):
                    exit_result = (
                        current.close,
                        self._exit_with_slippage(
                            current.close,
                            open_position.signal.action,
                        ),
                        "session_close",
                    )
                if exit_result is not None:
                    (
                        raw_exit_price,
                        exit_price,
                        exit_reason,
                    ) = exit_result
                    trade = self._close_trade(
                        open_position,
                        current,
                        raw_exit_price,
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
            history = ordered[
                max(0, index + 1 - self.config.maximum_history_bars)
                : index + 1
            ]
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
            if (
                self.config.flatten_at_session_end
                and not _same_trading_session(current, next_bar)
            ):
                continue
            open_position = self._open_position(
                signal,
                next_bar,
                index + 1,
                equity,
                entry_regime=features.regime.value,
            )

        if open_position is not None:
            final_bar = ordered[-1]
            self._update_excursions(open_position, final_bar)
            exit_price = self._exit_with_slippage(
                final_bar.close, open_position.signal.action
            )
            trade = self._close_trade(
                open_position,
                final_bar,
                final_bar.close,
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
        *,
        entry_regime: str,
    ) -> _OpenPosition | None:
        raw_entry_price = entry_bar.open
        if signal.action is SignalAction.ENTER_LONG:
            entry_price = raw_entry_price * (
                Decimal("1") + self.config.slippage_bps / BPS
            )
            risk_per_share = entry_price - signal.stop_price
            if (
                risk_per_share <= ZERO
                or entry_price >= signal.target_price
            ):
                return None
        else:
            entry_price = raw_entry_price * (
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
            raw_entry_price=raw_entry_price,
            entry_price=entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            quantity=quantity,
            initial_risk=risk_per_share * quantity,
            entry_index=entry_index,
            entry_regime=entry_regime,
        )

    def _position_exit(
        self,
        position: _OpenPosition,
        bar: MarketBar,
        index: int,
    ) -> tuple[Decimal, Decimal, str] | None:
        is_long = position.signal.action is SignalAction.ENTER_LONG
        if is_long:
            stop_hit = bar.low <= position.stop_price
            target_hit = bar.high >= position.target_price
        else:
            stop_hit = bar.high >= position.stop_price
            target_hit = bar.low <= position.target_price
        if stop_hit:
            self._update_exit_excursion(
                position,
                position.stop_price,
                favorable=False,
            )
            return (
                position.stop_price,
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
            self._update_exit_excursion(
                position,
                position.target_price,
                favorable=True,
            )
            return (
                position.target_price,
                self._exit_with_slippage(
                    position.target_price, position.signal.action
                ),
                "take_profit",
            )
        self._update_excursions(position, bar)
        if (
            index - position.entry_index + 1
            >= self.config.maximum_holding_bars
        ):
            return (
                bar.close,
                self._exit_with_slippage(
                    bar.close, position.signal.action
                ),
                "maximum_holding_period",
            )
        return None

    @staticmethod
    def _update_excursions(
        position: _OpenPosition,
        bar: MarketBar,
    ) -> None:
        if position.signal.action is SignalAction.ENTER_LONG:
            favorable = max(
                ZERO,
                (bar.high - position.raw_entry_price)
                * position.quantity,
            )
            adverse = max(
                ZERO,
                (position.raw_entry_price - bar.low)
                * position.quantity,
            )
        else:
            favorable = max(
                ZERO,
                (position.raw_entry_price - bar.low)
                * position.quantity,
            )
            adverse = max(
                ZERO,
                (bar.high - position.raw_entry_price)
                * position.quantity,
            )
        position.maximum_favorable_excursion = max(
            position.maximum_favorable_excursion,
            favorable,
        )
        position.maximum_adverse_excursion = max(
            position.maximum_adverse_excursion,
            adverse,
        )

    @staticmethod
    def _update_exit_excursion(
        position: _OpenPosition,
        price: Decimal,
        *,
        favorable: bool,
    ) -> None:
        excursion = (
            abs(price - position.raw_entry_price)
            * position.quantity
        )
        if favorable:
            position.maximum_favorable_excursion = max(
                position.maximum_favorable_excursion,
                excursion,
            )
        else:
            position.maximum_adverse_excursion = max(
                position.maximum_adverse_excursion,
                excursion,
            )

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
        raw_exit_price: Decimal,
        exit_price: Decimal,
        exit_reason: str,
        exit_index: int,
    ) -> BacktestTrade:
        if position.signal.action is SignalAction.ENTER_LONG:
            gross_pnl = (
                raw_exit_price - position.raw_entry_price
            ) * position.quantity
            after_slippage_pnl = (
                exit_price - position.entry_price
            ) * position.quantity
            side = "long"
        else:
            gross_pnl = (
                position.raw_entry_price - raw_exit_price
            ) * position.quantity
            after_slippage_pnl = (
                position.entry_price - exit_price
            ) * position.quantity
            side = "short"
        commission_costs = (
            self.config.commission_per_share
            * position.quantity
            * Decimal("2")
        )
        slippage_costs = max(
            ZERO,
            gross_pnl - after_slippage_pnl,
        )
        transaction_costs = slippage_costs + commission_costs
        pnl = gross_pnl - transaction_costs
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
            gross_pnl=gross_pnl,
            transaction_costs=transaction_costs,
            pnl=pnl,
            r_multiple=r_multiple,
            exit_reason=exit_reason,
            holding_bars=exit_index - position.entry_index + 1,
            entry_regime=position.entry_regime,
            entry_hour_new_york=_new_york_local(
                position.entry_timestamp
            ).strftime("%H"),
            reason_codes=position.signal.reason_codes,
            maximum_favorable_excursion=(
                position.maximum_favorable_excursion
            ),
            maximum_adverse_excursion=(
                position.maximum_adverse_excursion
            ),
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
        if (
            self.config.regular_session_only
            and ordered
            and not ordered[0].timeframe.endswith(("Day", "D"))
        ):
            ordered = tuple(
                bar for bar in ordered if _is_regular_session_bar(bar)
            )
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
        warmup_start = max(
            0, split_index - self.config.maximum_history_bars
        )
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

    def chronological_stability(
        self,
        bars: Iterable[MarketBar],
        *,
        folds: int = 3,
        initial_fraction: Decimal = Decimal("0.50"),
        minimum_trades_per_fold: int = 2,
        drawdown_limit: Decimal = Decimal("0.10"),
    ) -> ChronologicalStabilityReport:
        """Evaluate a fixed strategy across non-overlapping later periods.

        The strategy parameters are not fitted inside this method. Historical
        bars before each test window are used only as bounded feature warmup;
        signals and P/L begin at the fold boundary.
        """
        if not 2 <= folds <= 6:
            raise ValueError("Chronological folds must be between 2 and 6")
        if not Decimal("0.40") <= initial_fraction <= Decimal("0.70"):
            raise ValueError(
                "Chronological initial fraction must be 0.40-0.70"
            )
        if minimum_trades_per_fold < 1:
            raise ValueError(
                "Minimum trades per fold must be positive"
            )
        if not ZERO < drawdown_limit <= Decimal("0.50"):
            raise ValueError(
                "Chronological drawdown limit must be in (0, 0.50]"
            )
        ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
        if (
            self.config.regular_session_only
            and ordered
            and not ordered[0].timeframe.endswith(("Day", "D"))
        ):
            ordered = tuple(
                bar for bar in ordered if _is_regular_session_bar(bar)
            )
        minimum_warmup = FeatureEngine().minimum_bars + 2
        first_test_index = int(
            Decimal(len(ordered)) * initial_fraction
        )
        test_bar_count = len(ordered) - first_test_index
        if (
            first_test_index < minimum_warmup
            or test_bar_count < folds * 10
        ):
            raise ValueError(
                "Not enough bars for chronological stability folds"
            )

        fold_results: list[ChronologicalFoldResult] = []
        for fold_index in range(folds):
            test_start_index = first_test_index + (
                test_bar_count * fold_index // folds
            )
            test_end_index = first_test_index + (
                test_bar_count * (fold_index + 1) // folds
            )
            warmup_start = max(
                0,
                test_start_index - self.config.maximum_history_bars,
            )
            result = Backtester(
                self.strategy, config=self.config
            ).run(
                ordered[warmup_start:test_end_index],
                signal_start_at=ordered[test_start_index].timestamp,
            )
            metrics = result.metrics
            fold_gates = {
                "minimum_trade_count": (
                    metrics.trade_count >= minimum_trades_per_fold
                ),
                "positive_net_profit": metrics.net_profit > ZERO,
                "maximum_drawdown": (
                    metrics.maximum_drawdown <= drawdown_limit
                ),
                "positive_expectancy": metrics.expectancy > ZERO,
            }
            fold_results.append(
                ChronologicalFoldResult(
                    fold_number=fold_index + 1,
                    test_start=ordered[
                        test_start_index
                    ].timestamp.isoformat(),
                    test_end=ordered[
                        test_end_index - 1
                    ].timestamp.isoformat(),
                    bar_count=test_end_index - test_start_index,
                    metrics=metrics,
                    r_multiples=tuple(
                        trade.r_multiple for trade in result.trades
                    ),
                    gates=fold_gates,
                    passed=all(fold_gates.values()),
                )
            )

        returns = tuple(
            fold.metrics.total_return for fold in fold_results
        )
        gross_profit = sum(
            (
                fold.metrics.gross_profit
                for fold in fold_results
            ),
            start=ZERO,
        )
        gross_loss = sum(
            (
                fold.metrics.gross_loss
                for fold in fold_results
            ),
            start=ZERO,
        )
        total_trades = sum(
            fold.metrics.trade_count for fold in fold_results
        )
        profitable_fraction = (
            Decimal(sum(value > ZERO for value in returns))
            / Decimal(len(returns))
        )
        passed_fraction = (
            Decimal(sum(fold.passed for fold in fold_results))
            / Decimal(len(fold_results))
        )
        median_return = Decimal(str(median(returns)))
        worst_drawdown = max(
            fold.metrics.maximum_drawdown
            for fold in fold_results
        )
        pooled_profit_factor = (
            gross_profit / gross_loss if gross_loss > ZERO else None
        )
        gates = {
            "all_folds_completed": len(fold_results) == folds,
            "minimum_total_trade_count": (
                total_trades >= folds * minimum_trades_per_fold
            ),
            "positive_median_fold_return": median_return > ZERO,
            "profitable_fold_majority": (
                profitable_fraction >= Decimal("0.50")
            ),
            "maximum_fold_drawdown": (
                worst_drawdown <= drawdown_limit
            ),
        }
        warnings: list[str] = []
        if not all(gates.values()):
            warnings.append("chronological_stability_failed")
        return ChronologicalStabilityReport(
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.version,
            folds_requested=folds,
            folds_completed=len(fold_results),
            folds=tuple(fold_results),
            trade_r_multiples=tuple(
                r_multiple
                for fold in fold_results
                for r_multiple in fold.r_multiples
            ),
            total_trade_count=total_trades,
            profitable_fold_fraction=profitable_fraction,
            passed_fold_fraction=passed_fraction,
            median_fold_return=median_return,
            worst_fold_drawdown=worst_drawdown,
            pooled_profit_factor=pooled_profit_factor,
            gates=gates,
            passed=all(gates.values()),
            warnings=tuple(warnings),
        )


def result_payload(result: BacktestResult) -> dict:
    return asdict(result)


def _is_regular_session_bar(bar: MarketBar) -> bool:
    local = _new_york_local(bar.timestamp)
    return (
        local.weekday() < 5
        and time(9, 30) <= local.time().replace(tzinfo=None) < time(16)
    )


def _same_trading_session(left: MarketBar, right: MarketBar) -> bool:
    return (
        _new_york_local(left.timestamp).date()
        == _new_york_local(right.timestamp).date()
    )


def _is_last_bar_of_session(
    ordered: tuple[MarketBar, ...], index: int
) -> bool:
    if index + 1 >= len(ordered):
        return True
    return not _same_trading_session(ordered[index], ordered[index + 1])


def _new_york_local(value: datetime) -> datetime:
    """Convert UTC to US Eastern time without an external tzdata package."""
    utc_value = value.astimezone(timezone.utc)
    year = utc_value.year
    march_sundays = [
        week[calendar.SUNDAY]
        for week in calendar.monthcalendar(year, 3)
        if week[calendar.SUNDAY]
    ]
    november_sundays = [
        week[calendar.SUNDAY]
        for week in calendar.monthcalendar(year, 11)
        if week[calendar.SUNDAY]
    ]
    dst_start = datetime(
        year,
        3,
        march_sundays[1],
        7,
        tzinfo=timezone.utc,
    )
    dst_end = datetime(
        year,
        11,
        november_sundays[0],
        6,
        tzinfo=timezone.utc,
    )
    offset_hours = -4 if dst_start <= utc_value < dst_end else -5
    return utc_value.astimezone(
        timezone(timedelta(hours=offset_hours))
    )
