# fsi-eval-harness

**A verify-and-regenerate loop for LLM output on regulated financial data.** An LLM drafts an
analyst-style signal brief from a filing's structured XBRL facts; a **deterministic verifier**
checks every numeric GAAP claim against that ground truth (value, us-gaap tag, exact period),
then regenerates until each claim reconciles or is dropped, emitting the full keep/drop audit
trail.

**Author:** Avinash Saraswat. Built and published in a personal capacity, on personal time,
against public SEC data. Views are his own and not those of any employer.

## Run it without an API key

Two things work on a clean clone with no keys, no network and no account. They are the fastest
way to see whether the method is worth your time.

```bash
git clone https://github.com/avinashsaraswat/fsi-eval-harness
cd fsi-eval-harness
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"   # Python 3.11+; [dev] adds pytest

python evals/detection.py --selftest   # the statistics layer, self-checking
pytest -q                              # offline tests, including the verifier and the grounding gate
```

The self-test checks Wilson intervals, cluster bootstrap and a prevalence-formula helper.
Its sizing convention finds n = 54 for round(0.90 * n) observed detections to clear an
80% Wilson lower bound. This is not a power calculation. The corpus report withholds
the prevalence correction because its available inputs do not support that estimate.

Reproducing the full published detection table needs `SEC_USER_AGENT` set, because it fetches
company facts from EDGAR. Each corpus item pins the accession number it was built against, so a
re-run resolves the same filing rather than whatever the company has filed since.

## Scope of slice 1

This slice isolates the **verification loop** from the **extraction problem** on purpose, and the
distinction decides how to read everything below.

- **The generator writes from a structured fact sheet, not from filing prose.** Each brief is
  drafted from a rendered table of XBRL company-facts (concept, us-gaap tag, period, value), with
  a standing instruction to use only those values. Verification resolves every claim against the
  same company-facts source through an *independent* code path. So this slice does **not** yet
  demonstrate pulling a correct claim out of narrative 10-Q/K text; that (filing-text grounding)
  is **v2**. What it **does** demonstrate: the model can still transcribe a wrong number, cite the
  wrong period or tag, import a figure from memory, or botch a derivation, and the verifier
  catches all four.
- **The engineered asset is the loop, not the brief.** The contribution is the verification,
  routing, and audit discipline around the model, not the prose the model produces.
- **10-Q / 10-K only.** 8-K earnings exhibits are *furnished*, not *filed*: no contemporaneous
  XBRL to verify against; real XBRL arrives with the 10-Q/K.
- **Consolidated, non-dimensional facts only** (company-facts carries no segment detail); segment
  claims are out of scope and dropped if generated. **No banks/insurers** in the default set
  (non-standard income-statement tagging). **No earnings-call transcripts** (licensing). Bring your own.

What *is* non-trivial here: a deterministic routing wall that keeps every number out of the
model's own judgment, and a grounding gate that had to be rebuilt after it was caught passing
100% of random numbers. The evaluation needs tests that can expose its own failures.

## Why an eval-harness

As frontier models converge, the durable engineering asset is the **harness** around the model:
the evals, verification, and regeneration that hold it to a standard on real work. Most eval work
grades one model's output with another model: useful, but circular and hard to refute. The harder
claim is a harness whose output is checked against **machine-verifiable ground truth for every
numeric claim**. Qualitative claims here still go to an LLM judge, but only after the numbers they
rest on are verified, so the judge is never the arbiter of a fact.

Financial filings are the first vertical because XBRL is public, exact ground truth in an
unforgiving numeric domain. Slice 1 tests one narrow piece of the thesis: that a
*generate → verify → regenerate* loop converges when the facts are handed to it. Three things are
measured: numeric claims against ground truth, the code-vs-model routing split, and qualitative
judgment scored against the verified facts. v2 extends generation to filing text; the pattern
(thin verification core, domain-specific packs) is meant to generalize to any field with a
checkable source of truth.

## Verification scope

Reported structured claims are checked for value, unit, concept, tag and period.
Derived claims are recomputed from their declared expressions and cited facts.
The verifier does not prove that an expression is the right financial formula or
that a sentence assigns a grounded number to the right company, concept or direction.
Narrative coverage checks recognizable numeric tokens. It is not a semantic proof.

