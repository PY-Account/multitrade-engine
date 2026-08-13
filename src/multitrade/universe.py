from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from multitrade.audit import SqliteAuditReader, SqliteAuditStore
from multitrade.brokers.alpaca import AlpacaPaperBroker
from multitrade.config import Settings
from multitrade.domain import ZERO
from multitrade.health import write_health
from multitrade.market import AlpacaMarketDataClient, MarketBar
from multitrade.portfolio import AccountPlan, load_account_plans


SEC_DATA_URL = "https://data.sec.gov"
SEC_TICKER_URL = (
    "https://www.sec.gov/files/company_tickers_exchange.json"
)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal") from exc


def _symbols(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip().upper()
            for value in values
            if str(value).strip()
        )
    )


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    index_id: str
    label: str
    as_of: str
    source_url: str
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.index_id or not self.label:
            raise ValueError("Index snapshot identity is required")
        if not self.as_of or not self.source_url:
            raise ValueError(
                "Index snapshots require an as-of date and source"
            )
        if not self.source_url.startswith("https://"):
            raise ValueError("Index snapshot source must use HTTPS")


@dataclass(frozen=True, slots=True)
class AssetReference:
    symbol: str
    company_size_usd: Decimal
    size_method: str
    as_of: str
    source_url: str

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Asset reference symbol is required")
        if self.company_size_usd <= ZERO:
            raise ValueError("Asset company size must be positive")
        if self.size_method not in {
            "market_cap",
            "sec_shares_times_price",
            "sec_public_float",
        }:
            raise ValueError("Unsupported company-size method")
        if not self.as_of or not self.source_url:
            raise ValueError(
                "Asset references require an as-of date and source"
            )
        if not self.source_url.startswith("https://"):
            raise ValueError("Asset reference source must use HTTPS")


@dataclass(frozen=True, slots=True)
class UniversePolicy:
    policy_id: str
    candidate_source: str
    seed_symbols: tuple[str, ...]
    most_active_limit: int
    lookback_days: int
    minimum_price: Decimal
    minimum_company_size_usd: Decimal
    minimum_average_daily_share_volume: Decimal
    minimum_average_daily_dollar_volume: Decimal
    allowed_exchanges: tuple[str, ...]
    required_index_sets: tuple[str, ...]
    maximum_company_size_age_days: int
    maximum_index_snapshot_age_days: int
    maximum_recommendations: int

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("Universe policy_id is required")
        if self.candidate_source not in {
            "manual",
            "alpaca_most_active",
            "combined",
        }:
            raise ValueError("Unsupported universe candidate source")
        if not 1 <= self.most_active_limit <= 100:
            raise ValueError("most_active_limit must be 1-100")
        if not 5 <= self.lookback_days <= 90:
            raise ValueError("Universe lookback_days must be 5-90")
        if self.minimum_price <= ZERO:
            raise ValueError("Universe minimum price must be positive")
        if self.minimum_company_size_usd <= ZERO:
            raise ValueError(
                "Universe minimum company size must be positive"
            )
        if (
            self.minimum_average_daily_share_volume < ZERO
            or self.minimum_average_daily_dollar_volume < ZERO
        ):
            raise ValueError("Universe liquidity floors cannot be negative")
        if not 1 <= self.maximum_recommendations <= 600:
            raise ValueError("maximum_recommendations must be 1-600")
        if not 30 <= self.maximum_company_size_age_days <= 730:
            raise ValueError(
                "maximum_company_size_age_days must be 30-730"
            )
        if not 1 <= self.maximum_index_snapshot_age_days <= 365:
            raise ValueError(
                "maximum_index_snapshot_age_days must be 1-365"
            )


@dataclass(frozen=True, slots=True)
class StrategyUniverseAssignment:
    strategy_id: str
    selection_mode: str
    policy_id: str | None
    manual_symbols: tuple[str, ...]
    maximum_symbols: int

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("Strategy universe strategy_id is required")
        if self.selection_mode not in {
            "account_watchlist",
            "manual",
            "recommended",
            "combined",
        }:
            raise ValueError("Unsupported strategy selection mode")
        if self.selection_mode in {"recommended", "combined"}:
            if not self.policy_id:
                raise ValueError(
                    "Recommended strategy selection requires policy_id"
                )
        if not 1 <= self.maximum_symbols <= 600:
            raise ValueError("Strategy maximum_symbols must be 1-600")


