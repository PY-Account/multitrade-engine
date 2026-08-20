# Options Income Research Lab

`support_delta_put_income` tests the user's put-income hypothesis without
assuming that prior discretionary results imply a durable edge.

## Underlying setup

The closed bar must be within the configured ATR distance of the higher of:

- the 20-period lower Bollinger band; or
- the lowest low in the preceding 40 closed bars.

It must trade through that level, close back above it with a bullish body, and
close above the previous bar. Detected downtrends and closes below the slow
moving average are rejected. Baseline, strict, and broad proximity variants
are frozen before evaluation.

## Option vehicle

The only configured vehicle is a defined-risk Bull Put Credit Spread. The
baseline policy requires:

- 30-60 calendar days to expiration;
- short-put absolute delta at or below 0.22 (target 0.20);
- long protective put near 0.08 absolute delta;
- strike width no greater than $5;
- positive modeled package theta;
- conservative entry credit at least 15% of maximum spread loss;
- liquid two-sided quotes and freshness checks before Paper submission.

The conservative package risk is `(width - credit) * 100`. Position quantity
is then limited by both the strategy capital allocation and the account risk
authority. A 50% credit capture is the modeled profit target, with a 1.5x
opening-credit loss trigger and a mandatory exit before 10 DTE.

## Evidence boundary

Historical underlying results only screen the timing hypothesis. They do not
prove historical option fills, Greeks, theta capture, assignment behavior, or
executable profitability. Those claims require decision-time chain snapshots,
exact-contract paths, conservative spread pricing, and prospective Paper
observations. The allocation is enabled for observation but its
`paper_execution_allowed` flag remains false.

## Index weekly/daily cadence

`index_put_credit_14dte` is the shared identity for the SPX/RUT defined-risk
put-credit idea. Do not create a second strategy just to test daily versus
weekly frequency. Set the allocation-level
`minimum_entry_interval_minutes` instead:

- `1440`: at most one entry per day per proxy symbol.
- `10080`: at most one entry per week per proxy symbol.

The default Paper options allocation keeps the user's daily test preference
with `1440`. A TradingWarz-style weekly income trial should change only that
interval and preregister the change before comparing results.

## V2 profitability loop

V1 produced small positive gross results in two variants, but modeled costs
removed the edge and the range regime was the dominant loss segment. V2 is a
new strategy identity, not a retroactive edit. It preregisters:

- an established `trend_up` regime;
- positive 30-bar slow-average slope;
- a maximum ATR percentage to reject extreme volatility;
- an 18% minimum credit-to-maximum-loss ratio for the spread.

Every frozen option package now reports premium, risk capital, conservative
path P/L after configured slippage, return on risk, premium capture, and the
first policy exit. This measures option economics rather than treating an
underlying stock proxy as proof of Theta profitability. V2 remains blocked
until a new holdout and prospective exact-contract Paper evidence pass.