The historical tables below predate the October 8 guard changes. They remain records
of those configurations. No new live model measurement is claimed.
See [maintenance findings](docs/MAINTENANCE.md) for the tested fixes and remaining limits.

## What the loop demonstrates

- **A grounding gate tested against random inputs.** The narrative-grounding gate
  was first built against all-pairs arithmetic over the fact base, which turned 24 facts into
  ~460k candidate values, dense enough that **100% of random numbers passed**. A gate that
  cannot fail is not a gate. It was rebuilt as ~200 *typed* derivations (same-concept
  period-over-period deltas, revenue-denominated margins, margin deltas), with the residual
  false-ground rates measured and published (~28% for %-tokens, ~12% bp, ~26% dollar-scale; see
  `docs/DESIGN.md` D7). The eval needed its own eval.
- **Citation integrity.** A correct number attached to the wrong us-gaap tag or the wrong period
  still **fails** (`wrong_tag` / `wrong_period`). Getting the figure right is not enough; the
  claim must point at the fact it came from.
- **A routing wall.** Numeric/GAAP claims are verified deterministically in code or **dropped**.
  They never fall through to the LLM judge. Only genuinely qualitative claims (materiality, tone,
  why-it-matters) reach the judge, and only once the numbers are already clean.
- **A full audit trail.** Every candidate fact, claim, and narrative number gets a logged
  keep / drop / correct row with a reason code (`droplog.md` / `.jsonl`), down to per-token
  grounding provenance.

## The loop

- **Claims-first structured generation.** The brief is a narrative plus a machine-readable
  `claims[]` array (us-gaap tag, exact period dates, unit, value, and a derivation expression for
  computed figures). A deterministic sweep flags any narrative number with no backing claim; no
  fragile NLP extraction stage.
- **Verify → correct → regenerate.** Failed verification produces a structured correction block;
  the generator gets up to 2 retries (3 attempts total). Briefs emit as **RECONCILED**,
  **PARTIAL** (failing claims stripped and logged, judge-rejected prose removed), or **FAILED**
  (fewer than 5 claims survive or unsafe prose remains, so there is no brief, but the drop-log and metrics still emit, because a
  failed brief is a data point).
- **Deterministic numeric checking.** No LLM in the verify path. Derived figures are recomputed
  from ground-truth fact values via a whitelisted arithmetic evaluator (`+ - * /` and numeric
  constants over fact_id symbols only, with no functions and no other names). Reported values use a 0.5%
  relative tolerance (diluted EPS uses a $0.005 absolute tolerance); the delta is always logged.

## What the real run shows

Read the table as **integrity** metrics for slice 1, narrowly:

- Because the generator is handed the fact values (see *Scope*), a high **reconcile rate** shows
  the loop preserves transcription, period, and derivation integrity, *not* that it judges a
  filing well. It says little about analytical quality, and nothing about whether the gate is
  load-bearing. That is what *Detection* below measures.
- **Deterministic routing share** is a descriptive ratio (numeric claims ÷ total claims), not a
  target: a brief with more qualitative sentences routes less to code.
- **Narrative coverage** is a *tripwire* for stray numbers with a measured false-ground floor
  (~12 to 28% on random inputs), not a hard pass/fail.
- The one **hard** number is per-claim numeric verification (value, tag, period), in the
  `numeric pass` column.

The headline block below is the **2026-07-31 cold run on the default configuration**
(`gemini-3.6-flash` generation + judging). The cold-run cost is noted after the block.

<!-- metrics:begin -->
### Run `20260731-gemini-full` (real)

