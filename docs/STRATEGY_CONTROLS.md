# Paper Strategy Controls

The Management workspace changes only three fields for a strategy that
already exists in the tracked Paper portfolio:

- enabled or disabled;
- an explicit list of stock or option-underlying symbols;
- permission for that strategy to submit guarded Alpaca Paper orders.

The static JSON remains the reviewed baseline. A saved row becomes an audited
runtime override in the shared SQLite database. The automation worker reloads
it before each market cycle. Strategy Lab also reloads it and includes the
configured execution symbols in the next research cycle.

## Safety boundary

The dashboard cannot configure a live endpoint, add an unknown strategy,
change capital weights or risk fractions, change the server-wide automation
or Paper-submission switches, or bypass broker, market, data-quality, option,
position, daily-order, and centralized risk checks.

Paper submission requires every independent gate:

1. `TRADING_AUTOMATION_ENABLED=true`;
2. `TRADING_ENABLE_PAPER_ORDERS=true`;
3. `TRADING_EMERGENCY_STOP=false`;
4. the strategy is enabled;
5. the strategy's Paper permission is enabled;
6. the signal, account, market, data, lifecycle, and risk checks all pass.

## Update integrity

The browser sends the authenticated request with a one-hour, client-bound CSRF
token. Each row carries an optimistic revision. A stale browser receives a
revision conflict instead of overwriting a newer change. SQLite commits the
override and `strategy_configuration_changed` audit event in one transaction.

Symbols are normalized to uppercase, deduplicated, and limited to 100 per
strategy. The current strategy engine accepts US stock and option-underlying
symbols such as `AAPL` or `BRK.B`; broker-specific crypto or FX pairs are not
part of this Alpaca stock/options control in release 0.16.

An empty symbol list means the strategy uses the account watchlist. After a
save, the effective configuration is loaded on the next worker cycle; no
container restart is required.
