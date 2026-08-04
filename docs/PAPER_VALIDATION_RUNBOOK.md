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
# Add a real organization/contact only on the VPS to enable SEC size evidence.
TRADING_SEC_USER_AGENT=MultiTrade Research operator@example.com
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
docker compose --profile public-dashboard logs --tail=100 asset-universe
docker compose --profile public-dashboard logs --tail=100 strategy-lab
```

Expected dashboard state:

- Engine and strategy worker healthy.
- Alpaca environment says Paper.
- Account controls are green.
- Strategy runtime rows appear after a cycle.
- Asset Universe recommendations or explicit failed gates appear after its
  first cycle.
- Strategy Lab reports appear after its first, longer historical cycle.
- The first family-comparison cycle registers one of the two sensitivity
  variants per strategy; the other rotates in during the next six-hour slot.
- An accelerated screening run is manual and appears under Strategy Lab ->
  Accelerated Validation. It evaluates all frozen variants without registering
  prospective trials.
- Signals may be absent for long periods; absence is not a fault.
- No orders are submitted.

Run this stage for at least two complete US market sessions.

## Stage 1: automated and manual walk-forward checks

The Strategy Lab now performs the strategy-assigned universe baseline
automatically. In
the dashboard, review Strategy Lab -> Model Validation and require sufficient
symbol coverage, out-of-sample trade count, and adverse-cost results. A green
readiness label is permission only for further observation, not for orders.

Then review Strategy Lab -> Family Comparison. Baseline and selected variant
must show the same assigned symbols for a comparable cycle. Treat favorable
variant results as stability evidence only. Comparison variants are
structurally `research_only`, and the current program has no untouched final
holdout.

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

To screen all registered baseline and comparison candidates in one bounded
historical cycle:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine \
  multitrade accelerated-validation --workers 2
```

Review the scorecards under Strategy Lab -> Accelerated Validation. A high
score only prioritizes further review. It does not create new prospective
days, change a frozen candidate, grant Paper permission, or authorize an
order.

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

### Separate options admission

Do not approve an option allocation in the first stock Paper stage. First
confirm that the dashboard reports options trading level 3 for spreads and
that the VPS uses:

```dotenv
TRADING_OPTION_DATA_FEED=opra
```

The free indicative feed is delayed/modified and is accepted for observation,
not for an option submission. Then review at least five sessions of option
dry-run records for DTE, selected deltas, width, bid/ask liquidity, signed net
price, maximum loss, modeled theta, and exit thresholds.

Approve only one defined-risk option allocation and one liquid underlying.
Keep risk at or below 0.3%, maximum open positions low, and verify the first
MLeg in both Alpaca and the dashboard. Confirm that:

- every leg filled as one parent package;
- the contract symbols match the recorded decision;
- reserved risk remains until the package closes;
- the exit worker records a conservative liquidation price;

- positive-theta trade P/L is not described as pure theta attribution; and
- no package remains open inside its configured pre-expiration window.

Protective puts require an already managed long stock position of at least
100 shares per contract. They are not allowed as standalone bearish bets.

### Multi-timeframe historical re-baseline

Changing bar resolution changes the research candidate and requires a fresh
historical baseline. Run all frozen candidates independently on hourly,
four-hour, and daily bars with:

```bash
docker compose run --rm engine multitrade accelerated-validation \
  --workers 2 \
  --timeframes 1Hour,4Hour,1Day \
  --force-all \
  --optimize \
  --max-candidates 48
```

The account's live/Paper automation timeframe is not changed by this command.
Every research resolution produces a separate audited run. Intraday-only
models such as 0DTE session logic may correctly produce insufficient evidence
on daily bars and must not be interpreted as daily execution candidates.

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

No live-trading promotion is part of release 0.12. A separate live program
requires independent code review, PostgreSQL, recovery drills, alerting,
credential rotation, reconciliation tests, legal/tax review, and explicit
authorization.
