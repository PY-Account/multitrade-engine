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
stores every model report rather than selecting only a favorable trial.
Readiness is an analytical label and has no path to broker credentials or
order permission.

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

`paper_portfolio.json` defines the account watchlist, timeframe, maximum
positions, maximum daily orders, symbol cooldown, capital weights, confidence
filters, risk budgets, per-strategy execution-symbol subsets, and per-strategy
Paper approval. Every execution symbol must belong to the account watchlist.
Capital weights for enabled strategies cannot exceed 100%.

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
drawdown, adverse-cost return, and per-symbol validation gates. A fully
passing result can only be labeled an extended-Paper-observation candidate.

The daily evidence model has a separate simulator. A decision after day `t`
closes can change exposure only at day `t+1`'s open; its first credited return
ends at the following open. Exposure changes pay configured one-way costs.
Results are compared with a fully invested SPY benchmark and include excess
return, drawdown, risk-adjusted ratios, turnover, and cost estimates. Even a
fully passing report is only an extended-Paper-observation candidate.

The research worker also calculates pairwise daily correlations and an
effective-breadth estimate. This is an observation report, not a covariance
optimizer or a hedge order generator.

The dashboard is read-only. Its primary workspaces are Account, Asset Universe,
Strategy Lab, Allocation & Risk, and Operations, with horizontal secondary
navigation and an account selector ready for the later multi-account boundary.
It uses
authenticated HTTPS, strict security headers, safe DOM text rendering,
SQLite query-only connections, and browser-local preferences. It cannot
change trading configuration.

## Deployment

The VPS runs seven isolated containers:

- `engine`: frequent broker truth/reconciliation heartbeat.
- `automation`: five-minute data, feature, signal, allocation, and guarded
  Paper cycle.
- `research`: hourly closed-daily-bar evidence model; no execution path.
- `asset-universe`: daily asset eligibility and assignment evidence; no
  execution path.
- `strategy-lab`: six-hour intraday cross-symbol validation; no execution
  path.
- `dashboard`: authenticated query-only monitoring.
- `caddy`: public TLS termination and reverse proxy.

All application containers drop Linux capabilities, enable
`no-new-privileges`, use a read-only root filesystem, and write only to the
shared `/app/var` volume.

## Scaling boundary

Release 0.7 intentionally supports one enabled Paper account and one
orchestrator. Multi-account/cross-broker execution requires PostgreSQL with
account-scoped reservations, a portfolio-wide exposure service, credential
isolation per connector, and distributed locking. Dedicated crypto and forex
adapters must normalize their different sessions, leverage, order types, and
failure modes before being allowed through the same risk authority.
