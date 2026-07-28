# Architecture

MultiTrade uses explicit boundaries so a strategy cannot bypass account
controls or broker reconciliation.

```text
Alpaca Market Data
  -> Normalized closed bars
  -> Asset universe and strategy-specific research assignments
  -> Observation-only evidence model and thesis proxies
  -> Features and market regime
  -> Versioned strategy candidates
  -> Deterministic signals and evidence
  -> Account allocation and confidence filters
  -> Central risk authority
  -> Alpaca Paper order construction
  -> Broker reconciliation and lifecycle ledger
  -> Audit, backtests, analytics, and dashboard
```

## Functional departments

### Data and research

The market-data adapter records the source feed and request IDs. Bars are
normalized to `Decimal`, persisted idempotently, and excluded until their
timeframe has closed. Reproducible backtests consume the same normalized bar
contract as the strategy worker.

Intraday execution research keeps raw prices. Long-horizon research explicitly
requests Alpaca's full corporate-action adjustment for splits, dividends, and
spin-offs and records the adjustment provenance with every stored bar.

An independent hourly `research` service loads at least 253 closed daily bars
per instrument. It stores weighted trend, relative-strength, liquidity,
drawdown, volatility, and crisis-state observations. It has no broker object
and no order-submission dependency. Its target-risk multiplier is therefore
an analytical result, not an instruction to trade.

The evidence registry is code-reviewed and versioned. It stores favorable
findings and material caveats together. Internet material is never ingested
straight into an executable strategy: a candidate must have a plausible
mechanism, independent support, chronological out-of-sample results, realistic
costs, stability checks, and a Paper observation period.

An independent `asset-universe` worker combines configured seeds with Alpaca's
most-active screener. It verifies active/tradable equity status and exchange
from Alpaca, measures price and recent share/dollar liquidity from daily bars,
and requires dated company-size evidence from configured references or SEC
company facts. Optional index restrictions require dated constituent
snapshots; listing exchange is never substituted for index membership.
Recommendations and rejected gate evidence are persisted, but the worker has
no order-submission path.

An independent `strategy-lab` worker refreshes raw intraday history every six
hours. It evaluates each configured model across its manual, recommended,
combined, or account-watchlist research assignment using chronological 60/40
splits, next-bar entries, regular US
sessions, forced session-end exits, base costs, and adverse-cost stress. It
then evaluates the frozen model in several non-overlapping later windows and
runs deterministic trade-sequence stress on the resulting out-of-sample
R-multiples. It stores every model report rather than selecting only a
favorable trial.
Readiness is an analytical label and has no path to broker credentials or
order permission.

The Strategy Lab report and its model-trial registry entry are committed
atomically. The trial separately fingerprints the strategy implementation and
parameters, validation/allocation configuration, and exact normalized market
bars. Append-only triggers protect registered rows and their associated
reports, while a per-account/per-strategy hash chain makes broken lineage
visible through read-only dashboard reporting views. This ledger is locally
tamper-evident, not
an externally signed or write-once audit service.

Before a configured intraday strategy enters the lab, the worker binds it to
one frozen experiment manifest. The binding checks the strategy version and
exact parameters, labels the trial as pre-observation, prospective, or
review-due, and becomes part of the configuration fingerprint and trial hash.
The immutable manifest stores the economic hypothesis, mechanism, candidate
family, observation boundary, minimum duration and trial count, and final
holdout status. It cannot grant execution permission.

The experiment program also contains two predeclared sensitivity variants for
each baseline. Every six-hour slot deterministically selects one variant per
strategy and evaluates it on the same complete assigned universe as the
baseline. The next slot rotates to the other variant. This approximately
doubles, rather than triples, laboratory work while preserving comparable
market inputs. Comparison variants are never registered with the automation
service and their readiness is structurally capped at `research_only`.

### Pattern and regime analysis

`FeatureEngine` calculates decision-time features without future data.
`MarketRegime` identifies trend-up, trend-down, range, elevated volatility,
and insufficient-data states. A high-volatility regime blocks automated
entries in the current policy.

### Strategy desk

Each strategy is deterministic and versioned. It receives no broker
credentials and returns at most a `StrategySignal` containing entry reference,
stop, target, confidence, reason codes, and raw evidence. A signal ID is
derived from account, strategy/version, symbol, action, and closed-bar time.

### Portfolio manager

`paper_portfolio.json` defines each account's credential namespace, optional
broker-account identity pin, watchlist, timeframe, maximum
positions, maximum daily orders, symbol cooldown, capital weights, confidence
filters, risk budgets, per-strategy execution-symbol subsets, and per-strategy
Paper approval. Every execution symbol must belong to the account watchlist.
Capital weights for enabled strategies cannot exceed 100%.
Each allocation declares its execution asset class. Option allocations also
freeze a signal source, structure, DTE window, delta targets, maximum width,
minimum modeled theta, quote-age ceiling, profit/loss exits, and mandatory
pre-expiration exit window.

### Risk authority

The risk engine applies:

- Account status, daily-loss, and drawdown kill switches.
- 3% hard per-trade risk ceiling and stricter strategy budget.
- 10% hard aggregate active-risk ceiling.
- Notional limits and stress/slippage buffers.
- Defined-risk option payoff analysis across expiry breakpoints.
- Rejection of uncovered short calls, unsupported mixed expiries, and
  unsupported crypto shorts.

