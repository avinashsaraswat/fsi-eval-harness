# Judge-detection measurement on an error-injected corpus (built 2026-07-28)

The correlation eval-of-the-eval is UNDEFINED on shipped output (self_grade = 5.0 on all
gold briefs, zero variance, see evals/correlation.py): the harness only emits claims it
already reconciled, so it grades itself on a test it is engineered to ace. This measurement
replaces it with the detection framing: plant known errors in real briefs, measure whether
the pipeline catches them. Corpus: `evals/corpus/` (blinded, labels held out; builder
`python -m harness corpus`); runner: `python evals/detection.py --judge real`.

**Corpus provenance.** The test briefs were drafted during July 2026 prototyping, before the
project switched to Gemini, with a non-Google model. They are kept as a fixed test set; errors
are injected into them by fixed code templates.

**Measurement configuration.** The historical detection table records one configuration: the
fixed corpus, scored by the same runner with the shipped default LLM judge, `gemini-3.6-flash`
(`python evals/detection.py --judge real`, measured 2026-07-31, $0.33 cold judge spend).
The runner writes `out/detection/report-real.md` and `out/detection/results-real.json`. The judge
model is a versioned dependency of this evaluation, so cite these numbers only with the judge that
produced them. The deterministic classes do not depend on the judge.
The October 8 code changes have not received a new live judge measurement. These historical results are not measurements of the revised code.

Corpus: 194 items over 8 filers. Floor 80% / target 90%; Wilson 95% + cluster bootstrap (8 clusters, 10000 reps, seed 20260728).

**Sizing convention:** n = 54 with round(0.90 × 54) = 49 observed detections clears the 80% Wilson lower bound. This is a count threshold, not a power calculation or guaranteed sample size.

Detection by error class:

| class | n | detected | TPR | Wilson 95% | bootstrap 95% | vs floor/target |
|---|---|---|---|---|---|---|
| wrong_value | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| wrong_period | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| wrong_tag | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| omission | 43 | 34 | 79.1% | [64.8%, 88.6%] | [71.1%, 88.4%] | underpowered (dangling-citation path only) |
| fabrication | 32 | 32 | 100.0% | [89.3%, 100.0%] | [100.0%, 100.0%] | meets floor, including all 16 semantic-overreach items |

## Flags on unseeded baseline items

The eight unseeded briefs contain 92 numeric claims and 24 qualitative claims.
The default configuration leaves all 92 numeric claims and 19 qualitative claims unflagged.
It flags five qualitative claims. An earlier adjudicator accepted these baseline claims;
their qualitative reference labels were not independently adjudicated.

The flags therefore do not establish false alarms, and the unflagged fraction does
not establish qualitative specificity. Whether a flag is warranted requires separate
adjudication against the source evidence. Claims share eight source briefs, so
claim-level intervals do not resolve dependence within each brief.

## Earlier prevalence calculation withdrawn

The earlier 97.2% corrected-prevalence figure is not a valid deployment estimate.
Its calculation combined item-level detection with an unweighted mean of numeric
and qualitative claim-level unflagged fractions. Those inputs concern different units.
The qualitative baseline labels also lack independent adjudication.

The corpus contains 186 seeded variants among 194 items. This designed injection
fraction does not estimate production error prevalence. The runner retains descriptive
item counts and emits no corrected-prevalence estimate for this corpus. A future
prevalence estimate requires consistent units, independently established reference
labels and evidence that the measured operating characteristics transfer to the target population.

## Reading the omission row

Omission TPR measures a *designed blindness*: reconciliation checks what is present and cannot see what is absent. Any non-zero detection comes from dangling citations (a kept qualitative claim citing a removed numeric). That is an incidental path, not a completeness check. This is the paper's negative-result core, stated as a measured number rather than an assertion.

## Fabrication by species

| species | n | detected |
|---|---|---|
| no_citation | 8 | 8 |
| overreach_causal | 8 | 8 |
| overreach_superlative | 8 | 8 |
| phantom_citation | 8 | 8 |

The rule-based citation baseline (--judge mock) detects 16/32 fabrications: both
citation-integrity species, zero semantic overreach. The LLM judge detects all 16 seeded overreach cases in this corpus.
They reuse two fixed sentences across eight source briefs. Generalization to other
overreach forms remains unmeasured.
