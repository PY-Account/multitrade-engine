# Changelog

## 0.26.0 - 2026-08-02

- Added a causal Signal Inversion Laboratory for breakout/retest, trend
  pullback, chart-pattern, and T3 candidates. Mirrored candidates have separate
  identities, symmetric stops/targets, frozen manifests, and no execution
  authority.
- Added Put Income V2.1 with correctly scaled cumulative slow-average return,
  replacing the over-strict adjacent-SMA slope used by V2.
- Enabled V2.1 observation and disabled V2 observation; all Paper submission
  gates remain blocked.
- Added research universes and immutable baseline experiments for every new
  profitability-discovery candidate.
- Bounded every dashboard table to an internal viewport with sticky headers,
  contained overscroll, stable scrollbars, and a smaller mobile height. Long
  evidence sets no longer create an unbounded page.

## 0.25.1 - 2026-08-02

- Fixed the put-income V2 base evaluator dispatch for the server's CPython
  runtime. Frozen slotted dataclass inheritance could raise
  `super(type, obj)` before accelerated validation began.
- Retained the separate V2 identity, frozen parameters, risk constraints, and
  execution blocks unchanged.

## 0.25.0 - 2026-08-02

- Added separately identified and preregistered
  `support_delta_put_income_v2` instead of mutating V1 after observing it.
- V2 requires an uptrend, positive slow-average slope, bounded ATR percentage,
  a maximum 0.22 short-put absolute delta, and at least 18% credit-to-risk.
- Disabled V1 account observation and enabled V2 observation while keeping
  Paper-order submission blocked.
- Added three V2 experiments and a bounded optimization space; every candidate
  still requires a new holdout.
- Added option-package profitability evidence: premium, capital at risk,
  slippage-adjusted P/L, return on risk, premium capture, and exit outcome.

## 0.24.0 - 2026-08-02

- Added the research-only `support_delta_put_income` underlying signal: price
  must reject a lower Bollinger band or recent support, close bullishly, and
  avoid a detected downtrend.
- Added a defined-risk Bull Put Spread allocation with 30-60 DTE, a maximum
  short-put absolute delta of 0.22, a five-dollar maximum width, and a minimum
  15% credit-to-maximum-loss ratio.
- Added fail-closed option-policy enforcement for maximum short delta and
  minimum credit-to-risk, while retaining liquidity, quote freshness, positive
  theta, account-level 3% trade risk, and 10% aggregate-risk controls.
- Registered baseline, strict-support, and broad-support immutable experiments
  for accelerated historical screening and later prospective Paper evidence.
- Exposed the new option constraints in the account allocation dashboard.
- Paper order submission remains disabled for the new strategy.

## 0.23.1 - 2026-08-02

- Replaced the browser-native HTTP Basic prompt with a first-party login page
  so the Codex in-app browser can authenticate normally.
- Added opaque server-side sessions with a secure `__Host-` cookie, HttpOnly,
  SameSite Strict, an eight-hour maximum lifetime, and immediate revocation on
  logout or dashboard restart.
- Added login CSRF validation, bounded form parsing, existing authentication
  throttling/temporary lockout, a CSRF-protected logout control, and a strict
  self-only form CSP.
- Retained HTTP Basic support for existing API clients while unauthenticated
  browser navigation now redirects to the login page.

## 0.23.0 - 2026-08-02

- Added an opt-in, HTTPS-compatible Analyst API with separate Bearer-token
  authentication and snapshot, validation, strategy, trade, and health routes.
- Added recursive response redaction for credential, secret, password, token,
  API-key, request-ID, and broker-order-ID fields.
- Added per-client request limiting, no-store security headers, successful-read
  audit events, and fail-closed behavior when the audit database is unavailable.
- Analyst routes support GET only and contain no configuration, broker, order,
  strategy activation, or execution mutation handlers.
- The gateway remains disabled by default and requires a unique token of at
  least 32 characters that differs from the dashboard password.

## 0.22.0 - 2026-08-02

- Fixed the runtime package and dashboard HTTP server versions so the deployed
  release badge reports 0.22.0 rather than the previous hard-coded value.
- Added deterministic, closed-bar mathematical detectors for engulfing
  candles, bullish pin bars, dragonfly/gravestone doji, bear/bull traps, bull
  flags, FVG retests, golden/death crosses, ABCD equality, and confirmed
  head-and-shoulders structures.
- Added explicit causal pivot confirmation so no current or future bar can
  retroactively create a decision-time pattern.
