# MultiTrade Engine

MultiTrade is a Paper-only, always-on foundation for operating a small
algorithmic-trading organization. It separates market analysis, strategy
signals, portfolio allocation, central risk approval, broker execution,
reconciliation, audit, backtesting, and monitoring.

Release 0.21 adds a source-labeled T3 + adaptive Range Filter trend candidate,
derived conservatively from a YouTube Gold-strategy concept. Because the video
does not disclose complete settings and Alpaca does not provide spot Gold, the
implementation is explicitly an equity research adaptation—not a claimed
reproduction—and remains disabled for execution. Release 0.20 added opt-in
nested parameter optimization: bounded parameter
grids are ranked on an earlier development segment and only the winner of each
strategy is tested once on a later untouched holdout. Results remain
research-only and cannot change account configuration or execution authority.
Release 0.19 turned trade-attribution diagnostics into a bounded research
decision queue. It ranks candidates within each family, distinguishes a
broken signal from an edge erased by costs, and drafts a preregistered next
hypothesis while keeping automatic parameter changes and execution blocked.
Release 0.18 added trade-attribution diagnostics to bounded, all-candidate
accelerated historical validation. Scorecards now separate gross signal
results from modeled costs and identify symbol, regime, entry-hour, exit,
MFE, and MAE failure concentrations on top of audited Paper strategy controls,
exact-contract option evidence, atomic firm-wide risk, and isolated
multi-account Alpaca Paper supervision. It does not support live trading and
it does not claim that any strategy is profitable.

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
- Unlimited-loss option structures are rejected. Option automation is limited
  to defined-risk packages, requires the broker trading level for the package,
  and requires fresh OPRA quotes before a Paper submission.
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
- Next-open research-model simulation with fully adjusted daily bars,
  exposure-change costs, SPY comparison, excess return, drawdown, Sharpe,
  Sortino, information ratio, turnover, and immutable promotion gates.
- Universe correlation monitoring with high-correlation clusters and effective
  breadth, so several symbols are not mistaken for several independent risks.
- Versioned evidence registry with positive findings, contradictory caveats,
  data limitations, execution candidacy, and internal validation requirements.
- Research-only public AI compute/power price proxy. It is explicitly not a
  reconstruction of any investment firm's holdings or trading rules.
- Versioned and explainable stock strategy candidates:
  breakout/retest, trend pullback, volatility-contraction breakout, and
  range mean reversion.
- Account-specific watchlists, strategy weights, confidence filters, risk
  budgets, position limits, order limits, cooldowns, and Paper approvals.
- Multiple Alpaca Paper accounts in one VPS runtime, each with a unique
  credential namespace, broker-account identity pin, independent
  reconciliation, allocations, risk reservations, research results, and
  failure isolation.
- Centralized sizing, atomic SQLite risk reservations, duplicate prevention,
  and broker lifecycle reconciliation.
- Conservative backtesting with next-bar entry, modeled costs, stop-first
  handling when both levels touch in one bar, regular-session-only validation,
  forced session-end flattening, and chronological walk-forward gates.
- An always-on Strategy Lab that evaluates every configured intraday model
  across a strategy-specific manual/recommended research universe, repeats
  the out-of-sample test with adverse costs, aggregates breadth and robustness
  gates, evaluates frozen parameters across non-overlapping chronological
  windows, runs deterministic trade-sequence tail stress, and can never enable
  execution.
- A one-command accelerated validation runner that reuses one account-level
  market-data download to evaluate every frozen baseline and comparison
  variant in a bounded worker pool. Its scorecards are stored separately and
  cannot add prospective experiment trials or authorize execution.
- Additive accelerated diagnostics that distinguish negative gross signal
  behavior from cost-eroded behavior and attribute outcomes by symbol, market
  regime, New York entry hour, exit reason, signal reason set, and
  conservatively measured MFE/MAE.
- An append-only model-trial registry that fingerprints strategy code and
  parameters, laboratory configuration, and exact market inputs; registered
  trials are hash-chained per account/strategy and independently verified by
  the authenticated operations dashboard.
- Frozen strategy-experiment manifests that preregister each intraday
  hypothesis, mechanism, exact parameters, related candidate family,
  prospective observation boundary, minimum evidence period, and explicit
  final-holdout status. Runtime parameter drift fails closed.
- Eight research-only sensitivity variants around the four execution
  baselines. One variant per strategy rotates into each six-hour laboratory
  cycle using the same assigned symbols as its baseline; passing results
  remain ineligible for automatic promotion.
- An Asset Universe department combining operator seeds and Alpaca's
  most-active screener, then failing closed through price, active/tradable
  status, exchange, company-size evidence, evidence age, share-volume,
  dollar-volume, and optional dated index-membership gates.
- Manual, recommended, combined, or account-watchlist research assignments
  per strategy, kept separate from each strategy's reviewed account execution
  symbols.
- Alpaca option-chain normalization, Greeks, deterministic delta/DTE contract
  selection, and liquidity-filtered bull-call/bear-put debit spreads,
  bull-put/bear-call credit spreads, iron condors, and protective puts.
- Defined-risk option packages can pass the same central account risk
  authority and Alpaca Paper MLeg path as stocks only after global and
  per-strategy controls agree. Positive-theta candidates must have positive
  decision-time net theta.
- Managed option exits use atomic close MLeg orders for configured profit,
  loss, and pre-expiration limits. Fill reconciliation records package P/L
  using Alpaca's signed net-price convention.
- A non-executable option evidence worker freezes every selected package and
  replays only its exact contracts with conservative historical trade-bar
  marks, missing-leg coverage, MFE/MAE, underwater time, and policy-exit
  diagnostics. It never labels bar proxies or modeled theta as realized
  profit.
