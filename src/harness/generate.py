"""Brief generators: ApiGenerator (provider-backed, cached, budgeted) and MockGenerator ($0).

The generator's ONLY numeric input is the rendered fact sheet. The standing rule in the
prompt: use only fact_ids from the sheet; never introduce numbers from memory.
The provider seam lives in providers.py; this module never talks to a vendor SDK.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .config import CACHE_DIR, GEN_MODEL
from .facts import FactIndex
from .schema import Brief, FilingRef, NumericClaim, Period, QualitativeClaim


@dataclass
class FeedbackBlock:
    """Structured corrections fed back to the generator on reconciliation failure."""
    corrections: list[str] = field(default_factory=list)

    def render(self) -> str:
        rule = ("Standing rule: use ONLY fact_ids from the fact sheet; do not introduce "
                "numbers from memory; correct or remove every claim listed below.")
        return rule + "\n\n" + "\n".join(f"- {c}" for c in self.corrections)


class Generator(Protocol):
    name: str

    def generate(self, fact_sheet: str, index: FactIndex, filing: FilingRef,
                 feedback: Optional[FeedbackBlock]) -> tuple[Brief, dict]:
        """Returns (brief, usage) where usage = {model, input_tokens, output_tokens, cache_hit}."""
        ...


# --------------------------------------------------------------------------- #
# API-backed (provider seam: Gemini default, Anthropic behind the same interface)
# --------------------------------------------------------------------------- #

_SYSTEM = """You are a sell-side-quality financial analyst. Write a curated signal brief for the
given SEC filing using ONLY the fact sheet provided — never numbers from memory.

Return a JSON object matching this schema exactly:
{
  "ticker": str,
  "narrative": str,          // analyst-memo markdown, 250-400 words, every number backed by a claim
  "claims": [
    {"id": "c1", "kind": "numeric", "text": <the narrative sentence containing this number>,
     "concept": <fact sheet concept>, "gaap_tag": <fact sheet tag>,
     "period": {"type": "instant"|"duration", "start": <ISO or null>, "end": <ISO>, "fiscal_label": str},
     "unit": "USD"|"USD/share"|"shares"|"pct", "value": float,
     "basis": "reported"|"derived", "source_fact_ids": ["f1", ...],
     "derivation": <null, or an arithmetic expression over fact_ids like "(f1 - f2) / f2 * 100">},
    {"id": "c8", "kind": "qualitative", "text": str,
     "quality": "materiality"|"tone"|"why_it_matters", "supporting_claim_ids": ["c1", ...]}
  ]
}

Rules:
- Every number appearing in the narrative MUST have a numeric claim; claim.period must copy the
  cited fact's exact dates; derived values (YoY %, margins) MUST carry a derivation over fact_ids.
- 6-10 numeric claims, 2-4 qualitative claims. No segment-level numbers (not in the sheet).
- Output ONLY the JSON object."""


class ApiGenerator:
    def __init__(self, client=None, model: str = GEN_MODEL, ledger=None):
        from .config import PROVIDER
        from .providers import make_client
        self.client = client if client is not None else make_client(PROVIDER)
        self.name = self.client.name
        self.model = model
        self.ledger = ledger

    def generate(self, fact_sheet: str, index: FactIndex, filing: FilingRef,
                 feedback: Optional[FeedbackBlock]) -> tuple[Brief, dict]:
        user = fact_sheet
        if feedback:
            user += "\n\n## Corrections from the deterministic verifier (previous attempt)\n" + feedback.render()
        raw, usage = cached_call(self.client, model=self.model, system=_SYSTEM, user=user,
                                 max_tokens=8000, ledger=self.ledger)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        try:
            return Brief.model_validate_json(text), usage
        except ValueError:
            # Gemini JSON mode can emit raw control chars (literal newlines) inside strings;
            # json's non-strict decoder accepts them where the strict validator refuses.
            return Brief.model_validate(json.loads(text, strict=False)), usage


def cached_call(client, *, model: str, system: str, user: str, max_tokens: int, ledger=None) -> tuple[str, dict]:
    """Disk-cached provider call. Key = sha256(model, system, user, max_tokens).

    The key is provider-agnostic by construction — the model id disambiguates — so
    prototyping-era cache entries replay unchanged regardless of the active provider.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(
        {"model": model, "system": system, "user": user, "max_tokens": max_tokens},
        sort_keys=True).encode()).hexdigest()[:32]
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        stored = json.loads(path.read_text())
        if ledger is not None:
            ledger.note_cache_hit()
        return stored["text"], {"model": model, "input_tokens": 0, "output_tokens": 0, "cache_hit": True}

    if ledger is not None:
        ledger.precheck()
    text, u = client.complete(model=model, system=system, user=user, max_tokens=max_tokens)
    usage = {"model": model, "input_tokens": u["input_tokens"],
             "output_tokens": u["output_tokens"], "cache_hit": False}
    if ledger is not None:
        ledger.add(model, usage["input_tokens"], usage["output_tokens"])
    path.write_text(json.dumps({"text": text, "usage": usage}))
    return text, usage


# --------------------------------------------------------------------------- #
# Mock
# --------------------------------------------------------------------------- #

class MockGenerator:
    """Deterministic template generator with three injected faults on attempt 0.

    Fault 1: first reported value perturbed +3%          -> value_mismatch
    Fault 2: second claim's period end shifted            -> wrong_period
    Fault 3: narrative gains an unclaimed number          -> unclaimed_numeric
    Attempt 1 (feedback present): clean brief             -> reconciles
    """
    name = "mock"

    def generate(self, fact_sheet: str, index: FactIndex, filing: FilingRef,
                 feedback: Optional[FeedbackBlock]) -> tuple[Brief, dict]:
        inject = feedback is None
        claims: list = []
        sentences: list[str] = []
        current = [f for f in index.facts if f.end == filing.report_date]
        for i, f in enumerate(current, start=1):
            value = f.value
            period = Period(type=f.period_type, start=f.start, end=f.end, fiscal_label=f.period_label)
            if inject and i == 1:
                value = round(f.value * 1.03, 4)
            if inject and i == 2:
                shifted = f.end[:8] + ("15" if f.end[8:] != "15" else "14")
                period = Period(type=f.period_type, start=f.start, end=shifted, fiscal_label=f.period_label)
            unit = "USD/share" if f.unit == "USD/shares" else ("shares" if f.unit == "shares" else "USD")
            text = f"{filing.ticker} reported {f.concept.replace('_', ' ')} of {value:,.2f} {unit} for the period ending {f.end}."
            sentences.append(text)
            claims.append(NumericClaim(id=f"c{i}", text=text, concept=f.concept, gaap_tag=f.gaap_tag,
                                       period=period, unit=unit, value=value, basis="reported",
                                       source_fact_ids=[f.fact_id]))
        qid = len(claims) + 1
        qual = QualitativeClaim(id=f"c{qid}", text="Overall the filing signals steady operating execution.",
                                quality="tone", supporting_claim_ids=[claims[0].id] if claims else [])
        claims.append(qual)
        narrative = f"## {filing.company_name} ({filing.ticker}) — {filing.form} signal brief\n\n" + " ".join(sentences)
        if inject:
            narrative += " Management also highlighted 123,456,789 of unrelated momentum."
        narrative += f"\n\n{qual.text}"
        brief = Brief(ticker=filing.ticker, narrative=narrative, claims=claims)
        return brief, {"model": "mock", "input_tokens": 0, "output_tokens": 0, "cache_hit": False}
