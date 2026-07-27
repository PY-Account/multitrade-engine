# Architecture Direction

MultiTrade will evolve as a modular trading platform with hard separation
between signal generation and broker execution.

```text
Market Data
    -> Feature and Regime Analysis
    -> Strategies and Signals
    -> Portfolio Allocation
    -> Central Risk Authority
    -> Trade Construction
    -> Broker Execution
    -> Reconciliation
    -> Audit, Analytics, and Dashboard
```

## Core boundaries

- Strategy modules may propose trades but cannot access broker credentials.
- The portfolio layer maps strategy proposals to account-specific allocations.
- The risk authority is the only component allowed to approve opening risk.
- Broker adapters normalize different broker APIs behind one internal contract.
- Reconciliation treats broker positions and orders as the external source of
  truth.
- Explanations are generated from decision-time evidence stored in the audit
  record, not invented after a trade closes.

## Account model

Every account will have its own:

- Broker connection and Paper/Live environment.
- Allowed asset classes and instruments.
- Enabled strategies and allocation weights.
- Risk limits that may be stricter than global limits.
- Currency, leverage, session, and liquidity rules.
- Pause state and emergency controls.

Global risk also aggregates correlated exposure across accounts and brokers.

## Deployment evolution

The first release is a single Paper-only process and SQLite store. The next
production foundation will use PostgreSQL for account, allocation, order, risk,
and audit state; Redis will be introduced only when distributed coordination is
actually needed.

The dashboard will expose read-only monitoring first. Configuration changes,
Paper order enablement, and later Live activation will require role-based
permissions, MFA, step-up authentication, and immutable audit records.

## Implemented in release 0.2

- A Paper-only Alpaca adapter locked to the Paper endpoint.
- Normalized account, account-control, position, open-order, and market-clock
  reconciliation.
- Central 3% per-trade and 10% aggregate active-risk ceilings.
- Atomic risk reservations and structured audit events in SQLite.
- An authenticated, read-only HTTPS operations dashboard.
- A fast-forward-only VPS update procedure that preserves HTTPS automatically.

## Deliberately not implemented yet

- Automated strategy signals or claims of profitability.
- Historical-data ingestion and reproducible backtests.
- Paper or Live order submission in the deployed configuration.
- Multi-account execution or cross-account exposure aggregation.
- Editable web configuration, administrative roles, or Live activation.

These boundaries prevent presentation-layer progress from being mistaken for
trading readiness.
