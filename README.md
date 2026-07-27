# MultiTrade Engine

MultiTrade is a Paper-only, always-on foundation for operating a small
algorithmic-trading organization. It separates market analysis, strategy
signals, portfolio allocation, central risk approval, broker execution,
reconciliation, audit, backtesting, and monitoring.

Release 0.4 is designed for controlled Alpaca Paper observation and dry-run
testing. It does not support live trading and it does not claim that any
strategy is profitable.

## Safety invariants

- The Alpaca trading adapter rejects every endpoint except
  `https://paper-api.alpaca.markets`.
- A deterministic `client_order_id` makes repeated strategy cycles
  idempotent.
- Global automation, global Paper submission, per-strategy Paper approval,
  and the emergency stop are independent controls.
- The default portfolio approves no strategy for order submission.
- Every opening stock order produced by automation is a broker-side bracket
  with both stop-loss and take-profit children.
- Per-trade risk cannot exceed 3% of equity; configured strategy budgets are
  normally 0.3%-0.5%.
- aggregate reserved/open risk cannot exceed 10% of equity.
- Daily-loss and drawdown guards, maximum positions, daily order limits, and
  per-symbol cooldowns are enforced.
- Unlimited-loss option structures are rejected.
- API secrets are excluded from Git, container images, responses, and logs.

The 3% and 10% figures are hard ceilings, not operating targets.

## Implemented components

- Alpaca Paper account, controls, positions, recent orders, and US-market
  clock reconciliation.
- Paginated Alpaca stock bars using closed bars only, with explicit IEX/SIP
  feed selection.
- Feature service: moving averages, ATR, volume, volatility, Donchian levels,
  trend strength, and market-regime classification.
- Separate observation-only research service: closed daily bars, medium-term
  momentum/trend, market and relative trend, liquidity, realized-volatility
  scaling without leverage, and panic/rebound guards.
- Versioned evidence registry with positive findings, contradictory caveats,
  data limitations, execution candidacy, and internal validation requirements.
- Research-only public AI compute/power price proxy. It is explicitly not a
  reconstruction of any investment firm's holdings or trading rules.
- Versioned and explainable stock strategy candidates:
  breakout/retest, trend pullback, volatility-contraction breakout, and
  range mean reversion.
- Account-specific watchlists, strategy weights, confidence filters, risk
  budgets, position limits, order limits, cooldowns, and Paper approvals.
- Centralized sizing, atomic SQLite risk reservations, duplicate prevention,
  and broker lifecycle reconciliation.
- Conservative backtesting with next-bar entry, modeled costs, stop-first
  handling when both levels touch in one bar, and chronological walk-forward
  gates.
- Alpaca option-chain normalization and liquidity-filtered bull-call and
  bear-put debit-spread construction. This layer is research-only.
- Authenticated HTTPS operations dashboard with account/risk state, strategy
  runtime, signals, trade explanations, price context, validation results,
  audit events, dark/light/system theme, and browser/locale/date/time-zone
  preferences.
- Dashboard authentication includes per-client failure throttling; Caddy adds
  TLS and HSTS.
- Docker Compose services for the heartbeat, strategy worker, dashboard, and
  Caddy TLS proxy.

## Operating modes

The tracked defaults generate and record signals but cannot place orders:

```dotenv
TRADING_AUTOMATION_ENABLED=false
TRADING_ENABLE_PAPER_ORDERS=false
TRADING_EMERGENCY_STOP=false
```

Each strategy also has this default in `config/paper_portfolio.json`:

```json
"paper_execution_allowed": false
```

Behavior:

| Controls | Result |
|---|---|
| Automation false | Signals are recorded as observation-only |
| Automation true, Paper orders false | Risk-evaluated dry run |
| Both true, strategy approval false | Per-strategy dry run |
| Both true, strategy approval true | Guarded Alpaca Paper bracket submission |
| Emergency stop true | No new Paper submission |

The emergency stop does not cancel or close existing broker orders or
positions. Those remain visible and must still be managed at Alpaca.

## Verification

PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m compileall -q src tests
python -m unittest discover -s tests -v
python -m multitrade demo
```

Configuration checks:

```powershell
Copy-Item .env.example .env
# Add Alpaca Paper and dashboard credentials to .env.
$env:PYTHONPATH="src"
python -m multitrade doctor
python -m multitrade run --once
python -m multitrade automate --once
python -m multitrade research --once
python -m multitrade evidence-catalog
```

Backtest one strategy:

```powershell
python -m multitrade backtest `
  --strategy breakout_retest `
  --symbol SPY `
  --timeframe 5Min `
  --start 2026-04-01 `
  --end 2026-07-25 `
  --validate
```

Read-only option-chain engineering check:

```powershell
python -m multitrade option-scan `
  --underlying AAPL `
  --minimum-dte 21 `
  --maximum-dte 60
```

## VPS deployment and update

The `.env` file remains only on the VPS. Do not paste API keys into a shell
command, GitHub, or this chat.

```bash
cd /opt/multitrade/app
bash ops/update.sh
```

The updater:

1. Stops if tracked server files were changed.
2. Fetches and fast-forwards private `main`.
3. Preserves the existing `.env` and HTTPS profile.
4. Builds the image and runs `multitrade doctor`.
5. Recreates the heartbeat, automation, dashboard, and proxy services.

After updating:

```bash
cd /opt/multitrade/app
docker compose --profile public-dashboard ps
docker compose --profile public-dashboard logs --tail=100 automation
docker compose --profile public-dashboard logs --tail=100 engine
docker compose --profile public-dashboard logs --tail=100 research
```

The controlled testing sequence is documented in
[`docs/PAPER_VALIDATION_RUNBOOK.md`](docs/PAPER_VALIDATION_RUNBOOK.md).
Strategy definitions and limitations are documented in
[`docs/STRATEGY_CATALOG.md`](docs/STRATEGY_CATALOG.md).
Evidence admission and the public-thesis boundary are documented in
[`docs/RESEARCH_GOVERNANCE.md`](docs/RESEARCH_GOVERNANCE.md).

## Current boundary

This release supports one enabled Alpaca Paper account in the running workers.
The configuration model is account-scoped, but true multi-account execution,
PostgreSQL portfolio aggregation, dedicated crypto/forex broker adapters,
role-based administration/MFA, and any live-trading program remain separate
future releases. SQLite is safe here only because execution is intentionally a
single account/single orchestrator.
