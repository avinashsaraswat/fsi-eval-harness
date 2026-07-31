import subprocess
import sys
from pathlib import Path

import pytest

from harness.facts import FactIndex
from harness.inject import (
    apply_omission,
    apply_value_injection,
    build_items_for_brief,
    eligible_sites,
    fabricate,
    perturb_value,
)
from harness.judge import MockJudge
from harness.schema import Brief, Fact, FilingRef, NumericClaim, Period, QualitativeClaim
from harness.verify import verify_claim

REPO = Path(__file__).resolve().parents[1]


def _fact(fid, concept, tag, value, start="2024-12-29", end="2025-03-29"):
    return Fact(fact_id=fid, concept=concept, gaap_tag=tag, unit="USD", period_type="duration",
                start=start, end=end, duration_days=90, period_label="Q FY2025-Q2",
                value=value, accession="a", form="10-Q")


def _claim(cid, concept, tag, value, text, fid):
    return NumericClaim(id=cid, text=text, concept=concept, gaap_tag=tag,
                        period=Period(type="duration", start="2024-12-29", end="2025-03-29"),
                        unit="USD", value=value, basis="reported", source_fact_ids=[fid])


@pytest.fixture
def index():
    return FactIndex([
        _fact("f1", "revenue", "Revenues", 100_000_000_000.0),
        _fact("f2", "net_income", "NetIncomeLoss", 25_000_000_000.0),
    ])


@pytest.fixture
def brief():
    c1 = _claim("n1", "revenue", "Revenues", 100_000_000_000.0,
                "Revenue was $100,000,000,000 for the quarter.", "f1")
    c2 = _claim("n2", "net_income", "NetIncomeLoss", 25_000_000_000.0,
                "Net income reached $25.0 billion in the period.", "f2")
    q = QualitativeClaim(id="q1", text="Profitability remained resilient.", quality="tone",
                         supporting_claim_ids=["n2"])
    narrative = f"## Test brief\n\n{c1.text} {c2.text}\n\n{q.text}"
    return Brief(ticker="TST", narrative=narrative, claims=[c1, c2, q])


@pytest.fixture
def filing():
    return FilingRef(ticker="TST", cik="1", form="10-Q", accession="a",
                     filing_date="2025-05-01", report_date="2025-03-29", company_name="Test Co")


def test_perturb_value_flips_value_mismatch(index, brief):
    site = brief.numeric_claims()[0]
    assert verify_claim(site, index).status == "pass"
    perturbed = perturb_value(site, 0.01)
    v = verify_claim(perturbed, index)
    assert (v.status, v.reason) == ("fail", "value_mismatch")


def test_perturb_value_rewrites_display_token():
    c = _claim("n1", "revenue", "Revenues", 94_900_000_000.0,
               "Revenue of $94.9 billion grew strongly.", "f1")
    p = perturb_value(c, 0.05)
    assert "99.6 billion" in p.text and "94.9" not in p.text
    assert abs(p.value - 94_900_000_000.0 * 1.05) < 1.0


def test_perturb_value_skips_tokenless_sentence():
    c = _claim("n1", "revenue", "Revenues", 100_000_000_000.0, "Revenue grew.", "f1")
    assert perturb_value(c, 0.01) is None


def test_apply_value_injection_keeps_brief_internally_consistent(index, brief):
    site = brief.numeric_claims()[0]
    b = apply_value_injection(brief, site, 0.01)
    new = next(c for c in b.numeric_claims() if c.id == "n1")
    assert new.text in b.narrative and site.text not in b.narrative
    assert verify_claim(new, index).reason == "value_mismatch"
    # the untouched claim still passes — one variable changed
    other = next(c for c in b.numeric_claims() if c.id == "n2")
    assert verify_claim(other, index).status == "pass"


def test_omission_removes_claims_and_sentences_keeps_dangling_qualitative(brief):
    b = apply_omission(brief, "net_income")
    assert all(c.concept != "net_income" for c in b.numeric_claims())
    assert "Net income" not in b.narrative
    q = b.qualitative_claims()[0]                # kept, now citing a removed claim
    assert q.supporting_claim_ids == ["n2"]
    assert apply_omission(brief, "eps_diluted") is None   # concept absent -> no item


def test_fabrication_species_and_mock_judge_baseline(index, brief):
    verdicts = [verify_claim(c, index) for c in brief.numeric_claims()]
    for species, mock_catches in [("phantom_citation", True), ("no_citation", True),
                                  ("overreach_causal", False), ("overreach_superlative", False)]:
        b, q = fabricate(brief, species)
        assert q.text in b.narrative and q.id in {c.id for c in b.claims}
        flags = {jv.claim_id for jv in MockJudge().judge(b, verdicts) if not jv.supported}
        # rule-based baseline: citation-integrity species caught, semantic overreach passes —
        # the delta the LLM judge must close
        assert (q.id in flags) is mock_catches, species


def test_build_items_blinding_and_counts(index, brief, filing):
    items, labels = build_items_for_brief(brief, index, filing)
    by_class = {}
    for lb in labels:
        by_class[lb.error_class] = by_class.get(lb.error_class, 0) + 1
    assert by_class["clean"] == 1
    assert by_class["wrong_value"] == 2 and by_class["wrong_period"] == 2 and by_class["wrong_tag"] == 2
    assert by_class["omission"] == 2 and by_class["fabrication"] == 4
    # blinding: item payload carries no label fields; ids match 1:1 and are deterministic
    assert {i.item_id for i in items} == {lb.item_id for lb in labels}
    assert "error_class" not in items[0].model_dump()["brief"]
    items2, _ = build_items_for_brief(brief, index, filing)
    assert [i.item_id for i in items] == [i.item_id for i in items2]


def test_eligible_sites_excludes_derived_and_failing(index, brief):
    sites = eligible_sites(brief, index)
    assert [s.id for s in sites] == ["n1", "n2"]


def test_detection_stats_selftest():
    r = subprocess.run([sys.executable, str(REPO / "evals" / "detection.py"), "--selftest"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest OK" in r.stdout
