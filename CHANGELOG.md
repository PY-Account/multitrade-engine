# Changelog

## 0.4.0 - 2026-07-28

- Added a versioned evidence registry that records source, finding, caveats,
  intended role, independent support, and required internal checks.
- Added an hourly observation-only research worker using closed daily bars,
  1/3/6/12-month trend components, 200-day trend state, relative strength,
  realized-volatility scaling, liquidity floors, and panic/rebound guards.
- Added a hard-capped, no-leverage volatility target and zero-risk fail-closed
  outputs for insufficient data, poor liquidity, or stressed market states.
- Added a public AI compute/power price-proxy theme derived only from public
  essay topics, with explicit non-attribution and a hard ban on Paper orders.
- Persisted every research decision and its evidence IDs in the audit database.
- Added research health, decisions, evidence, caveats, and source links to the
  authenticated read-only dashboard.
- Added tests for observation-only behavior, insufficient data, closed bars,
  crash-state blocking, audit persistence, and theme execution prohibition.

This release remains Alpaca Paper-only. The new research service cannot place
orders and does not claim that the observed effects will persist.

## 0.3.0 - 2026-07-28

- Added browser-local theme, locale, date-order, time-zone, and 24-hour-clock
  preferences.
- Added historical stock bars, feature extraction, market regimes, and four
  versioned stock strategy candidates.
- Added account-specific allocation, confidence, risk, position, daily-order,
  cooldown, and per-strategy Paper controls.
- Added an always-on guarded strategy worker with idempotent signals.
- Added conservative backtesting and chronological walk-forward validation.
- Added option-chain normalization and defined-risk debit-spread construction
  without automatic option execution.
- Added broker order/position lifecycle reconciliation and release of completed
  risk reservations.
- Expanded the dashboard with strategy runtime, signals, trade explanations,
  price context, lifecycle/P/L, validation results, and operating mode.
- Added a global emergency stop and a staged Paper validation runbook.

This release remains Alpaca Paper-only.
