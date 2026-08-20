# Defined-Risk Options Paper Operations

Release 0.14 supports defined-risk options only on Alpaca Paper. This is an
engineering and measurement program, not evidence that an option strategy is
profitable and not permission for live trading.

Forward exact-contract measurement and its limitations are documented in
[OPTION_EVIDENCE_LAB.md](OPTION_EVIDENCE_LAB.md).

## Supported structures

- Bull-call and bear-put debit spreads.
- Bull-put and bear-call credit spreads.
- Iron condors.
- Protective puts against an already managed long stock position.

Naked short calls, uncovered packages, mixed expirations, more than four legs,
fractional contracts, and structures whose expiry payoff is unbounded are
rejected.

## Account allocation

Every option bot is a normal account allocation in
`config/paper_portfolio.json`. It has its own strategy ID, source signal,
capital weight, risk budget, confidence floor, symbols, Paper permission, and
entry interval. The `option_policy` freezes:

- structure and required Alpaca options level;
- minimum/maximum days to expiration;
- long, short, and wing delta targets;
- maximum strike width;
- minimum modeled theta for positive-theta structures;
- profit target and loss multiple;
- mandatory days-before-expiration exit; and
- maximum quote age.

The income-oriented option allocations may be enabled for guarded Paper
submission. Protective puts remain disabled by default because they require an
existing managed stock position.

`minimum_entry_interval_minutes` controls how often the same account, symbol,
and strategy may submit a new entry after a broker submission. For
`spx_rut_put_credit_14dte`, `1440` means at most one entry per day per proxy
symbol. A weekly income protocol can use `10080` without creating a duplicate
strategy identity.

## Entry gates

An option Paper order requires all ordinary controls plus:

1. Alpaca Paper account status active.
2. Options trading level 2 for a protective put or level 3 for a spread.
3. OPRA data for a production-quality submission, or an explicit Paper-only
   opt-in to Alpaca's indicative feed for engineering tests.
4. Fresh decision-time quotes for every selected leg.
5. Bid and ask on every leg, bounded relative spread, and quote size.
6. One underlying and one expiration.
7. Width at or below the allocation limit.
8. Finite maximum loss under the central payoff analyzer.
9. Positive modeled package theta when positive theta is the hypothesis.

Alpaca MLeg net-price signs are stored exactly: positive is a debit and
negative is a credit.

## Risk and lifecycle

The central risk engine values each package at expiry breakpoints, adds an
option slippage reserve, applies the allocation's stricter risk budget, and
then applies the 3% per-trade and 10% aggregate hard ceilings. Reservations
are scoped by account and store every OCC contract symbol.

The automation worker evaluates open packages before looking for new entries.
It estimates the current package liquidation value using buys at the ask and
sells at the bid. It submits one reduce-only closing MLeg when a configured
profit target, loss limit, or pre-expiration boundary triggers. The exit order
uses `buy_to_close`/`sell_to_close` on every leg. A broker fill is linked back
to the opening intent, realizes package P/L, and releases the opening risk.

The emergency stop blocks new opening risk, not eligible reduce-only option
closes. Reduce-only submission remains available while
`TRADING_ENABLE_PAPER_ORDERS=true`, even if automation entries are disabled or
the emergency stop is active. Setting `TRADING_ENABLE_PAPER_ORDERS=false`
blocks every application submission, including exits.

These exits are application-managed. Alpaca does not provide the stock-style
bracket used by the equity bots for these packages. A process, network, data,
or broker outage can delay an exit. Alpaca can also exercise, assign, or
liquidate expiring positions. The pre-expiration policy reduces that risk but
cannot eliminate it.

## Statistics and theta

The audit database accumulates per account and strategy:

- signal, decision, and lifecycle-state counts;
- closed trades, wins, losses, breakevens, and win rate;
- gross/average/total realized P/L;
- average realized R, profit factor, and realized drawdown;
- option and per-structure realized P/L;
- realized P/L for trades that had positive modeled theta at entry; and
- current decision-time modeled theta per day for open packages.

“Positive-theta trade P/L” is the whole package result. It is not pure theta
profit: delta, gamma, vega, volatility-surface movement, spreads, timing, and
fills also affect it. A defensible theta attribution model would require
time-series Greeks and counterfactual repricing and is not claimed here.

## Paper activation

Paper submission still needs all three controls:

```dotenv
TRADING_AUTOMATION_ENABLED=true
TRADING_ENABLE_PAPER_ORDERS=true
TRADING_EMERGENCY_STOP=false
TRADING_OPTION_DATA_FEED=opra
```

If OPRA is unavailable and the run is strictly Paper Trading, the engine can be
allowed to submit from Alpaca's indicative option feed:

```dotenv
TRADING_OPTION_DATA_FEED=indicative
TRADING_ALLOW_INDICATIVE_PAPER_OPTIONS=true
```

This is intentionally not a live-trading setting. Alpaca describes the
indicative feed as useful for testing/debugging, not for production trading
decisions.

The specific allocation must also set `paper_execution_allowed` to `true`
through a reviewed release. Follow `docs/PAPER_VALIDATION_RUNBOOK.md`; do not
activate multiple new strategies together.

Release 0.35.3 separates Paper option submission into the dedicated
`alpaca-paper-options` account. The default `alpaca-paper` account is
stock-only. The firm and account risk controls still apply, so large packages
such as 25-point SPX/RUT credit spreads may be selected but then rejected when
one contract exceeds the option account's configured risk capacity.

## Broker references

- [Alpaca options trading](https://docs.alpaca.markets/us/docs/options-trading)
- [Alpaca Level 3 multi-leg trading](https://docs.alpaca.markets/us/docs/options-level-3-trading)
- [Alpaca option snapshots and Greeks](https://docs.alpaca.markets/us/reference/optionsnapshots)
- [Alpaca options expiration behavior](https://docs.alpaca.markets/us/docs/options-trading-overview)