@dataclass(frozen=True, slots=True)
class AssetUniverseProgram:
    policies: dict[str, UniversePolicy]
    strategy_assignments: dict[str, StrategyUniverseAssignment]
    index_snapshots: dict[str, IndexSnapshot]
    asset_references: dict[str, AssetReference]

    def __post_init__(self) -> None:
        unknown_policies = {
            assignment.policy_id
            for assignment in self.strategy_assignments.values()
            if assignment.policy_id
            and assignment.policy_id not in self.policies
        }
        if unknown_policies:
            raise ValueError(
                "Unknown universe policies: "
                + ", ".join(sorted(unknown_policies))
            )
        required_indexes = {
            index_id
            for policy in self.policies.values()
            for index_id in policy.required_index_sets
        }
        unknown_indexes = required_indexes - set(self.index_snapshots)
        if unknown_indexes:
            raise ValueError(
                "Unknown index snapshots: "
                + ", ".join(sorted(unknown_indexes))
            )

    def assigned_symbols(
        self,
        strategy_id: str,
        *,
        account_watchlist: Iterable[str],
        recommendations_by_policy: dict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        assignment = self.strategy_assignments.get(strategy_id)
        if assignment is None:
            return _symbols(account_watchlist)
        manual = assignment.manual_symbols
        recommended = (
            recommendations_by_policy.get(assignment.policy_id or "", ())
        )
        if assignment.selection_mode == "account_watchlist":
            selected = _symbols(account_watchlist)
        elif assignment.selection_mode == "manual":
            selected = manual
        elif assignment.selection_mode == "recommended":
            selected = recommended
        else:
            selected = _symbols((*manual, *recommended))
        if not selected:
            selected = _symbols(account_watchlist)
        return selected[: assignment.maximum_symbols]


def load_asset_universe_program(
    path: str | Path,
) -> AssetUniverseProgram:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    snapshots: dict[str, IndexSnapshot] = {}
    for row in payload.get("index_snapshots", []):
        snapshot = IndexSnapshot(
            index_id=str(row.get("index_id", "")).strip(),
            label=str(row.get("label", "")).strip(),
            as_of=str(row.get("as_of", "")).strip(),
            source_url=str(row.get("source_url", "")).strip(),
            symbols=_symbols(row.get("symbols", [])),
        )
        if snapshot.index_id in snapshots:
            raise ValueError(
                f"Duplicate index snapshot: {snapshot.index_id}"
            )
        snapshots[snapshot.index_id] = snapshot

    references: dict[str, AssetReference] = {}
    for row in payload.get("asset_references", []):
        reference = AssetReference(
            symbol=str(row.get("symbol", "")).strip().upper(),
            company_size_usd=_decimal(
                row.get("company_size_usd", "0"),
                "company_size_usd",
            ),
            size_method=str(row.get("size_method", "")).strip(),
            as_of=str(row.get("as_of", "")).strip(),
            source_url=str(row.get("source_url", "")).strip(),
        )
        if reference.symbol in references:
            raise ValueError(
                f"Duplicate asset reference: {reference.symbol}"
            )
        references[reference.symbol] = reference

    policies: dict[str, UniversePolicy] = {}
    for row in payload.get("policies", []):
        policy = UniversePolicy(
            policy_id=str(row.get("policy_id", "")).strip(),
            candidate_source=str(
                row.get("candidate_source", "combined")
            ).strip(),
            seed_symbols=_symbols(row.get("seed_symbols", [])),
            most_active_limit=int(row.get("most_active_limit", 30)),
            lookback_days=int(row.get("lookback_days", 30)),
            minimum_price=_decimal(
                row.get("minimum_price", "3"), "minimum_price"
            ),
            minimum_company_size_usd=_decimal(
                row.get("minimum_company_size_usd", "300000000"),
                "minimum_company_size_usd",
            ),
            minimum_average_daily_share_volume=_decimal(
                row.get(
                    "minimum_average_daily_share_volume", "500000"
                ),
                "minimum_average_daily_share_volume",
            ),
            minimum_average_daily_dollar_volume=_decimal(
                row.get(
                    "minimum_average_daily_dollar_volume", "10000000"
                ),
                "minimum_average_daily_dollar_volume",
            ),
            allowed_exchanges=tuple(
                str(value).strip().upper()
                for value in row.get(
                    "allowed_exchanges",
                    ["NASDAQ", "NYSE", "ARCA", "AMEX"],
                )
                if str(value).strip()
            ),
            required_index_sets=tuple(
                str(value).strip()
                for value in row.get("required_index_sets", [])
                if str(value).strip()
            ),
            maximum_company_size_age_days=int(
                row.get("maximum_company_size_age_days", 550)
            ),
            maximum_index_snapshot_age_days=int(
                row.get("maximum_index_snapshot_age_days", 45)
            ),
            maximum_recommendations=int(
                row.get("maximum_recommendations", 20)
            ),
        )
        if policy.policy_id in policies:
            raise ValueError(
                f"Duplicate universe policy: {policy.policy_id}"
            )
        policies[policy.policy_id] = policy
    if not policies:
        raise ValueError("Asset universe requires at least one policy")

    assignments: dict[str, StrategyUniverseAssignment] = {}
    for row in payload.get("strategy_assignments", []):
        assignment = StrategyUniverseAssignment(
            strategy_id=str(row.get("strategy_id", "")).strip(),
            selection_mode=str(
                row.get("selection_mode", "account_watchlist")
            ).strip(),
            policy_id=(
                str(row["policy_id"]).strip()
                if row.get("policy_id")
                else None
            ),
            manual_symbols=_symbols(row.get("manual_symbols", [])),
            maximum_symbols=int(row.get("maximum_symbols", 20)),
        )
        if assignment.strategy_id in assignments:
            raise ValueError(
                "Duplicate strategy universe assignment: "
                f"{assignment.strategy_id}"
            )
        assignments[assignment.strategy_id] = assignment

    return AssetUniverseProgram(
        policies=policies,
        strategy_assignments=assignments,
        index_snapshots=snapshots,
        asset_references=references,
    )


@dataclass(frozen=True, slots=True)
class CompanySizeEvidence:
    value_usd: Decimal
    method: str
    as_of: str
    source_url: str


class SecCompanyFactsClient:
    """Read-only SEC evidence client with an explicitly configured identity."""

    def __init__(
        self,
        user_agent: str,
        *,
        timeout_seconds: int = 20,
    ) -> None:
        if not user_agent.strip():
            raise ValueError(
                "SEC user agent is required for SEC fundamentals"
            )
        if "@" not in user_agent and "http" not in user_agent.lower():
            raise ValueError(
                "SEC user agent must identify an organization and contact"
            )
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self._ticker_map: dict[str, int] | None = None

    def company_size(
        self, symbol: str, price: Decimal
    ) -> CompanySizeEvidence | None:
        cik = self._ticker_to_cik().get(symbol.upper())
        if cik is None:
            return None
        source_url = (
            f"{SEC_DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json"
        )
        payload = self._request(source_url)
        facts = payload.get("facts", {}).get("dei", {})
        shares = self._latest_fact(
            facts.get("EntityCommonStockSharesOutstanding", {}),
            "shares",
        )
        if shares is not None:
            value, as_of = shares
            return CompanySizeEvidence(
                value_usd=value * price,
                method="sec_shares_times_price",
                as_of=as_of,
                source_url=source_url,
            )
        public_float = self._latest_fact(
            facts.get("EntityPublicFloat", {}), "USD"
        )
        if public_float is not None:
            value, as_of = public_float
            return CompanySizeEvidence(
                value_usd=value,
                method="sec_public_float",
                as_of=as_of,
                source_url=source_url,
            )
        return None

    def _ticker_to_cik(self) -> dict[str, int]:
        if self._ticker_map is not None:
            return self._ticker_map
        payload = self._request(SEC_TICKER_URL)
        fields = payload.get("fields")
        rows = payload.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise RuntimeError("SEC ticker mapping has an invalid shape")
        try:
            ticker_index = fields.index("ticker")
            cik_index = fields.index("cik")
        except ValueError as exc:
            raise RuntimeError(
                "SEC ticker mapping is missing required fields"
            ) from exc
        self._ticker_map = {
            str(row[ticker_index]).upper(): int(row[cik_index])
            for row in rows
            if (
                isinstance(row, list)
                and len(row) > max(ticker_index, cik_index)
                and row[ticker_index]
            )
        }
        return self._ticker_map

    @staticmethod
    def _latest_fact(
        fact: dict[str, Any], unit: str
    ) -> tuple[Decimal, str] | None:
        rows = fact.get("units", {}).get(unit, [])
        eligible = [
            row
            for row in rows
            if (
                isinstance(row, dict)
                and row.get("val") is not None
                and row.get("filed")
                and str(row.get("form", "")).upper()
                in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
            )
        ]
        if not eligible:
            return None
        latest = max(
            eligible,
            key=lambda row: (
                str(row.get("filed", "")),
                str(row.get("end", "")),
            ),
        )
        value = _decimal(latest["val"], "SEC fact value")
        if value <= ZERO:
            return None
        return value, str(latest.get("end") or latest["filed"])

    def _request(self, url: str) -> dict[str, Any]:
        if not (
            url.startswith(f"{SEC_DATA_URL}/")
            or url == SEC_TICKER_URL
        ):
            raise ValueError("SEC client refuses unknown endpoints")
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        try:
            with urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"SEC returned HTTP {exc.code}: {body[:500]}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Cannot reach SEC data: {exc.reason}"
            ) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SEC response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("SEC response was not an object")
        return payload


