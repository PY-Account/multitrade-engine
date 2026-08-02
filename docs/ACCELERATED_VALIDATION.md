# Accelerated Strategy Validation

The accelerated validator reduces computation time for historical candidate
screening. It does not shorten the calendar time needed to collect genuinely
new Paper observations.

## What one run does

For each enabled Paper account, one run:

1. reloads the effective, audited account and strategy configuration;
2. downloads the union of all assigned research and execution symbols once;
3. evaluates every frozen baseline and every registered comparison variant;
4. uses the existing next-bar, adverse-cost, cross-symbol, chronological
   walk-forward, and trade-sequence stress gates;
5. runs candidate evaluations with a bounded worker pool;
6. produces transparent, comparable scorecards and failure explanations; and
7. stores one non-executable accelerated-run record and audit event.

The runner does not write ordinary Strategy Lab reports, model-trial records,
experiment observations, or the continuous worker's health file. Re-running
it therefore cannot inflate prospective-day or prospective-trial counts.

## Run it on the VPS

Use two workers on the current two-vCPU VPS:

```bash
cd /opt/multitrade/app
docker compose run --rm --no-deps engine \
  multitrade accelerated-validation --workers 2
```

The command exits with a non-zero status if any configured account fails. It
prints a structured JSON result and continues other account evaluations when
one account fails.

After completion, open:

**Strategy Lab → Accelerated Validation**

The dashboard shows the latest account-specific run, symbol coverage, run
duration, candidate classification, failed gates, and plain-language
diagnostics.

The **Trade Attribution Diagnostics** table requires a run produced by
version 0.18 or later. It separates:

- gross P/L before modeled slippage and commissions;
- modeled transaction costs and net P/L;
- strongest and weakest symbols and market regimes;
- weakest New York entry hour and largest-loss exit reason;
- average maximum favorable and adverse excursion.

The complete stored payload also contains additive rows for every symbol,
regime, entry hour, exit reason, and complete signal-reason set. These rows
explain where a fixed candidate failed. They are not an optimizer and do not
modify parameters.

## Research score

The score is a deterministic summary of existing pass/fail gates, not a
profit forecast:

| Component | Weight |
|---|---:|
| Evidence and sample coverage | 20 |
| Return after base and stressed costs | 25 |
| Breadth and chronological stability | 20 |
| Drawdown control | 15 |
| Trade-sequence stress | 20 |

If a candidate fails a minimum evidence gate, its total score is capped at
39. The score does not override any individual failed gate.

Classifications mean:

- `insufficient_evidence`: symbol or trade evidence is below a minimum gate;
- `rejected_by_research_gates`: material research gates failed;
- `continue_research`: the score is relatively stronger but one or more
  required gates still failed;
- `prospective_observation_candidate`: a baseline passed the historical
  screen and may be considered for new forward observation;
- `family_review_candidate`: a comparison variant passed the historical
  screen but still requires explicit family review and a new preregistered
  decision.

No classification authorizes an order.

## Safety and evidence boundaries

- Alpaca remains Paper-only.
- `execution_eligible` is always false for the run and every scorecard.
- Historical screening never grants global or per-strategy Paper permission.
- Candidate parameters remain frozen; the runner does not optimize or mutate
  them.
- Results on unchanged data are one historical screen, not multiple
  independent observations.
- Assigned strategy universes may differ, so the stored dataset fingerprint
  is candidate-specific even though the account-level download is shared.
- Market bars are persisted in the existing audit database with their
  provenance, while every scorecard retains its exact dataset fingerprint.
- Option package evidence remains in the separate Option Evidence Lab.
  Historical stock bars and option trade-bar proxies do not prove executable
  option fills or realized theta.

Promoting, modifying, or retiring a candidate remains a reviewed,
version-controlled process. The accelerated validator only helps prioritize
that review.

After introducing diagnostics, rerun the unchanged frozen candidates once.
Use that run to write a limited, mechanism-based Strategy v2 hypothesis.
Because the diagnostic dataset has then been inspected, it remains
development evidence; final acceptance still requires untouched or future
observations.

## Research decision queue

Version 0.19 translates each fixed candidate's diagnostics into one bounded
next action. The action differentiates no evidence, negative expectancy before
costs, gross edge erased by costs, insufficient profit factor, remaining
robustness work, and historical candidates that should stop tuning and move to
untouched confirmation.

Candidates are ranked only against the other frozen members of their family.
The family winner is a research priority, not a trading approval. A losing
regime, entry hour, or symbol is mentioned as a V2 hypothesis only when it has
at least five trades and represents at least ten percent of the candidate's
out-of-sample sample. The system never deletes those trades retrospectively.

Once the queue uses a dataset to draft a hypothesis, that dataset is labeled
`inspected_not_holdout`. A V2 candidate must be preregistered as a distinct
experiment and evaluated on a later untouched chronological holdout, under
adverse costs and cross-symbol breadth gates. Automatic parameter mutation and
execution eligibility remain false.