- Per-account/per-strategy Paper statistics: signals, decisions, state counts,
  wins/losses, realized P/L, realized R, profit factor, realized drawdown,
  option P/L, positive-theta-trade P/L, and current modeled theta exposure.
  Modeled theta is never presented as realized profit.
- Atomic firm-wide risk limits cap aggregate, repeated-underlying, and
  repeated-strategy risk across every managed account. Stale account equity is
  excluded from capacity while its active reservations remain counted.
- Authenticated HTTPS operations dashboard with hierarchical Account, Asset
  Universe, Strategy Lab, Allocation & Risk, Operations, and Management
  workspaces; account selection; strategy runtime, signals, trade
  explanations, validation results, browser-local display preferences,
  bilingual terminology, and audited Paper-only strategy controls.
- Dashboard authentication includes per-client failure throttling; Caddy adds
  TLS and HSTS.
- Docker Compose services for the heartbeat, strategy worker, research worker,
  Asset Universe, Strategy Lab, Option Evidence, dashboard, and Caddy TLS
  proxy.

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

The authenticated **Management → Strategy Controls** page can override a
strategy's enabled state, exact execution symbols, and per-strategy Paper
permission without editing files. Every update is CSRF-protected,
revision-checked, and written atomically to SQLite with an audit event. The
automation and Strategy Lab workers reload the effective settings on their
next cycle. These controls cannot select a live endpoint, cannot change the
server-wide gates, and cannot bypass account or risk checks. See
`docs/STRATEGY_CONTROLS.md`.

Behavior:

| Controls | Result |
|---|---|
| Automation false | Signals are recorded as observation-only |
| Automation true, Paper orders false | Risk-evaluated dry run |
| Both true, strategy approval false | Per-strategy dry run |
| Both true, stock strategy approval true | Guarded Alpaca Paper bracket submission |
| Both true, option approval true, Level/OPRA/quote checks pass | Guarded Alpaca Paper option or MLeg submission |
| Emergency stop true | No new opening order; eligible reduce-only option protection may continue |

The emergency stop blocks new exposure but does not cancel broker orders.
When `TRADING_ENABLE_PAPER_ORDERS=true`, application-managed reduce-only option
exits may still submit so an entry stop does not disable position protection.
Set `TRADING_ENABLE_PAPER_ORDERS=false` to block every application submission;
existing broker orders and positions must then be managed directly at Alpaca.

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
python -m multitrade asset-universe --once
python -m multitrade strategy-lab --once
python -m multitrade accelerated-validation --workers 2
python -m multitrade evidence-catalog
python -m multitrade research-backtest --symbol QQQ --cost-bps 10
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
5. Recreates the heartbeat, automation, research, Asset Universe, Strategy
   Lab, dashboard, and proxy services.

After updating:

```bash
cd /opt/multitrade/app
docker compose --profile public-dashboard ps
docker compose --profile public-dashboard logs --tail=100 automation
docker compose --profile public-dashboard logs --tail=100 engine
docker compose --profile public-dashboard logs --tail=100 research
docker compose --profile public-dashboard logs --tail=100 asset-universe
docker compose --profile public-dashboard logs --tail=100 strategy-lab
docker compose --profile public-dashboard logs --tail=100 option-evidence
```

The controlled testing sequence is documented in
[`docs/PAPER_VALIDATION_RUNBOOK.md`](docs/PAPER_VALIDATION_RUNBOOK.md).
Strategy definitions and limitations are documented in
[`docs/STRATEGY_CATALOG.md`](docs/STRATEGY_CATALOG.md).
The closed-bar mathematical definitions, causal pivot rules, structural
invalidation, and validation boundary for chart patterns are documented in
[`docs/CHART_PATTERN_MATHEMATICS.md`](docs/CHART_PATTERN_MATHEMATICS.md).
Evidence admission and the public-thesis boundary are documented in
[`docs/RESEARCH_GOVERNANCE.md`](docs/RESEARCH_GOVERNANCE.md).
Asset selection, SEC company-size evidence, index snapshots, and manual versus
recommended strategy assignments are documented in
[`docs/ASSET_UNIVERSE.md`](docs/ASSET_UNIVERSE.md).
The frozen experiment families and prospective evidence boundary are
documented in
[`docs/STRATEGY_EXPERIMENTS.md`](docs/STRATEGY_EXPERIMENTS.md).
Trial fingerprints, hash chains, and their security limitations are documented
in [`docs/MODEL_TRIAL_REGISTRY.md`](docs/MODEL_TRIAL_REGISTRY.md).
Defined-risk option selection, lifecycle, theta accounting, and Paper gates are
documented in
[`docs/OPTIONS_PAPER_OPERATIONS.md`](docs/OPTIONS_PAPER_OPERATIONS.md).
Multi-account credentials, identity pinning, configuration, and failure
semantics are documented in
[`docs/MULTI_ACCOUNT_OPERATIONS.md`](docs/MULTI_ACCOUNT_OPERATIONS.md).
Accelerated validation operation and score semantics are documented in
[`docs/ACCELERATED_VALIDATION.md`](docs/ACCELERATED_VALIDATION.md).
The remaining validation expansion and read-only HTTPS Analyst API/Connector
are recorded in
[`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md).

## Current boundary

This release supports multiple Alpaca Paper accounts in one supervised VPS
instance. Account cycles are isolated and sequential inside each worker, and
all database writes remain local to the one Compose deployment. Horizontal
multi-host execution, PostgreSQL portfolio aggregation, cross-account net
exposure controls, dedicated crypto/forex broker adapters, role-based
administration/MFA, and any live-trading program remain separate future
releases.
