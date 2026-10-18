"""Offline regression cases found during the October 8 maintenance audit."""
import json
from types import SimpleNamespace

import pytest

from harness.facts import FactIndex, build_fact_index
from harness.generate import FeedbackBlock, MockGenerator
from harness.judge import ApiJudge, MockJudge
from harness.loop import run_ticker
from harness.metrics import BudgetExceeded, Ledger
from harness.schema import Brief, JudgeVerdict, NumericClaim, Period, QualitativeClaim
from harness.verify import narrative_coverage, verify_claim


def clean_brief(filing, raw_facts):
    index, _ = build_fact_index(raw_facts, filing)
    brief, _ = MockGenerator().generate('', index, filing, FeedbackBlock())
    return brief, index


def test_reported_unit_mismatch_is_rejected(filing, raw_facts):
    brief, index = clean_brief(filing, raw_facts)
    claim = brief.numeric_claims()[0].model_copy(update={'unit': 'shares'})
    assert verify_claim(claim, index).status != 'pass'


def test_period_type_mismatch_is_rejected(filing, raw_facts):
    brief, index = clean_brief(filing, raw_facts)
    claim = brief.numeric_claims()[0].model_copy(deep=True)
    claim.period.type = 'instant'
    assert verify_claim(claim, index).status != 'pass'


def test_reported_zero_matches_zero(filing, raw_facts):
    brief, index = clean_brief(filing, raw_facts)
    claim = brief.numeric_claims()[0].model_copy(update={'value': 0})
    fact = index.by_id[claim.source_fact_ids[0]].model_copy(update={'value': 0})
    assert verify_claim(claim, FactIndex([fact])).status == 'pass'
    assert verify_claim(claim.model_copy(update={'value': 1}), FactIndex([fact])).status == 'fail'


def test_constant_only_derivation_is_rejected(filing, raw_facts):
    brief, index = clean_brief(filing, raw_facts)
    claim = brief.numeric_claims()[0].model_copy(update={'value': 123, 'basis': 'derived', 'derivation': '123'})
    assert verify_claim(claim, index).status != 'pass'


@pytest.mark.parametrize('response', [[],
    [{'claim_id': 'wrong', 'supported': True, 'reason': 'ok'}],
    [{'claim_id': 'q', 'supported': True, 'reason': 'ok'}] * 2,
    [{'claim_id': 'q', 'supported': 'yes', 'reason': 'ok'}]])
def test_invalid_judge_response_cannot_approve_claims(monkeypatch, response):
    import harness.generate as gen
    monkeypatch.setattr(gen, 'cached_call', lambda *a, **k: (json.dumps(response), {}))
    brief = Brief(ticker='TEST', narrative='q', claims=[QualitativeClaim(id='q', text='q', quality='tone')])
    with pytest.raises(ValueError):
        ApiJudge(client=SimpleNamespace(name='stub')).judge(brief, [])


def test_partial_does_not_emit_uncovered_number(monkeypatch, filing, raw_facts):
    import harness.loop as loop
    monkeypatch.setattr(loop, 'latest_filing', lambda _: filing)
    monkeypatch.setattr(loop, 'company_facts', lambda _: raw_facts)
    brief, _ = clean_brief(filing, raw_facts)
    brief.narrative += ' Unbacked forecast: $123,456,789.'
    generator = SimpleNamespace(generate=lambda *args: (brief.model_copy(deep=True), {}))
    result = run_ticker('TEST', generator, MockJudge(), 'guard', max_retries=0)
    assert result.status == 'FAILED'
    assert result.brief is None


def test_partial_rejects_unmatched_unsupported_prose(monkeypatch, filing, raw_facts):
    import harness.loop as loop
    monkeypatch.setattr(loop, 'latest_filing', lambda _: filing)
    monkeypatch.setattr(loop, 'company_facts', lambda _: raw_facts)
    brief, _ = clean_brief(filing, raw_facts)
    q = brief.qualitative_claims()[0]
    brief.narrative = brief.narrative.replace(q.text, 'Its business is unbeatable.')
    generator = SimpleNamespace(generate=lambda *args: (brief.model_copy(deep=True), {}))
    judge = SimpleNamespace(judge=lambda *args: [JudgeVerdict(claim_id=q.id, supported=False, reason='unsupported')])
    result = run_ticker('TEST', generator, judge, 'guard', max_retries=0)
    assert result.status == 'FAILED'
    assert result.brief is None


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_budget_is_rejected(value):
    with pytest.raises(ValueError):
        Ledger(max_spend=value)