| ticker   | form   | status     |   attempts | numeric pass   | det-routing   |   coverage % |   drops |
|----------|--------|------------|------------|----------------|---------------|--------------|---------|
| AAPL     | 10-Q   | RECONCILED |          1 | 8/8            | 80%           |          100 |       0 |
| MSFT     | 10-Q   | RECONCILED |          2 | 8/8            | 80%           |          100 |       1 |
| NVDA     | 10-Q   | RECONCILED |          1 | 8/8            | 73%           |          100 |       0 |
| COST     | 10-Q   | RECONCILED |          3 | 9/9            | 82%           |          100 |       4 |
| KO       | 10-Q   | RECONCILED |          1 | 7/7            | 70%           |          100 |       1 |
| JNJ      | 10-Q   | RECONCILED |          1 | 9/9            | 75%           |          100 |       2 |
| CAT      | 10-Q   | RECONCILED |          2 | 10/10          | 83%           |          100 |       2 |
| CVX      | 10-Q   | RECONCILED |          1 | 8/8            | 80%           |          100 |       4 |

- Reconcile rate: **100%** (8/8; 0 partial, 0 failed) · mean retries-to-reconcile: 0.5
- Pooled deterministic routing share: **78%**
- Drops by reason: `{"judge_unsupported": 4, "concept_missing_optional": 10}`
- Spend: **$0.23** (26,570 in / 25,302 out tokens; 17 live calls, 7 cached)

<!-- metrics:end -->

**Own the null: the numeric verifier caught nothing on this run**, with 0 value/tag/period failures
and 8/8 reconciled. That is the expected result: a frontier model handed clean facts rarely fumbles
transcription. This run demonstrates structured-claim reconciliation and an audit trail. It does not
establish sentence-level factual accuracy. The same harness holds a cheaper model, a future model, or a drifted prompt to the
identical bar. `--mock` seeds three faults (perturbed value, shifted period, unclaimed number) and
all three are caught. No numeric claim failed verification in this eight-filer run.
That observation does not establish a population false-positive rate.

Where the loop *did* bite on real data: beyond `concept_missing_optional` (10, untagged concepts
like JNJ/CVX operating income), the judge rejected **four** qualitative overreaches in-loop across
the three tickers that iterated: an unbacked $694B asset figure inside a qualitative claim
(MSFT), an uncited stockholders'-equity boost and an operating-cash-flow assertion citing only
revenue (COST), and cash-balance/R&D statements with no verified backing (CAT). Each brief
reconciled only after the rejected prose was corrected. That is the judgment layer doing real
work on real filings.

Cold full 8-ticker run: **≈ $0.23** on the default configuration (`gemini-3.6-flash`, 26.6K in /
25.3K out tokens). The spend inside the metrics block reflects whichever run last wrote it, and a
cache-served re-verify shows ~$0. Every model call is disk-cached under a
`--max-spend` threshold checked before each uncached call. A call can exceed the remaining budget.

## What the harness actually catches

The metrics above test whether the loop is *internally* consistent. They do not test whether the
gate is load-bearing. That takes planted errors.

**The correlation eval-of-the-eval was retracted (2026-07-26).** The original plan was to grade a
gold set by hand and report the correlation between the harness's self-grade and an expert's. That
number is undefined on shipped output: `self_grade` is 5.0 on every gold brief with **zero
variance**, because the harness only emits claims it already reconciled. It would have been
grading itself on a test it is engineered to ace. (`evals/correlation.py` was separately corrected
first: it had been correlating `self_grade` against `expert_grade`, which averages all four rubric
dimensions and so embeds the two auto dims that *constitute* `self_grade`, so it was self-correlated by
construction and incapable of cleanly failing. It now headlines the expert-only dimensions the
harness cannot see. The statistic is honest; the population is still degenerate.)

The replacement asks something this output can answer: **plant a known error, and see whether the
pipeline catches it.** The corpus in `evals/corpus/` holds 194 items over 8 filers: 186 seeded variants
and 8 unseeded baseline items. Each seeded variant has one planted error. Items have stable hashed identifiers.
Labels are held separately in `labels.yaml`. See *About the test data* below for provenance. `python -m harness corpus` is the builder: it rebuilds from whatever reconciled briefs sit in the local cache, so a rebuild today
yields a different corpus. Pass `--out` to a new directory to keep the fixed set intact.
`python evals/detection.py --judge real` scores it. Floor 80% / target 90%; Wilson 95% plus a
cluster bootstrap (8 clusters by filer, 10,000 reps).

