# MultiTrade Engine

MultiTrade is a Paper-first foundation for a multi-account, multi-strategy
trading platform. The current milestone connects only to Alpaca Paper and
provides centralized risk checks, audit logging, broker payload construction,
and an always-on account heartbeat.

This repository does not contain a profitable strategy and does not support
live trading.

## Safety invariants

- The Alpaca adapter rejects every endpoint except
  `https://paper-api.alpaca.markets`.
- Paper order submission is disabled unless
  `TRADING_ENABLE_PAPER_ORDERS=true`.
- Strategies produce trade intents; they never hold broker credentials.
- Central risk limits are enforced before an order can reach the broker.
- Per-trade risk is capped at 3% and total active risk at 10%.
- Unlimited-loss option positions are rejected.
- API secrets are excluded from Git, container images, and logs.

The 3% and 10% values are hard ceilings, not recommended operating targets.
Normal strategy allocations will be lower.

## Current components

- Stock, defined-risk option, and spot-crypto trade-intent models.
- Centralized position sizing and kill switches.
- Atomic SQLite risk reservation for a single orchestrator.
- Alpaca Paper account, position, and order adapter.
- Multi-leg option order payloads with up to four legs.
- Structured audit events.
- Freshness-based container health checks.
- Authenticated read-only operations dashboard.
- Hardened Docker Compose deployment with no public ports.

SQLite is intentionally temporary. Before multiple workers, multiple accounts,
or live trading, the risk ledger must move to PostgreSQL and full broker
reconciliation must be implemented.

## Local verification

PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
python -m multitrade demo
```

Create a local configuration:

```powershell
Copy-Item .env.example .env
```

Place Alpaca Paper credentials in `.env`, then run:

```powershell
$env:PYTHONPATH="src"
python -m multitrade doctor
python -m multitrade run --once
```

The CLI loads `.env` without overriding environment variables already supplied
by Docker, CI, or the operating system.

## Docker

```bash
cp .env.example .env
# Add Alpaca Paper credentials to .env.
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 engine
docker compose logs --tail=100 dashboard
```

The default service performs read-only Paper account heartbeats. It cannot
submit orders while `TRADING_ENABLE_PAPER_ORDERS=false`.

The dashboard service reads the audit database through SQLite query-only
connections. It requires `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`, and it
is available only inside the Docker network. No dashboard port is published
until a later HTTPS reverse-proxy milestone is explicitly approved.

## Hostinger

The server setup is documented in
[`docs/SERVER_SETUP.md`](docs/SERVER_SETUP.md). It uses Ubuntu 24.04, Docker
Compose, a private Git repository, and Hostinger's browser terminal. Direct SSH
from the current workstation is blocked by its Zscaler network path.

## Planned milestones

1. Secure, reproducible Alpaca Paper deployment.
2. PostgreSQL account, allocation, risk, and audit model.
3. HTTPS access to the authenticated operations dashboard.
4. First researched strategy and backtesting pipeline.
5. Multi-account strategy allocation.
6. Additional broker adapters for forex and crypto accounts.
7. Controlled Paper order execution and reconciliation.
8. Separate, explicitly approved live-trading program.
