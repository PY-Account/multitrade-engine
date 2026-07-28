# Firm-Wide Risk Authority

Release 0.15 adds a consolidated authority above the existing account risk
engines. Every account still enforces its own maximum 3% risk per trade and 10%
total open risk. The firm authority prevents several individually valid
account decisions from becoming one oversized company-level exposure.

## Atomic controls

For every new opening intent, one SQLite `BEGIN IMMEDIATE` transaction:

1. reads all active reservations across accounts;
2. calculates capacity from fresh reconciled account-equity snapshots;
3. measures total, underlying, and strategy risk across the firm;
4. applies the tightest remaining limit to position sizing;
5. writes the reservation before releasing the transaction lock.

This closes the race where two workers could read the same remaining capacity
and both approve it. Reduce-only option exits bypass new-risk capacity because
they can only decrease a verified broker position.

Default Paper limits:

```dotenv
RISK_FIRM_WIDE_ENABLED=true
RISK_FIRM_MAX_TOTAL_OPEN=0.10
RISK_FIRM_MAX_SYMBOL_OPEN=0.03
RISK_FIRM_MAX_STRATEGY_OPEN=0.05
RISK_FIRM_EQUITY_MAX_AGE_SECONDS=900
```

- `RISK_FIRM_MAX_TOTAL_OPEN` caps all active reserved risk divided by fresh firm
  equity.
- `RISK_FIRM_MAX_SYMBOL_OPEN` caps repeated exposure to one underlying across
  accounts, including option packages whose reservation uses that underlying.
- `RISK_FIRM_MAX_STRATEGY_OPEN` caps one strategy across symbols and accounts.
  Stock and option execution wrappers are mapped back to their shared
  `source_strategy_id`, so changing the vehicle cannot bypass this limit.
- `RISK_FIRM_EQUITY_MAX_AGE_SECONDS` prevents a disconnected account's old
  equity from continuing to expand risk capacity.

All active reservations remain in the numerator even when the corresponding
account equity is stale and excluded from the denominator. That failure mode is
conservative: stale connectivity can reduce or exhaust capacity, never create
extra capacity.

## Sizing and rejection

The firm layer first accepts the account risk engine's smaller quantity. If the
firm has less capacity, the quantity is reduced using the same whole-share,
whole-contract, or fractional-crypto granularity as the account engine. If even
the minimum unit does not fit, the intent is rejected with a reason identifying
the binding limit:

- `firm_total_risk_budget_exhausted`
- `firm_symbol_risk_budget_exhausted`
- `firm_strategy_risk_budget_exhausted`

The audit event includes firm equity, active risk, all three ceilings, remaining
capacity, the binding limit, and projected risk. The authenticated dashboard
shows the current firm capacity and exposure breakdown under **Allocation &
Risk → Risk Authority**.

These are loss-at-risk controls based on the engine's conservative risk model.
They do not estimate portfolio VaR, liquidity liquidation cost during market
stress, or nonlinear correlation convergence. The research concentration model
remains diagnostic and cannot loosen these hard limits.