| class | n | detected | TPR | Wilson 95% | vs floor/target |
|---|---|---|---|---|---|
| `wrong_value` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `wrong_period` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `wrong_tag` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `omission` | 43 | 34 | **79.1%** | [64.8%, 88.6%] | **underpowered** |
| `fabrication` | 32 | 32 | 100.0% | [89.3%, 100.0%] | meets floor |

On the eight unseeded baseline items, `gemini-3.6-flash` left 92/92 numeric claims
and 19/24 qualitative claims unflagged. The five qualitative flags are observations,
not established false alarms. An earlier adjudicator accepted these baseline claims;
their qualitative reference labels were not independently adjudicated. These counts
do not establish qualitative specificity. Claims share eight source briefs, so
claim-level intervals do not resolve dependence within each brief.

**Read the sizing rule before the rates.** At n = 54, the rounded 90% target count is 49 detections.
That observed count gives a Wilson lower bound above 80%. This is not a power calculation or a guarantee for a detector whose true rate is 90%. Most classes here sit at n=37, so every interval is
wider than the point estimates suggest. Growing the corpus is mechanical work still to do; until
then these are directional, not settled.

**The omission row is the honest negative result.** It measures a *designed* blindness:
reconciliation checks what is present and cannot see what is absent. The 79.1% is not a partial
omission detector. It is dangling citations (a kept qualitative claim pointing at a removed
numeric) firing incidentally. There is no completeness gate in this slice, and the number says so
rather than the README asserting it.

**What the judge is worth, measured.** The rule-based citation baseline (`--judge mock`) catches
16/32 fabrications: both citation-integrity species (`no_citation`, `phantom_citation`) and zero
semantic overreach. The LLM judge detects 16/16 seeded cases across
`overreach_causal` and `overreach_superlative` in this corpus. Those cases reuse two
fixed overreach sentences across eight briefs. This result does not establish
complete detection of naturally occurring overreach.

The per-species counts and limits of the earlier prevalence calculation are in
`docs/DETECTION.md`.

### About the test data

The 194-item error-injection corpus, the eight gold briefs and the July 26 grading worksheet were drafted in July 2026 with a non-Google model, during prototyping and before this project moved to Google's Gemini models. They are kept as a fixed, already published test set, every value unchanged, so results stay comparable; four lines in two of those files were reworded only to drop a tool name. Errors are planted into them by fixed code templates, not by a model. This code calls only Gemini models. The reported qualitative model judgments use Gemini. Numeric reconciliation is deterministic.

### Where expert grading stands

`evals/gold/WORKSHEET-2026-07-26.md` is a blinded 8-brief worksheet: the claim-verdict table and
judge output are stripped, so an expert grades the narrative without anchoring to the harness's
self-assessment. Two dimensions, `materiality_judgment` and `clarity`, each scored 1 to 5. What it can answer:
whether the briefs are any good. What it **cannot** answer on this population: whether `self_grade`
predicts expert judgment, because `self_grade` has no variance here. Getting that number means
grading briefs the harness *rejected* alongside ones it shipped.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # set SEC_USER_AGENT (and GEMINI_API_KEY for --real)

.venv/bin/python -m harness run --mock            # $0, no model calls; needs SEC_USER_AGENT and network
.venv/bin/python -m harness coverage              # concept-coverage matrix across tickers
.venv/bin/python -m harness run --real --max-spend 10
```

Artifacts land in `out/{run_id}/{ticker}/`: `brief.md`, `droplog.md` / `.jsonl`, `metrics.json`,
plus a run-level `summary.md`. Design decisions and the real-data findings are in `docs/DESIGN.md`.

**Models.** The harness runs on the **Gemini API**: `gemini-3.6-flash` for both generation and
judging by default, with other Gemini models selected through `HARNESS_GEN_MODEL` /
`HARNESS_JUDGE_MODEL`. All SDK contact sits in one module (`src/harness/providers.py`), and every
model call is disk-cached, so a cached replay needs no API key. Measured numbers in this README and
in `docs/DETECTION.md` state the models that produced them; a judge-model change re-runs the
detection measurement before those numbers are cited for the new configuration.

## License

Apache-2.0
