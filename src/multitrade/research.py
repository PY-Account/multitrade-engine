from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from multitrade.audit import SqliteAuditStore
from multitrade.config import Settings
from multitrade.domain import ZERO
from multitrade.health import write_health
from multitrade.market import (
    AlpacaMarketDataClient,
    MarketBar,
    closed_bars,
)
from multitrade.portfolio import AccountPlan, load_account_plans
from multitrade.research_validation import (
    PortfolioCorrelationAnalyzer,
    ResearchModelBacktester,
)


class EvidenceGrade(StrEnum):
    REPLICATED = "replicated"
    SUPPORTED_WITH_CAVEATS = "supported_with_caveats"
    GOVERNANCE = "governance"
    RESEARCH_THESIS = "research_thesis"
    INTERNAL_HYPOTHESIS = "internal_hypothesis"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    title: str
    grade: EvidenceGrade
    source_url: str
    finding: str
    caveats: tuple[str, ...]
    role: str
    independent_support: bool
    execution_candidate: bool
    required_internal_checks: tuple[str, ...]


EVIDENCE_REGISTRY: tuple[EvidenceRecord, ...] = (
    EvidenceRecord(
        evidence_id="ts_momentum_mop_2012",
        title="Time Series Momentum",
        grade=EvidenceGrade.SUPPORTED_WITH_CAVEATS,
        source_url=(
            "https://www.sciencedirect.com/science/article/"
            "pii/S0304405X11002613"
        ),
        finding=(
            "Medium-horizon own-return trend was documented across a broad "
            "set of liquid futures markets."
        ),
        caveats=(
            "The original evidence is primarily futures-based.",
            "Later work finds volatility scaling explains part of the result.",
            "Costs, crowding, and regime changes can remove the premium.",
        ),
        role="directional_component",
        independent_support=True,
        execution_candidate=True,
        required_internal_checks=(
            "chronological_out_of_sample",
            "cost_and_slippage_stress",
            "multi_regime_sample",
            "paper_observation",
        ),
    ),
    EvidenceRecord(
        evidence_id="trend_century_hop_2017",
        title="A Century of Evidence on Trend-Following Investing",
        grade=EvidenceGrade.SUPPORTED_WITH_CAVEATS,
        source_url=(
            "https://www.aqr.com/Insights/Research/Journal-Article/"
            "A-Century-of-Evidence-on-Trend-Following-Investing"
        ),
        finding=(
            "Long historical samples provide evidence that diversified "
            "trend following was not confined to one recent period."
        ),
        caveats=(
            "Historical reconstructions differ from executable live returns.",
            "The study is not evidence for a single-stock intraday system.",
        ),
        role="independent_trend_support",
        independent_support=True,
        execution_candidate=True,
        required_internal_checks=(
            "instrument_specific_validation",
            "cost_and_slippage_stress",
        ),
    ),
    EvidenceRecord(
        evidence_id="volatility_managed_mm_2017",
        title="Volatility Managed Portfolios",
        grade=EvidenceGrade.SUPPORTED_WITH_CAVEATS,
        source_url=(
            "https://onlinelibrary.wiley.com/doi/10.1111/jofi.12513"
        ),
        finding=(
            "Reducing exposure when realized volatility is high can improve "
            "risk-adjusted portfolio behavior in the studied factors."
        ),
        caveats=(
            "Volatility scaling is a risk overlay, not a source of alpha.",
            "Subsequent studies report mixed results across specifications.",
            "This implementation never adds leverage when volatility is low.",
        ),
        role="risk_scaler",
        independent_support=True,
        execution_candidate=True,
        required_internal_checks=(
            "turnover_stress",
            "volatility_estimator_stability",
        ),
    ),
    EvidenceRecord(
        evidence_id="ts_momentum_scaling_critique_ktw_2016",
        title="Time Series Momentum and Volatility Scaling",
        grade=EvidenceGrade.GOVERNANCE,
        source_url=(
            "https://papers.ssrn.com/sol3/papers.cfm?"
            "abstract_id=2786955"
        ),
        finding=(
            "The authors find that volatility scaling explains a material "
            "part of reported time-series-momentum performance."
        ),
        caveats=(
            "This is contradictory evidence that must accompany the "
            "favorable time-series-momentum results.",
        ),
        role="contradictory_evidence",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "compare_scaled_and_unscaled_results",
            "separate_alpha_from_risk_scaling",
        ),
    ),
    EvidenceRecord(
        evidence_id="momentum_crashes_dm_2016",
        title="Momentum Crashes",
        grade=EvidenceGrade.SUPPORTED_WITH_CAVEATS,
        source_url="https://www.nber.org/papers/w20439",
        finding=(
            "Momentum portfolios can crash during volatile rebounds after "
            "large market declines."
        ),
        caveats=(
            "The paper studies long-short momentum portfolios.",
            "Our long-only panic/rebound guard is a conservative inference.",
        ),
        role="crash_state_guard",
        independent_support=True,
        execution_candidate=True,
        required_internal_checks=("crisis_period_replay",),
    ),
    EvidenceRecord(
        evidence_id="replication_hxz_2020",
        title="Replicating Anomalies",
        grade=EvidenceGrade.GOVERNANCE,
        source_url=(
            "https://academic.oup.com/rfs/article/33/5/2019/5236964"
        ),
        finding=(
            "Most published anomalies in the authors' 452-signal library "
            "failed robust replication or multiple-testing thresholds."
        ),
        caveats=(
            "A published backtest is not sufficient evidence for deployment.",
        ),
        role="evidence_admission_filter",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "multiple_testing_control",
            "independent_replication",
            "economic_mechanism",
        ),
    ),
    EvidenceRecord(
        evidence_id="value_momentum_everywhere_amp_2013",
        title="Value and Momentum Everywhere",
        grade=EvidenceGrade.REPLICATED,
        source_url=(
            "https://w4.stern.nyu.edu/facdir/lpederse/papers/"
            "ValMomEverywhere.pdf"
        ),
        finding=(
            "Value and momentum premia were documented across multiple "
            "markets and asset classes, with diversification between them."
        ),
        caveats=(
            "A stock implementation requires point-in-time fundamentals.",
            "A valid cross-sectional portfolio needs a much broader universe.",
            "Alpaca price bars alone cannot reproduce the value signal.",
        ),
        role="blocked_research_backlog",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "licensed_point_in_time_fundamentals",
            "survivorship_free_universe",
            "cross_sectional_cost_and_capacity_model",
        ),
    ),
    EvidenceRecord(
        evidence_id="trading_costs_nmv_2016",
        title="A Taxonomy of Anomalies and Their Trading Costs",
        grade=EvidenceGrade.GOVERNANCE,
        source_url=(
            "https://www.nber.org/system/files/working_papers/"
            "w20721/w20721.pdf"
        ),
        finding=(
            "Implementation costs materially affect whether an anomaly is "
            "economically usable."
        ),
        caveats=("Costs and capacity change through time and by account.",),
        role="cost_capacity_filter",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "spread_slippage_commission_model",
            "liquidity_capacity_limit",
        ),
    ),
    EvidenceRecord(
        evidence_id="aschenbrenner_public_thesis_2024",
        title="Situational Awareness: The Decade Ahead",
        grade=EvidenceGrade.RESEARCH_THESIS,
        source_url="https://situational-awareness.ai/",
        finding=(
            "The public essay advances a thesis about rapid AI progress and "
            "large compute, data-center, power, and security requirements."
        ),
        caveats=(
            "It is not a published trading strategy.",
            "The investment firm's holdings, sizing, exits, and risk rules "
            "are not disclosed by this source.",
            "Price proxies can diverge materially from the thesis.",
        ),
        role="observation_only_theme",
        independent_support=False,
        execution_candidate=False,
        required_internal_checks=(
            "independent_fundamental_data",
            "explicit_non_attribution",
            "out_of_sample_theme_validation",
        ),
    ),
    EvidenceRecord(
        evidence_id="backtest_overfitting_bblpz_2015",
        title="The Probability of Backtest Overfitting",
        grade=EvidenceGrade.GOVERNANCE,
        source_url=(
            "https://papers.ssrn.com/sol3/papers.cfm?"
            "abstract_id=2326253"
        ),
        finding=(
            "Repeated model selection on the same history can produce "
            "apparently strong strategies that degrade out of sample."
        ),
        caveats=(
            "A simple holdout does not eliminate selection bias.",
            "MultiTrade does not yet implement the paper's full "
            "combinatorially symmetric cross-validation method.",
        ),
        role="backtest_selection_bias_control",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "chronological_out_of_sample",
            "cross_symbol_validation",
            "adverse_cost_stress",
            "record_all_model_trials",
        ),
    ),
    EvidenceRecord(
        evidence_id="deflated_sharpe_ratio_blp_2014",
        title="The Deflated Sharpe Ratio",
        grade=EvidenceGrade.GOVERNANCE,
        source_url=(
            "https://papers.ssrn.com/sol3/papers.cfm?"
            "abstract_id=2460551"
        ),
        finding=(
            "Reported risk-adjusted performance should account for "
            "non-normal returns and the number of strategy trials."
        ),
        caveats=(
            "The method requires a reliable record of related trials.",
            "MultiTrade does not yet claim a Deflated Sharpe Ratio "
            "implementation.",
        ),
        role="multiple_testing_governance",
        independent_support=True,
        execution_candidate=False,
        required_internal_checks=(
            "immutable_candidate_trial_registry",
            "untouched_final_holdout",
            "non_normal_return_diagnostics",
        ),
    ),
    EvidenceRecord(
        evidence_id="intraday_patterns_internal",
        title="MultiTrade Intraday Pattern Candidates",
        grade=EvidenceGrade.INTERNAL_HYPOTHESIS,
        source_url="docs/STRATEGY_CATALOG.md",
        finding=(
            "Breakout/retest, pullback, contraction, and range signals are "
            "deterministic hypotheses implemented for testing."
        ),
        caveats=(
            "No external evidence proves these exact rules are profitable.",
            "They remain disabled for Paper submission by default.",
        ),
        role="internal_research_candidates",
        independent_support=False,
        execution_candidate=False,
        required_internal_checks=(
            "walk_forward_validation",
            "paper_observation",
            "parameter_stability",
        ),
    ),
)


