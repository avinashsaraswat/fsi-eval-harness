# fsi-eval-harness

**A verify-and-regenerate loop for LLM output on regulated financial data.** An LLM drafts an
analyst-style signal brief from a filing's structured XBRL facts; a **deterministic verifier**
checks every numeric GAAP claim against that ground truth (value, us-gaap tag, exact period),
then regenerates until each claim reconciles or is dropped, emitting the full keep/drop audit
trail.

*(Working name — slice 1.)*

## Scope (slice 1) — read this first

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
  (non-standard income-statement tagging). **No earnings-call transcripts** (licensing) — BYO.

What *is* non-trivial here: a deterministic routing wall that keeps every number out of the
model's own judgment, and a grounding gate that had to be rebuilt after it was caught passing
100% of random numbers. The hard part isn't the brief; it's making the eval un-foolable.

## Why an eval-harness

As frontier models converge, the durable engineering asset is the **harness** around the model —
the evals, verification, and regeneration that hold it to a standard on real work. Most eval work
grades one model's output with another model: useful, but circular and hard to refute. The harder
claim is a harness whose output is checked against **machine-verifiable ground truth for every
numeric claim**. Qualitative claims here still go to an LLM judge, but only after the numbers they
rest on are verified, so the judge is never the arbiter of a fact.

Financial filings are the first vertical because XBRL is public, exact ground truth — an
unforgiving numeric domain. Slice 1 tests one narrow piece of the thesis: that a
*generate → verify → regenerate* loop converges when the facts are handed to it. Three things are
measured — numeric claims against ground truth, the code-vs-model routing split, and qualitative
judgment scored against the verified facts. v2 extends generation to filing text; the pattern
(thin verification core, domain-specific packs) is meant to generalize to any field with a
checkable source of truth.

## What the loop demonstrates

- **An un-foolable grounding gate (the honest engineering story).** The narrative-grounding gate
  was first built against all-pairs arithmetic over the fact base, which turned 24 facts into
  ~460k candidate values — dense enough that **100% of random numbers passed**. A gate that
  cannot fail is not a gate. It was rebuilt as ~200 *typed* derivations (same-concept
  period-over-period deltas, revenue-denominated margins, margin deltas), with the residual
  false-ground rates measured and published (~28% for %-tokens, ~12% bp, ~26% dollar-scale; see
  `docs/DESIGN.md` D7). The eval needed its own eval.
- **Citation integrity.** A correct number attached to the wrong us-gaap tag or the wrong period
  still **fails** (`wrong_tag` / `wrong_period`). Getting the figure right is not enough; the
  claim must point at the fact it came from.
