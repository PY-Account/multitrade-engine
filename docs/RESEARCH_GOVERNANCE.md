# Research Governance

MultiTrade treats “proven” as a high evidence threshold, not a promise of
future profit. A strategy can be historically supported and still fail after
costs, during a regime change, or in this account's instruments and data feed.

## Admission pipeline

No strategy found online is automatically turned into an order rule.

1. **Source:** Prefer a peer-reviewed paper, working paper from the authors or
   research institution, official dataset, or official product documentation.
2. **Mechanism:** State why the effect might persist and who bears the other
   side of the trade. Reject a pattern whose only rationale is its backtest.
3. **Independent support:** Look for another sample, market, period, or
   independent study. Record contradictory evidence as prominently as
   favorable evidence.
4. **Data compatibility:** Confirm that decision-time data are actually
   available without survivorship, revision, or look-ahead bias. IEX volume is
   not consolidated US volume.
5. **Executable simulation:** Model spreads, slippage, commissions, turnover,
   borrow, corporate actions, gaps, partial fills, latency, and capacity.
6. **Chronological validation:** Freeze parameters, preserve a final untouched
   holdout, and use multiple-testing controls. Do not repeatedly tune against
   the same “out-of-sample” period.
7. **Stress:** Replay crashes, rebounds, volatility spikes, low liquidity, and
   feed/broker failures. Inspect portfolio correlation and clustered stops.
8. **Paper observation:** Run for weeks across enough independent decisions.
   Compare expected versus observed signals, fills, slippage, and drawdown.
9. **Approval:** Promotion is a reviewed configuration change. A passing test
   never edits execution permissions.

## Current evidence interpretation

- **Momentum/trend:** Broad and long-lived evidence exists, but the payoff is
  not stable, some results depend on volatility scaling, and momentum can
  crash in volatile rebounds. It is admitted only as a research component.
- **Volatility management:** Used only to reduce risk. It is not counted as
  alpha and is capped at 1.0 so low volatility never creates leverage.
- **Intraday breakout/retest and related patterns:** Deterministic internal
  hypotheses. They do not inherit the evidence for medium-horizon diversified
  trend following.
- **Value plus momentum:** Supported across several asset classes, but a valid
  stock implementation needs point-in-time fundamentals, a broad tradable
  universe, corporate-action handling, and cross-sectional portfolio
  construction. Alpaca price bars alone are insufficient, so it is not
  implemented as an executable strategy.
- **Options:** Defined-risk construction is implemented, but automatic
  selection and execution remain blocked until OPRA-quality history and full
  lifecycle tests exist.

## Aschenbrenner public-thesis boundary

`Situational Awareness: The Decade Ahead` discusses possible AI capability
progress, very large compute clusters, power/data-center requirements, and
security implications. It is not a trading manual and does not disclose the
author's firm's positions, weights, hedges, risk limits, or execution rules.

`config/research_program.json` therefore defines only a transparent,
MultiTrade-authored price proxy. QQQ, SMH, XLU, and SPY are broad, imperfect
proxies for technology, semiconductor compute, utilities, and the market.
They can rise or fall for reasons unrelated to the thesis. The theme is:

- labeled `research_only`;
- hard-coded against Paper execution;
- stored with the original source and non-attribution notice;
- shown separately in the dashboard; and
- excluded from strategy allocation and broker execution.

## Continuous research limitation

The VPS continuously evaluates approved, versioned models against market data.
It does not autonomously browse the internet, rewrite its own strategy, or
promote new claims. New research must be reviewed, cited, implemented,
backtested, and released through the same code-review process. This boundary
prevents a web article or data-mined result from silently changing account
risk.

The intraday Strategy Lab is intentionally adversarial to model promotion. It
records all configured candidates, holds out the newest 40% of each symbol's
history, requires cross-symbol breadth, and repeats validation with adverse
costs. It now also applies fixed-parameter evaluation across non-overlapping
chronological windows and deterministic trade-sequence stress to the resulting
out-of-sample R-multiples. This implements practical safeguards motivated by
the published backtest-overfitting literature, but it is not a full
implementation of combinatorially symmetric cross-validation, the probability
of backtest overfitting, or the Deflated Sharpe Ratio. See
`ROBUSTNESS_VALIDATION.md` for the exact gates and limitations.

## Daily-model validation rules

The daily research simulator requests `adjustment=all` from Alpaca so splits,
cash dividends, and spin-offs do not masquerade as investment returns. It
stores this provenance separately from raw intraday execution bars.

A decision formed after one daily bar closes is applied only at the following
session's open. The model earns no same-close return. Every exposure change
pays the configured one-way cost, exposure is capped at 100%, and SPY is
tracked as a fully invested benchmark.

The scorecard requires at least 252 scored observations, positive after-cost
and excess returns, bounded drawdown, drawdown no worse than the benchmark, a
minimum Sharpe ratio, and bounded annual turnover. These thresholds are
screening rules—not p-values, not a guarantee, and not execution approval.

Pairwise correlation and effective breadth are reported for the monitored
universe. This exposes clusters but does not claim that historical correlation
is stable or automatically create hedges.
