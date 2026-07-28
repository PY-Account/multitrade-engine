# Strategy Robustness Validation

MultiTrade uses several distinct historical checks because one favorable
train/test split is weak evidence. All outputs in this document are
research-only. They cannot edit account allocation, grant Paper permission, or
authorize an order.

## Chronological stability

For every strategy and assigned symbol, the Strategy Lab:

1. freezes the strategy implementation and parameters;
2. reserves the first half of the available regular-session history as prior
   context;
3. divides the later half into configured non-overlapping test windows;
4. uses only bounded earlier bars to warm up indicators;
5. begins signals and profit/loss measurement at each test boundary; and
6. records every fold, including failed and empty folds.

The default is three chronological folds. Each fold is independently checked
for a minimum trade count, positive net profit, positive expectancy, and a
maximum drawdown no greater than 10%. The aggregate also checks fold coverage,
total trades, the median fold return, the fractions of profitable and
individually passing folds, pooled profit factor, and the worst fold drawdown.

This is a fixed-parameter stability test, not walk-forward optimization. The
lab does not fit parameters inside a fold or select the best fold.

## Trade-sequence stress

The lab pools the R-multiples produced only by the chronological test windows.
A deterministic bootstrap then creates 100-5,000 resampled trade paths using
the strategy's configured risk per trade. The stored report includes:

- the 5th-percentile simulated return;
- median simulated return;
- 95th-percentile maximum drawdown;
- the fraction of paths reaching the configured drawdown limit;
- the sample size, path count, and a non-secret seed fingerprint; and
- every pass/fail gate.

The default is 500 paths. The test fails closed when fewer than 20 observed
test trades exist, the adverse return breaches the drawdown budget, the tail
drawdown breaches the limit, or more than 10% of paths reach the limit.

The bootstrap is reproducible, but it resamples the observed trades as if they
were exchangeable. It does not preserve serial dependence, changing market
regimes, cross-symbol correlation, gaps beyond those already observed, or
future liquidity. Those limitations mean it is a sensitivity diagnostic, not
a forecast.

## Selection-bias boundary

MultiTrade records all configured strategy reports and adds breadth,
transaction-cost, chronological, and trade-ordering checks. These controls are
motivated by published evidence that repeated selection on the same history
can create attractive but fragile backtests.

The current release does **not** claim to implement:

- combinatorially symmetric cross-validation;
- Probability of Backtest Overfitting;
- the Deflated Sharpe Ratio; or
- an automatic multiple-hypothesis correction.

Those methods require a complete, immutable registry of every economically
related candidate and parameter trial. Version 0.9.0 starts that registry for
new Strategy Lab cycles, but it does not retroactively invent trial history.
Version 0.10.0 freezes the initial candidate-family definitions and prospective
observation boundaries, but repeated observations of one baseline are not
distinct candidate variants. MultiTrade must still accumulate enough genuine
variants and preserve an untouched final holdout. Adding the formulas before
those conditions exist would provide false precision. See
`MODEL_TRIAL_REGISTRY.md` and `STRATEGY_EXPERIMENTS.md`.

## Operational interpretation

A passing report may receive only the label
`extended_paper_observation_candidate`. The operator must still approve any
configuration change after weeks of Paper observation and comparison of
expected versus observed signals, fills, costs, and drawdowns.