class ResearchState(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    RISK_OFF = "risk_off"
    WATCH = "watch"
    RISK_ON = "risk_on"


@dataclass(frozen=True, slots=True)
class MarketModelDecision:
    model_id: str
    model_version: str
    account_id: str
    symbol: str
    bar_timestamp: str | None
    evaluated_at: str
    state: ResearchState
    score: Decimal
    target_risk_multiplier: Decimal
    execution_eligible: bool
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    components: dict[str, Any]


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


def _clamp(
    value: Decimal, minimum: Decimal, maximum: Decimal
) -> Decimal:
    return max(minimum, min(value, maximum))


class EvidenceWeightedMarketModel:
    """Daily, long-only market observer. It never creates broker orders."""

    model_id = "evidence_weighted_market_model"
    version = "1.0.0"
    minimum_bars = 253
    target_annual_volatility = Decimal("0.10")
    minimum_average_dollar_volume = Decimal("2000000")

    def evaluate(
        self,
        *,
        account_id: str,
        symbol: str,
        bars: Iterable[MarketBar],
        benchmark_bars: Iterable[MarketBar],
        evaluated_at: datetime | None = None,
    ) -> MarketModelDecision:
        checked_at = (evaluated_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        ordered = tuple(sorted(bars, key=lambda row: row.timestamp))
        benchmark = tuple(
            sorted(benchmark_bars, key=lambda row: row.timestamp)
        )
        common = {
            "model_id": self.model_id,
            "model_version": self.version,
            "account_id": account_id,
            "symbol": symbol,
            "evaluated_at": checked_at.isoformat(),
            "execution_eligible": False,
            "evidence_ids": (
                "ts_momentum_mop_2012",
                "trend_century_hop_2017",
                "volatility_managed_mm_2017",
                "ts_momentum_scaling_critique_ktw_2016",
                "momentum_crashes_dm_2016",
                "replication_hxz_2020",
                "trading_costs_nmv_2016",
            ),
        }
        if len(ordered) < self.minimum_bars:
            return MarketModelDecision(
                **common,
                bar_timestamp=(
                    ordered[-1].timestamp.isoformat() if ordered else None
                ),
                state=ResearchState.INSUFFICIENT_DATA,
                score=ZERO,
                target_risk_multiplier=ZERO,
                reason_codes=("minimum_253_closed_daily_bars_required",),
                components={
                    "sample_size": len(ordered),
                    "benchmark_sample_size": len(benchmark),
                },
            )
        if symbol != "SPY" and len(benchmark) < self.minimum_bars:
            return MarketModelDecision(
                **common,
                bar_timestamp=ordered[-1].timestamp.isoformat(),
                state=ResearchState.INSUFFICIENT_DATA,
                score=ZERO,
                target_risk_multiplier=ZERO,
                reason_codes=("benchmark_history_unavailable",),
                components={
                    "sample_size": len(ordered),
                    "benchmark_sample_size": len(benchmark),
                },
            )

        benchmark = ordered if symbol == "SPY" else benchmark
        closes = tuple(row.close for row in ordered)
        benchmark_closes = tuple(row.close for row in benchmark)
        one_month = closes[-1] / closes[-22] - Decimal("1")
        three_to_one = closes[-22] / closes[-64] - Decimal("1")
        six_to_one = closes[-22] / closes[-127] - Decimal("1")
        twelve_to_one = closes[-22] / closes[-253] - Decimal("1")
        benchmark_twelve_to_one = (
            benchmark_closes[-22] / benchmark_closes[-253]
            - Decimal("1")
        )
        relative_twelve_to_one = (
            twelve_to_one - benchmark_twelve_to_one
        )
        own_sma_200 = _mean(closes[-200:])
        benchmark_sma_200 = _mean(benchmark_closes[-200:])
        own_above_trend = closes[-1] > own_sma_200
        benchmark_above_trend = (
            benchmark_closes[-1] > benchmark_sma_200
        )

        daily_returns = tuple(
            current / previous - Decimal("1")
            for previous, current in zip(closes[-61:-1], closes[-60:])
            if previous > ZERO
        )
        annual_volatility = _stdev(daily_returns) * Decimal("252").sqrt()
        recent_high = max(closes[-253:])
        drawdown = closes[-1] / recent_high - Decimal("1")
        average_dollar_volume = _mean(
            row.close * row.volume for row in ordered[-20:]
        )

        momentum_score = (
            _clamp(one_month / Decimal("0.08"), Decimal("-1"), Decimal("1"))
            * Decimal("0.10")
            + _clamp(
                three_to_one / Decimal("0.12"),
                Decimal("-1"),
                Decimal("1"),
            )
            * Decimal("0.20")
            + _clamp(
                six_to_one / Decimal("0.20"),
                Decimal("-1"),
                Decimal("1"),
            )
            * Decimal("0.30")
            + _clamp(
                twelve_to_one / Decimal("0.30"),
                Decimal("-1"),
                Decimal("1"),
            )
            * Decimal("0.40")
        )
        score = (
            momentum_score * Decimal("0.55")
            + (Decimal("1") if own_above_trend else Decimal("-1"))
            * Decimal("0.15")
            + (
                Decimal("1")
                if benchmark_above_trend
                else Decimal("-1")
            )
            * Decimal("0.15")
            + _clamp(
                relative_twelve_to_one / Decimal("0.20"),
                Decimal("-1"),
                Decimal("1"),
            )
            * Decimal("0.15")
        )
        score = _clamp(score, Decimal("-1"), Decimal("1"))

        panic_state = (
            drawdown <= Decimal("-0.15")
            and annual_volatility >= Decimal("0.30")
        )
        volatile_rebound_state = (
            drawdown <= Decimal("-0.08")
            and one_month >= Decimal("0.08")
            and annual_volatility >= Decimal("0.25")
        )
        liquidity_pass = (
            average_dollar_volume >= self.minimum_average_dollar_volume
        )
        reasons: list[str] = []
        if panic_state:
            reasons.append("panic_state_guard")
        if volatile_rebound_state:
            reasons.append("volatile_rebound_guard")
        if not liquidity_pass:
            reasons.append("liquidity_capacity_floor_failed")
        if not benchmark_above_trend:
            reasons.append("benchmark_below_200_day_average")
        if own_above_trend:
            reasons.append("price_above_200_day_average")
        else:
            reasons.append("price_below_200_day_average")
        reasons.append(
            "positive_weighted_momentum"
            if momentum_score > ZERO
            else "nonpositive_weighted_momentum"
        )

        volatility_multiplier = (
            _clamp(
                self.target_annual_volatility / annual_volatility,
                ZERO,
                Decimal("1"),
            )
            if annual_volatility > ZERO
            else ZERO
        )
        if (
            panic_state
            or volatile_rebound_state
            or not liquidity_pass
            or not benchmark_above_trend
            or score <= Decimal("-0.10")
        ):
            state = ResearchState.RISK_OFF
            multiplier = ZERO
        elif score >= Decimal("0.25") and own_above_trend:
            state = ResearchState.RISK_ON
            multiplier = volatility_multiplier
        else:
            state = ResearchState.WATCH
            multiplier = volatility_multiplier * Decimal("0.25")

        return MarketModelDecision(
            **common,
            bar_timestamp=ordered[-1].timestamp.isoformat(),
            state=state,
            score=score.quantize(Decimal("0.0001")),
            target_risk_multiplier=multiplier.quantize(
                Decimal("0.0001")
            ),
            reason_codes=tuple(reasons),
            components={
                "one_month_return": one_month,
                "three_to_one_month_return": three_to_one,
                "six_to_one_month_return": six_to_one,
                "twelve_to_one_month_return": twelve_to_one,
                "benchmark_twelve_to_one_month_return": (
                    benchmark_twelve_to_one
                ),
                "relative_twelve_to_one_month_return": (
                    relative_twelve_to_one
                ),
                "momentum_score": momentum_score,
                "annualized_realized_volatility": annual_volatility,
                "volatility_target": self.target_annual_volatility,
                "drawdown_from_252_day_high": drawdown,
                "average_20_day_dollar_volume": average_dollar_volume,
                "own_200_day_average": own_sma_200,
                "benchmark_200_day_average": benchmark_sma_200,
                "panic_state": panic_state,
                "volatile_rebound_state": volatile_rebound_state,
                "data_feed_limitation": (
                    "IEX volume can understate consolidated market volume"
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class ThemePillar:
    pillar_id: str
    symbols: tuple[str, ...]
    weight: Decimal


@dataclass(frozen=True, slots=True)
class ResearchTheme:
    theme_id: str
    title: str
    source_url: str
    attribution_notice: str
    status: str
    paper_execution_allowed: bool
    pillars: tuple[ThemePillar, ...]


@dataclass(frozen=True, slots=True)
class ResearchProgram:
    benchmark: str
    universe: tuple[str, ...]
    themes: tuple[ResearchTheme, ...]


def load_research_program(path: str | Path) -> ResearchProgram:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    benchmark = str(payload.get("benchmark", "")).strip().upper()
    universe = tuple(
        dict.fromkeys(
            str(symbol).strip().upper()
            for symbol in payload.get("universe", [])
            if str(symbol).strip()
        )
    )
    if not benchmark or benchmark not in universe:
        raise ValueError("Research benchmark must appear in the universe")
    themes: list[ResearchTheme] = []
    for row in payload.get("themes", []):
        pillars = tuple(
            ThemePillar(
                pillar_id=str(item["pillar_id"]),
                symbols=tuple(
                    str(symbol).strip().upper()
                    for symbol in item["symbols"]
                ),
                weight=Decimal(str(item["weight"])),
            )
            for item in row.get("pillars", [])
        )
        if not pillars or sum(
            (item.weight for item in pillars), start=ZERO
        ) != Decimal("1"):
            raise ValueError("Theme pillar weights must sum to 1.0")
        referenced = {
            symbol for pillar in pillars for symbol in pillar.symbols
        }
        if referenced - set(universe):
            raise ValueError("Theme symbols must appear in the universe")
        paper_allowed = bool(row.get("paper_execution_allowed", False))
        if paper_allowed:
            raise ValueError("Research themes cannot enable Paper execution")
        themes.append(
            ResearchTheme(
                theme_id=str(row["theme_id"]),
                title=str(row["title"]),
                source_url=str(row["source_url"]),
                attribution_notice=str(row["attribution_notice"]),
                status=str(row.get("status", "research_only")),
                paper_execution_allowed=False,
                pillars=pillars,
            )
        )
    return ResearchProgram(
        benchmark=benchmark,
        universe=universe,
        themes=tuple(themes),
    )


@dataclass(frozen=True, slots=True)
class ResearchCycleResult:
    status: str
    account_id: str
    universe_size: int
    decisions_recorded: int
    risk_on: int
    watch: int
    risk_off: int
    insufficient_data: int
    validation_reports_recorded: int
    promotion_candidates: int
    portfolio_risk_state: str
    execution_enabled: bool
    request_ids: tuple[str, ...]


class ContinuousResearchService:
    def __init__(
        self,
        *,
        settings: Settings,
        market_data: AlpacaMarketDataClient,
        store: SqliteAuditStore,
        account_plan: AccountPlan,
        program: ResearchProgram,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.store = store
        self.account_plan = account_plan
        self.program = program
        self.model = EvidenceWeightedMarketModel()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ContinuousResearchService":
        plans = tuple(
            plan
            for plan in load_account_plans(
                settings.portfolio_config_path
            )
            if plan.enabled
        )
        if len(plans) != 1:
            raise ValueError(
                "ContinuousResearchService.from_settings requires exactly "
                "one enabled Paper account"
            )
        return cls.from_account_plan(settings, plans[0])

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
        program: ResearchProgram | None = None,
    ) -> "ContinuousResearchService":
        key_id, secret_key, _ = settings.alpaca_credentials_for(
            account_plan.credential_env_prefix
        )
        return cls(
            settings=settings,
            market_data=AlpacaMarketDataClient(
                key_id,
                secret_key,
                feed=settings.market_data_feed,
            ),
            store=store or SqliteAuditStore(settings.db_path),
            account_plan=account_plan,
            program=(
                program
                or load_research_program(
                    settings.research_program_path
                )
            ),
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> ResearchCycleResult:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        universe = tuple(
            dict.fromkeys(
                (*self.program.universe, *self.account_plan.watchlist)
            )
        )
        fetched = self.market_data.fetch_stock_bars(
            universe,
            "1Day",
            checked_at - timedelta(
                days=self.settings.research_lookback_days
            ),
            checked_at,
            adjustment="all",
        )
        usable = {
            symbol: closed_bars(rows, now=checked_at)
            for symbol, rows in fetched.items()
        }
        self.store.record_market_bars(
            bar for rows in usable.values() for bar in rows
        )
        decisions = tuple(
            self.model.evaluate(
                account_id=self.account_plan.account_id,
                symbol=symbol,
                bars=usable.get(symbol, ()),
                benchmark_bars=usable.get(
                    self.program.benchmark, ()
                ),
                evaluated_at=checked_at,
            )
            for symbol in universe
        )
        for decision in decisions:
            self.store.record_research_decision(decision)
        self._record_theme_observations(decisions, checked_at)
        portfolio_risk = PortfolioCorrelationAnalyzer().analyze(
            account_id=self.account_plan.account_id,
            bars_by_symbol={
                symbol: usable.get(symbol, ()) for symbol in universe
            },
            evaluated_at=checked_at,
        )
        self.store.record_portfolio_risk_report(portfolio_risk)
        validation_reports = []
        for symbol in universe:
            try:
                report = ResearchModelBacktester(self.model).run(
                    symbol_bars=usable.get(symbol, ()),
                    benchmark_bars=usable.get(
                        self.program.benchmark, ()
                    ),
                    account_id=self.account_plan.account_id,
                )
            except ValueError:
                continue
            self.store.record_research_backtest(report)
            validation_reports.append(report)
        counts = {
            state: sum(
                1 for decision in decisions if decision.state is state
            )
            for state in ResearchState
        }
        result = ResearchCycleResult(
            status="ok",
            account_id=self.account_plan.account_id,
            universe_size=len(universe),
            decisions_recorded=len(decisions),
            risk_on=counts[ResearchState.RISK_ON],
            watch=counts[ResearchState.WATCH],
            risk_off=counts[ResearchState.RISK_OFF],
            insufficient_data=counts[
                ResearchState.INSUFFICIENT_DATA
            ],
            validation_reports_recorded=len(validation_reports),
            promotion_candidates=sum(
                1
                for report in validation_reports
                if report.promotion_status
                == "extended_paper_observation_candidate"
            ),
            portfolio_risk_state=portfolio_risk.state,
            execution_enabled=False,
            request_ids=tuple(self.market_data.request_ids),
        )
        self.store.record_event(
            "research_cycle_completed",
            self.account_plan.account_id,
            asdict(result),
        )
        write_health(
            self.settings.research_health_path, "ok", asdict(result)
        )
        return result

    def _record_theme_observations(
        self,
        decisions: tuple[MarketModelDecision, ...],
        checked_at: datetime,
    ) -> None:
        by_symbol = {item.symbol: item for item in decisions}
        for theme in self.program.themes:
            score = ZERO
            multiplier = Decimal("1")
            missing: list[str] = []
            pillar_details: dict[str, Any] = {}
            for pillar in theme.pillars:
                available = [
                    by_symbol[symbol]
                    for symbol in pillar.symbols
                    if symbol in by_symbol
                    and by_symbol[symbol].state
                    is not ResearchState.INSUFFICIENT_DATA
                ]
                if not available:
                    missing.append(pillar.pillar_id)
                    continue
                pillar_score = _mean(
                    item.score for item in available
                )
                pillar_multiplier = min(
                    item.target_risk_multiplier for item in available
                )
                score += pillar_score * pillar.weight
                multiplier = min(multiplier, pillar_multiplier)
                pillar_details[pillar.pillar_id] = {
                    "symbols": [item.symbol for item in available],
                    "score": pillar_score,
                    "weight": pillar.weight,
                }
            if missing:
                state = ResearchState.INSUFFICIENT_DATA
                multiplier = ZERO
                reasons = ("theme_pillars_missing_data",)
            elif multiplier == ZERO:
                state = ResearchState.RISK_OFF
                reasons = ("one_or_more_proxy_risk_guards_active",)
            elif score >= Decimal("0.25"):
                state = ResearchState.RISK_ON
                reasons = ("public_thesis_price_proxies_positive",)
            elif score <= Decimal("-0.10"):
                state = ResearchState.RISK_OFF
                multiplier = ZERO
                reasons = ("public_thesis_price_proxies_negative",)
            else:
                state = ResearchState.WATCH
                multiplier *= Decimal("0.25")
                reasons = ("public_thesis_price_proxies_mixed",)
            self.store.record_research_decision(
                MarketModelDecision(
                    model_id="public_thesis_proxy",
                    model_version="1.0.0",
                    account_id=self.account_plan.account_id,
                    symbol=f"THEME:{theme.theme_id}",
                    bar_timestamp=max(
                        (
                            item.bar_timestamp
                            for item in decisions
                            if item.bar_timestamp is not None
                        ),
                        default=None,
                    ),
                    evaluated_at=checked_at.isoformat(),
                    state=state,
                    score=score.quantize(Decimal("0.0001")),
                    target_risk_multiplier=multiplier.quantize(
                        Decimal("0.0001")
                    ),
                    execution_eligible=False,
                    reason_codes=reasons,
                    evidence_ids=(
                        "aschenbrenner_public_thesis_2024",
                        "replication_hxz_2020",
                        "trading_costs_nmv_2016",
                    ),
                    components={
                        "title": theme.title,
                        "status": theme.status,
                        "source_url": theme.source_url,
                        "attribution_notice": theme.attribution_notice,
                        "paper_execution_allowed": False,
                        "missing_pillars": missing,
                        "pillars": pillar_details,
                        "limitation": (
                            "ETF price action is only an imperfect proxy "
                            "for the public macro thesis."
                        ),
                    },
                )
            )


def evidence_catalog() -> list[dict[str, Any]]:
    return [asdict(record) for record in EVIDENCE_REGISTRY]