SQLite uses an immediate transaction to evaluate and reserve risk atomically.
Reservations include account ID, asset class, underlying, and every option
contract symbol. Aggregate risk is calculated independently for each account;
contract-level identity prevents an option position from being mistaken for
an unrelated underlying position.
The audit store persists each account's start-of-day and observed peak equity,
so an intra-day recovery does not erase the drawdown reference. Any broker
position or open order that cannot be linked to an active engine reservation
blocks all new automated entries until reconciled.

### Execution and lifecycle

Only the broker adapter owns authenticated trading requests. The adapter is
locked to Alpaca Paper and uses the deterministic signal ID as
`client_order_id`. Stock entries include native Alpaca bracket children.

The heartbeat and strategy worker reconcile broker positions and recent
orders. Reservations remain active while an order or position is open.
Canceled/rejected entries release risk; filled entries release only after a
previously observed position is absent in two consecutive reconciliations.
When Alpaca returns the filled
bracket child, the ledger records entry, exit, exit reason, and estimated
realized P/L.

Alpaca Paper option entries use `day` limit orders. Multi-leg packages are one
`mleg` parent with explicit open/close position intents. A positive parent net
price is a debit and a negative one is a credit. Entry selection fails closed
on missing Greeks, bad liquidity, excessive spread width, wrong trading level,
stale quotes, or non-positive theta when theta is the stated mechanism.

The automation worker evaluates open option packages before new entries. It
constructs one atomic closing MLeg at conservative bid/ask prices when the
profit target, loss limit, or pre-expiration boundary triggers. A close fill is
linked to its parent trade and releases the original reservation. This is
application-managed protection, not a broker-native options bracket: a worker
outage can delay an exit, so options remain disabled for Paper submission in
the tracked configuration.

### Validation and oversight

Backtests enter on the next bar, include configurable costs, cap position size
by risk and capital, and resolve an ambiguous stop/target bar to the stop.
Intraday tests exclude extended hours and close open positions at the end of
each regular session. Walk-forward validation uses chronological 60/40
samples and stores each gate.
Passing a historical gate is evidence for further Paper testing, not proof of
future performance.

The Strategy Lab adds cross-symbol coverage, pooled trade count, median
out-of-sample return, profitable-symbol breadth, pooled profit factor, worst
drawdown, adverse-cost return, per-symbol validation gates, fixed-parameter
chronological folds, and deterministic trade-sequence tail diagnostics. A
fully passing result can only be labeled an extended-Paper-observation
candidate. The bootstrap does not preserve serial dependence or cross-symbol
correlation and is not presented as a forecast.

The daily evidence model has a separate simulator. A decision after day `t`
closes can change exposure only at day `t+1`'s open; its first credited return
ends at the following open. Exposure changes pay configured one-way costs.
Results are compared with a fully invested SPY benchmark and include excess
return, drawdown, risk-adjusted ratios, turnover, and cost estimates. Even a
fully passing report is only an extended-Paper-observation candidate.

The research worker also calculates pairwise daily correlations and an
effective-breadth estimate. This is an observation report, not a covariance
optimizer or a hedge order generator.

The Option Evidence worker freezes selected contract packages, including
decision-time quotes and Greeks, then evaluates only those exact symbols with
synchronized historical trade bars. Its P/L paths, MFE/MAE, underwater time,
and policy-exit events are conservative analytical proxies rather than fills,
historical BBO reconstruction, or theta attribution.

Every opening reservation also passes an atomic single-host firm authority.
Fresh reconciled equity supplies the denominator; all active reservations
supply the numerator. Total, underlying, and source-strategy ceilings are
applied across accounts before the reservation transaction commits.

The dashboard is read-only for broker state and trading actions. Its primary
workspaces are Account, Asset Universe, Strategy Lab, Allocation & Risk,
Operations, and Management. Management contains a narrowly scoped Paper-only
configuration control plane: it may change a known strategy's enabled state,
execution symbols, and per-strategy Paper permission. The API requires the
existing HTTPS authentication plus a page-bound CSRF token, validates symbols
and immutable account/strategy identities, uses optimistic revisions, and
commits the override and audit event in one SQLite transaction. It cannot
select a live broker endpoint, alter server-wide execution gates, change risk
budgets, or bypass the risk engine.

## Deployment

The VPS runs eight isolated containers:

- `engine`: frequent broker truth/reconciliation heartbeat.
- `automation`: five-minute data, feature, signal, allocation, and guarded
  Paper cycle.
- `research`: hourly closed-daily-bar evidence model; no execution path.
- `asset-universe`: daily asset eligibility and assignment evidence; no
  execution path.
- `strategy-lab`: six-hour intraday cross-symbol validation; no execution
  path.
- `option-evidence`: hourly exact-contract option path measurement; no
  execution path.
- `dashboard`: authenticated monitoring plus audited Paper-only strategy
  configuration; no broker-order client.
- `caddy`: public TLS termination and reverse proxy.

All application containers drop Linux capabilities, enable
`no-new-privileges`, use a read-only root filesystem, and write only to the
shared `/app/var` volume.

## Scaling boundary

Release 0.16 supports multiple Alpaca Paper accounts under one local
supervisor. Credentials are isolated by environment-variable prefix, every
multi-account connection is pinned to the expected Alpaca account UUID, and a
failure is contained to that account while the aggregate component health
becomes degraded. Account cycles are deliberately sequential, and opening
risk is consolidated atomically across accounts in the shared SQLite store.

Horizontal or multi-host execution still requires PostgreSQL or an equivalent
coordinated store, a distributed firm-risk transaction, a distributed lease
per account, and cross-instance idempotency. Dedicated crypto and forex adapters
must normalize their different sessions, leverage, order types, and failure
modes before being allowed through the same risk authority.
