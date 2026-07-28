# Alpaca Paper Validation Runbook

The purpose of Paper testing is to find defects and measure behavior. Paper
fills do not reproduce market impact, order queue position, latency slippage,
all fees, or live liquidity.

## Stage 0: deploy in observation mode

Keep these VPS values:

```dotenv
TRADING_AUTOMATION_ENABLED=false
TRADING_ENABLE_PAPER_ORDERS=false
TRADING_EMERGENCY_STOP=false
```

Keep every strategy:

```json
"paper_execution_allowed": false
```

Deploy and inspect:

```bash
cd /opt/multitrade/app
bash ops/update.sh
docker compose --profile public-dashboard ps
docker compose --profile public-dashboard logs --tail=100 automation
docker compose --profile public-dashboard logs --tail=100 strategy-lab
```

Expected dashboard state:

- Engine and strategy worker healthy.
- Alpaca environment says Paper.
- Account controls are green.
- Strategy runtime rows appear after a cycle.
- Strategy Lab reports appear after its first, longer historical cycle.
- Signals may be absent for long periods; absence is not a fault.
- No orders are submitted.

Run this stage for at least two complete US market sessions.

## Stage 1: automated and manual walk-forward checks

The Strategy Lab now performs the portfolio-wide baseline automatically. In
the dashboard, review Strategy Lab -> Model Validation and require sufficient
symbol coverage, out-of-sample trade count, and adverse-cost results. A green
readiness label is permission only for further observation, not for orders.

Run each enabled candidate on every intended symbol and more than one market
regime. Example:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine \
  multitrade backtest \
  --strategy breakout_retest \
  --symbol SPY \
  --timeframe 5Min \
  --start 2026-01-01 \
  --end 2026-07-25 \
  --validate
```

Record failures as useful results. Do not tune parameters repeatedly against
the same out-of-sample segment.

## Stage 2: risk-evaluated dry run

Edit only the VPS `.env`:

```dotenv
TRADING_AUTOMATION_ENABLED=true
TRADING_ENABLE_PAPER_ORDERS=false
TRADING_EMERGENCY_STOP=false
```

Redeploy:

```bash
cd /opt/multitrade/app
bash ops/deploy.sh
```

The engine now produces real risk decisions and trade records but sends no
orders. Compare every proposed entry, quantity, stop, target, risk amount,
market regime, and explanation against the chart for at least five sessions.

## Stage 3: tightly limited Paper orders

Only after review:

1. Approve one strategy in `config/paper_portfolio.json` by setting its
   `paper_execution_allowed` to `true`.
2. Use a liquid broad-market symbol first.
3. Keep the strategy risk budget at or below 0.5%.
4. Keep maximum positions and daily orders low.
5. Set `TRADING_ENABLE_PAPER_ORDERS=true`.
6. Deploy and watch the first cycle and the Alpaca Orders page.

This configuration file is tracked in Git. Make the approval through a reviewed
release; do not edit a tracked file directly on the VPS because the safe
updater will stop on local changes.

## Emergency response

To stop new submissions:

```dotenv
TRADING_EMERGENCY_STOP=true
```

Then:

```bash
cd /opt/multitrade/app
bash ops/deploy.sh
```

This does not cancel open orders or flatten positions. Inspect and manage those
at Alpaca. Do not disable the heartbeat or dashboard while exposure exists.

## Daily checks

- Engine, automation, dashboard, and Caddy are healthy.
- No broker account block or transfer/trading suspension.
- Broker positions and open orders match the dashboard.
- No stale market-data, failed-cycle, duplicate, or risk-release anomaly.
- Reserved risk matches open exposure and remains below the configured cap.
- Every submitted entry has active broker-side protection.
- Daily order count, day-trade count, and realized/unrealized P/L are expected.

## Promotion rule

No live-trading promotion is part of release 0.6. A separate live program
requires independent code review, PostgreSQL, recovery drills, alerting,
credential rotation, reconciliation tests, legal/tax review, and explicit
authorization.