@dataclass(frozen=True, slots=True)
class AssetCandidateEvaluation:
    symbol: str
    sources: tuple[str, ...]
    exchange: str | None
    tradable: bool
    price: Decimal | None
    average_daily_share_volume: Decimal | None
    average_daily_dollar_volume: Decimal | None
    company_size_usd: Decimal | None
    company_size_method: str | None
    company_size_as_of: str | None
    company_size_source_url: str | None
    index_memberships: tuple[str, ...]
    gates: dict[str, bool]
    eligible: bool
    reason_codes: tuple[str, ...]
    score: Decimal


@dataclass(frozen=True, slots=True)
class AssetUniverseReport:
    report_id: str
    account_id: str
    policy_id: str
    evaluated_at: datetime
    candidates_requested: tuple[str, ...]
    recommendations: tuple[str, ...]
    evaluations: tuple[AssetCandidateEvaluation, ...]
    warnings: tuple[str, ...]
    execution_eligible: bool = False

    def __post_init__(self) -> None:
        if self.execution_eligible:
            raise ValueError(
                "Asset-universe reports cannot authorize execution"
            )


@dataclass(frozen=True, slots=True)
class AssetUniverseCycleResult:
    account_id: str
    evaluated_at: datetime
    policies_evaluated: int
    candidates_evaluated: int
    recommendations: int
    request_ids: tuple[str, ...]
    execution_enabled: bool = False


