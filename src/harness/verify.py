"""Pure deterministic verifier: NumericClaim x FactIndex -> Verdict. No I/O, no LLM.

Routing wall: numeric claims are verified here or DROPPED (unverifiable) — they never
fall through to the LLM judge.
"""

from __future__ import annotations

import ast
import math
import re
from typing import Optional

from .config import ABS_TOL_DERIVED_PCT, ABS_TOL_EPS, REL_TOL_REPORTED
from .facts import FactIndex
from .schema import Brief, NumericClaim, Verdict

_NUM_RE = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{3,})(\s*(?:billion|million|trillion|B|M|T))?%?")
_SCALE = {"billion": 1e9, "B": 1e9, "million": 1e6, "M": 1e6, "trillion": 1e12, "T": 1e12}


def verify_claim(claim: NumericClaim, index: FactIndex) -> Verdict:
    # 1. All cited fact_ids must exist
    facts = []
    for fid in claim.source_fact_ids:
        f = index.by_id.get(fid)
        if f is None:
            return Verdict(claim_id=claim.id, status="unverifiable", reason="unknown_fact_id",
                           claimed=claim.value, detail=f"cited fact_id {fid!r} not in fact sheet")
        facts.append(f)
    if not facts:
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unresolvable",
                       claimed=claim.value, detail="claim cites no facts")

    if claim.basis == "reported":
        f = facts[0]
        # citation integrity: right number on the wrong fact is still a fail
        if f.concept != claim.concept or f.gaap_tag != claim.gaap_tag:
            return Verdict(claim_id=claim.id, status="fail", reason="wrong_tag",
                           claimed=claim.value, actual=f.value,
                           detail=f"claim declares {claim.concept}/{claim.gaap_tag}, fact {f.fact_id} is {f.concept}/{f.gaap_tag}")
        if f.end != claim.period.end or (f.start or None) != (claim.period.start or None):
            return Verdict(claim_id=claim.id, status="fail", reason="wrong_period",
                           claimed=claim.value, actual=f.value,
                           detail=f"claim period {claim.period.start}→{claim.period.end}, fact period {f.start}→{f.end}")
        tol = ABS_TOL_EPS if claim.concept == "eps_diluted" else None
        return _compare(claim, f.value, abs_tol=tol)

    # derived
    expr = (claim.derivation or "").strip()
    if not expr:
        return Verdict(claim_id=claim.id, status="fail", reason="bad_derivation",
                       claimed=claim.value, detail="derived claim without a derivation expression")
    try:
        actual = _safe_eval(expr, {f.fact_id: f.value for f in facts})
    except Exception as exc:  # noqa: BLE001 — any parse/eval problem is the same verdict
        return Verdict(claim_id=claim.id, status="fail", reason="bad_derivation",
                       claimed=claim.value, detail=f"derivation {expr!r}: {exc}")
    if claim.unit == "pct":
        ok = abs(claim.value - actual) <= ABS_TOL_DERIVED_PCT
        return Verdict(claim_id=claim.id, status="pass" if ok else "fail",
                       reason="ok" if ok else "value_mismatch",
                       claimed=claim.value, actual=round(actual, 4),
                       delta_pct=round(abs(claim.value - actual), 4),
                       tolerance=f"±{ABS_TOL_DERIVED_PCT}pp abs",
                       detail=f"recomputed {expr!r} from ground truth")
    return _compare(claim, actual, detail=f"recomputed {expr!r} from ground truth")


def _compare(claim: NumericClaim, actual: float, abs_tol: Optional[float] = None, detail: str = "") -> Verdict:
    if abs_tol is not None:
        ok = abs(claim.value - actual) <= abs_tol
        tol_desc = f"±{abs_tol} abs"
        delta = abs(claim.value - actual)
    else:
        delta = abs(claim.value - actual) / abs(actual) if actual else float("inf")
        ok = delta <= REL_TOL_REPORTED
        tol_desc = f"±{REL_TOL_REPORTED:.1%} rel"
    return Verdict(claim_id=claim.id, status="pass" if ok else "fail",
                   reason="ok" if ok else "value_mismatch",
                   claimed=claim.value, actual=actual,
                   delta_pct=round(delta * (100 if abs_tol is None else 1), 6),
                   tolerance=tol_desc, detail=detail)


