# Immutable Model-Trial Registry

MultiTrade registers every Strategy Lab evaluation as an append-only model
trial. The purpose is to preserve failed and unfavorable trials alongside
successful ones and to create the lineage required for later multiple-testing
analysis.

The registry is research-only and cannot authorize Paper or live execution.

## Trial identity

Each trial stores three separate SHA-256 fingerprints:

1. **Candidate fingerprint:** strategy ID, declared version, deterministic
   parameters, and a source manifest covering the strategy module, feature
   engine, signal construction, backtester, and robustness simulator.
2. **Configuration fingerprint:** account/environment identity, timeframe,
   assigned symbols, allocation and risk inputs, and all Strategy Lab gates
   and stress settings.
3. **Dataset fingerprint:** every requested symbol, exact normalized OHLCV
   bars, timestamps, trade counts, VWAP, feed, adjustment, and missing-data
   state.

The full normalized definitions and compact fingerprints are both stored. A
changed parameter, validation rule, or historical input therefore creates
different evidence rather than silently inheriting an earlier result.

## Append-only enforcement

The report and trial are inserted together in one immediate SQLite
transaction. Registered trial rows reject SQL `UPDATE` and `DELETE`
statements. Their associated Strategy Lab report is also protected from
mutation or deletion.

For each account and strategy, a trial stores the hash of the preceding trial.
Its own hash covers:

- all three identities and their definitions;
- evaluated time and requested symbols;
- aggregate metrics, gates, warnings, and readiness;
- the previous hash; and
- the structurally false execution-eligibility value.

The read-only dashboard independently recomputes the row hash and checks the
chain link. A broken row or link is displayed as `Broken`.

## Security boundary

This is append-only and tamper-evident under normal application and SQL access;
it is not an externally anchored audit ledger. An administrator with full
filesystem and SQLite schema control could replace the database and its
triggers. Stronger assurance would require signed periodic roots copied to an
independent write-once service.

Trials produced before version 0.9.0 are not retroactively fingerprinted
because reconstructing the exact historical code, configuration, and data
would create false provenance. The first post-upgrade Strategy Lab cycle
starts each strategy's verified chain.

## Statistical boundary

The registry supplies a prerequisite for the Probability of Backtest
Overfitting and Deflated Sharpe Ratio, but does not make either statistic
valid by itself. MultiTrade must still define economically related candidate
families, accumulate enough distinct trials, preserve an untouched final
holdout, and implement the published procedures without cherry-picking the
family definition.
