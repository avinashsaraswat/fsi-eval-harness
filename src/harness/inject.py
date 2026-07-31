"""Error-injection corpus: planted-error variants of real cached briefs (D5 build).

Why this exists: the eval-of-the-eval came back UNDEFINED (2026-07-26) — `self_grade` is
5.0 on every gold brief with zero variance, because the harness only emits claims it has
already reconciled against XBRL: it grades itself on a test it is engineered to ace. The
honest measurement is therefore *detection*, not correlation: plant known errors in real
briefs and measure whether the pipeline catches them (TPR) without flagging clean content
(TNR).

This module extends the single-claim adversarial demo (adversarial.py, red->green) into a
corpus builder. Discipline is unchanged: ONE planted error per item, one variable at a
time, so every detection verdict is unambiguous.

Error classes (per the D5 ruling, 2026-07-28):
  wrong_value   claim value + its narrative token scaled beyond tolerance -> the brief is
                internally consistent but wrong vs XBRL (a hallucinated number)
  wrong_period  right number cited under the wrong reporting period (citation integrity)
  wrong_tag     right number cited under the wrong us-gaap tag (citation integrity)
  omission      a material concept's claims AND their narrative sentences removed — the
                deterministic layer is blind to absence BY DESIGN; this class exists to
                measure that blindness honestly, and for expert labeling
  fabrication   an unsupported qualitative claim appended (species: phantom_citation /
                no_citation / overreach) — the LLM-judge detection surface

Blinding: item ids are content hashes; ground-truth labels are written to a SEPARATE
labels file the expert labeler never opens. The machine runner is not the blinded party.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal, Optional

from pydantic import BaseModel

from .adversarial import perturb_period, perturb_tag
from .facts import FactIndex
from .schema import Brief, FilingRef, NumericClaim, QualitativeClaim
from .verify import _NUM_RE, _SCALE, verify_claim

ErrorClass = Literal["clean", "wrong_value", "wrong_period", "wrong_tag", "omission", "fabrication"]

# Value perturbations cycle through graded severities (fractions, signed). The smallest is
# 2x the 0.5% reported tolerance — detection is tested near the boundary, not only on
# gross errors.
VALUE_DELTAS = (0.01, -0.02, 0.05, -0.10)

# Concepts whose absence from a signal brief is a material omission. Order fixes which
# get used when capped.
OMITTABLE = ("revenue", "net_income", "eps_diluted", "operating_cash_flow",
             "operating_income", "gross_profit")

FABRICATION_SPECIES = ("phantom_citation", "no_citation", "overreach_causal", "overreach_superlative")

MAX_SITES_PER_CLASS = 8   # per brief, for the numeric perturbation classes
MAX_OMISSIONS = 6         # per brief


class CorpusLabel(BaseModel):
    """Ground truth for one item — lives in labels.yaml, never inside the item file."""
    item_id: str
    ticker: str
    error_class: ErrorClass
    target_claim_id: Optional[str] = None    # the perturbed/planted claim (numeric classes, fabrication)
    target_concept: Optional[str] = None     # omission
    species: Optional[str] = None            # fabrication
    detail: str = ""                         # what was changed, exactly
    expected_surface: str = ""               # which detector SHOULD fire (omission: "none_by_design")
    judge_needed: bool = False               # does measuring this item require the LLM judge


class CorpusItem(BaseModel):
    """What the detector (and the blinded expert) sees: the brief variant, no labels."""
    item_id: str
    ticker: str
    accession: str
    form: str
    report_date: str
    brief: Brief


def _item_id(ticker: str, error_class: str, discriminator: str) -> str:
    return hashlib.sha256(f"{ticker}|{error_class}|{discriminator}".encode()).hexdigest()[:12]


def eligible_sites(brief: Brief, index: FactIndex) -> list[NumericClaim]:
    """Reported, single-fact claims that verify clean — the honest baselines to perturb
    (same criteria as the adversarial demo's target pick, all of them)."""
    return [c for c in brief.numeric_claims()
            if c.basis == "reported" and len(c.source_fact_ids) == 1
            and verify_claim(c, index).status == "pass"]


# ---------------------------------------------------------------------------
# wrong_value: perturb the claim AND its narrative token, keeping the brief
# internally consistent — the realistic hallucinated-number state.
# ---------------------------------------------------------------------------

def _find_value_token(text: str, value: float) -> Optional[re.Match]:
    """The numeric token in `text` that displays `value` (any scale), or None."""
    for m in _NUM_RE.finditer(text):
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if m.group(2):
            num *= _SCALE[m.group(2).strip()]
        if value and abs(num - abs(value)) / abs(value) <= 0.02:
            return m
    return None


def _rewrite_number(numeral: str, factor: float) -> str:
    """Scale a display numeral, preserving its decimal places and comma grouping."""
    plain = numeral.replace(",", "")
    decimals = len(plain.split(".")[1]) if "." in plain else 0
    scaled = float(plain) * factor
    return f"{scaled:,.{decimals}f}" if "," in numeral else f"{scaled:.{decimals}f}"


def perturb_value(claim: NumericClaim, delta: float) -> Optional[NumericClaim]:
    """Scale the claim's value by (1+delta) and rewrite the matching token in its sentence.
    Returns None when the sentence carries no recognizable token for the value (site skipped
    rather than shipped inconsistent)."""
    m = _find_value_token(claim.text, claim.value)
    if m is None:
        return None
    new_numeral = _rewrite_number(m.group(1), 1 + delta)
    new_text = claim.text[:m.start(1)] + new_numeral + claim.text[m.end(1):]
    return claim.model_copy(update={"value": claim.value * (1 + delta), "text": new_text})


def _replace_value_tokens(text: str, value: float, factor: float) -> tuple[str, int]:
    """Rewrite EVERY numeric token in `text` that displays `value` (any scale) by `factor`.
    Replacing all displays keeps the perturbed brief internally consistent even when a
    value is mentioned in more than one sentence. Returns (new_text, tokens_replaced)."""
    out, last, replaced = [], 0, 0
    for m in _NUM_RE.finditer(text):
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if m.group(2):
            num *= _SCALE[m.group(2).strip()]
        if not value or abs(num - abs(value)) / abs(value) > 0.02:
            continue
        out.append(text[last:m.start(1)])
        out.append(_rewrite_number(m.group(1), factor))
        last = m.end(1)
        replaced += 1
    out.append(text[last:])
    return "".join(out), replaced


def apply_value_injection(brief: Brief, claim: NumericClaim, delta: float) -> Optional[Brief]:
    """Perturb the claim's value and every narrative display of it. The claim's recorded
    sentence is often a paraphrase rather than a verbatim narrative substring, so the
    narrative is rewritten token-wise by value, never by sentence match."""
    perturbed = perturb_value(claim, delta)
    if perturbed is None:
        return None
    narrative, replaced = _replace_value_tokens(brief.narrative, claim.value, 1 + delta)
    if replaced == 0:
        return None
    claims = [perturbed if c.id == claim.id else c for c in brief.claims]
    return Brief(ticker=brief.ticker, narrative=narrative, claims=claims)


def apply_claim_swap(brief: Brief, old: NumericClaim, new: NumericClaim) -> Brief:
    """Replace one claim object (period/tag perturbations — prose untouched, the error
    lives in the citation metadata, exactly the adversarial demo's class)."""
    return Brief(ticker=brief.ticker, narrative=brief.narrative,
                 claims=[new if c.id == old.id else c for c in brief.claims])


# ---------------------------------------------------------------------------
# omission: remove a material concept — claims and their narrative sentences.
# ---------------------------------------------------------------------------

def apply_omission(brief: Brief, concept: str) -> Optional[Brief]:
    doomed = [c for c in brief.numeric_claims() if c.concept == concept]
    if not doomed:
        return None
    narrative = brief.narrative
    for c in doomed:
        if c.text and c.text in narrative:
            narrative = narrative.replace(c.text, "").replace("  ", " ")
    doomed_ids = {c.id for c in doomed}
    kept = [c for c in brief.claims if c.id not in doomed_ids]
    # Qualitative claims that cited the omitted numerics are KEPT — a judge flagging their
    # now-dangling citations is the one legitimate machine path to omission detection.
    return Brief(ticker=brief.ticker, narrative=narrative, claims=kept)


# ---------------------------------------------------------------------------
# fabrication: append an unsupported qualitative claim (the judge's surface).
# ---------------------------------------------------------------------------

def fabricate(brief: Brief, species: str) -> tuple[Brief, QualitativeClaim]:
    numerics = brief.numeric_claims()
    rev = next((c for c in numerics if c.concept == "revenue"), numerics[0])
    ni = next((c for c in numerics if c.concept == "net_income"), numerics[-1])
    fid = f"qx-{species}"
    if species == "phantom_citation":
        q = QualitativeClaim(id=fid, quality="materiality", supporting_claim_ids=["n99x"],
                             text="The results reflect broad-based improvement across every operating segment.")
    elif species == "no_citation":
        q = QualitativeClaim(id=fid, quality="tone", supporting_claim_ids=[],
                             text="Management's execution this quarter was flawless, with every strategic initiative ahead of schedule.")
    elif species == "overreach_causal":
        q = QualitativeClaim(id=fid, quality="why_it_matters", supporting_claim_ids=[rev.id],
                             text="This revenue performance reflects decisive market-share gains taken directly from the company's key competitors.")
    else:  # overreach_superlative
        q = QualitativeClaim(id=fid, quality="materiality",
                             supporting_claim_ids=[rev.id, ni.id] if ni.id != rev.id else [rev.id],
                             text="These results are the strongest in the company's history and the best in its industry this quarter.")
    narrative = brief.narrative.rstrip() + "\n\n" + q.text
    return Brief(ticker=brief.ticker, narrative=narrative, claims=[*brief.claims, q]), q


# ---------------------------------------------------------------------------
# corpus assembly for one brief
# ---------------------------------------------------------------------------

def build_items_for_brief(brief: Brief, index: FactIndex, filing: FilingRef,
                          ) -> tuple[list[CorpusItem], list[CorpusLabel]]:
    """All corpus items derived from one clean reconciled brief: 1 clean + up to
    MAX_SITES_PER_CLASS per numeric class + up to MAX_OMISSIONS + one per fabrication
    species. Deterministic — no randomness, ids are content hashes."""
    items: list[CorpusItem] = []
    labels: list[CorpusLabel] = []

    def add(error_class: ErrorClass, discriminator: str, b: Brief, **label_kw) -> None:
        iid = _item_id(filing.ticker, error_class, discriminator)
        items.append(CorpusItem(item_id=iid, ticker=filing.ticker, accession=filing.accession,
                                form=filing.form, report_date=filing.report_date, brief=b))
        labels.append(CorpusLabel(item_id=iid, ticker=filing.ticker, error_class=error_class, **label_kw))

    add("clean", "base", brief, expected_surface="none", judge_needed=True,
        detail="unmodified reconciled brief")

    sites = eligible_sites(brief, index)[:MAX_SITES_PER_CLASS]
    for i, site in enumerate(sites):
        delta = VALUE_DELTAS[i % len(VALUE_DELTAS)]
        b = apply_value_injection(brief, site, delta)
        if b is not None:
            add("wrong_value", f"{site.id}:{delta}", b, target_claim_id=site.id,
                expected_surface="verifier:value_mismatch",
                detail=f"{site.concept} scaled by {1 + delta:+.2f}x (claim + narrative token)")
        b = apply_claim_swap(brief, site, perturb_period(site))
        add("wrong_period", site.id, b, target_claim_id=site.id,
            expected_surface="verifier:wrong_period",
            detail=f"{site.concept} period shifted one year back")
        b = apply_claim_swap(brief, site, perturb_tag(site))
        add("wrong_tag", site.id, b, target_claim_id=site.id,
            expected_surface="verifier:wrong_tag",
            detail=f"{site.concept} re-cited under the wrong us-gaap tag")

    present = [k for k in OMITTABLE if any(c.concept == k for c in brief.numeric_claims())]
    for concept in present[:MAX_OMISSIONS]:
        b = apply_omission(brief, concept)
        if b is not None:
            add("omission", concept, b, target_concept=concept, judge_needed=True,
                expected_surface="none_by_design",
                detail=f"all {concept} claims + their narrative sentences removed")

    for species in FABRICATION_SPECIES:
        b, q = fabricate(brief, species)
        add("fabrication", species, b, target_claim_id=q.id, species=species, judge_needed=True,
            expected_surface="judge:unsupported",
            detail=f"appended unsupported qualitative claim ({species})")

    return items, labels