def _safe_eval(expr: str, env: dict[str, float]) -> float:
    """Evaluate + - * / and parens over fact_id symbols. Nothing else."""
    node = ast.parse(expr, mode="eval")

    def walk(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = walk(n.left), walk(n.right)
            return {ast.Add: left.__add__, ast.Sub: left.__sub__,
                    ast.Mult: left.__mul__, ast.Div: left.__truediv__}[type(n.op)](right)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -walk(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise KeyError(f"unknown fact_id {n.id!r}")
            return env[n.id]
        raise ValueError(f"disallowed node {type(n).__name__}")

    return walk(node)


# Basis-point entries get a small absolute window: relative tol already covers large bp
# values ("230bp" vs 226.8 = 1.4%), so the absolute part only needs to absorb rounding on
# small deltas. NOT ±50 (0.5pp in bp units) — a window that wide grounds unrelated numbers.
_BP_ABS_TOL = 5.0

# One labeled ground entry: (value, kind, provenance). kind sets the tolerance policy —
# "raw" (dollar/share/scaled values): relative only; "pct" (growth/margin/pp): relative
# or ±ABS_TOL_DERIVED_PCT, matching derived-pct claim verification; "bp": relative or
# ±_BP_ABS_TOL. The label makes every grounded token auditable in the droplog.
_GroundEntry = tuple[float, str, str]
_ABS_TOL_BY_KIND = {"raw": 0.0, "pct": ABS_TOL_DERIVED_PCT, "bp": _BP_ABS_TOL}


def _ground_set(brief: Brief, index: Optional[FactIndex]) -> list[_GroundEntry]:
    """Every value a narrative number may LEGITIMATELY equal, labeled with its provenance.

    A prose number is grounded if it traces to ground truth: a claim value, a real XBRL
    fact value, or a TYPED derivation over the facts. Grounding is a *traceability* check
    that a stray/hallucinated number has nowhere to hide — it is SEPARATE from and does
    not weaken per-claim verification, which still checks every numeric claim against the
    XBRL facts strictly and independently (see verify_claim).

    Only semantically valid derivations enter the ground set:
      - same-concept period-over-period delta and growth % (YoY revenue growth, ...)
      - revenue-denominated margins for the same period (net/operating/gross margin, ...)
      - same-margin deltas across periods, in pp and bp
    An earlier draft grounded against ALL-PAIRS arithmetic over the fact base; with ~24
    facts that produced ~460k values — dense enough that any percentage-scale number
    passed, i.e. a coverage gate that could not fail. Typed derivations keep the set
    small (~hundreds) and falsifiable.
    """
    entries: dict[tuple[float, str], str] = {}

    def add(value: float, kind: str, label: str) -> None:
        # The token regex captures no sign, so narrative numbers are always unsigned
        # ("a −$5,202M outflow" reads as 5202) — ground magnitudes, never signed values.
        if math.isfinite(value):
            entries.setdefault((abs(value), kind), label)

    def add_scaled(value: float, label: str) -> None:
        add(value, "raw", label)
        for suffix, s in _SCALE.items():
            add(value / s, "raw", f"{label} in {suffix}")

    for c in brief.numeric_claims():
        if c.value is None or not math.isfinite(c.value):
            continue
        if c.unit == "pct":
            add(c.value, "pct", f"claim {c.id} (pct)")
            add(abs(c.value) * 100.0, "bp", f"claim {c.id} (bp)")
        else:
            add_scaled(c.value, f"claim {c.id}")

    facts = [] if index is None else [f for f in index.facts if math.isfinite(f.value)]
    by_concept: dict[str, list] = {}
    for f in facts:
        add_scaled(f.value, f"fact {f.fact_id} ({f.concept}, {f.period_label})")
        by_concept.setdefault(f.concept, []).append(f)

    # Same-concept period-over-period: delta and growth % (tokens are unsigned, so store
    # magnitudes — "declined 1.8%" appears as 1.8).
    for concept, fs in by_concept.items():
        for a in fs:
            for b in fs:
                if a.end <= b.end:
                    continue  # a strictly newer than b
                pair = f"{concept} {b.fact_id}→{a.fact_id}"
                add_scaled(abs(a.value - b.value), f"Δ {pair}")
                if b.value:
                    g = abs((a.value - b.value) / b.value * 100.0)
                    add(g, "pct", f"growth % {pair}")
                    add(g * 100.0, "bp", f"growth bp {pair}")

    # Revenue-denominated margins, same period only; then same-margin deltas (pp/bp).
    revenue = {(f.start, f.end): f for f in by_concept.get("revenue", [])}
    margins: dict[str, list[tuple[str, float]]] = {}
    for f in facts:
        if f.concept == "revenue" or f.period_type != "duration" or f.unit != "USD":
            continue
        r = revenue.get((f.start, f.end))
        if r is None or not r.value:
            continue
        m = f.value / r.value * 100.0
        add(m, "pct", f"{f.concept}/revenue margin % ({f.period_label})")
        margins.setdefault(f.concept, []).append((f.end, m))

    # Accounting identity: gross profit = revenue − cost of revenue, for filers that
    # don't tag GrossProfit (e.g. Costco). Same-period pairs only; its margin joins the
    # margins dict so period-over-period deltas fall out below.
    for r in by_concept.get("revenue", []):
        for cg in by_concept.get("cost_of_revenue", []):
            if (cg.start, cg.end) != (r.start, r.end):
                continue
            gp = r.value - cg.value
            add_scaled(gp, f"gross profit = {r.fact_id}−{cg.fact_id} ({r.period_label})")
            if r.value:
                add(gp / r.value * 100.0, "pct", f"derived gross margin % ({r.period_label})")
                margins.setdefault("gross_profit_derived", []).append((r.end, gp / r.value * 100.0))
    for concept, ms in margins.items():
        for end_a, ma in ms:
            for end_b, mb in ms:
                if end_a <= end_b:
                    continue
                d = abs(ma - mb)
                add(d, "pct", f"{concept} margin Δ pp ({end_b}→{end_a})")
                add(d * 100.0, "bp", f"{concept} margin Δ bp ({end_b}→{end_a})")

    return [(v, kind, label) for (v, kind), label in entries.items()]


def narrative_coverage(brief: Brief, index: Optional[FactIndex] = None,
                       tol: float = 0.02) -> tuple[float, list[str]]:
    """Every numeric token in the narrative must trace to ground truth.

    Returns (coverage_pct, ungrounded_tokens); see narrative_coverage_audit for the
    matched-provenance variant. `index` is optional for backward compatibility; pass it
    to ground against the fact set and its typed derivations (the loop always does).
    """
    pct, uncovered, _matches = narrative_coverage_audit(brief, index, tol)
    return pct, uncovered


def narrative_coverage_audit(brief: Brief, index: Optional[FactIndex] = None,
                             tol: float = 0.02) -> tuple[float, list[str], list[tuple[str, str]]]:
    """narrative_coverage plus the audit trail: (pct, uncovered_tokens, matches), where
    matches pairs each grounded token with the labeled ground value it traced to. Years
    and small prose counts are skipped."""
    ground = _ground_set(brief, index)
    tokens, uncovered, matches = [], [], []
    for m in _NUM_RE.finditer(brief.narrative):
        raw = m.group(1).replace(",", "")
        try:
            num = float(raw)
        except ValueError:
            continue
        if m.group(2):
            num *= _SCALE[m.group(2).strip()]
        if 1900 <= num <= 2100 and "." not in raw and not m.group(2):
            continue  # year
        if abs(num) < 10 and "." not in raw:
            continue  # prose counts ("three of the 8 filers")
        tokens.append(m.group(0))
        label = _grounded(num, ground, tol, pct_token=m.group(0).rstrip().endswith("%"))
        if label is None:
            uncovered.append(m.group(0).strip())
        else:
            matches.append((m.group(0).strip(), label))
    pct = 100.0 if not tokens else 100.0 * (len(tokens) - len(uncovered)) / len(tokens)
    return round(pct, 2), uncovered, matches


def _grounded(num: float, ground: list[_GroundEntry], tol: float,
              pct_token: bool = False) -> Optional[str]:
    """The provenance label of the ground entry `num` traces to, or None. Relative `tol`
    applies to every eligible entry; the entry kind's absolute tolerance applies on top.

    A %-suffixed token may only ground against percentage-typed ("pct") entries — "24.5%"
    coincidentally landing near a $24.78B fact scaled to billions (or near a bp value) is
    a semantic mismatch, and those coincidences were the biggest remaining leak. Unsuffixed
    tokens stay eligible for every kind (prose bp/pp mentions carry no % sign).

    Linear scan — the typed ground set is a few hundred entries."""
    for value, kind, label in ground:
        if pct_token and kind != "pct":
            continue
        if _near(num, value, tol) or abs(num - value) <= _ABS_TOL_BY_KIND[kind]:
            return label
    return None


def _near(a: float, b: float, tol: float) -> bool:
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= tol
