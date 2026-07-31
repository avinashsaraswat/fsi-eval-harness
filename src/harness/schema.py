"""Pydantic schemas: facts, claims, briefs, verdicts, drop-log, metrics."""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class FilingRef(BaseModel):
    ticker: str
    cik: str
    form: str                      # "10-Q" | "10-K"
    accession: str
    filing_date: str
    report_date: str
    company_name: str


class Fact(BaseModel):
    fact_id: str                   # f1, f2, ...
    concept: str                   # whitelist key
    gaap_tag: str                  # the resolved us-gaap tag
    unit: str
    period_type: Literal["instant", "duration"]
    start: Optional[str] = None    # ISO date; None for instant
    end: str
    duration_days: Optional[int] = None
    period_label: str              # e.g. "Q FY2025-Q2" — display only, matching is by dates
    value: float
    accession: str
    form: str
    fallback_accession: bool = False  # True when the value came from a later filing than the target


class Period(BaseModel):
    type: Literal["instant", "duration"]
    start: Optional[str] = None
    end: str
    fiscal_label: str = ""


class NumericClaim(BaseModel):
    id: str
    kind: Literal["numeric"] = "numeric"
    text: str                      # the brief sentence containing this number
    concept: str
    gaap_tag: str
    period: Period
    unit: Literal["USD", "USD/share", "shares", "pct"]
    value: float
    basis: Literal["reported", "derived"]
    source_fact_ids: list[str]
    derivation: Optional[str] = None  # e.g. "(f3 - f4) / f4 * 100" over fact_ids


class QualitativeClaim(BaseModel):
    id: str
    kind: Literal["qualitative"] = "qualitative"
    text: str
    quality: Literal["materiality", "tone", "why_it_matters"]
    supporting_claim_ids: list[str] = Field(default_factory=list)


Claim = Union[NumericClaim, QualitativeClaim]


class Brief(BaseModel):
    ticker: str
    narrative: str                 # markdown; every number must be backed by a NumericClaim
    claims: list[Claim]

    def numeric_claims(self) -> list[NumericClaim]:
        return [c for c in self.claims if isinstance(c, NumericClaim)]

    def qualitative_claims(self) -> list[QualitativeClaim]:
        return [c for c in self.claims if isinstance(c, QualitativeClaim)]


class Verdict(BaseModel):
    claim_id: str
    status: Literal["pass", "fail", "unverifiable"]
    reason: Literal[
        "ok", "value_mismatch", "wrong_period", "wrong_tag",
        "bad_derivation", "unknown_fact_id", "unresolvable",
    ]
    claimed: Optional[float] = None
    actual: Optional[float] = None
    delta_pct: Optional[float] = None
    tolerance: Optional[str] = None
    detail: str = ""


class JudgeVerdict(BaseModel):
    claim_id: str
    supported: bool
    reason: str


class DropEntry(BaseModel):
    run_id: str
    ticker: str
    attempt: int
    stage: Literal["fact_selection", "claim_verify", "narrative_coverage", "judge", "retry_exhaustion"]
    item_ref: str
    action: Literal["kept", "dropped", "corrected"]
    reason_code: str
    detail: str = ""


class BriefMetrics(BaseModel):
    ticker: str
    status: Literal["RECONCILED", "PARTIAL", "FAILED"]
    attempts: int
    claims_total: int
    numeric_claims: int
    qualitative_claims: int
    routed_deterministic: int
    routed_llm_judge: int
    routing_share_deterministic: float
    verified_pass: int
    failed_final: int
    dropped_by_reason: dict[str, int]
    retries_to_reconcile: Optional[int]
    narrative_coverage_pct: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