- **A routing wall.** Numeric/GAAP claims are verified deterministically in code or **dropped** —
  they never fall through to the LLM judge. Only genuinely qualitative claims (materiality, tone,
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
  (fewer than 5 claims survive — no brief; the drop-log and metrics still emit, because a failed
  brief is a data point).
- **Deterministic numeric checking.** No LLM in the verify path. Derived figures are recomputed
  from ground-truth fact values via a whitelisted arithmetic evaluator (`+ - * /` and numeric
  constants over fact_id symbols only — no functions, no other names). Reported values use a 0.5%
  relative tolerance (diluted EPS uses a $0.005 absolute tolerance); the delta is always logged.

## What the real run shows — and what it doesn't

Read the table as **integrity** metrics for slice 1, narrowly:

- Because the generator is handed the fact values (see *Scope*), a high **reconcile rate** shows
  the loop preserves transcription, period, and derivation integrity, *not* that it judges a
  filing well. It says little about analytical quality, and nothing about whether the gate is
  load-bearing — that is what *Detection* below measures.
- **Deterministic routing share** is a descriptive ratio (numeric claims ÷ total claims), not a
  target: a brief with more qualitative sentences routes less to code.
- **Narrative coverage** is a *tripwire* for stray numbers with a measured false-ground floor
  (~12–28% on random inputs), not a hard pass/fail.
- The one **hard** number is per-claim numeric verification (value, tag, period), in the
  `numeric pass` column.

The headline block below is the **2026-07-31 cold run on the default configuration**
(`gemini-3.6-flash` generation + judging). The prototyping-era `claude-opus-4-8` run (2026-07-18,
same 8/8 reconcile / 100% coverage result) remains reproducible from cache via
`--provider anthropic`; both cold-run costs are noted after the block.

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

**Own the null: the numeric verifier caught nothing on this run** — 0 value/tag/period failures,
8/8 reconciled. That is the expected result: a frontier model handed clean facts rarely fumbles
transcription. The loop's value is therefore a machine-checkable *guarantee* and audit trail, not
a catch-rate — the same harness holds a cheaper model, a future model, or a drifted prompt to the
identical bar. `--mock` seeds three faults (perturbed value, shifted period, unclaimed number) and
all three are caught, proving the checks fire; the clean real run proves they don't false-positive.

Where the loop *did* bite on real data: beyond `concept_missing_optional` (10, untagged concepts
like JNJ/CVX operating income), the judge rejected **four** qualitative overreaches in-loop across
the three tickers that iterated — an unbacked $694B asset figure inside a qualitative claim
(MSFT), an uncited stockholders'-equity boost and an operating-cash-flow assertion citing only
revenue (COST), and cash-balance/R&D statements with no verified backing (CAT). Each brief
reconciled only after the rejected prose was corrected. The prototyping-era Opus run showed the
same pattern (2 judge drops, both COST). That is the judgment layer doing real work on real
filings, across two model stacks.

Cold full 8-ticker runs: **≈ $0.23** on the default configuration (`gemini-3.6-flash`, 26.6K in /
25.3K out tokens) and **≈ $1.13** on the prototyping-era configuration (`claude-opus-4-8`
generation + `claude-haiku-4-5` judge). The spend inside the metrics block reflects whichever run
last wrote it — a cache-served re-verify shows ~$0. Every model call is disk-cached under a
`--max-spend` hard abort.

## Detection — what the harness actually catches

The metrics above test whether the loop is *internally* consistent. They do not test whether the
gate is load-bearing. That takes planted errors.

**The correlation eval-of-the-eval was retracted (2026-07-26).** The original plan was to grade a
gold set by hand and report the correlation between the harness's self-grade and an expert's. That
number is undefined on shipped output: `self_grade` is 5.0 on every gold brief with **zero
variance**, because the harness only emits claims it already reconciled. It would have been
grading itself on a test it is engineered to ace. (`evals/correlation.py` was separately corrected
first: it had been correlating `self_grade` against `expert_grade`, which averages all four rubric
dimensions and so embeds the two auto dims that *constitute* `self_grade` — self-correlated by
construction, incapable of cleanly failing. It now headlines the expert-only dimensions the
harness cannot see. The statistic is honest; the population is still degenerate.)

The replacement asks something this output can answer: **plant a known error, and see whether the
pipeline catches it.** `python -m harness corpus` builds a blinded corpus from the cached
reconciled real briefs — 194 items over 8 filers, one planted error per item, content-hash ids,
ground truth held out in `labels.yaml`. `python evals/detection.py --judge real` scores it. Floor
80% / target 90%; Wilson 95% plus a cluster bootstrap (8 clusters by filer, 10,000 reps).

| class | n | detected | TPR | Wilson 95% | vs floor/target |
|---|---|---|---|---|---|
| `wrong_value` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `wrong_period` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `wrong_tag` | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| `omission` | 43 | 34 | **79.1%** | [64.8%, 88.6%] | **underpowered** |
| `fabrication` | 32 | 32 | 100.0% | [89.3%, 100.0%] | meets floor |

Specificity on clean items: 92/92 numeric claims and 24/24 qualitative claims unflagged — TNR 100%
in both cases, Wilson [96.0%, 100%] and [86.2%, 100%]. Conditioned on reconciled output, so the
clean briefs are the harness's own survivors.

**Read the sizing rule before the rates.** A true-90% detector needs **n ≥ 54** per class for its
Wilson lower bound to clear the 80% floor. Most classes here sit at n=37, so every interval is
wider than the point estimates suggest. Growing the corpus is mechanical work still to do; until
then these are directional, not settled.

**The omission row is the honest negative result.** It measures a *designed* blindness:
reconciliation checks what is present and cannot see what is absent. The 79.1% is not a partial
omission detector — it is dangling citations (a kept qualitative claim pointing at a removed
numeric) firing incidentally. There is no completeness gate in this slice, and the number says so
rather than the README asserting it.

**What the judge is worth, measured.** The rule-based citation baseline (`--judge mock`) catches
16/32 fabrications — both citation-integrity species (`no_citation`, `phantom_citation`) and zero
semantic overreach. The LLM judge closes the overreach gap completely: 16/16 across
`overreach_causal` and `overreach_superlative`. That delta is the judge's contribution, measured
rather than claimed.

Full measurement — including the Rogan-Gladen prevalence correction and the per-species
breakdown — is in `docs/DETECTION.md`.

### Expert grading — where it stands

`evals/gold/WORKSHEET-2026-07-26.md` is a blinded 8-brief worksheet: the claim-verdict table and
judge output are stripped, so an expert grades the narrative without anchoring to the harness's
self-assessment. Two dimensions, 1–5 — `materiality_judgment` and `clarity`. What it can answer:
whether the briefs are any good. What it **cannot** answer on this population: whether `self_grade`
predicts expert judgment, because `self_grade` has no variance here. Getting that number means
grading briefs the harness *rejected* alongside ones it shipped.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # set SEC_USER_AGENT (and GEMINI_API_KEY for --real)

.venv/bin/python -m harness run --mock            # $0, deterministic, full pipeline
.venv/bin/python -m harness coverage              # concept-coverage matrix across tickers
.venv/bin/python -m harness run --real --max-spend 10
```

Artifacts land in `out/{run_id}/{ticker}/`: `brief.md`, `droplog.md` / `.jsonl`, `metrics.json`,
plus a run-level `summary.md`. Design decisions and the real-data findings are in `docs/DESIGN.md`.

**Models & providers.** The supported default is the **Gemini API** (`gemini-3.6-flash` for both
generation and judging). The harness is provider-agnostic behind a one-module seam
(`src/harness/providers.py`): `--provider anthropic` selects the Anthropic adapter, which also
replays the earlier measured runs from the local cache at $0. Measured numbers in this README and
in `docs/DETECTION.md` state the models that produced them; a judge-model change re-runs the
detection measurement before those numbers are cited for the new configuration.

## License

Apache-2.0