class AssetUniverseEvaluator:
    @staticmethod
    def _fresh(
        value: str | None,
        *,
        evaluated_at: datetime,
        maximum_age_days: int,
    ) -> bool:
        if not value:
            return False
        try:
            observed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            try:
                observed = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return False
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = evaluated_at.astimezone(timezone.utc) - observed.astimezone(
            timezone.utc
        )
        return timedelta(0) <= age <= timedelta(
            days=maximum_age_days
        )

    def evaluate(
        self,
        *,
        account_id: str,
        policy: UniversePolicy,
        evaluated_at: datetime,
        candidate_sources: dict[str, tuple[str, ...]],
        bars_by_symbol: dict[str, tuple[MarketBar, ...]],
        assets_by_symbol: dict[str, dict[str, Any]],
        size_evidence: dict[str, CompanySizeEvidence],
        index_snapshots: dict[str, IndexSnapshot],
        warnings: Iterable[str] = (),
    ) -> AssetUniverseReport:
        evaluations: list[AssetCandidateEvaluation] = []
        for symbol in candidate_sources:
            bars = tuple(
                sorted(
                    bars_by_symbol.get(symbol, ()),
                    key=lambda bar: bar.timestamp,
                )
            )
            asset = assets_by_symbol.get(symbol, {})
            price = bars[-1].close if bars else None
            average_shares = (
                sum((bar.volume for bar in bars), start=ZERO)
                / Decimal(len(bars))
                if bars
                else None
            )
            average_dollars = (
                sum(
                    (
                        bar.volume * (bar.vwap or bar.close)
                        for bar in bars
                    ),
                    start=ZERO,
                )
                / Decimal(len(bars))
                if bars
                else None
            )
            evidence = size_evidence.get(symbol)
            memberships = tuple(
                snapshot.index_id
                for snapshot in index_snapshots.values()
                if symbol in snapshot.symbols
            )
            exchange = (
                str(asset.get("exchange", "")).upper() or None
            )
            tradable = bool(asset.get("tradable", False))
            required_snapshots = tuple(
                index_snapshots[index_id]
                for index_id in policy.required_index_sets
            )
            gates = {
                "active_tradable_us_equity": (
                    str(asset.get("status", "")).lower() == "active"
                    and str(asset.get("class", "")).lower()
                    == "us_equity"
                    and tradable
                ),
                "allowed_exchange": (
                    exchange in policy.allowed_exchanges
                ),
                "minimum_price": (
                    price is not None
                    and price >= policy.minimum_price
                ),
                "minimum_company_size": (
                    evidence is not None
                    and evidence.value_usd
                    >= policy.minimum_company_size_usd
                ),
                "company_size_evidence_fresh": (
                    evidence is not None
                    and self._fresh(
                        evidence.as_of,
                        evaluated_at=evaluated_at,
                        maximum_age_days=(
                            policy.maximum_company_size_age_days
                        ),
                    )
                ),
                "minimum_share_volume": (
                    average_shares is not None
                    and average_shares
                    >= policy.minimum_average_daily_share_volume
                ),
                "minimum_dollar_volume": (
                    average_dollars is not None
                    and average_dollars
                    >= policy.minimum_average_daily_dollar_volume
                ),
                "required_index_membership": (
                    not policy.required_index_sets
                    or bool(
                        set(memberships)
                        & set(policy.required_index_sets)
                    )
                ),
                "index_snapshot_fresh": (
                    not required_snapshots
                    or all(
                        self._fresh(
                            snapshot.as_of,
                            evaluated_at=evaluated_at,
                            maximum_age_days=(
                                policy.maximum_index_snapshot_age_days
                            ),
                        )
                        for snapshot in required_snapshots
                    )
                ),
            }
            reasons = tuple(
                f"failed_{name}"
                for name, passed in gates.items()
                if not passed
            )
            eligible = all(gates.values())
            liquidity_score = (
                average_dollars
                / policy.minimum_average_daily_dollar_volume
                if (
                    average_dollars is not None
                    and policy.minimum_average_daily_dollar_volume > ZERO
                )
                else ZERO
            )
            evaluations.append(
                AssetCandidateEvaluation(
                    symbol=symbol,
                    sources=candidate_sources[symbol],
                    exchange=exchange,
                    tradable=tradable,
                    price=price,
                    average_daily_share_volume=average_shares,
                    average_daily_dollar_volume=average_dollars,
                    company_size_usd=(
                        evidence.value_usd if evidence else None
                    ),
                    company_size_method=(
                        evidence.method if evidence else None
                    ),
                    company_size_as_of=(
                        evidence.as_of if evidence else None
                    ),
                    company_size_source_url=(
                        evidence.source_url if evidence else None
                    ),
                    index_memberships=memberships,
                    gates=gates,
                    eligible=eligible,
                    reason_codes=reasons,
                    score=min(liquidity_score, Decimal("100")),
                )
            )
        ranked = tuple(
            sorted(
                evaluations,
                key=lambda row: (row.eligible, row.score, row.symbol),
                reverse=True,
            )
        )
        recommendations = tuple(
            row.symbol for row in ranked if row.eligible
        )[: policy.maximum_recommendations]
        report_warnings = list(warnings)
        if not recommendations:
            report_warnings.append("no_candidates_passed_all_gates")
        if any(
            row.company_size_usd is None for row in ranked
        ):
            report_warnings.append(
                "company_size_evidence_missing_for_some_candidates"
            )
        return AssetUniverseReport(
            report_id=str(uuid4()),
            account_id=account_id,
            policy_id=policy.policy_id,
            evaluated_at=evaluated_at,
            candidates_requested=tuple(candidate_sources),
            recommendations=recommendations,
            evaluations=ranked,
            warnings=tuple(dict.fromkeys(report_warnings)),
            execution_eligible=False,
        )


