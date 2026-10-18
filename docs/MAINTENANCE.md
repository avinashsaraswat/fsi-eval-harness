# Maintenance review, October 8, 2026

The harness is a research prototype for reconciling structured financial claims.
It is not yet a validator of arbitrary financial prose. The October 8 review found
failures beyond the historical test suite and added targeted regression checks.

## Architecture

| Component | Responsibility | Important boundary |
|---|---|---|
| `edgar.py`, `facts.py` | Fetch public SEC data and select a fact index | Generation and verification share this source. |
| `generate.py`, `providers.py` | Generate a structured brief, with cached model calls | A model response is untrusted input. |
| `schema.py`, `verify.py` | Validate claim structure and compare numeric claims with facts | Recomputing a formula does not establish its financial meaning. |
| `judge.py`, `loop.py` | Judge qualitative claims and retry or withhold unsafe output | A missing verdict cannot count as approval. |
| `report.py`, `metrics.py` | Preserve output status, traces and estimated spend | The spend threshold is checked before calls, not reserved in advance. |
| `inject.py`, `evals/detection.py` | Build planted variants and measure detection | Seeded detection is distinct from independently adjudicated specificity. |

## Corrections verified offline

- Reported claims reject mismatched units and period types. Exact zero matches zero. Constant-only derivations fail.
- Briefs reject duplicate claim IDs. Judge responses require one strictly typed verdict for every qualitative claim. Missing supporting citations cannot pass.
- Coverage now examines small explicit currency, percentage and basis-point tokens. Years and unqualified small prose counts remain excluded.
- Partial briefs are withheld when uncovered numbers remain or rejected claims cannot be located in the prose for removal.
- Zero spend remains zero. Negative and nonfinite budgets fail validation. A budget abort returns failure even after earlier tickers succeeded.
- A failed rerun removes the earlier brief at that output location. Emitted briefs display their status.
- Corpus creation refuses a nonempty destination, including the recorded dataset.
- Measurement JSON schema version 2 reports descriptive baseline flags. It withholds the unsupported prevalence correction and qualitative specificity claim.

The suite contains 84 passing tests: 57 existing cases, 25 boundary checks from this
review, and two reporting cases. All run offline. A separate offline check over the
recorded 194-item corpus reproduced the rule-based counts: 37/37 for each numeric
error class, 34/43 omissions and 16/32 fabrications. This is not a new live model
measurement. Historical model tables retain their original configuration and date.

## Remaining work and limits

| Priority | Finding | Required next evidence or change |
|---|---|---|
| Before asserting sentence accuracy | Numeric coverage can match a value attached to the wrong concept or direction. Derived expressions lack a financial formula type system. | Add independent sentence-to-claim checks or narrow the output contract to structured claims. |
| Before claiming independent specificity | The qualitative baseline labels lack independent adjudication. | Establish a reference-label protocol and label held-out material independently. |
| Before claiming archival reproducibility | Filing accessions are pinned, but company-facts responses can contain later comparative values. Exact SEC inputs and resolved facts are not packaged with the corpus. | Freeze and hash source snapshots, resolved facts, prompts, model configuration and code for each measurement. |
| Before a paid run | The spend ledger can exceed a positive threshold by one call. Local prices and fallback prices are estimates. | Verify applicable prices. Add reservations if a strict cap is required. |
| Before a fresh model comparison | Cache keys omit SDK and adapter configuration. A stable model name does not identify every setting. | Version the complete call configuration and capture model and SDK metadata in results. |
| Before external adoption claims | The corpus contains eight source briefs, no banks or insurers, and templated perturbations. | Measure on independent inputs and record actual user runs and feedback. |

Item IDs are stable hashes of ticker, error class and discriminator. They are not
content checksums. Rebuilding into a new folder does not make those IDs attest to
unchanged content. Preserve explicit file hashes when comparing datasets.

The sample-size helper finds the first n whose rounded 90% target count clears the
80% Wilson lower bound. It is not a power calculation. Cluster dependence and only
eight source briefs limit inference beyond the tested corpus.

Google still lists [Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)
as a stable model. Keeping that model preserves the historical configuration.
A model upgrade needs a separate comparison. No upgrade or live call was made here.

## Release verification

Run the tests and statistics self-test against the exact candidate to be published.
Review the complete diff against public main. Preserve the historical corpus and
model tables. Record new measurements under their own code and input hashes.

Release 0.2.0 includes these corrections and was checked this way before publication.
Repeat the same checks against the exact commit of each later release.
