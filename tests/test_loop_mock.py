"""End-to-end mock loop: fail -> feedback -> regenerate -> reconcile, deterministically, no network."""

import harness.loop as loop_mod
from harness.generate import MockGenerator
from harness.judge import MockJudge
from harness.loop import run_ticker
from harness.schema import Brief, NumericClaim, Period, QualitativeClaim


def _patch(monkeypatch, filing, raw_facts):
    monkeypatch.setattr(loop_mod, "latest_filing", lambda t: filing)
    monkeypatch.setattr(loop_mod, "company_facts", lambda t: raw_facts)


def test_mock_reconciles_on_attempt_1(monkeypatch, filing, raw_facts):
    _patch(monkeypatch, filing, raw_facts)
    r = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="t1")
    assert r.status == "RECONCILED"
    assert r.metrics.retries_to_reconcile == 1      # attempt 0 fails (3 injected faults), attempt 1 clean
    assert r.metrics.attempts == 2
    assert r.brief is not None
    assert r.metrics.narrative_coverage_pct == 100.0
    assert r.metrics.verified_pass >= 5


def test_injected_faults_are_caught(monkeypatch, filing, raw_facts):
    _patch(monkeypatch, filing, raw_facts)
    r = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="t2")
    reasons = {d.reason_code for d in r.drops if d.action == "dropped"}
    assert "value_mismatch" in reasons          # fault 1
    assert "wrong_period" in reasons            # fault 2
    assert "unclaimed_numeric" in reasons       # fault 3


def test_routing_wall_measured(monkeypatch, filing, raw_facts):
    _patch(monkeypatch, filing, raw_facts)
    r = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="t3")
    m = r.metrics
    assert m.routed_deterministic == m.numeric_claims
    assert m.routed_llm_judge == m.qualitative_claims
    assert 0 < m.routing_share_deterministic < 1


def test_deterministic_across_runs(monkeypatch, filing, raw_facts):
    _patch(monkeypatch, filing, raw_facts)
    a = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="same")
    b = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="same")
    assert a.brief.model_dump() == b.brief.model_dump()
    assert a.metrics.model_dump() == b.metrics.model_dump()
    assert [d.model_dump() for d in a.drops] == [d.model_dump() for d in b.drops]


def test_judge_kept_supported_qualitative(monkeypatch, filing, raw_facts):
    _patch(monkeypatch, filing, raw_facts)
    r = run_ticker("TEST", MockGenerator(), MockJudge(), run_id="t4")
    assert r.judge_verdicts and all(j.supported for j in r.judge_verdicts)


class _StubbornGenerator:
    """Never fixes its bad claim: 5 correct reported claims, a 6th with a perturbed
    value on every attempt, and a qualitative claim citing the bad one — so the loop
    exhausts retries with the numbers still dirty (the judge never ran in-loop)."""
    name = "stubborn"

    def generate(self, fact_sheet, index, filing, feedback):
        claims, sentences = [], []
        current = [f for f in index.facts if f.end == filing.report_date][:6]
        for i, f in enumerate(current, start=1):
            value = f.value if i < 6 else round(f.value * 1.07, 4)
            unit = "USD/share" if f.unit == "USD/shares" else ("shares" if f.unit == "shares" else "USD")
            text = f"{f.concept} was {value:,.2f} {unit} for the period ending {f.end}."
            sentences.append(text)
            claims.append(NumericClaim(id=f"c{i}", text=text, concept=f.concept, gaap_tag=f.gaap_tag,
                                       period=Period(type=f.period_type, start=f.start, end=f.end),
                                       unit=unit, value=value, basis="reported",
                                       source_fact_ids=[f.fact_id]))
        qual = QualitativeClaim(id="c7", text="Momentum looks exceptional on every line.",
                                quality="tone", supporting_claim_ids=["c6"])
        claims.append(qual)
        narrative = "## Test Corp (TEST) brief\n\n" + " ".join(sentences) + f"\n\n{qual.text}"
        brief = Brief(ticker=filing.ticker, narrative=narrative, claims=claims)
        return brief, {"model": "mock", "input_tokens": 0, "output_tokens": 0, "cache_hit": False}


def test_partial_brief_is_still_judged(monkeypatch, filing, raw_facts):
    """Regression: exhaustion with dirty numbers must not ship unjudged qualitative
    claims — the judge runs on the stripped brief and rejected prose is removed."""
    _patch(monkeypatch, filing, raw_facts)
    r = run_ticker("TEST", _StubbornGenerator(), MockJudge(), run_id="t5")
    assert r.status == "PARTIAL"
    assert r.judge_verdicts, "PARTIAL brief shipped without a judge pass"
    assert not any(j.supported for j in r.judge_verdicts if j.claim_id == "c7")
    assert "Momentum looks exceptional" not in r.brief.narrative
    assert all(q.id != "c7" for q in r.brief.qualitative_claims())
    assert any(d.reason_code == "judge_unsupported_stripped" for d in r.drops)
