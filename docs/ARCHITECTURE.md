# Architecture

MultiTrade uses explicit boundaries so a strategy cannot bypass account
controls or broker reconciliation.

```text
Alpaca Market Data
  -> Normalized closed bars
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
filters, risk budgets, and per-strategy Paper approval. Capital weights for
enabled strategies cannot exceed 100%.

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
Walk-forward validation uses chronological 60/40 samples and stores each gate.
Passing a historical gate is evidence for further Paper testing, not proof of
future performance.

The dashboard is read-only. It uses authenticated HTTPS, strict security
headers, safe DOM text rendering, SQLite query-only connections, and
browser-local preferences. It cannot change trading configuration.

## Deployment

The VPS runs four isolated containers:

- `engine`: frequent broker truth/reconciliation heartbeat.
- `automation`: five-minute data, feature, signal, allocation, and guarded
  Paper cycle.
- `dashboard`: authenticated query-only monitoring.
- `caddy`: public TLS termination and reverse proxy.

All application containers drop Linux capabilities, enable
`no-new-privileges`, use a read-only root filesystem, and write only to the
shared `/app/var` volume.

## Scaling boundary

Release 0.3 intentionally supports one enabled Paper account and one
orchestrator. Multi-account/cross-broker execution requires PostgreSQL with
account-scoped reservations, a portfolio-wide exposure service, credential
isolation per connector, and distributed locking. Dedicated crypto and forex
adapters must normalize their different sessions, leverage, order types, and
failure modes before being allowed through the same risk authority.
