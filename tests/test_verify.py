import pytest

from harness.facts import FactIndex
from harness.schema import Brief, Fact, NumericClaim, Period
from harness.verify import narrative_coverage, verify_claim


def _fact(fid, concept, tag, value, start="2024-12-29", end="2025-03-29", ptype="duration", unit="USD"):
    return Fact(fact_id=fid, concept=concept, gaap_tag=tag, unit=unit, period_type=ptype,
                start=start if ptype == "duration" else None, end=end,
                duration_days=90 if ptype == "duration" else None, period_label="Q FY2025-Q2",
                value=value, accession="a", form="10-Q")


@pytest.fixture
def index():
    return FactIndex([
        _fact("f1", "revenue", "Revenues", 100_000_000_000.0),
        _fact("f2", "revenue", "Revenues", 80_000_000_000.0, start="2023-12-31", end="2024-03-30"),
        _fact("f3", "eps_diluted", "EarningsPerShareDiluted", 1.65, unit="USD/shares"),
    ])


def _claim(value, basis="reported", fids=("f1",), concept="revenue", tag="Revenues",
           start="2024-12-29", end="2025-03-29", unit="USD", derivation=None, cid="c1"):
    return NumericClaim(id=cid, text=f"revenue was {value}", concept=concept, gaap_tag=tag,
                        period=Period(type="duration", start=start, end=end),
                        unit=unit, value=value, basis=basis,
                        source_fact_ids=list(fids), derivation=derivation)


def test_reported_pass_within_tolerance(index):
    v = verify_claim(_claim(100_200_000_000.0), index)  # +0.2% < 0.5%
    assert v.status == "pass"


def test_reported_value_mismatch(index):
    v = verify_claim(_claim(103_000_000_000.0), index)
    assert (v.status, v.reason) == ("fail", "value_mismatch")


def test_wrong_period(index):
    v = verify_claim(_claim(100_000_000_000.0, end="2025-03-15"), index)
    assert (v.status, v.reason) == ("fail", "wrong_period")


def test_wrong_tag_citation_integrity(index):
    v = verify_claim(_claim(100_000_000_000.0, concept="net_income", tag="NetIncomeLoss"), index)
    assert (v.status, v.reason) == ("fail", "wrong_tag")


def test_unknown_fact_id(index):
    v = verify_claim(_claim(1.0, fids=("f9",)), index)
    assert (v.status, v.reason) == ("unverifiable", "unknown_fact_id")


def test_derived_yoy_recomputed(index):
    ok = _claim(25.2, basis="derived", fids=("f1", "f2"), unit="pct",
                derivation="(f1 - f2) / f2 * 100")
    bad = _claim(26.0, basis="derived", fids=("f1", "f2"), unit="pct",
                 derivation="(f1 - f2) / f2 * 100")
    assert verify_claim(ok, index).status == "pass"       # truth = 25.0, ±0.5pp
    assert verify_claim(bad, index).status == "fail"


def test_eps_absolute_tolerance(index):
    near = _claim(1.653, concept="eps_diluted", tag="EarningsPerShareDiluted",
                  fids=("f3",), unit="USD/share")
    far = _claim(1.66, concept="eps_diluted", tag="EarningsPerShareDiluted",
                 fids=("f3",), unit="USD/share")
    assert verify_claim(near, index).status == "pass"
    assert verify_claim(far, index).status == "fail"


def test_derivation_rejects_non_arithmetic(index):
    evil = _claim(1.0, basis="derived", fids=("f1",), derivation="__import__('os').system('true')")
    v = verify_claim(evil, index)
    assert (v.status, v.reason) == ("fail", "bad_derivation")


def test_narrative_coverage_flags_unclaimed():
    brief = Brief(ticker="T", narrative="Revenue was 100,000,000,000.00 USD. Mystery 123,456,789 momentum.",
                  claims=[_claim(100_000_000_000.0)])
    pct, uncovered = narrative_coverage(brief)
    assert pct < 100.0 and any("123,456,789" in u for u in uncovered)


