# Gold-set grading rubric

Each graded brief gets one YAML record in `briefs/` (see `template.yaml`). Four dimensions, 1–5:

| Dimension | Graded by | Definition |
|---|---|---|
| `factual_precision` | **harness (auto)** | Verifier-derived: share of numeric claims passing deterministic verification, scaled 1–5 (5 = 100% pass, 1 = <60%). |
| `completeness` | **harness (auto)** | Narrative-coverage + concept-coverage derived: did the brief use the material facts available? |
| `materiality_judgment` | human expert | Did the brief surface what actually matters in this filing, and rank it sensibly? |
| `clarity` | human expert | Would a busy FSI operator get the signal in one read? |

`self_grade` = mean of the two auto dimensions (what the harness believes about itself).
`expert_grade` = mean of all four after a human grades the two expert dimensions and
sanity-checks the auto ones (experts may override auto scores; overrides are the point).

## The correlation claim — retracted on this population (2026-07-26)

`python evals/correlation.py` reports the Spearman correlation between `self_grade` (the harness's
deterministic self-assessment) and the **expert-only** dimensions it cannot see —
`mean(materiality, clarity)` — once ≥5 expert-graded records exist. **It cannot produce a number on
the current gold set.** Every record in `briefs/` comes from a brief the harness *shipped*, and the
harness ships only what it already reconciled, so `self_grade` is 5.0 across the board with zero
variance. Spearman against a constant is undefined (the rank denominator is 0, and the function
returns `nan` by design). The measurement that replaced it is detection against planted errors —
see `docs/DETECTION.md`.

Two separate problems, worth keeping straight:

1. **The statistic was wrong, and was fixed.** It used to headline
   `Spearman(self_grade, expert_grade)`. `expert_grade` averages all four dimensions, so it embeds
   the two auto dims that constitute `self_grade` (`expert_grade == 0.5·self_grade +
   0.5·expert_only`) — self-correlated by construction, incapable of cleanly failing, and it
   inflates rho. It now headlines the expert-only pair; the full-grade number is still printed,
   labelled as inflated reference.
2. **The population is degenerate, and is not fixed.** A correct statistic over briefs that all
   score 5.0 still yields nothing. Answering the original question needs records with spread —
   which means grading briefs the harness **rejected** (PARTIAL and FAILED runs) alongside the ones
   it shipped. That is an open design question, not a task on this worksheet.

**Grade the set anyway.** The two expert dimensions are independently useful: they say whether the
briefs are any good, which the verifier metrics cannot. Just don't expect a correlation out of it.