class ContinuousAssetUniverseService:
    """Builds research-only symbol recommendations with explicit evidence."""

    def __init__(
        self,
        *,
        account_plan: AccountPlan,
        program: AssetUniverseProgram,
        broker: AlpacaPaperBroker,
        market_data: AlpacaMarketDataClient,
        store: SqliteAuditStore,
        health_path: str | Path,
        sec_client: SecCompanyFactsClient | None = None,
    ) -> None:
        self.account_plan = account_plan
        self.program = program
        self.broker = broker
        self.market_data = market_data
        self.store = store
        self.health_path = str(health_path)
        self.sec_client = sec_client

    @classmethod
    def from_settings(
        cls, settings: Settings
    ) -> "ContinuousAssetUniverseService":
        plans = tuple(
            plan
            for plan in load_account_plans(
                settings.portfolio_config_path
            )
            if plan.enabled
        )
        if len(plans) != 1:
            raise ValueError(
                "ContinuousAssetUniverseService.from_settings requires "
                "exactly one enabled Paper account"
            )
        return cls.from_account_plan(settings, plans[0])

    @classmethod
    def from_account_plan(
        cls,
        settings: Settings,
        account_plan: AccountPlan,
        *,
        store: SqliteAuditStore | None = None,
        program: AssetUniverseProgram | None = None,
    ) -> "ContinuousAssetUniverseService":
        key_id, secret_key, base_url = (
            settings.alpaca_credentials_for(
                account_plan.credential_env_prefix
            )
        )
        sec_client = (
            SecCompanyFactsClient(settings.sec_user_agent)
            if settings.sec_user_agent
            else None
        )
        return cls(
            account_plan=account_plan,
            program=(
                program
                or load_asset_universe_program(
                    settings.asset_universe_config_path
                )
            ),
            broker=AlpacaPaperBroker(
                key_id,
                secret_key,
                base_url=base_url,
            ),
            market_data=AlpacaMarketDataClient(
                key_id,
                secret_key,
                feed=settings.market_data_feed,
            ),
            store=store or SqliteAuditStore(settings.db_path),
            health_path=settings.asset_universe_health_path,
            sec_client=sec_client,
        )

    def run_cycle(
        self, *, now: datetime | None = None
    ) -> AssetUniverseCycleResult:
        evaluated_at = now or datetime.now(timezone.utc)
        assets_by_symbol = self.broker.list_active_stock_assets()
        reports: list[AssetUniverseReport] = []
        all_request_ids: list[str] = []
        for policy in self.program.policies.values():
            warnings: list[str] = []
            candidate_sources: dict[str, list[str]] = {}
            if policy.candidate_source in {"manual", "combined"}:
                for symbol in policy.seed_symbols:
                    candidate_sources.setdefault(symbol, []).append(
                        "configured_seed"
                    )
            for index_id in policy.required_index_sets:
                snapshot = self.program.index_snapshots.get(index_id)
                if snapshot is None:
                    continue
                for symbol in snapshot.symbols:
                    candidate_sources.setdefault(symbol, []).append(
                        f"index_snapshot:{index_id}"
                    )
            if policy.candidate_source in {
                "alpaca_most_active",
                "combined",
            }:
                try:
                    active = self.market_data.fetch_most_active_stocks(
                        top=policy.most_active_limit
                    )
                    all_request_ids.extend(
                        self.market_data.request_ids
                    )
                    for symbol in active:
                        candidate_sources.setdefault(symbol, []).append(
                            "alpaca_most_active"
                        )
                except RuntimeError:
                    warnings.append(
                        "alpaca_most_active_source_unavailable"
                    )
            normalized_sources = {
                symbol: tuple(sources)
                for symbol, sources in candidate_sources.items()
            }
            start = evaluated_at - timedelta(
                days=max(policy.lookback_days * 2, 14)
            )
            bars = (
                self.market_data.fetch_stock_bars(
                    normalized_sources,
                    "1Day",
                    start,
                    evaluated_at,
                    adjustment="all",
                )
                if normalized_sources
                else {}
            )
            trimmed_bars = {
                symbol: tuple(rows[-policy.lookback_days :])
                for symbol, rows in bars.items()
            }
            size_evidence: dict[str, CompanySizeEvidence] = {}
            sec_available = self.sec_client is not None
            for symbol, rows in trimmed_bars.items():
                if not rows:
                    continue
                configured = self.program.asset_references.get(symbol)
                if configured is not None:
                    size_evidence[symbol] = CompanySizeEvidence(
                        value_usd=configured.company_size_usd,
                        method=configured.size_method,
                        as_of=configured.as_of,
                        source_url=configured.source_url,
                    )
                    continue
                if not sec_available or self.sec_client is None:
                    continue
                try:
                    evidence = self.sec_client.company_size(
                        symbol, rows[-1].close
                    )
                except RuntimeError:
                    evidence = None
                    sec_available = False
                    warnings.append("sec_company_facts_source_unavailable")
                if evidence is not None:
                    size_evidence[symbol] = evidence
            if self.sec_client is None and not self.program.asset_references:
                warnings.append(
                    "sec_user_agent_not_configured_company_size_fails_closed"
                )
            report = AssetUniverseEvaluator().evaluate(
                account_id=self.account_plan.account_id,
                policy=policy,
                evaluated_at=evaluated_at,
                candidate_sources=normalized_sources,
                bars_by_symbol=trimmed_bars,
                assets_by_symbol=assets_by_symbol,
                size_evidence=size_evidence,
                index_snapshots=self.program.index_snapshots,
                warnings=warnings,
            )
            self.store.record_asset_universe_report(report)
            reports.append(report)
            all_request_ids.extend(self.market_data.request_ids)
        self.store.record_event(
            "asset_universe_cycle_completed",
            self.account_plan.account_id,
            {
                "policies_evaluated": len(reports),
                "candidates_evaluated": sum(
                    len(report.evaluations) for report in reports
                ),
                "recommendations": sum(
                    len(report.recommendations) for report in reports
                ),
                "execution_enabled": False,
            },
        )
        details = {
            "account_id": self.account_plan.account_id,
            "policies_evaluated": len(reports),
            "recommendations": sum(
                len(report.recommendations) for report in reports
            ),
            "execution_enabled": False,
        }
        write_health(self.health_path, "ok", details)
        return AssetUniverseCycleResult(
            account_id=self.account_plan.account_id,
            evaluated_at=evaluated_at,
            policies_evaluated=len(reports),
            candidates_evaluated=sum(
                len(report.evaluations) for report in reports
            ),
            recommendations=sum(
                len(report.recommendations) for report in reports
            ),
            request_ids=tuple(
                dict.fromkeys(
                    (*all_request_ids, *self.broker.request_ids)
                )
            ),
            execution_enabled=False,
        )


def recommendations_from_reports(
    reader: SqliteAuditReader,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for report in reader.recent_asset_universe_reports(100):
        policy_id = str(report["policy_id"])
        if policy_id not in result:
            result[policy_id] = _symbols(report["recommendations"])
    return result


def program_payload(program: AssetUniverseProgram) -> dict[str, Any]:
    def normalized(value: Any) -> Any:
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, tuple):
            return [normalized(item) for item in value]
        if isinstance(value, dict):
            return {
                key: normalized(item) for key, item in value.items()
            }
        return value

    return {
        "policies": [
            normalized(asdict(value))
            for value in program.policies.values()
        ],
        "strategy_assignments": [
            normalized(asdict(value))
            for value in program.strategy_assignments.values()
        ],
        "index_snapshots": [
            normalized(asdict(value))
            for value in program.index_snapshots.values()
        ],
        "execution_enabled": False,
    }
