# Preregistered Strategy Experiments

MultiTrade freezes the current intraday research questions before prospective
observation. The goal is to make parameter changes and selective reporting
visible, not to claim that a strategy is proven.

The source of truth is `config/strategy_experiments.json`. Release publication
in Git is the code-reviewed registration point. The SQLite copy is append-only
under normal application and SQL access, but it is not externally timestamped
or independently controlled.

## Current families

| Family | Frozen candidates | Economic relationship |
|---|---:|---|
| `intraday_breakout_continuation` | 6 | Directional expansion after supply contraction or absorption |
| `intraday_trend_continuation` | 3 | Continuation after a temporary pullback inside an established trend |
| `intraday_range_reversion` | 3 | Reversion after displacement while the regime remains non-directional |

The twelve candidates consist of four account baselines and eight
research-only sensitivity variants. The family sizes are still too small for
a responsible Probability of Backtest Overfitting or Deflated Sharpe Ratio.

## Sensitivity matrix

| Strategy | Variant | Frozen change from baseline |
|---|---|---|
| Breakout/retest | `selective_v1` | 30-bar resistance, 0.2% retest tolerance, 1.30 volume |
| Breakout/retest | `responsive_v1` | 15-bar resistance, 0.4% retest tolerance, 1.05 volume |
| Trend pullback | `tight_touch_v1` | 0.25% fast-average touch tolerance |
| Trend pullback | `broad_touch_v1` | 0.6% fast-average touch tolerance |
| Volatility contraction | `strict_contraction_v1` | 0.60 contraction ratio, 1.35 relative volume |
| Volatility contraction | `broad_contraction_v1` | 0.80 contraction ratio, 1.10 relative volume |
| Range reversion | `deep_displacement_v1` | 2.5 deviations, 1.5 reward multiple |
| Range reversion | `moderate_displacement_v1` | 1.5 deviations, 1.25 reward multiple |

These are stability probes around fixed mechanisms. They are not the output of
an optimizer and must not be relabeled after results arrive.

## Frozen boundary

The four baseline manifests were registered at `2026-07-28 10:00 UTC`; the
eight comparison manifests declare registration at
`2026-07-28 11:00 UTC`. All twelve declare:

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

1. Each configured strategy must have exactly one primary baseline manifest.
   Every comparison must reference that baseline's family.
2. Runtime strategy ID, version, and all deterministic parameters must match.
3. The manifest fingerprint and evidence phase become part of the trial's
   configuration fingerprint and hash.
4. The manifest and first linked trial are inserted atomically.
5. Ordinary SQL updates and deletes against the manifest are rejected.
6. The dashboard independently recomputes manifest integrity and shows
   prospective trials, distinct days, candidates, and datasets.

Every cycle evaluates all four baselines. Within each strategy, one comparison
variant is selected deterministically from a six-hour rotation and is tested
on the same complete assigned symbol universe. The next slot selects the other
variant. This bounds VPS work while keeping baseline/variant inputs
comparable.

Comparison candidates are never loaded by the automation worker. Even if all
performance gates pass, their readiness is capped at `research_only` with a
required family-review warning.

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
