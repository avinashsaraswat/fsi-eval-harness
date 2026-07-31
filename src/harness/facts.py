"""XBRL company-facts -> canonical fact index, coverage matrix, and generator fact sheet.

The fact sheet (generation input) and the fact index (verification ground truth) are
built here from the same source through INDEPENDENT paths downstream: the generator
sees a rendered table; the verifier resolves claims against the index by fact_id,
concept/tag, and exact period dates. See README limitation note (slice-1 scope).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from tabulate import tabulate

from .config import CONCEPTS, PERIOD_BANDS, Concept
from .schema import Fact, FilingRef


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def classify_duration(days: int) -> Optional[str]:
    for (lo, hi), label in PERIOD_BANDS:
        if lo <= days <= hi:
            return label
    return None


class FactIndex:
    """Canonical facts for one ticker, keyed for exact lookup."""

    def __init__(self, facts: list[Fact]):
        self.facts = facts
        self.by_id = {f.fact_id: f for f in facts}

    def lookup(self, concept: str, start: Optional[str], end: str) -> Optional[Fact]:
        for f in self.facts:
            if f.concept == concept and f.end == end and (f.start or None) == (start or None):
                return f
        return None


def _select_fact(entries: list[dict], target_accn: str) -> Optional[dict]:
    """Prefer facts reported in the target filing; fall back to the latest-filed."""
    if not entries:
        return None
    in_target = [e for e in entries if e.get("accn") == target_accn]
    if in_target:
        return in_target[-1]
    return max(entries, key=lambda e: e.get("filed", ""))


def build_fact_index(raw_facts: dict, filing: FilingRef) -> tuple[FactIndex, dict[str, str]]:
    """Resolve the concept whitelist against company-facts for the target filing.

    Returns (index, coverage) where coverage maps concept key -> resolved tag | "MISSING".
    Current period + prior-year comparative are both indexed where available.
    """
    gaap = raw_facts.get("facts", {}).get("us-gaap", {})
    out: list[Fact] = []
    coverage: dict[str, str] = {}
    fid = 0

    for key, concept in CONCEPTS.items():
        resolved: Optional[str] = None
        for tag in concept.tags:
            entries = _unit_entries(gaap, tag, concept.unit)
            if not entries:
                continue
            current = _current_period_entries(entries, concept, filing)
            if not current:
                continue
            resolved = tag
            chosen = _select_fact(current, filing.accession)
            fid += 1
            cur_fact = _to_fact(f"f{fid}", key, tag, concept, chosen, filing)
            out.append(cur_fact)
            prior = _prior_year_entry(entries, concept, chosen)
            if prior is not None:
                fid += 1
                out.append(_to_fact(f"f{fid}", key, tag, concept, prior, filing))
            break
        coverage[key] = resolved or "MISSING"

    return FactIndex(out), coverage


def _unit_entries(gaap: dict, tag: str, unit: str) -> list[dict]:
    units = gaap.get(tag, {}).get("units", {})
    if unit in units:
        return units[unit]
    # unit label variants (e.g. "USD/shares" listed under exactly that key)
    for k, v in units.items():
        if k.replace(" ", "") == unit.replace(" ", ""):
            return v
    return []


def _current_period_entries(entries: list[dict], concept: Concept, filing: FilingRef) -> list[dict]:
    """Entries whose period ends on the filing's report date (the filing's own period).

    10-K wants the FY duration; 10-Q wants the quarter — except ytd_ok concepts
    (cash flow is YTD-only in most 10-Qs), which fall back to H1/9M when no Q exists.
    """
    if concept.period_type == "instant":
        return [e for e in entries if e.get("end") == filing.report_date and not e.get("start")]

    preference = ["FY"] if filing.form == "10-K" else (["Q", "H1", "9M"] if concept.ytd_ok else ["Q"])
    by_label: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("end") != filing.report_date or not e.get("start"):
            continue
        label = classify_duration(_days(e["start"], e["end"]))
        if label:
            by_label.setdefault(label, []).append(e)
    for label in preference:
        if by_label.get(label):
            return by_label[label]
    return []


def _prior_year_entry(entries: list[dict], concept: Concept, current: dict) -> Optional[dict]:
    """Same-shape period ending ~1 year before the current fact's end."""
    cur_end = date.fromisoformat(current["end"])
    candidates = []
    for e in entries:
        if e is current or e.get("end") == current["end"]:
            continue
        try:
            end = date.fromisoformat(e["end"])
        except (KeyError, ValueError):
            continue
        if not (330 <= (cur_end - end).days <= 400):
            continue
        if concept.period_type == "duration":
            if not e.get("start") or not current.get("start"):
                continue
            if classify_duration(_days(e["start"], e["end"])) != classify_duration(
                _days(current["start"], current["end"])
            ):
                continue
        elif e.get("start"):
            continue
        candidates.append(e)
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.get("filed", ""))


def _to_fact(fact_id: str, key: str, tag: str, concept: Concept, e: dict, filing: FilingRef) -> Fact:
    duration_days = _days(e["start"], e["end"]) if concept.period_type == "duration" and e.get("start") else None
    dur_label = classify_duration(duration_days) if duration_days else "instant"
    # NOTE: EDGAR's fy/fp mark the filing a fact was REPORTED in, not the fact's own
    # period — useless for labeling comparatives. Label from the fact's own dates.
    return Fact(
        fact_id=fact_id,
        concept=key,
        gaap_tag=tag,
        unit=concept.unit,
        period_type=concept.period_type,  # type: ignore[arg-type]
        start=e.get("start"),
        end=e["end"],
        duration_days=duration_days,
        period_label=f"{dur_label} ending {e['end']}" if dur_label != "instant" else f"as of {e['end']}",
        value=float(e["val"]),
        accession=e.get("accn", ""),
        form=e.get("form", ""),
        fallback_accession=e.get("accn") != filing.accession,
    )


def render_fact_sheet(index: FactIndex, filing: FilingRef) -> str:
    """The generator's ONLY numeric input: one row per fact."""
    rows = []
    for f in index.facts:
        rows.append([
            f.fact_id, f.concept, f.gaap_tag, f.period_label,
            f"{f.start or '—'} → {f.end}", f.unit, f"{f.value:,.2f}",
        ])
    header = (
        f"# Fact sheet — {filing.company_name} ({filing.ticker})\n"
        f"Filing: {filing.form}, accession {filing.accession}, period ending {filing.report_date}, "
        f"filed {filing.filing_date}\n\n"
    )
    table = tabulate(rows, headers=["fact_id", "concept", "us-gaap tag", "period", "dates", "unit", "value"],
                     disable_numparse=True)
    return header + table
