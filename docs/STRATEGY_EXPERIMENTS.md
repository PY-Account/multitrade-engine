# Preregistered Strategy Experiments

MultiTrade freezes the current intraday research questions before prospective
observation. The goal is to make parameter changes and selective reporting
visible, not to claim that a strategy is proven.

The source of truth is `config/strategy_experiments.json`. Release publication
in Git is the code-reviewed registration point. The SQLite copy is append-only
under normal application and SQL access, but it is not externally timestamped
or independently controlled.

## Current families

| Family | Frozen baseline candidates | Economic relationship |
|---|---|---|
| `intraday_breakout_continuation` | `breakout_retest`, `volatility_contraction` | Directional expansion after supply contraction or absorption |
| `intraday_trend_continuation` | `trend_pullback` | Continuation after a temporary pullback inside an established trend |
| `intraday_range_reversion` | `range_mean_reversion` | Reversion after displacement while the regime remains non-directional |

There are currently four baselines but only three families. One or two
baselines in a family are not enough to estimate the Probability of Backtest
Overfitting or a Deflated Sharpe Ratio.

## Frozen boundary

All four baseline manifests declare:

- registration at `2026-07-28 10:00 UTC`;
- prospective observation beginning `2026-07-29 00:00 UTC`;
- no review before `2026-08-19 00:00 UTC`;
- at least 21 distinct prospective observation days;
- at least 20 prospective Strategy Lab trials;
- `final_holdout_status=not_reserved`; and
- `execution_eligible=false`.

A trial before the observation boundary is preserved as `pre_observation` but
does not count toward prospective evidence. A later trial is labeled
`prospective_observation`; after the earliest review date it is `review_due`.
Meeting time and count minima means only that a human review may begin.

## Enforcement

For every Strategy Lab cycle:

1. The configured strategy must have exactly one active manifest.
2. Runtime strategy ID, version, and all deterministic parameters must match.
3. The manifest fingerprint and evidence phase become part of the trial's
   configuration fingerprint and hash.
4. The manifest and first linked trial are inserted atomically.
5. Ordinary SQL updates and deletes against the manifest are rejected.
6. The dashboard independently recomputes manifest integrity and shows
   prospective trials, distinct days, candidates, and datasets.

A parameter or implementation change must use a reviewed new strategy version,
variant, and experiment ID. It must not overwrite the baseline manifest.

## Statistical and execution boundary

Repeated runs of the same candidate on refreshed data are useful prospective
observations, but they are not independent candidate variants. Dataset counts
and trial counts must not be presented as multiple-testing correction.

No untouched final holdout has been reserved in this release. Consequently,
the system makes no PBO, Deflated-Sharpe, statistical significance,
profitability, or production-readiness claim. Experiment status never edits
portfolio allocation or Paper/live execution permissions.
