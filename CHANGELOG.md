# Changelog

## 0.17.3 - 2026-07-28

- Partitioned stock-bar retrieval by both symbol group and 30-day time window,
  so long Strategy Lab lookbacks cannot exhaust one pagination chain even
  when a 25-symbol batch contains more than 600,000 bars.
- Reset and validate Alpaca page tokens independently for every symbol/time
  partition, then merge and timestamp-deduplicate the normalized results.
- Added regression coverage for a 50-symbol, 120-day retrieval requiring 64
  pages across eight independently bounded partitions.

The operation-wide page ceiling remains enforced. This patch does not change
candidate definitions, validation gates, risk controls, or execution access.

## 0.17.2 - 2026-07-28

- Split large Alpaca stock-bar requests into independent batches of at most
  25 symbols so a large effective strategy universe no longer shares one
  pagination ceiling.
- Retained the 60-page limit per symbol batch and added a separate 240-page
  ceiling for the complete retrieval operation.
- Added regression coverage for a 50-symbol dataset requiring 62 total pages
  across two bounded batches.

This patch changes retrieval partitioning only. Candidate definitions,
validation gates, risk limits, and all execution permissions are unchanged.

## 0.17.1 - 2026-07-28

- Raised the bounded Alpaca stock-bar pagination budget from 20 to 60 pages
  so the accelerated validator can load the configured multi-symbol, 120-day,
  five-minute dataset instead of stopping after 200,000 bars.
- Added an explicit 100-page absolute ceiling and repeated-page-token
  detection, preserving fail-closed protection against unbounded or malformed
  pagination.
- Added regression coverage for a successful 21-page historical request and
  a repeated-token failure.

This patch changes data retrieval capacity only. It does not change strategy
rules, research gates, risk limits, Paper permissions, or execution authority.

## 0.17.0 - 2026-07-28

- Added `multitrade accelerated-validation --workers N`, which downloads an
  account's assigned market universe once and evaluates every frozen baseline
  and registered comparison variant in one bounded historical cycle.
- Added deterministic 0-100 research scorecards covering evidence, net
  returns, breadth/stability, drawdown, and trade-sequence stress, with an
  evidence-failure cap, classifications, failed gates, and explanatory
  diagnostics.
- Added a separate accelerated-run audit table and completion event with
  account, candidate, dataset, request, duration, and classification
  provenance.
- Added Strategy Lab -> Accelerated Validation to the dashboard with
  account-specific scorecards and explicit historical-versus-prospective and
  execution-blocked labels.
- Kept accelerated runs isolated from ordinary Strategy Lab reports,
  append-only model trials, prospective evidence counts, and continuous-worker
  health, so repeated historical screens cannot masquerade as new market
  observations.
- Added regression coverage proving all comparison candidates can be screened
  together while no trial or execution authority is created.

This release remains Alpaca Paper-only. Accelerated scores prioritize research
review; they cannot promote a model, change its parameters, grant Paper
permission, or authorize an order.

## 0.16.0 - 2026-07-28

- Added an authenticated Management workspace for enabling/disabling known
  strategies, assigning exact execution symbols, and granting or revoking
  per-strategy Alpaca Paper-order permission.
- Added CSRF protection, strict JSON and symbol validation, optimistic
  revisions, and an atomic `strategy_configuration_changed` audit event for
  every configuration update.
- Made the automation worker load effective strategy overrides before each
  cycle and made Strategy Lab include newly assigned execution symbols in its
  next research cycle without a container restart.
- Kept the control boundary Paper-only: the dashboard cannot select a live
  endpoint, change server-wide submission gates or risk budgets, or bypass any
  broker, lifecycle, data-quality, or risk check.
- Replaced the opaque Strategy Lab `Attention` indicator with a specific
  health explanation for missing, invalid, stale, failed, or successful
  worker reports.
- Added a bilingual English/Hebrew glossary and terminology tooltips,
  including explicit definitions for experiment registration, prospective
  trials, out-of-sample evidence, option path metrics, and risk terms.

This release is still Alpaca Paper-only. The global automation and Paper-order
gates remain server-managed independent controls.

## 0.13.0 - 2026-07-28

- Replaced the single-account runtime restriction with an isolated
  multi-account Alpaca Paper supervisor for reconciliation, strategy
  automation, daily research, Strategy Lab, and Asset Universe cycles.
- Added per-account credential namespaces referenced by configuration while
  keeping every secret in the VPS environment file.
- Required unique credential namespaces and explicit expected Alpaca account
  IDs whenever multiple accounts are enabled, preventing swapped credentials
  from trading the wrong Paper account.
- Added broker account identity normalization and fail-closed verification
  before any account state, risk decision, or order cycle is processed.
- Added per-account failure isolation with aggregate `ok`, `degraded`, or
  `error` health and retained structured account failure details.
