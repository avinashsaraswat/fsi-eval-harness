"""LLM judge for QUALITATIVE claims only (materiality / tone / why-it-matters).

Numeric claims never reach this module — the routing wall lives in loop.py and the
share of claims on each side is measured, not asserted.
"""

from __future__ import annotations

import json
from typing import Protocol

from .config import JUDGE_MODEL
from .schema import Brief, JudgeVerdict, NumericClaim, Verdict


class Judge(Protocol):
    name: str

    def judge(self, brief: Brief, verdicts: list[Verdict]) -> list[JudgeVerdict]: ...


_SYSTEM = """You are a strict financial editor. For each qualitative claim, decide whether it is
supported by the VERIFIED numeric claims it cites (values given). Unsupported = speculation,
overreach beyond the cited numbers, or citing failed/absent claims.
Return ONLY a JSON array: [{"claim_id": str, "supported": bool, "reason": str}, ...]"""


class ApiJudge:
    def __init__(self, client=None, model: str = JUDGE_MODEL, ledger=None):
        from .config import PROVIDER
        from .providers import make_client
        self.client = client if client is not None else make_client(PROVIDER)
        self.name = self.client.name
        self.model = model
        self.ledger = ledger

    def judge(self, brief: Brief, verdicts: list[Verdict]) -> list[JudgeVerdict]:
        from .generate import cached_call
        quals = brief.qualitative_claims()
        if not quals:
            return []
        passed = {v.claim_id for v in verdicts if v.status == "pass"}
        numeric = {c.id: c for c in brief.numeric_claims()}
        payload = {
            "verified_numeric_claims": [
                {"id": c.id, "text": c.text, "value": c.value, "unit": c.unit}
                for cid, c in numeric.items() if cid in passed
            ],
            "qualitative_claims": [
                {"id": q.id, "text": q.text, "quality": q.quality,
                 "supporting_claim_ids": q.supporting_claim_ids} for q in quals
            ],
        }
        raw, _ = cached_call(self.client, model=self.model, system=_SYSTEM,
                             user=json.dumps(payload, indent=1), max_tokens=2000, ledger=self.ledger)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        # strict=False: tolerate raw control chars inside strings (Gemini JSON-mode quirk)
        return [JudgeVerdict.model_validate(v) for v in json.loads(text, strict=False)]


class MockJudge:
    """Rule stub: supported iff the claim cites >=1 supporting id and all cited ids passed."""
    name = "mock"

    def judge(self, brief: Brief, verdicts: list[Verdict]) -> list[JudgeVerdict]:
        passed = {v.claim_id for v in verdicts if v.status == "pass"}
        out = []
        for q in brief.qualitative_claims():
            ok = bool(q.supporting_claim_ids) and all(cid in passed for cid in q.supporting_claim_ids)
            out.append(JudgeVerdict(claim_id=q.id, supported=ok,
                                    reason="cited claims verified" if ok else "cites unverified or no claims"))
        return out