def test_run_honors_zero_budget_and_returns_abort(monkeypatch):
    import harness.cli as cli
    import harness.loop as loop
    budgets = []
    def run(*args, ledger, **kwargs):
        budgets.append(ledger.max_spend)
        ledger.precheck()
        raise AssertionError('Zero budget must stop a live call')
    monkeypatch.setattr(loop, 'run_ticker', run)
    monkeypatch.setattr(cli, '_key_missing', lambda _: False)
    monkeypatch.setattr(cli, '_resolve_provider', lambda _: ('gemini', 'stub', 'stub', SimpleNamespace(name='stub')))
    args = SimpleNamespace(tickers=['TEST'], run_id='guard', max_spend=0, mock=False)
    assert cli._run(args) == 1
    assert budgets == [0]


def test_budget_abort_after_success_still_returns_failure(monkeypatch):
    import harness.cli as cli
    import harness.loop as loop
    import harness.report as report
    result = SimpleNamespace(status='RECONCILED', metrics=SimpleNamespace(attempts=1, verified_pass=5, numeric_claims=5, narrative_coverage_pct=100))
    def run(ticker, *args, **kwargs):
        if ticker == 'SECOND':
            raise BudgetExceeded('stop')
        return result
    monkeypatch.setattr(loop, 'run_ticker', run)
    monkeypatch.setattr(report, 'write_result', lambda *args: None)
    monkeypatch.setattr(report, 'write_run_summary', lambda *args: '')
    args = SimpleNamespace(tickers=['FIRST','SECOND'], run_id='guard', max_spend=1, mock=True)
    assert cli._run(args) == 1


def test_reused_output_does_not_retain_failed_brief(monkeypatch, tmp_path, filing, raw_facts):
    import harness.loop as loop
    import harness.report as report
    monkeypatch.setattr(loop, 'latest_filing', lambda _: filing)
    monkeypatch.setattr(loop, 'company_facts', lambda _: raw_facts)
    monkeypatch.setattr(report, 'OUT_DIR', tmp_path)
    result = run_ticker('TEST', MockGenerator(), MockJudge(), 'guard')
    output = report.write_result('guard', result)
    assert (output / 'brief.md').exists()
    result.brief = None
    result.status = 'FAILED'
    result.metrics.status = 'FAILED'
    report.write_result('guard', result)
    assert not (output / 'brief.md').exists()


@pytest.mark.parametrize('narrative', ['Growth was 77%.', 'Growth was 2%.', 'Revenue was $3.', 'Revenue was $2025.', 'Margin changed 50 bp.'])
def test_small_explicit_numeric_tokens_are_checked(narrative):
    pct, uncovered = narrative_coverage(Brief(ticker='TEST', narrative=narrative, claims=[]))
    assert pct < 100 and uncovered


def test_years_and_unqualified_small_counts_are_not_financial_claims():
    pct, uncovered = narrative_coverage(Brief(ticker='TEST', narrative='In 2025, 8 of 12 teams met.', claims=[]))
    assert pct == 100 and uncovered == []


def test_duplicate_claim_ids_are_rejected():
    q = QualitativeClaim(id='q', text='q', quality='tone')
    with pytest.raises(ValueError):
        Brief(ticker='TEST', narrative='q', claims=[q, q])


def test_judge_cannot_support_absent_citations(monkeypatch):
    import harness.generate as gen
    monkeypatch.setattr(gen, 'cached_call', lambda *a, **k: ('[{"claim_id":"q","supported":true,"reason":"ok"}]', {}))
    brief = Brief(ticker='TEST', narrative='q', claims=[QualitativeClaim(id='q', text='q', quality='tone', supporting_claim_ids=['absent'])])
    result = ApiJudge(client=SimpleNamespace(name='stub')).judge(brief, [])
    assert len(result) == 1 and result[0].supported is False


def test_corpus_rebuild_refuses_an_existing_dataset(monkeypatch, tmp_path):
    import harness.cli as cli
    (tmp_path / 'labels.yaml').write_text('preserved')
    def no_provider(*args):
        raise AssertionError('Refuse overwrite before resolving a provider')
    monkeypatch.setattr(cli, '_resolve_provider', no_provider)
    assert cli._corpus(SimpleNamespace(out=str(tmp_path), tickers=None)) == 2
    assert (tmp_path / 'labels.yaml').read_text() == 'preserved'
