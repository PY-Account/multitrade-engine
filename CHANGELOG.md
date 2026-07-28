# Changelog

## 0.9.0 - 2026-07-28

- Added an append-only model-trial registry for every new Strategy Lab
  evaluation.
- Added independent SHA-256 fingerprints for the evaluated strategy
  implementation/parameters, laboratory and allocation configuration, and
  exact normalized market-bar dataset.
- Made Strategy Lab report and trial insertion atomic and blocked ordinary SQL
  updates or deletes against registered trials and their associated reports.
- Added per-account/per-strategy hash chains covering trial identity, outcomes,
  gates, warnings, lineage, and the structurally false execution flag.
- Added read-time self-hash and chain-link verification plus a dedicated
  dashboard Trial Registry view.
- Documented that this local ledger is tamper-evident rather than externally
  anchored, that pre-0.9 trials are not assigned invented provenance, and that
  PBO/Deflated-Sharpe calculations still require enough distinct candidates
  and an untouched final holdout.

This release remains Alpaca Paper-only. Trial registration preserves research
evidence but does not promote a model or grant execution permission.

## 0.8.0 - 2026-07-28

- Added fixed-parameter Strategy Lab evaluation across three configurable,
  non-overlapping chronological test windows per strategy and symbol.
- Persisted every fold's period, trade count, return, drawdown, profit factor,
  R-multiples, gates, and status rather than retaining only one favorable
  aggregate result.
- Added deterministic trade-sequence bootstrap stress over chronological
  out-of-sample R-multiples, including adverse return, tail drawdown, and
  drawdown-limit probability gates.
- Added fail-closed aggregate admission gates for chronological coverage,
  sample size, profitable-window breadth, median return, drawdown, and
  trade-sequence stress.
- Added a dedicated dashboard Robustness tab with fold-level and
  trade-sequence diagnostics.
- Added the Deflated Sharpe Ratio to the governance evidence registry while
  explicitly documenting that the formula, PBO, and full multiple-testing
  control require an immutable candidate-trial registry and are not yet
  implemented.

This release remains Alpaca Paper-only. Robustness results are diagnostics,
not forecasts, profitability claims, allocation changes, or order approval.

## 0.7.1 - 2026-07-28

- Added the deployed application version and short Git revision to the
  authenticated dashboard header.
- Added the full version and immutable build commit to `/api/overview`.
- Embedded the checked-out Git revision in the application image during the
  controlled deployment and added the corresponding OCI image label.
- Invalid or locally unavailable revision metadata fails safely to `unknown`
  instead of presenting an unverified build identifier.

## 0.7.0 - 2026-07-28

- Added a dedicated, research-only Asset Universe worker using configured
  seeds plus Alpaca's most-active stock screener.
- Added fail-closed gates for active/tradable US equity status, exchange,
  minimum price, fresh company-size evidence, average daily share volume,
  average daily dollar volume, and optional dated index membership.
- Added optional SEC company-facts integration with an explicitly configured
  organization/contact User-Agent; stored reports include the size method,
  evidence date, and source URL.
- Added dated operator-provided company-size references and index constituent
  snapshots, with freshness gates. NASDAQ listing is never treated as
  Nasdaq-100 membership.
- Added manual, recommended, combined, and account-watchlist research
  assignments per strategy. The Strategy Lab now tests the assigned universe
  instead of forcing every strategy onto the same account watchlist.
- Added separate per-strategy execution-symbol subsets, each structurally
  constrained to the reviewed account watchlist.
- Persisted universe reports and health, added CLI and hardened Compose
  services, and added the Asset Universe dashboard workspace for
  recommendations, rejected candidates, policies, provenance, and
  assignments.
- Expanded the default candidate seeds beyond indexes and the largest
  technology companies while retaining evidence gates before recommendation.

This release remains Alpaca Paper-only. Asset recommendations never edit the
account watchlist, grant Paper permission, or authorize an order.

## 0.6.0 - 2026-07-28

- Added an isolated, always-on Strategy Lab for every configured intraday
  model and every symbol in the account watchlist.
- Added cross-symbol out-of-sample aggregation, minimum trade and breadth
  gates, pooled profit factor, worst drawdown, and profitable-symbol coverage.
- Added a second validation pass under adverse trading costs; no lab result
  can edit strategy permissions or authorize execution.
- Restricted intraday backtests to the regular New York session, blocked
  next-session entries, and forced open simulated positions out at session
  end.
- Bounded backtest feature history to keep long Strategy Lab cycles linear
  enough for the VPS while preserving all strategy lookbacks.
- Added persistent Strategy Lab reports, health monitoring, CLI commands, and
  a dedicated hardened Compose service.
- Reorganized the dashboard into Account, Strategy Lab, Allocation & Risk,
  and Operations workspaces with horizontal secondary tabs and an
  account-selection boundary.
- Added an account allocation/readiness view and a continuous model-validation
  scorecard.
- Added published backtest-overfitting evidence and explicit disclosure that
  the full probability-of-backtest-overfitting method is not yet implemented.

This release remains Alpaca Paper-only. Strategy Lab readiness is evidence for
continued observation, not evidence of profitability and not order approval.

## 0.5.0 - 2026-07-28

- Added next-open research-model backtesting so a daily decision cannot earn
  the closing price that created it.
- Added a fully invested SPY benchmark, after-cost and excess returns,
  annualized return/volatility, Sharpe, Sortino, information ratio, drawdown,
  exposure, turnover, and estimated-cost reporting.
- Added validation gates and a promotion scorecard that can recommend extended
  Paper observation but can never enable execution.
- Added rolling pairwise correlation, high-correlation clusters, and effective
  breadth to identify a universe that behaves like one concentrated bet.
- Added fully adjusted daily research bars for splits, dividends, and
  spin-offs while retaining raw intraday execution bars.
- Persisted validation and portfolio-risk reports and exposed both in the
  authenticated dashboard.
- Added a manual `research-backtest` command and automatic hourly validation
  for the configured research and account universe.
- Expanded the research default history to 1,500 calendar days and require at
  least 252 scored observations for a validation gate.

This release remains observation/Paper-only. Validation status never changes
order permissions.

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
