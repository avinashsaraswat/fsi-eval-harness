"""Adversarial citation-integrity demo on REAL data.

Why this exists: on the clean real filing set the deterministic verifier never fires
`wrong_tag` / `wrong_period` — the model cites its numbers correctly — so that
citation-integrity guarantee is only *proven* load-bearing under `--mock`. This takes a
REAL Opus-generated brief (served from the local cache -> $0) over REAL XBRL facts, injects
one period error and one tag error into a claim the model got right, and shows the SAME
verifier flip that claim RED, then GREEN again once the citation is corrected. One variable
changes at a time, so the red<->green is unambiguous.

The injected errors are the two "right number, wrong citation" failures the verifier is
built to catch: a value cited under the wrong reporting period, and a value cited under the
wrong us-gaap tag. The model did not actually make these — we make them, to prove the gate
fires on real data, not just on synthetic mock faults.

Budget: default max-spend is $0, so it runs only if the generation is already cached and
aborts cleanly otherwise (no surprise spend). Pass --allow-spend to permit one live Opus
generation when the brief is not cached.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tabulate import tabulate

from .config import OUT_DIR
from .edgar import company_facts, latest_filing
from .facts import FactIndex, build_fact_index, render_fact_sheet
from .metrics import BudgetExceeded, Ledger
from .schema import NumericClaim, Verdict
from .verify import verify_claim


def perturb_period(claim: NumericClaim) -> NumericClaim:
    """Shift the claim's period back one year — the number and tag stay right, only the
    cited period is wrong. Verifier must return `wrong_period`."""
    def back_one_year(iso: str | None) -> str | None:
        return f"{int(iso[:4]) - 1}{iso[4:]}" if iso else iso

    period = claim.period.model_copy(update={
        "end": back_one_year(claim.period.end),
        "start": back_one_year(claim.period.start),
    })
    return claim.model_copy(update={"period": period})


def perturb_tag(claim: NumericClaim) -> NumericClaim:
    """Re-label the claim under a different us-gaap tag — the number and period stay right,
    only the cited tag is wrong. Verifier must return `wrong_tag`."""
    wrong = "NetIncomeLoss" if claim.gaap_tag != "NetIncomeLoss" else "Revenues"
    return claim.model_copy(update={"gaap_tag": wrong})


def _pick_target(brief, index: FactIndex) -> NumericClaim | None:
    """First reported, single-fact claim that verifies clean — the honest baseline to perturb."""
    for c in brief.numeric_claims():
        if c.basis == "reported" and len(c.source_fact_ids) == 1 and verify_claim(c, index).status == "pass":
            return c
    return None


def _row(label: str, v: Verdict) -> list[str]:
    mark = "🟢 GREEN" if v.status == "pass" else "🔴 RED"
    return [label, mark, v.status, v.reason, (v.detail or "")[:88]]


def demonstrate(ticker: str, allow_spend: bool, client=None, model: str | None = None) -> int:
    filing = latest_filing(ticker)
    index, _cov = build_fact_index(company_facts(ticker), filing)
    fact_sheet = render_fact_sheet(index, filing)

    from .config import GEN_MODEL
    from .generate import ApiGenerator

    model = model or GEN_MODEL
    ledger = Ledger(max_spend=1.0 if allow_spend else 0.0)
    gen = ApiGenerator(client, model=model, ledger=ledger)
    try:
        brief, usage = gen.generate(fact_sheet, index, filing, None)
    except BudgetExceeded:
        print(
            f"[adversarial] {ticker}: this generation is not in the local cache for {model}, so a "
            f"live call would be needed.\n  Re-run with --allow-spend once real spend is approved "
            f"(one {model} generation, well under $1) — or --provider anthropic to replay the "
            f"prototyping-era cache.",
            file=sys.stderr,
        )
        return 3

    provenance = "local cache ($0)" if usage.get("cache_hit") else f"LIVE {model} call (${ledger.spend:.4f})"

    target = _pick_target(brief, index)
    if target is None:
        print(f"[adversarial] {ticker}: no clean reported single-fact claim to perturb.", file=sys.stderr)
        return 4
    fact = index.by_id[target.source_fact_ids[0]]

    green = verify_claim(target, index)
    red_period = verify_claim(perturb_period(target), index)
    red_tag = verify_claim(perturb_tag(target), index)
    green_again = verify_claim(target, index)  # correction = restore the citation; target is unmutated

    ok = (
        green.status == "pass"
        and (red_period.status, red_period.reason) == ("fail", "wrong_period")
        and (red_tag.status, red_tag.reason) == ("fail", "wrong_tag")
        and green_again.status == "pass"
    )

    table = tabulate(
        [
            _row("baseline (model's real claim)", green),
            _row("inject wrong period", red_period),
            _row("inject wrong tag", red_tag),
            _row("correct the citation", green_again),
        ],
        headers=["state", "gate", "status", "reason", "detail"],
        tablefmt="github",
    )

    artifact = (
        f"# Adversarial citation-integrity demo — {filing.company_name} ({ticker})\n\n"
        f"Filing: **{filing.form} {filing.accession}**, period ending {filing.report_date}. "
        f"Brief provenance: {provenance}. Facts: real XBRL from data.sec.gov.\n\n"
        f"The claim under test is the model's own, and it reconciles against XBRL ground truth "
        f"(🟢). We then change exactly one thing at a time — first the cited period, then the "
        f"cited us-gaap tag — leaving the (correct) number untouched. The deterministic verifier "
        f"catches each as a citation failure (🔴), and restoring the citation returns it to 🟢. "
        f"This proves on **real data** what the mock faults prove synthetically: a right number "
        f"on the wrong citation does not pass the gate.\n\n"
        f"**Claim under test — `{target.id}`:** {target.concept} / `{target.gaap_tag}`, "
        f"period {target.period.start}→{target.period.end}, value {target.value:,.4g} "
        f"(cites fact `{fact.fact_id}`, XBRL truth {fact.value:,.4g} for "
        f"{fact.start}→{fact.end}).\n\n"
        f"{table}\n\n"
        f"Injected period error: `{target.period.end}` → `{perturb_period(target).period.end}` "
        f"(one year back). Injected tag error: `{target.gaap_tag}` → "
        f"`{perturb_tag(target).gaap_tag}`. Neither changed the number.\n\n"
        f"**Result: {'PASS — the gate is load-bearing on real data.' if ok else 'UNEXPECTED — see verdicts above.'}**\n"
    )

    out = OUT_DIR / "adversarial"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{ticker}.md"
    path.write_text(artifact)

    print(f"[adversarial] {ticker}: brief from {provenance}; claim {target.id} "
          f"({target.concept}/{target.gaap_tag})")
    print(table)
    print(f"\nartifact: {path}")
    print("RESULT:", "PASS — gate load-bearing on real data" if ok else "UNEXPECTED (verdicts above)")
    return 0 if ok else 5
