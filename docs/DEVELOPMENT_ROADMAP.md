# Future Development Roadmap

This roadmap records planned work only. It does not enable a service, change
the current Paper configuration, or authorize live trading.

## 1. Accelerated strategy-validation pipeline

### Objective

Reduce the calendar time required to reject weak strategies and identify the
small set worth prospective Paper observation, without presenting repeated
tests on unchanged data as new evidence.

### Planned capabilities

- Cache normalized historical data once and reuse the exact immutable dataset
  across candidates.
- Run all frozen strategies, assigned symbols, chronological windows, and
  market-regime segments in parallel within explicit VPS resource limits.
- Expand walk-forward and out-of-sample evaluation across bullish, bearish,
  sideways, high-volatility, and low-liquidity periods.
- Apply base and adverse assumptions for slippage, spreads, fees, delayed
  entries, and incomplete fills.
- Run deterministic bootstrap and Monte Carlo trade-sequence stress.
- Produce comparable per-strategy scorecards covering sample size, return,
  drawdown, profit factor, stability, symbol breadth, regime dependence,
  turnover, and cost sensitivity.
- Add fail-closed elimination gates and a ranked research shortlist. Ranking
  may prioritize further research but cannot grant execution permission.
- Schedule reproducible diagnostic reports and preserve candidate,
  configuration, dataset, and result fingerprints.
- Add option-specific historical quote evidence when a suitable licensed OPRA
  data source is available. Trade-bar proxies must remain separately labeled
  and cannot prove realized theta.

### Evidence boundary

Parallel historical testing can compress computation from weeks to hours or
days. It cannot compress independent future market days. Re-running an
unchanged strategy on unchanged data does not create another prospective
trial. Final Paper conclusions still require new observations across enough
days, trades, and market regimes.

### Acceptance criteria

- One command or scheduled job evaluates every registered candidate against
  the same frozen dataset manifest.
- No lookahead, overlapping-test leakage, silent survivorship assumption, or
  automatic parameter promotion is permitted.
- Reports clearly separate historical screening, prospective evidence, Paper
  execution evidence, and unavailable metrics.
- A failed candidate includes machine-readable failed gates and an explanatory
  diagnostic; proposed variants become new preregistered candidates instead
  of silently modifying the original.

## 2. Read-only HTTPS Analyst API and Codex connector

### Objective

Allow an authorized analyst or Codex session to inspect the VPS runtime and
produce conclusions without SSH, broker credentials, dashboard-administrator
credentials, or any ability to trade or change configuration.

### Planned HTTPS API

Versioned, read-only resources are expected to include:

- component health and deployed release;
- effective Paper strategy configuration;
- sanitized account and risk summaries;
- recent signals and their block reasons;
- Paper trade decisions and lifecycle outcomes;
- Strategy Lab reports, experiment summaries, and trial integrity;
- option evidence and explicit data limitations;
- a bounded, sanitized diagnostic bundle.

The API must not expose environment variables, Alpaca keys, dashboard
passwords, full broker account identifiers, unrestricted logs, arbitrary SQL,
filesystem access, or mutation endpoints.

### Planned access channel

- Provide a dedicated MCP/Connector with a small allowlist of read-only tools,
  rather than giving a model a general-purpose HTTP or server shell.
- Store credentials in the connector's secret store, never in source code,
  URLs, browser JavaScript, GitHub, or chat messages.
- Use a separate revocable, expiring analyst credential with the narrowest
  possible scope.
- Keep a manually generated, short-lived signed diagnostic export as a
  fallback when the connector is unavailable.

### Security requirements

- HTTPS only, with no direct exposure of the dashboard container.
- Authentication independent of the dashboard operator password.
- Rate limits, response-size limits, strict schemas, and request timeouts.
- Audit every analyst access with credential identity, operation, timestamp,
  and outcome, without logging the credential.
- Redact secrets and sensitive account identifiers before serialization.
- Support immediate credential revocation and rotation.
- No order, cancellation, configuration, risk-limit, deployment, shell, SQL,
  or file-write capability in the analyst interface.
- Complete authorization, redaction, CSRF/CORS boundary, rate-limit, and
  penetration-oriented regression tests before public deployment.

### Acceptance criteria

- An authorized connector can retrieve all required diagnostic evidence over
  HTTPS and cannot invoke any state-changing operation.
- An expired, revoked, missing, or wrongly scoped credential fails closed.
- Responses contain no configured secret values or full broker identity.
- Every successful and rejected access appears in the audit record.
- Disabling the analyst credential immediately removes external access without
  interrupting the trading workers.

## Proposed delivery order

1. Freeze data contracts and threat model.
2. Implement historical dataset cache and parallel validation runner.
3. Add scorecards, elimination diagnostics, and scheduled reports.
4. Implement the sanitized Analyst API behind the existing HTTPS proxy.
5. Implement and authorize the read-only MCP/Connector.
6. Complete security review and Paper-only operational validation.

