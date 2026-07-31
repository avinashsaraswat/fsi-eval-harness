# Judge-detection measurement — error-injected corpus (built 2026-07-28)

The correlation eval-of-the-eval is UNDEFINED on shipped output (self_grade = 5.0 on all
gold briefs, zero variance — see evals/correlation.py): the harness only emits claims it
already reconciled, so it grades itself on a test it is engineered to ace. This measurement
replaces it with the detection framing: plant known errors in real briefs, measure whether
the pipeline catches them. Corpus: `python -m harness corpus` (evals/corpus/, blinded,
labels held out); runner: `python evals/detection.py --judge real`.

**Measurement configurations in this document:** the 2026-07-28 baseline below used LLM judge
`claude-haiku-4-5-20251001`; briefs and injections derive from the cached `claude-opus-4-8` real
runs. The 2026-07-31 re-measurement (last section) runs the same corpus under the shipped default
judge, `gemini-3.6-flash`. Cite each set of numbers only for its own configuration.


Corpus: 194 items over 8 filers. Floor 80% / target 90%; Wilson 95% + cluster bootstrap (8 clusters, 10000 reps, seed 20260728).

**Sizing rule:** a true-90% detector needs **n ≥ 54** per class for its Wilson lower bound to clear the 80% floor.

| class | n | detected | TPR | Wilson 95% | bootstrap 95% | vs floor/target |
|---|---|---|---|---|---|---|
| wrong_value | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| wrong_period | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| wrong_tag | 37 | 37 | 100.0% | [90.6%, 100.0%] | [100.0%, 100.0%] | meets target |
| omission | 43 | 34 | 79.1% | [64.8%, 88.6%] | [71.1%, 88.4%] | underpowered |
| fabrication | 32 | 32 | 100.0% | [89.3%, 100.0%] | [100.0%, 100.0%] | meets floor |

## Specificity (clean items only — claim level)

- numeric claims: 92/92 unflagged — TNR 100.0%, Wilson [96.0%, 100.0%]. *Conditioned on reconciled output — clean briefs are the harness's own survivors.*
- qualitative claims (real judge): 24/24 unflagged — TNR 100.0%, Wilson [86.2%, 100.0%]

## Rogan-Gladen check

Pooled Se 95.2%, Sp 100.0%. Observed item flag rate 91.2% on a corpus with true injection prevalence 95.9% → corrected estimate **95.9%**. In production the same correction turns the pipeline's observed flag rate into a true-error-prevalence estimate.

## Reading the omission row

Omission TPR measures a *designed blindness*: reconciliation checks what is present and cannot see what is absent. Any non-zero detection comes from dangling citations (a kept qualitative claim citing a removed numeric) — an incidental path, not a completeness check. This is the paper's negative-result core, stated as a measured number rather than an assertion.

*Judge spend this run: $0.2330 (cache hits: 8)*

## Fabrication by species (real judge)

| species | n | detected |
|---|---|---|
| no_citation | 8 | 8 |
| overreach_causal | 8 | 8 |
| overreach_superlative | 8 | 8 |
| phantom_citation | 8 | 8 |

The rule-based citation baseline (--judge mock) detects 16/32 fabrications — both
citation-integrity species, zero semantic overreach. The LLM judge closes the overreach
gap completely (16/16). That delta is the judge's measured contribution.

## Re-measurement — `gemini-3.6-flash` judge (2026-07-31)

Same corpus, same runner, judge swapped to the shipped default (`python evals/detection.py
--judge real`, $0.33). Deterministic classes are judge-independent and reproduce exactly.

| class | n | detected | TPR | Wilson 95% | vs floor/target |
|---|---|---|---|---|---|
| wrong_value | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| wrong_period | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| wrong_tag | 37 | 37 | 100.0% | [90.6%, 100.0%] | meets target |
| omission | 43 | 34 | 79.1% | [64.8%, 88.6%] | underpowered (dangling-citation path only) |
| fabrication | 32 | 32 | 100.0% | [89.3%, 100.0%] | meets floor — incl. all 16 semantic-overreach items |

Specificity: numeric claims 92/92 unflagged (TNR 100.0%). **Qualitative claims 19/24 unflagged —
TNR 79.2%, Wilson [59.5%, 90.8%], down from the Haiku baseline's 24/24.** The Gemini judge
matches the baseline's sensitivity (every fabrication species 8/8) but flags 5 clean qualitative
claims the baseline passed: equal recall, lower precision. Production consequence is extra
regeneration churn on clean prose, not errors escaping — the failure direction is the safe one —
but the delta is real, the n is small (24), and the CI is wide. Follow-up: persist per-item
qualitative flag reasons so false-flag species can be audited the way fabrication species are.
Rogan-Gladen under this judge: pooled Se 95.2% / Sp 89.6%, corrected prevalence estimate 97.2%
(true 95.9%).
