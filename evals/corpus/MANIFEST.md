# Detection corpus — blinded mixed set (clean + error-injected)

Built by `python -m harness corpus` from the cached reconciled real briefs. One planted
error per item; item ids are content hashes. **Ground-truth labels live ONLY in
`labels.yaml` — an expert labeling blind must not open it.**

| class | items |
|---|---|
| clean | 8 |
| fabrication | 32 |
| omission | 43 |
| wrong_period | 37 |
| wrong_tag | 37 |
| wrong_value | 37 |
| **total** | **194** |

## Blind labeling protocol (expert)

1. Take `items/*.yaml` and the matching `factsheets/<ticker>.md` (XBRL ground truth).
2. For each item, record: is the brief clean or does it contain an error? If an error —
   which claim/sentence, and what kind (wrong number · wrong period · wrong tag ·
   material omission · unsupported assertion)?
3. Return the label sheet; score against `labels.yaml` (inter-rater vs machine —
   never the eval-of-the-eval).

## Machine detection

Run `python evals/detection.py` (deterministic surfaces, $0) or
`python evals/detection.py --judge real` (adds the LLM judge; budgeted).