def test_narrative_coverage_full():
    brief = Brief(ticker="T", narrative="Revenue was 100,000,000,000.00 USD in fiscal 2025.",
                  claims=[_claim(100_000_000_000.0)])
    pct, uncovered = narrative_coverage(brief)
    assert pct == 100.0 and not uncovered


@pytest.fixture
def grounding_index():
    """Current + prior-year revenue and net income: YoY growth 25% / 33.3%, net margin
    20% vs 18.75%, margin delta 1.25pp = 125bp."""
    return FactIndex([
        _fact("f1", "revenue", "Revenues", 100_000_000_000.0),
        _fact("f2", "revenue", "Revenues", 80_000_000_000.0, start="2023-12-31", end="2024-03-30"),
        _fact("f3", "net_income", "NetIncomeLoss", 20_000_000_000.0),
        _fact("f4", "net_income", "NetIncomeLoss", 15_000_000_000.0, start="2023-12-31", end="2024-03-30"),
    ])


def test_grounding_accepts_typed_derivations(grounding_index):
    brief = Brief(ticker="T", narrative=(
        "Revenue grew 25.0% to $100.0 billion from 80,000,000,000.00 a year ago. "
        "Net margin reached 20.0%, up 125 bp (1.25 points) from 18.75%."),
        claims=[_claim(100_000_000_000.0)])
    pct, uncovered = narrative_coverage(brief, grounding_index)
    assert pct == 100.0 and not uncovered


def test_grounding_rejects_hallucinated_numbers(grounding_index):
    """Vacuousness canary: the ground set must stay sparse enough that arbitrary
    percentage- and dollar-scale numbers FAIL grounding. An all-pairs derivation set
    (~460k values from 24 facts) made this impossible — any pct passed."""
    brief = Brief(ticker="T", narrative=(
        "Revenue grew 47.3% with 61.2% attach rates, a 35.1% margin, "
        "and $12.7 billion of mystery momentum."),
        claims=[_claim(100_000_000_000.0)])
    pct, uncovered = narrative_coverage(brief, grounding_index)
    assert pct < 100.0
    for tok in ("47.3", "61.2", "35.1", "12.7"):
        assert any(tok in u for u in uncovered), f"{tok} should be ungrounded"


def test_grounding_is_unsigned(grounding_index):
    """The token regex drops signs — prose citing a −$5.0B outflow reads as 5.0, and must
    ground against the negative fact/claim value (KO full-run finding, 2026-07-18)."""
    idx = FactIndex(grounding_index.facts + [
        _fact("f5", "operating_cash_flow", "NetCashProvidedByUsedInOperatingActivities",
              -5_202_000_000.0)])
    brief = Brief(ticker="T", narrative="A reversal from the prior $5,202 million outflow, or $5.2 billion.",
                  claims=[_claim(100_000_000_000.0)])
    pct, uncovered = narrative_coverage(brief, idx)
    assert pct == 100.0 and not uncovered


def test_grounding_gross_profit_identity():
    """Filers without a GrossProfit tag (Costco): revenue − cost_of_revenue is a legit
    derived figure the analyst may cite (COST full-run finding, 2026-07-18)."""
    idx = FactIndex([
        _fact("f1", "revenue", "Revenues", 63_200_000_000.0),
        _fact("f2", "cost_of_revenue", "CostOfGoodsAndServicesSold", 54_190_000_000.0),
    ])
    brief = Brief(ticker="T", narrative="Gross profit came to $9.01B on a 14.26% gross margin.",
                  claims=[_claim(63_200_000_000.0)])
    pct, uncovered = narrative_coverage(brief, idx)
    assert pct == 100.0 and not uncovered


def test_grounding_audit_labels_provenance(grounding_index):
    from harness.verify import narrative_coverage_audit
    brief = Brief(ticker="T", narrative="Revenue grew 25.0% while net margin was 18.75% last year.",
                  claims=[_claim(100_000_000_000.0)])
    _pct, _unc, matches = narrative_coverage_audit(brief, grounding_index)
    labels = dict(matches)
    assert "growth %" in labels["25.0%"]
    assert "margin %" in labels["18.75%"]