- Added the research-only `chart_pattern_confluence` strategy. A visual shape
  cannot trade alone: aggregate evidence must agree with regime and relative
  volume, while the measured structure defines invalidation and ATR-buffered
  risk.
- Added baseline, strict, and broad immutable experiments, all disabled and
  execution-ineligible, plus liquid-universe coverage.
- Added a bounded nested-optimization grid for geometric, confirmation, stop,
  and reward thresholds. Optimization remains unable to mutate configuration
  or authorize execution.
- Added a mathematical specification documenting formulas, causal boundaries,
  evidence weights, and the mandatory holdout/prospective validation path.

## 0.21.0 - 2026-08-02

- Added `t3_range_trend`, a transparent US-equity research adaptation of the
  Gold strategy concept presented in YouTube video `BPFwaD0CgZ8`.
- Implemented explicit Tillson T3 and adaptive Range Filter calculations,
  dual-filter transition entry, ATR stop, and configurable reward multiple.
- Added baseline, fast-filter, and slow-filter immutable experiments, all
  research-only and execution-ineligible.
- Added the strategy to the bounded nested parameter optimizer with explicit
  T3, range, stop, and reward search spaces.
- Added disabled account configuration and liquid-universe assignment covering
  US equities plus GLD/SLV research proxies. No Gold/Forex equivalence is
  claimed.
- Added a source-evidence document separating transcript-stated rules and
  publisher performance claims from the undisclosed settings and MultiTrade's
  independently testable adaptation.

## 0.20.0 - 2026-08-02

- Added opt-in bounded parameter optimization to accelerated validation via
  `--optimize --max-candidates N`.
- Added explicit, code-defined grids for the four equity strategy families;
  candidate generation is deterministic, capped, auditable, and does not
  mutate the frozen production configuration.
- Added nested chronological evaluation: generated candidates are selected
  using only the earlier 70% development segment, then the single winner for
  each strategy is evaluated once against the later untouched 30% holdout.
- Development ranking uses the complete Strategy Lab gate set, including
  adverse costs, cross-symbol breadth, chronological stability, drawdown, and
  trade-sequence stress. Finding a profitable development result does not end
  the search or count as validation.
- Added a Nested Parameter Optimization dashboard table with parameters,
  development and holdout returns, profit factor, stressed results, gate
  counts, and explicit rejection/pass status.
- Optimization output is research-only. It cannot edit account allocations,
  replace frozen experiments, enable a strategy, or submit Paper/live orders.

## 0.19.0 - 2026-08-02

- Added an evidence-to-research decision engine to every accelerated
  candidate scorecard. It distinguishes missing evidence, negative gross
  expectancy, cost-erased edge, weak profit factor, robustness work, and
  candidates ready for untouched confirmation.
- Added deterministic within-family ranking and a research shortlist. Ranking
  prioritizes investigation only and cannot authorize Paper or live orders.
- Added supported-loss-segment detection across regime, New York entry hour,
  and symbol. A segment requires at least five trades and at least ten percent
  of the candidate sample, preventing one-trade anecdotes from becoming V2
  filters.
- Added a dashboard Research Decision Queue showing the recommended action,
  rationale, preregistered V2 hypothesis, supporting segment, and mandatory
  next evidence.
- Every recommendation marks the inspected dataset as development evidence,
  requires a new untouched chronological holdout and adverse-cost retest, and
  explicitly disables automatic parameter changes and execution eligibility.

## 0.18.0 - 2026-07-30

- Added decision-time diagnostic attribution to every accelerated candidate:
  gross P/L before modeled costs, modeled transaction costs, net P/L, cost
  drag, win rate, profit factor, average R, holding time, MFE, and MAE.
- Added additive breakdowns by symbol, market regime, New York entry hour,
  exit reason, and complete signal-reason set, plus strongest/weakest buckets
  and a machine-readable primary diagnosis.
- Extended backtest trades with regime, entry hour, reason-code, cost, and
  conservative excursion evidence without changing order simulation, sizing,
  risk, or net-P/L arithmetic.
- Added a Trade Attribution Diagnostics table under Strategy Lab ->
  Accelerated Validation. Historical runs remain readable and explicitly ask
  for a rerun when attribution evidence is unavailable.
- Preserved conservative intrabar ambiguity: if stop and target touch in one
  bar, stop-first handling does not claim the favorable excursion also
  occurred.

This release explains candidate failure but does not tune parameters, create
Strategy v2 candidates, increment prospective evidence, or authorize
execution. New candidates should be designed only after reviewing a fresh
diagnostic run.

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
