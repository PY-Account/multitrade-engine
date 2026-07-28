# Option Evidence Lab

Release 0.14 adds a non-executable, forward option evidence program. Its job is
to measure the exact structures that the configured option allocations select;
it does not search historical chains until a profitable combination appears.

## What is frozen

The automation worker now stores an option observation as soon as a package is
constructed, including when global automation is disabled. Each observation
contains:

- account, strategy, underlying, structure, and decision time;
- exact OCC contract symbols, sides, ratios, strikes, expiry, and decision
  marks;
- decision-time delta, gamma, theta, vega, and implied-volatility inputs when
  Alpaca supplied them;
- signed opening net price, modeled maximum risk per package, feed, and
  execution configuration;
- construction failures, without inventing missing legs or Greeks.

An observation is not a fill. Risk-reviewed orders and broker-reconciled results
remain in the separate trade ledger.

## Exact-contract replay

The `option-evidence` worker requests historical bars only for the contract
symbols stored in the original observation. At every timestamp shared by all
legs it:

1. constructs the signed mark of the complete package;
2. subtracts the configured price-point slippage for every leg;
3. compares that conservative liquidation mark with the frozen decision limit;
4. calculates proxy P/L, favorable/adverse excursion, time underwater, and the
   first configured profit, loss, or expiry-window exit event;
5. stores missing-leg and timestamp-alignment warnings.

The replay uses historical option **trade bars**. It is not a reconstruction of
historical bid/ask quotes and cannot prove that the displayed package could
have filled. Historical Greeks are not available in this bar response and are
never reconstructed or labeled as realized theta income.

The latest package evidence is visible under **Strategy Lab → Option Evidence**.
Actual broker-realized P/L remains separate in **Account → Strategy
Performance**.

## Configuration

```dotenv
TRADING_OPTION_EVIDENCE_CYCLE_SECONDS=3600
TRADING_OPTION_EVIDENCE_HEALTH_PATH=var/option-evidence-health.json
TRADING_OPTION_EVIDENCE_HEALTH_MAX_AGE_SECONDS=10800
TRADING_OPTION_EVIDENCE_TIMEFRAME=15Min
TRADING_OPTION_EVIDENCE_MAXIMUM_OBSERVATIONS=100
TRADING_OPTION_EVIDENCE_SLIPPAGE_PER_LEG=0.05
```

`TRADING_OPTION_EVIDENCE_SLIPPAGE_PER_LEG` is an option price amount, so `0.05`
means five cents per contract share, per leg. It is an analytical haircut and
does not replace broker fill reconciliation.

One manual diagnostic cycle:

```bash
docker compose run --rm option-evidence multitrade option-evidence --once
```

Service status:

```bash
docker compose ps option-evidence
docker compose logs --tail=100 option-evidence
```

## Data boundary

Alpaca documents historical option data availability beginning in February
2024. The indicative feed is a derived, delayed product; OPRA is the official
consolidated feed and requires the applicable subscription. A feed label is
persisted on every observation and report, so evidence from the two sources is
not silently mixed.

Primary references:

- [Alpaca historical option bars](https://docs.alpaca.markets/us/reference/optionbars)
- [Alpaca historical option data](https://docs.alpaca.markets/us/docs/historical-option-data)
- [Alpaca options trading](https://docs.alpaca.markets/us/docs/options-trading)

