# Strategy Catalog

These are deterministic research candidates, not promises of profitability.
All are disabled for Paper submission by default and require walk-forward and
multi-week Paper evidence before approval.

## Breakout and retest (`breakout_retest` v1.0.0)

Long-only stock candidate:

- A completed bar closes above the prior 20-bar resistance.
- Breakout volume exceeds the historical average by the configured factor.
- The following completed bar retests the old resistance, closes back above
  it, and has a bullish body.
- Stop is below the retest/ATR structure; target is two initial-risk units.

Primary failure modes: false breakouts, opening gaps, sparse IEX volume, and
rapid regime reversal.

## Trend pullback (`trend_pullback` v1.0.0)

Long-only stock candidate:

- Fast average is above the slow average and price confirms an uptrend.
- Price pulls back to the fast average.
- A completed bullish bar closes back above that average with acceptable
  relative volume.
- Stop uses the recent swing and ATR; target is two initial-risk units.

Primary failure modes: late-stage trends, news gaps, and choppy transitions.

## Volatility-contraction breakout (`volatility_contraction` v1.0.0)

Long-only stock candidate:

- Recent average range contracts relative to the preceding sample.
- A completed bar closes above the contraction range.
- Relative volume confirms the move.
- Stop uses the contraction low/ATR; target is 2.25 initial-risk units.

Primary failure modes: low-liquidity squeezes and expansion immediately
reversing into the base.

## Range mean reversion (`range_mean_reversion` v1.0.0)

Long-only stock candidate:

- Regime classifier reports a range.
- Price rejects a lower statistical band and closes bullishly back above it.
- Target is capped by the recent mean.

This strategy is disabled even for signal generation in the default portfolio
because regime transitions make mean reversion especially sensitive.

## Evidence-weighted daily market model (`evidence_weighted_market_model` v1.0.0)

This is an observation and risk-regime model, not an entry strategy:

- Requires at least 253 closed daily bars.
- Combines 1-month and skip-month 3/6/12-month own-price momentum.
- Checks price and the SPY benchmark against their 200-day averages.
- Includes 12-to-1-month relative strength versus SPY.
- Scales exposure downward toward a 10% annualized-volatility target, capped
  at 100%; it never introduces leverage.
- Returns zero risk when the benchmark trend is negative, liquidity is below
  the configured floor, or panic/volatile-rebound guards fire.
- Writes `risk_on`, `watch`, `risk_off`, or `insufficient_data` decisions with
  all component values and evidence IDs.

It cannot place Paper orders. A later release may use a validated version as
one input to portfolio allocation, but only after tests demonstrate that the
combined system improves results after costs without hiding tail risk.

## Public AI infrastructure thesis proxy (`public_thesis_proxy` v1.0.0)

This is MultiTrade's independent research proxy for public themes discussed in
Leopold Aschenbrenner's 2024 essay: broad technology, compute/semiconductors,
power infrastructure, and the broad market. The configured ETFs are imperfect
price proxies, not a claim about the essay author's portfolio.

The public essay does not disclose fund holdings, entries, exits, sizing, or
risk limits. Accordingly:

- The model must not be described as Aschenbrenner's trading strategy.
- Its `paper_execution_allowed` value is structurally required to be false.
- Its output is excluded from the broker execution pipeline.
- Missing proxy data fails closed to `insufficient_data`.
- Fundamental and alternative data are still needed to validate the thesis
  rather than merely extrapolating ETF price trends.

## Defined-risk option structures

The option layer currently normalizes Alpaca chains, parses OCC symbols,
records indicative/OPRA provenance, enforces a quote-spread policy, and builds:

- Bull-call debit spreads.
- Bear-put debit spreads.

Pricing uses the conservative long ask minus short bid. Risk uses those same
leg prices plus the configured option slippage buffer. Automatic option
selection and submission are deliberately disabled pending:

1. Verified account option approval level.
2. OPRA subscription and quote-quality checks.
3. Option-specific historical data and walk-forward tests.
4. Paper verification of multi-leg fill, replace, exit, expiry, assignment,
   and exercise behavior.
5. Explicit expiry-day and early-assignment controls.

Credit spreads and iron condors are not routed automatically in this release.

## Validation gates

The stored out-of-sample gates currently require:

- At least 20 trades.
- Positive net profit.
- Maximum drawdown no greater than 10%.
- Profit factor at least 1.10.
- Positive expectancy.

Passing does not automatically edit `paper_execution_allowed`. Approval
remains a deliberate configuration change reviewed after Paper observation.