- Separated opening-order permission from reduce-only option protection:
  the emergency entry stop can no longer silently disable managed closes
  while the global Paper-order transport remains enabled.
- Added complete account views to the dashboard API and made the account
  selector switch broker state, positions, orders, buying power, and risk
  capacity instead of configuration alone.
- Updated Strategy Lab to evaluate unique source models once per account even
  when stock and option allocations share the same signal model.
- Added multi-account configuration, credential, identity, supervisor,
  dashboard-isolation, and health-detail regression tests.

This release is still Alpaca Paper-only. The tracked portfolio contains one
account, so existing deployment secrets remain compatible and no new account
is enabled automatically.

## 0.12.0 - 2026-07-28

- Made risk reservations explicitly account-scoped and persisted the asset
  class plus all option contract symbols used by a package.
- Added account/strategy execution statistics with honest missing-sample
  handling: signals, decisions, lifecycle states, wins/losses, realized P/L,
  realized R, profit factor, realized drawdown, option P/L, and per-structure
  results.
- Added decision-time delta, gamma, theta, vega, and implied-volatility
  provenance without presenting modeled theta as realized profit.
- Added defined-risk bull-put/bear-call credit spreads, iron condors, and
  protective puts alongside the existing debit spreads.
- Added deterministic DTE/delta contract selection, liquidity and width
  gates, positive-theta enforcement, Alpaca option-level checks, OPRA-only
  submission policy, and a fresh-quote ceiling.
- Added guarded Alpaca Paper option/MLeg submission from account allocations,
  with signed debit/credit net prices and explicit open position intents.
- Added reduce-only, atomic MLeg exits for profit, loss, and pre-expiration
  policies; reconciled exit fills link to their parent trade and release the
  original risk reservation.
- Added positive-theta-trade P/L as a whole-package statistic, clearly
  separated from any unsupported pure-theta attribution claim.
- Added four option allocations to the default account: three enabled for
  observation and one disabled protective-hedge template. Every Paper
  permission remains false.
- Added an account Strategy Performance dashboard tab, richer option
  allocation/lifecycle display, broker options-level status, migration tests,
  and an options Paper operations runbook.

This release remains Alpaca Paper-only. Option exits are application-managed,
so a service or data outage can delay them. All option Paper permissions remain
off in the tracked configuration.

## 0.11.0 - 2026-07-28

- Expanded the frozen experiment program from four baselines to twelve
  candidates across the three predeclared economic families.
- Added two bounded parameter-sensitivity variants for each intraday strategy
  without adding any variant to the signal or broker-execution registry.
- Added safe construction and exact runtime verification of configured equity
  candidates; missing, extra, duplicate, or mistyped parameters fail closed.
- Added deterministic six-hour rotation of one comparison variant per
  strategy, using the same full assigned research universe as its baseline.
  This bounds VPS work while retaining dataset comparability.
- Structurally capped comparison-variant readiness at `research_only`, even
  when every performance gate passes.
- Extended immutable experiment summaries with latest symbol coverage,
  out-of-sample trades, primary metric, stressed return, drawdown, and
  readiness.
- Added a dedicated Strategy Lab `Family Comparison` tab and exposed variant
  identity in the model-trial registry while keeping baseline allocation and
  robustness views separate.
- Extended the deployment doctor to validate all twelve candidate
  constructors, parameter freezes, family relationships, and runtime
  bindings.

This release remains Alpaca Paper-only. The sensitivity matrix tests whether
results survive nearby predeclared parameters; it does not optimize a model,
reserve a final holdout, or prove an edge.

## 0.10.0 - 2026-07-28

- Added frozen, versioned experiment manifests for all four configured
  intraday strategy candidates.
- Preregistered economic hypotheses, mechanisms, exact parameters, candidate
  families, primary metrics, prospective observation boundaries, earliest
  review dates, and minimum evidence counts.
- Added fail-closed runtime binding: a missing manifest, changed strategy
  version, changed parameter, retired experiment, or invalid timestamp stops
  Strategy Lab evidence registration.
- Linked the complete experiment binding to each trial configuration
  fingerprint and hash-chained evidence row.
- Added immutable SQLite manifest storage, fingerprint verification, and
  prospective trial/day, dataset, and family-candidate summaries.
- Added a read-only Strategy Lab dashboard table that distinguishes
  pre-observation evidence, prospective collection, minimum evidence
  completion, and review eligibility.
- Explicitly recorded that no final untouched holdout is currently reserved,
  so observation counts and passing gates cannot authorize execution or
  support a profitability, PBO, or Deflated-Sharpe claim.

This release remains Alpaca Paper-only. Experiment registration organizes
evidence; it does not create an edge, approve a strategy, or place an order.

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
