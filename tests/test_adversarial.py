"""The adversarial demo's core claim, proven on synthetic facts (no API key, no cache):
a right number on the wrong period or wrong tag fails the verifier, and correcting the
citation reconciles it again. This is the red<->green the demo shows on real data."""

from harness.adversarial import perturb_period, perturb_tag
from harness.facts import FactIndex
from harness.schema import Fact, NumericClaim, Period
from harness.verify import verify_claim


def _index() -> FactIndex:
    return FactIndex([
        Fact(fact_id="f1", concept="revenue", gaap_tag="Revenues", unit="USD",
             period_type="duration", start="2024-12-29", end="2025-03-29",
             duration_days=90, period_label="Q FY2025-Q2", value=100_000_000_000.0,
             accession="a", form="10-Q"),
    ])


def _claim() -> NumericClaim:
    return NumericClaim(id="c1", text="revenue was 100,000,000,000.00 USD", concept="revenue",
                        gaap_tag="Revenues", period=Period(type="duration", start="2024-12-29", end="2025-03-29"),
                        unit="USD", value=100_000_000_000.0, basis="reported", source_fact_ids=["f1"])


def test_baseline_claim_passes():
    assert verify_claim(_claim(), _index()).status == "pass"


def test_period_perturbation_fires_wrong_period():
    v = verify_claim(perturb_period(_claim()), _index())
    assert (v.status, v.reason) == ("fail", "wrong_period")


def test_tag_perturbation_fires_wrong_tag():
    v = verify_claim(perturb_tag(_claim()), _index())
    assert (v.status, v.reason) == ("fail", "wrong_tag")


def test_perturbation_changes_only_the_citation_not_the_value():
    c = _claim()
    assert perturb_period(c).value == c.value
    assert perturb_tag(c).value == c.value


def test_red_then_green_is_a_clean_single_variable_flip():
    c, idx = _claim(), _index()
    assert verify_claim(c, idx).status == "pass"                 # 🟢 baseline
    assert verify_claim(perturb_period(c), idx).status == "fail"  # 🔴 injected error
    assert verify_claim(c, idx).status == "pass"                 # 🟢 corrected (c unmutated)
