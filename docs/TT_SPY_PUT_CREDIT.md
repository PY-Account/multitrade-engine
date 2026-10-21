# TT-inspired SPY put credit spread — Paper test

Strategy ID: `tt_spy_put_credit`. Release: 0.35.24.

This is an executable Alpaca **Paper** candidate, not a validated investment
system. The engine has no live-account adapter. No 15% account return, 75%
capture of published results, or 20% drawdown ceiling has been established.

## Source and implementation boundary

[TT: Performance Analysis: Static and Dynamic Spreads (2024-02-05)](https://www.tastylive.com/news-insights/How-to-Use-Static-and-Dynamic-Spreads-to-Trade-SPY-Options)
reports 49% annualized ROC, 77% winners, and $109 average losing trade for a
30-delta, $5-wide SPY put credit spread opened near 45 DTE and closed at 21 DTE.
That is external research, not this engine's account return. The accessible
publication does not disclose all sampling, sizing, or transaction-cost details.

The engine uses these option-selection/exit rules, plus explicitly independent
engineering rules: Monday scheduling, a 40–50 DTE window, a 0.35 maximum short
delta, the existing positive-theta/liquidity/high-volatility gates, conservative
bid/ask package prices, and central risk controls. It is not an exact replication.

## Frozen rules

- SPY only; option allocation only; closed 5-minute bars.
- Evaluate entries on Monday, 10:00 inclusive to 15:00 exclusive, New York time.
  A Monday holiday means no entry that week. DST-aware timezone data is required;
  without it the strategy produces no entry signal.
- Choose the available eligible expiration nearest 45 calendar days, restricted
  to 40–50 days. Equidistant expirations choose the earlier date. If that
  expiration cannot form a valid package, skip rather than change the rules.
- Sell the eligible OTM Put nearest absolute delta 0.30, never above 0.35.
- Buy the Put exactly $5 lower, same expiry and quantity. Missing exact hedge,
  missing Greeks/theta, poor liquidity, or nonpositive package theta: skip.
- Enter with a single two-leg Limit package. Never open a naked leg.
- Reserve at most 1% of current account equity per opening package, including
  the engine's slippage reserve, and at most 10% account active reserved risk
  when approving an entry. Server/firm limits can tighten these further.
- Block new entries at 10% account drawdown from the stored equity peak, even
  if the server's drawdown setting is looser. This guard is not a persistent
  manual-review latch; it clears if account equity recovers below the threshold.
- Minimum seven days between submitted entries. Existing engine underlying
  exclusivity allows only one managed SPY package at a time; this version does
  not stack weekly SPY tranches. Other SPY strategies can therefore block it.
- Close the whole package at the first executable market cycle at or below
  21 calendar days to expiry. No profit target, premium stop, doubling, rolling,
  or removal of the hedge. Broker fees and execution slippage are additional.
- Emergency stop blocks entries; eligible reduce-only exits remain available
  while the global Paper-order gate remains enabled. Do not turn that gate off
  with open positions unless managing them directly through the broker.

Maximum contractual expiry loss per spread is `(5 - credit) * 100`. The hedge
does not eliminate early assignment, stale-data, broker outage, or expiration
handling risks. Application exits are not broker-hosted guaranteed fills.
Twenty percent is the user's drawdown tolerance, not a promised loss bound.

## Update and make it visible in the existing dashboard

On the existing VPS:

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

The normal portfolio adds this strategy **disabled**, with Paper permission
false; existing strategies and environment variables are not changed. In
Management → Strategy Controls, choose the options Paper account and find
`tt_spy_put_credit`. Disable another strategy or reduce its configured weight
before enabling this one if enabled weights already total 100%. Its default
capital weight is 1%, and its per-entry risk ceiling is 1%.

Keep symbols `SPY` and timeframe `5Min`. The frozen profile rejects incompatible
overrides. First enable observation only. For actual Paper fills, grant the
strategy's Paper permission and ensure both global automation/Paper gates are
true, the emergency stop is false, options level is at least 3, and OPRA quotes
are available. Existing explicit indicative-Paper opt-in remains supported for
engineering checks; indicative fills do not establish strategy profitability.

## Isolated test profile

`config/tt_spy_paper.json` contains only this strategy, enabled for observation
and still denied Paper submission. Use a dedicated, empty Alpaca Paper options
account and a separate deployment/database for an isolated test. Do not switch
an operating account's database to hide existing positions or risk reservations.

Set these values in that deployment's `.env` (credentials stay outside Git):

```dotenv
TRADING_PORTFOLIO_CONFIG=config/tt_spy_paper.json
TRADING_DB_PATH=var/tt-spy-paper.db
TRADING_AUTOMATION_ENABLED=false
TRADING_ENABLE_PAPER_ORDERS=false
TRADING_EMERGENCY_STOP=false
TRADING_OPTION_DATA_FEED=opra
RISK_MAX_OPTION_PER_TRADE=0.01
RISK_MAX_TOTAL_OPEN=0.10
RISK_MAX_DRAWDOWN=0.10
```

Supply the dedicated account's `ALPACA_OPTIONS_*` credentials and
`TRADING_ALPACA_OPTIONS_ACCOUNT_UUID` using the existing multi-account runbook.
The Docker image copies the repository's `config/` directory, so the profile
is available to the workers after rebuilding.

```bash
docker compose run --rm automation multitrade doctor
docker compose run --rm automation multitrade automate --once
```

These commands inherit `.env`; keep the two global submission gates false for
the initial check. Outside the Monday window, no entry signal is expected.
After inspecting observations, enable the strategy's Paper permission in the
dashboard, set both global gates true in `.env`, and recreate the workers:

```bash
bash ops/deploy.sh /opt/multitrade/app
docker compose logs --tail=100 automation
docker compose logs --tail=100 option-evidence
```

The option-evidence worker measures frozen exact contracts and honors
`expiration_only`. Its trade-bar paths are proxies, not executed returns. Do not
use the stock-bar `backtest` command to claim an options return for this signal.
Record net account returns, costs, average deployed risk, drawdown, and skipped
entries before considering any capital increase. A short Paper test cannot
validate a multi-year return or drawdown target.
