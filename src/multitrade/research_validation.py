from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Protocol

from multitrade.domain import ZERO
from multitrade.market import MarketBar


BPS = Decimal("10000")
ANNUAL_PERIODS = Decimal("252")


class DailyMarketModel(Protocol):
    model_id: str
    version: str
    minimum_bars: int

    def evaluate(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class ResearchBacktestConfig:
    initial_equity: Decimal = Decimal("100000")
    one_way_cost_bps: Decimal = Decimal("10")
    minimum_observations: int = 252
    maximum_drawdown: Decimal = Decimal("0.15")
    minimum_sharpe: Decimal = Decimal("0.50")
    maximum_annual_turnover: Decimal = Decimal("24")

    def __post_init__(self) -> None:
        if self.initial_equity <= ZERO:
            raise ValueError("Research initial equity must be positive")
        if not ZERO <= self.one_way_cost_bps <= Decimal("100"):
            raise ValueError("Research costs must be between 0 and 100 bps")
        if self.minimum_observations < 20:
            raise ValueError("Minimum observations must be at least 20")
        if not ZERO < self.maximum_drawdown <= Decimal("1"):
            raise ValueError("Maximum drawdown gate must be in (0, 1]")
        if self.minimum_sharpe < ZERO:
            raise ValueError("Minimum Sharpe cannot be negative")
        if self.maximum_annual_turnover <= ZERO:
            raise ValueError("Maximum annual turnover must be positive")


@dataclass(frozen=True, slots=True)
class ResearchBacktestPoint:
    decision_timestamp: str
    execution_timestamp: str
    return_end_timestamp: str
    state: str
    score: Decimal
    target_exposure: Decimal
    previous_exposure: Decimal
    asset_return: Decimal
    benchmark_return: Decimal
    gross_strategy_return: Decimal
    cost_return: Decimal
    net_strategy_return: Decimal
    strategy_equity: Decimal
    benchmark_equity: Decimal


@dataclass(frozen=True, slots=True)
class ResearchBacktestMetrics:
    starting_equity: Decimal
    ending_equity: Decimal
    benchmark_ending_equity: Decimal
    total_return: Decimal
    benchmark_total_return: Decimal
    excess_total_return: Decimal
    annualized_return: Decimal
    benchmark_annualized_return: Decimal
    annualized_volatility: Decimal
    benchmark_annualized_volatility: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    information_ratio: Decimal | None
    maximum_drawdown: Decimal
    benchmark_maximum_drawdown: Decimal
    observation_count: int
    exposure_changes: int
    average_exposure: Decimal
    annual_turnover: Decimal
    estimated_cost_amount: Decimal
    risk_on_fraction: Decimal
    watch_fraction: Decimal
    risk_off_fraction: Decimal


@dataclass(frozen=True, slots=True)
class ResearchBacktestReport:
    report_id: str
    model_id: str
    model_version: str
    symbol: str
    benchmark: str
    timeframe: str
    started_at: str
    completed_at: str
    config: ResearchBacktestConfig
    metrics: ResearchBacktestMetrics
    gates: dict[str, bool]
    warnings: tuple[str, ...]
    promotion_status: str
    execution_eligible: bool
    points: tuple[ResearchBacktestPoint, ...]


@dataclass(frozen=True, slots=True)
class CorrelationPair:
    left_symbol: str
    right_symbol: str
    correlation: Decimal
    observations: int


@dataclass(frozen=True, slots=True)
class PortfolioRiskReport:
    report_id: str
    account_id: str
    evaluated_at: str
    lookback_days: int
    state: str
    symbols_requested: tuple[str, ...]
    symbols_included: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    average_positive_correlation: Decimal
    maximum_positive_correlation: Decimal
    effective_breadth: Decimal
    high_correlation_pairs: tuple[CorrelationPair, ...]
    all_pairs: tuple[CorrelationPair, ...]
    reason_codes: tuple[str, ...]
    execution_eligible: bool


def _mean(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(values)
    return (
        sum(rows, start=ZERO) / Decimal(len(rows))
        if rows
        else ZERO
    )


def _stdev(values: Iterable[Decimal]) -> Decimal:
    rows = tuple(values)
    if len(rows) < 2:
        return ZERO
    average = _mean(rows)
    return _mean((value - average) ** 2 for value in rows).sqrt()


def _ratio(
    numerator: Decimal, denominator: Decimal
) -> Decimal | None:
    if denominator <= ZERO:
        return None
    return numerator / denominator


def _annualized_return(
    ending_equity: Decimal,
    starting_equity: Decimal,
    observations: int,
) -> Decimal:
    if observations < 1 or ending_equity <= ZERO:
        return ZERO
    value = math.pow(
        float(ending_equity / starting_equity),
        252.0 / float(observations),
    ) - 1.0
    return Decimal(str(value))


def _maximum_drawdown(curve: Iterable[Decimal]) -> Decimal:
    peak = ZERO
    maximum = ZERO
    for value in curve:
        peak = max(peak, value)
        if peak > ZERO:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _state_value(decision: Any) -> str:
    value = getattr(decision.state, "value", decision.state)
    return str(value)


class ResearchModelBacktester:
    """Simulates a daily model with next-open execution and no leverage."""

    def __init__(
        self,
        model: DailyMarketModel,
        *,
        config: ResearchBacktestConfig | None = None,
    ) -> None:
        self.model = model
        self.config = config or ResearchBacktestConfig()

    def run(
        self,
        *,
        symbol_bars: Iterable[MarketBar],
        benchmark_bars: Iterable[MarketBar],
        account_id: str = "research-backtest",
    ) -> ResearchBacktestReport:
        started_at = datetime.now(timezone.utc)
        symbol_rows, benchmark_rows = self._aligned_rows(
            symbol_bars, benchmark_bars
        )
        minimum = self.model.minimum_bars + 2
        if len(symbol_rows) < minimum:
            raise ValueError(
                f"Research backtest requires at least {minimum} "
                "aligned daily bars"
            )

        equity = self.config.initial_equity
        benchmark_equity = self.config.initial_equity
        strategy_curve = [equity]
        benchmark_curve = [benchmark_equity]
        strategy_returns: list[Decimal] = []
        benchmark_returns: list[Decimal] = []
        points: list[ResearchBacktestPoint] = []
        previous_exposure = ZERO
        total_turnover = ZERO
        estimated_cost_amount = ZERO
        exposure_changes = 0
        state_counts: dict[str, int] = {
            "risk_on": 0,
            "watch": 0,
            "risk_off": 0,
            "insufficient_data": 0,
        }

        for execution_index in range(
            self.model.minimum_bars, len(symbol_rows) - 1
        ):
            decision_index = execution_index - 1
            symbol_history = symbol_rows[: decision_index + 1]
            benchmark_history = benchmark_rows[: decision_index + 1]
            decision_bar = symbol_history[-1]
            decision = self.model.evaluate(
                account_id=account_id,
                symbol=symbol_rows[0].symbol,
                bars=symbol_history,
                benchmark_bars=benchmark_history,
                evaluated_at=decision_bar.timestamp
                + timedelta(days=1),
            )
            state = _state_value(decision)
            state_counts[state] = state_counts.get(state, 0) + 1
            exposure = max(
                ZERO,
                min(
                    Decimal("1"),
                    Decimal(decision.target_risk_multiplier),
                ),
            )
            execution_bar = symbol_rows[execution_index]
            return_end_bar = symbol_rows[execution_index + 1]
            benchmark_execution = benchmark_rows[execution_index]
            benchmark_return_end = benchmark_rows[execution_index + 1]
            asset_return = (
                return_end_bar.open / execution_bar.open
                - Decimal("1")
            )
            benchmark_return = (
                benchmark_return_end.open / benchmark_execution.open
                - Decimal("1")
            )
            turnover = abs(exposure - previous_exposure)
            if turnover > ZERO:
                exposure_changes += 1
            cost_return = turnover * self.config.one_way_cost_bps / BPS
            gross_return = exposure * asset_return
            net_return = gross_return - cost_return
            cost_amount = equity * cost_return
            if Decimal("1") + net_return <= ZERO:
                raise ValueError("Research simulation equity became invalid")
            equity *= Decimal("1") + net_return
            benchmark_equity *= Decimal("1") + benchmark_return
            total_turnover += turnover
            estimated_cost_amount += cost_amount
            strategy_returns.append(net_return)
            benchmark_returns.append(benchmark_return)
            strategy_curve.append(equity)
            benchmark_curve.append(benchmark_equity)
            points.append(
                ResearchBacktestPoint(
                    decision_timestamp=decision_bar.timestamp.isoformat(),
                    execution_timestamp=(
                        execution_bar.timestamp.isoformat()
                    ),
                    return_end_timestamp=(
                        return_end_bar.timestamp.isoformat()
                    ),
                    state=state,
                    score=Decimal(decision.score),
                    target_exposure=exposure,
                    previous_exposure=previous_exposure,
                    asset_return=asset_return,
                    benchmark_return=benchmark_return,
                    gross_strategy_return=gross_return,
                    cost_return=cost_return,
                    net_strategy_return=net_return,
                    strategy_equity=equity,
                    benchmark_equity=benchmark_equity,
                )
            )
            previous_exposure = exposure

        observations = len(points)
        strategy_volatility = _stdev(strategy_returns)
        benchmark_volatility = _stdev(benchmark_returns)
        downside = _mean(
            min(value, ZERO) ** 2 for value in strategy_returns
        ).sqrt()
        active_returns = tuple(
            strategy - benchmark
            for strategy, benchmark in zip(
                strategy_returns, benchmark_returns
            )
        )
        active_volatility = _stdev(active_returns)
        annualized_volatility = (
            strategy_volatility * ANNUAL_PERIODS.sqrt()
        )
        benchmark_annualized_volatility = (
            benchmark_volatility * ANNUAL_PERIODS.sqrt()
        )
        sharpe = _ratio(
            _mean(strategy_returns) * ANNUAL_PERIODS.sqrt(),
            strategy_volatility,
        )
        sortino = _ratio(
            _mean(strategy_returns) * ANNUAL_PERIODS.sqrt(),
            downside,
        )
        information = _ratio(
            _mean(active_returns) * ANNUAL_PERIODS.sqrt(),
            active_volatility,
        )
        total_return = (
            equity / self.config.initial_equity - Decimal("1")
        )
        benchmark_total_return = (
            benchmark_equity / self.config.initial_equity
            - Decimal("1")
        )
        annual_turnover = (
            total_turnover * ANNUAL_PERIODS / Decimal(observations)
            if observations
            else ZERO
        )
        metrics = ResearchBacktestMetrics(
            starting_equity=self.config.initial_equity,
            ending_equity=equity,
            benchmark_ending_equity=benchmark_equity,
            total_return=total_return,
            benchmark_total_return=benchmark_total_return,
            excess_total_return=total_return - benchmark_total_return,
            annualized_return=_annualized_return(
                equity, self.config.initial_equity, observations
            ),
            benchmark_annualized_return=_annualized_return(
                benchmark_equity,
                self.config.initial_equity,
                observations,
            ),
            annualized_volatility=annualized_volatility,
            benchmark_annualized_volatility=(
                benchmark_annualized_volatility
            ),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            information_ratio=information,
            maximum_drawdown=_maximum_drawdown(strategy_curve),
            benchmark_maximum_drawdown=_maximum_drawdown(
                benchmark_curve
            ),
            observation_count=observations,
            exposure_changes=exposure_changes,
            average_exposure=_mean(
                point.target_exposure for point in points
            ),
            annual_turnover=annual_turnover,
            estimated_cost_amount=estimated_cost_amount,
            risk_on_fraction=(
                Decimal(state_counts["risk_on"]) / Decimal(observations)
                if observations
                else ZERO
            ),
            watch_fraction=(
                Decimal(state_counts["watch"]) / Decimal(observations)
                if observations
                else ZERO
            ),
            risk_off_fraction=(
                Decimal(state_counts["risk_off"])
                / Decimal(observations)
                if observations
                else ZERO
            ),
        )
        gates = {
            "minimum_observations": (
                observations >= self.config.minimum_observations
            ),
            "positive_net_return_after_costs": total_return > ZERO,
            "positive_excess_return": (
                metrics.excess_total_return > ZERO
            ),
            "maximum_drawdown": (
                metrics.maximum_drawdown
                <= self.config.maximum_drawdown
            ),
            "drawdown_not_worse_than_benchmark": (
                metrics.maximum_drawdown
                <= metrics.benchmark_maximum_drawdown
            ),
            "minimum_sharpe": (
                metrics.sharpe_ratio is not None
                and metrics.sharpe_ratio
                >= self.config.minimum_sharpe
            ),
            "maximum_turnover": (
                metrics.annual_turnover
                <= self.config.maximum_annual_turnover
            ),
        }
        warnings: list[str] = [
            "backtest_is_not_proof_of_future_returns",
            "risk_free_rate_assumed_zero",
            "open_prices_do_not_model_partial_fills",
            "corporate_actions_depend_on_data_vendor_adjustment",
        ]
        if observations < 252:
            warnings.append("less_than_one_year_of_scored_observations")
        if not all(gates.values()):
            warnings.append("model_not_validated")
        promotion_status = (
            "extended_paper_observation_candidate"
            if all(gates.values())
            else "research_only"
        )
        completed_at = datetime.now(timezone.utc)
        identity = "|".join(
            (
                self.model.model_id,
                self.model.version,
                symbol_rows[0].symbol,
                benchmark_rows[0].symbol,
                symbol_rows[0].timestamp.isoformat(),
                symbol_rows[-1].timestamp.isoformat(),
                format(self.config.one_way_cost_bps, "f"),
            )
        )
        report_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return ResearchBacktestReport(
            report_id=report_id,
            model_id=self.model.model_id,
            model_version=self.model.version,
            symbol=symbol_rows[0].symbol,
            benchmark=benchmark_rows[0].symbol,
            timeframe="1Day",
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            config=self.config,
            metrics=metrics,
            gates=gates,
            warnings=tuple(warnings),
            promotion_status=promotion_status,
            execution_eligible=False,
            points=tuple(points),
        )

    @staticmethod
    def _aligned_rows(
        symbol_bars: Iterable[MarketBar],
        benchmark_bars: Iterable[MarketBar],
    ) -> tuple[tuple[MarketBar, ...], tuple[MarketBar, ...]]:
        symbol_rows = tuple(
            sorted(symbol_bars, key=lambda row: row.timestamp)
        )
        benchmark_rows = tuple(
            sorted(benchmark_bars, key=lambda row: row.timestamp)
        )
        if not symbol_rows or not benchmark_rows:
            raise ValueError("Symbol and benchmark daily bars are required")
        if any(row.timeframe != "1Day" for row in symbol_rows):
            raise ValueError("Research backtest requires 1Day symbol bars")
        if any(row.timeframe != "1Day" for row in benchmark_rows):
            raise ValueError(
                "Research backtest requires 1Day benchmark bars"
            )
        symbol_by_day = {row.timestamp.date(): row for row in symbol_rows}
        benchmark_by_day = {
            row.timestamp.date(): row for row in benchmark_rows
        }
        common_days = tuple(
            sorted(set(symbol_by_day) & set(benchmark_by_day))
        )
        return (
            tuple(symbol_by_day[day] for day in common_days),
            tuple(benchmark_by_day[day] for day in common_days),
        )


class PortfolioCorrelationAnalyzer:
    def __init__(
        self,
        *,
        lookback_days: int = 60,
        high_correlation: Decimal = Decimal("0.80"),
    ) -> None:
        if lookback_days < 20:
            raise ValueError("Correlation lookback must be at least 20")
        if not ZERO < high_correlation <= Decimal("1"):
            raise ValueError("High-correlation threshold must be in (0, 1]")
        self.lookback_days = lookback_days
        self.high_correlation = high_correlation

    def analyze(
        self,
        *,
        account_id: str,
        bars_by_symbol: dict[str, Iterable[MarketBar]],
        evaluated_at: datetime | None = None,
    ) -> PortfolioRiskReport:
        checked_at = (evaluated_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        requested = tuple(sorted(bars_by_symbol))
        return_series: dict[str, dict[Any, Decimal]] = {}
        missing: list[str] = []
        for symbol in requested:
            rows = tuple(
                sorted(
                    bars_by_symbol[symbol],
                    key=lambda row: row.timestamp,
                )
            )
            if len(rows) < self.lookback_days + 1:
                missing.append(symbol)
                continue
            selected = rows[-self.lookback_days - 1 :]
            return_series[symbol] = {
                current.timestamp.date(): (
                    current.close / previous.close - Decimal("1")
                )
                for previous, current in zip(selected, selected[1:])
                if previous.close > ZERO
            }
        included = tuple(sorted(return_series))
        pairs: list[CorrelationPair] = []
        for left_index, left in enumerate(included):
            for right in included[left_index + 1 :]:
                common_days = tuple(
                    sorted(
                        set(return_series[left])
                        & set(return_series[right])
                    )
                )
                left_values = tuple(
                    return_series[left][day] for day in common_days
                )
                right_values = tuple(
                    return_series[right][day] for day in common_days
                )
                correlation = self._correlation(
                    left_values, right_values
                )
                pairs.append(
                    CorrelationPair(
                        left_symbol=left,
                        right_symbol=right,
                        correlation=correlation,
                        observations=len(common_days),
                    )
                )
        positive = tuple(
            max(pair.correlation, ZERO) for pair in pairs
        )
        average_positive = _mean(positive)
        maximum_positive = max(positive, default=ZERO)
        symbol_count = len(included)
        effective_breadth = (
            Decimal(symbol_count)
            / (
                Decimal("1")
                + Decimal(max(0, symbol_count - 1))
                * average_positive
            )
            if symbol_count
            else ZERO
        )
        high_pairs = tuple(
            pair
            for pair in pairs
            if pair.correlation >= self.high_correlation
        )
        reasons: list[str] = []
        if symbol_count < 2:
            state = "insufficient_data"
            reasons.append("at_least_two_symbols_required")
        elif (
            maximum_positive >= Decimal("0.90")
            or effective_breadth
            < max(Decimal("1.50"), Decimal(symbol_count) * Decimal("0.55"))
        ):
            state = "concentrated"
            reasons.append("correlation_concentration_detected")
        elif high_pairs:
            state = "watch"
            reasons.append("high_correlation_pairs_present")
        else:
            state = "diversified"
            reasons.append("no_high_correlation_cluster_detected")
        if missing:
            reasons.append("some_symbols_missing_history")
        identity = "|".join(
            (
                account_id,
                checked_at.date().isoformat(),
                str(self.lookback_days),
                ",".join(requested),
            )
        )
        return PortfolioRiskReport(
            report_id=hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest(),
            account_id=account_id,
            evaluated_at=checked_at.isoformat(),
            lookback_days=self.lookback_days,
            state=state,
            symbols_requested=requested,
            symbols_included=included,
            missing_symbols=tuple(missing),
            average_positive_correlation=average_positive,
            maximum_positive_correlation=maximum_positive,
            effective_breadth=effective_breadth,
            high_correlation_pairs=high_pairs,
            all_pairs=tuple(pairs),
            reason_codes=tuple(reasons),
            execution_eligible=False,
        )

    @staticmethod
    def _correlation(
        left: tuple[Decimal, ...],
        right: tuple[Decimal, ...],
    ) -> Decimal:
        if len(left) != len(right) or len(left) < 2:
            return ZERO
        left_mean = _mean(left)
        right_mean = _mean(right)
        covariance = _mean(
            (left_value - left_mean) * (right_value - right_mean)
            for left_value, right_value in zip(left, right)
        )
        denominator = _stdev(left) * _stdev(right)
        if denominator <= ZERO:
            return ZERO
        return max(
            Decimal("-1"),
            min(Decimal("1"), covariance / denominator),
        )
