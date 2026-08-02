# Mathematical chart-pattern research

This module translates visual pattern descriptions into deterministic rules.
It is inspired by the TradingKit pattern catalogue, but the thresholds and
algorithmic definitions below are MultiTrade research hypotheses, not rules
published or performance-validated by TradingKit.

## Notation and causal boundary

For closed bar `t`, let `O_t, H_t, L_t, C_t, V_t` be open, high, low, close,
and volume. Define:

- `R_t = H_t - L_t` (range)
- `B_t = |C_t - O_t|` (body)
- `U_t = H_t - max(O_t, C_t)` (upper wick)
- `D_t = min(O_t, C_t) - L_t` (lower wick)
- `mean(x)` as the arithmetic mean
- `slope(x)` as the ordinary-least-squares slope over equally spaced bars

Every pivot at bar `i` requires two already-closed bars on both sides. The
current signal bar is never used to confirm a prior pivot. This avoids
look-ahead bias.

## Executable definitions

### Engulfing candle

Bullish:

`C_(t-1) < O_(t-1), C_t > O_t, O_t <= C_(t-1), C_t >= O_(t-1)`.

Bearish reverses every inequality. Invalidation is the signal-bar low for a
bullish match and high for a bearish match.

### Bullish pin bar, dragonfly doji, and gravestone doji

Bullish pin bar:

`D_t >= 2 * max(B_t, 0.02 R_t)`,
`U_t <= max(B_t, 0.02 R_t)`, and
`C_t >= L_t + 0.65 R_t`.

Dragonfly doji:

`B_t/R_t <= 0.10`, `U_t/R_t <= 0.10`, and `D_t/R_t >= 0.60`.

Gravestone doji is the symmetric bearish definition.

### Bear trap and bull trap

For prior support `S = min(L_(t-n), ..., L_(t-1))`, a bear trap requires:

`L_t < S(1-tolerance)`, `C_t > S`, and `C_t > O_t`.

The bull-trap rule is symmetric around prior resistance. The breached extreme
is the invalidation point.

### Bull flag

The flagpole must return at least `m`:

`pole_return = C_p/C_0 - 1 >= m`.

For the subsequent flag closes, normalized OLS slope must be in
`[-0.01, 0.001]`. Retracement is:

`(pole_high - flag_low)/(pole_high - pole_start) <= r`.

Entry evidence requires a bullish close above the flag high and
`V_t >= volume_multiplier * mean(V_flag)`. The flag low is invalidation.

### Fair-value-gap retest

A bullish three-bar imbalance exists when `L_i > H_(i-2)`. Its zone is
`[H_(i-2), L_i]`. A later bullish bar must touch the zone and close above its
midpoint. The bearish definition is symmetric. Only gaps completed before the
signal bar are searched.

### Golden/death cross

For fast and slow simple moving averages, a golden cross requires
`fast_(t-1) <= slow_(t-1)` and `fast_t > slow_t`. A death cross reverses these
inequalities. It is deliberately low-weight evidence because a cross alone is
lagging and does not authorize a trade.

### ABCD

Four confirmed alternating pivots `A,B,C,D` must have equally directed legs
within tolerance:

`abs(|CD|/|AB| - 1) <= 0.15`.

This evidence has low weight and cannot pass the baseline strategy threshold
alone; it must be confirmed by another structure plus regime and volume.

### Head and shoulders

For confirmed pivots `P1..P5 = high,low,high,low,high`:

`P3 > max(P1,P5)` and `|P5-P1|/|P1| <= 0.15`.

The neckline is `(P2+P4)/2`; confirmation requires `C_t < neckline`. The head
is invalidation. Inverse head and shoulders reverses the extrema and breakout.

## Strategy integration

`chart_pattern_confluence` aggregates same-direction match scores, but emits
no signal unless all of these are true:

1. aggregate pattern score reaches the frozen experiment threshold;
2. bullish evidence agrees with an uptrend, or bearish evidence with a
   downtrend;
3. relative volume reaches the configured threshold;
4. directional evidence is stronger than conflicting evidence;
5. a valid structure-based stop and positive target exist.

The stop uses the structural invalidation plus an ATR buffer and the target is
a fixed multiple of initial risk. Every match, formula measurement, score,
regime, and volume value is stored in signal evidence.

The strategy is disabled and Paper execution is denied by default. Three
immutable variants enter accelerated validation: baseline, strict, and broad.
The nested optimizer may explore only the explicit bounded grid. It cannot
edit allocations, enable execution, or promote a candidate.

## Interpretation

Pattern matching is a testable hypothesis, not proof of predictive edge.
Similarity thresholds create multiple-comparison and overfitting risk. A
candidate must survive costs, multiple symbols, chronological folds, stress,
an untouched holdout, and prospective Paper observation before any separate
execution decision.
