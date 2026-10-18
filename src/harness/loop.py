"""Orchestrator: generate -> verify -> feedback -> regenerate. Stop conditions per the contract.

RECONCILED  all numeric claims pass, narrative fully covered, >= MIN_CLAIMS survive -> judge runs.
PARTIAL     retry limit hit, >= MIN_CLAIMS pass: failing claims stripped (logged), affected
            sentences removed deterministically, emitted with status flag.
FAILED      retry limit hit, < MIN_CLAIMS pass: no brief emitted; drop-log + metrics still are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import CONCEPTS, MAX_RETRIES
from .edgar import company_facts, latest_filing
from .facts import build_fact_index, render_fact_sheet
from .generate import FeedbackBlock, Generator
from .judge import Judge
from .metrics import brief_metrics, decide_status
from .schema import Brief, BriefMetrics, DropEntry, FilingRef, JudgeVerdict, QualitativeClaim, Verdict
from .verify import narrative_coverage, narrative_coverage_audit, verify_claim


@dataclass
class BriefResult:
    ticker: str
    filing: FilingRef
    status: str
    brief: Optional[Brief]
    verdicts: list[Verdict]
    judge_verdicts: list[JudgeVerdict]
    drops: list[DropEntry]
    metrics: BriefMetrics
    coverage: dict[str, str]


def run_ticker(ticker: str, generator: Generator, judge: Judge, run_id: str,
               max_retries: int = MAX_RETRIES, ledger=None) -> BriefResult:
    # Snapshot the shared cost ledger so this brief's own token/$ spend can be recorded
    # per-brief (Finding 3): generator + judge for this ticker both bill to `ledger`, so the
    # delta across run_ticker is exactly this brief's cost. Cache-served briefs delta to $0.
    base_in = ledger.tokens_in if ledger is not None else 0
    base_out = ledger.tokens_out if ledger is not None else 0
    base_spend = ledger.spend if ledger is not None else 0.0

    filing = latest_filing(ticker)
    index, coverage = build_fact_index(company_facts(ticker), filing)
    drops: list[DropEntry] = []

    def log(attempt: int, stage: str, item: str, action: str, reason: str, detail: str = "") -> None:
        drops.append(DropEntry(run_id=run_id, ticker=ticker, attempt=attempt, stage=stage,  # type: ignore[arg-type]
                               item_ref=item, action=action, reason_code=reason, detail=detail))

    for key, tag in coverage.items():
        if tag == "MISSING":
            sev = "concept_missing_optional" if CONCEPTS[key].optional else "concept_missing"
            log(0, "fact_selection", key, "dropped", sev, "no tag in synonym chain resolved for the target period")
    for f in index.facts:
        log(0, "fact_selection", f.fact_id, "kept",
            "fallback_accession" if f.fallback_accession else "kept",
            f"{f.concept} {f.period_label}" + (" (value from a later filing)" if f.fallback_accession else ""))

    fact_sheet = render_fact_sheet(index, filing)
    feedback: Optional[FeedbackBlock] = None
    brief: Optional[Brief] = None
    verdicts: list[Verdict] = []
    judge_verdicts: list[JudgeVerdict] = []
    uncovered: list[str] = []
    coverage_pct = 0.0
    reconciled_at: Optional[int] = None

    for attempt in range(max_retries + 1):
        brief, _usage = generator.generate(fact_sheet, index, filing, feedback)
        verdicts = [verify_claim(c, index) for c in brief.numeric_claims()]
        coverage_pct, uncovered, grounded = narrative_coverage_audit(brief, index)
        failing = [v for v in verdicts if v.status != "pass"]

        for v in verdicts:
            log(attempt, "claim_verify", v.claim_id, "kept" if v.status == "pass" else "dropped",
                v.reason, v.detail)
        for tok in uncovered:
            log(attempt, "narrative_coverage", tok, "dropped", "unclaimed_numeric",
                "narrative number traceable to no fact, claim, or typed derivation")
        for tok, provenance in grounded:
            log(attempt, "narrative_coverage", tok, "kept", "grounded", provenance)

        # Judge runs only once the numbers are clean — a qualitative claim can't be judged
        # against unverified figures. Judge failures then get the SAME regeneration budget
        # as numeric ones: fix the numbers first, then fix the judgment (priority order).
        numeric_clean = not failing and not uncovered
        judge_verdicts = []
        judge_unsupported: list[JudgeVerdict] = []
        if numeric_clean:
            judge_verdicts = judge.judge(brief, verdicts)
            for jv in judge_verdicts:
                log(attempt, "judge", jv.claim_id, "kept" if jv.supported else "dropped",
                    "kept" if jv.supported else "judge_unsupported", jv.reason)
            judge_unsupported = [jv for jv in judge_verdicts if not jv.supported]

        if numeric_clean and not judge_unsupported:
            reconciled_at = attempt
            break
        if attempt < max_retries:
            feedback = _build_feedback(brief, verdicts, uncovered, index)
            for jv in judge_unsupported:
                feedback.corrections.append(
                    f"{jv.claim_id} (qualitative): {jv.reason} Rewrite the statement to stay within "
                    f"what the verified numbers show, or remove it — do not overreach beyond the figures.")
            for c in feedback.corrections:
                log(attempt, "claim_verify", "feedback", "corrected", "regenerate", c)
        else:
            log(attempt, "retry_exhaustion", "brief", "dropped", "retry_limit",
                f"{len(failing)} claims failing + {len(uncovered)} uncovered + "
                f"{len(judge_unsupported)} judge-unsupported after {attempt + 1} attempts")

    assert brief is not None
    passing = sum(1 for v in verdicts if v.status == "pass")
    fully_reconciled = reconciled_at is not None
    status = decide_status(passing, not fully_reconciled, fully_reconciled)

    if status == "PARTIAL":
        brief = _strip_failing(brief, verdicts, log)
        verdicts = [v for v in verdicts if v.status == "pass"]
        # If exhaustion hit while the numbers were still dirty, the judge never saw this
        # brief — a PARTIAL must not ship unjudged qualitative claims, so judge it now
        # against the surviving verified claims.
        if not judge_verdicts:
            judge_verdicts = judge.judge(brief, verdicts)
            for jv in judge_verdicts:
                log(-1, "judge", jv.claim_id, "kept" if jv.supported else "dropped",
                    "kept" if jv.supported else "judge_unsupported", jv.reason)
        # A judge-rejected qualitative claim must NOT survive into the emitted prose — the
        # retry budget ran out before it could be regenerated, so strip it deterministically.
        unsupported_ids = {jv.claim_id for jv in judge_verdicts if not jv.supported}
        if unsupported_ids:
            brief = _strip_qualitative(brief, unsupported_ids, log)
        coverage_pct, uncovered = narrative_coverage(brief, index)
        unsafe_prose = any(d.reason_code == "unsafe_partial_prose" for d in drops)
        if uncovered or unsafe_prose:
            status = "FAILED"
            log(-1, "retry_exhaustion", "brief", "dropped", "unsafe_partial",
                "remaining prose cannot be verified or safely removed")

    attempts_used = (reconciled_at + 1) if reconciled_at is not None else max_retries + 1
    m = brief_metrics(ticker, status, attempts_used, brief, verdicts, judge_verdicts,
                      drops, coverage_pct, reconciled_at)
    if ledger is not None:
        m.tokens_in = ledger.tokens_in - base_in
        m.tokens_out = ledger.tokens_out - base_out
        m.cost_usd = round(ledger.spend - base_spend, 6)
    emit = brief if status != "FAILED" else None
    return BriefResult(ticker=ticker, filing=filing, status=status, brief=emit, verdicts=verdicts,
                       judge_verdicts=judge_verdicts, drops=drops, metrics=m, coverage=coverage)


def _build_feedback(brief: Brief, verdicts: list[Verdict], uncovered: list[str], index) -> FeedbackBlock:
    fb = FeedbackBlock()
    claims = {c.id: c for c in brief.numeric_claims()}
    for v in verdicts:
        if v.status == "pass":
            continue
        c = claims[v.claim_id]
        if v.actual is not None:
            fb.corrections.append(
                f"{v.claim_id}: you stated {c.concept} = {v.claimed:,.4g}; ground truth for "
                f"{c.period.start or 'instant'}→{c.period.end} is {v.actual:,.4g} "
                f"({v.reason}). Correct the value/period or remove the claim. [{v.detail}]")
        else:
            fb.corrections.append(f"{v.claim_id}: {v.reason} — {v.detail}. Fix the citation or remove the claim.")
    for tok in uncovered:
        fb.corrections.append(f"narrative: number {tok!r} has no backing claim — claim it or remove it.")
    return fb


def _strip_failing(brief: Brief, verdicts: list[Verdict], log) -> Brief:
    """Deterministically remove failing claims and their narrative sentences."""
    failing_ids = {v.claim_id for v in verdicts if v.status != "pass"}
    narrative = brief.narrative
    for c in brief.numeric_claims():
        if c.id in failing_ids:
            if c.text and c.text in narrative:
                narrative = narrative.replace(c.text, "").replace("  ", " ")
                log(-1, "retry_exhaustion", c.id, "dropped", "dropped_after_retries", c.text[:120])
            else:
                log(-1, "retry_exhaustion", c.id, "dropped", "unsafe_partial_prose",
                    "failing numeric claim has no exact sentence match")
    kept = [c for c in brief.claims if c.id not in failing_ids]
    return Brief(ticker=brief.ticker, narrative=narrative, claims=kept)


def _strip_qualitative(brief: Brief, unsupported_ids: set[str], log) -> Brief:
    """Remove judge-rejected qualitative claims AND their narrative sentences, so no
    statement the judge refused to support survives into the emitted brief. Best-effort on
    the prose (exact-match on the claim's recorded sentence); the claim is always dropped."""
    narrative = brief.narrative
    for q in brief.qualitative_claims():
        if q.id in unsupported_ids:
            if q.text and q.text in narrative:
                narrative = narrative.replace(q.text, "").replace("  ", " ")
                log(-1, "judge", q.id, "dropped", "judge_unsupported_stripped", q.text[:120])
            else:
                log(-1, "judge", q.id, "dropped", "unsafe_partial_prose",
                    "unsupported qualitative claim has no exact sentence match")
    kept = [c for c in brief.claims
            if not (isinstance(c, QualitativeClaim) and c.id in unsupported_ids)]
    return Brief(ticker=brief.ticker, narrative=narrative, claims=kept)
