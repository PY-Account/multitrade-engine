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
   assigned symbols, allocation and risk inputs, all Strategy Lab gates and
   stress settings, and the complete frozen experiment binding.
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

Version 0.10.0 adds immutable experiment manifests. The manifest is inserted
in the same transaction as its first linked report and trial. An existing
experiment ID cannot be reused with a different fingerprint, and ordinary SQL
updates and deletes are blocked. Each later trial carries the experiment ID,
family, phase, prospective flag, and manifest fingerprint inside its
configuration evidence.

The Git release is the code-reviewed publication point for these manifests;
the local SQLite copy is not an independent public preregistration service.
An administrator with schema and filesystem control can still replace it.

Version 0.11.0 adds eight distinct parameter-sensitivity candidates. Their
strategy implementation fingerprint shares the same reviewed source scope,
while their parameter payload produces a distinct candidate fingerprint.
Each trial also records its variant ID, comparison flag, selected universe,
and experiment fingerprint. Repeating a candidate on new data increments
observations but not the family's distinct-candidate count.

## Statistical boundary

The registry and experiment families supply prerequisites for the Probability
of Backtest Overfitting and Deflated Sharpe Ratio, but do not make either
statistic valid by themselves. MultiTrade must still accumulate enough
genuinely distinct variants, reserve a final untouched holdout, and implement
the published procedures without cherry-picking. Repeated runs of one frozen
candidate do not create the cross-section of alternatives required for those
statistics.
