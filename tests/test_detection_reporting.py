"""Exercise the real corpus runner offline without treating baseline labels as truth."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from harness.facts import build_fact_index
from harness.generate import MockGenerator
from harness.inject import build_items_for_brief
from harness.judge import MockJudge
from harness.loop import run_ticker


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('judge_kind', ['mock', 'none'])
def test_report_preserves_detections_without_unvalidated_rates(monkeypatch, tmp_path, filing, raw_facts, judge_kind):
    import harness.edgar as edgar
    import harness.loop as loop

    for mod in (edgar, loop):
        monkeypatch.setattr(mod, 'latest_filing', lambda ticker: filing)
        monkeypatch.setattr(mod, 'company_facts', lambda ticker: raw_facts)
    monkeypatch.setattr(edgar, 'filing_by_accession', lambda *args: filing)
    result = run_ticker('TEST', MockGenerator(), MockJudge(), run_id='report-test')
    brief = result.brief.model_copy(deep=True)
    # An unseeded baseline label need not mean the claim is supported.
    brief.qualitative_claims()[0].supporting_claim_ids = []
    index, _ = build_fact_index(raw_facts, filing)
    items, labels = build_items_for_brief(brief, index, filing)
    corpus = tmp_path / 'corpus'
    (corpus / 'items').mkdir(parents=True)
    for item in items:
        (corpus / 'items' / (item.item_id + '.yaml')).write_text(yaml.safe_dump(item.model_dump(mode='json')))
    (corpus / 'labels.yaml').write_text(yaml.safe_dump([label.model_dump(mode='json') for label in labels]))

    root = Path(__file__).resolve().parents[1]
    candidate = _load(root / 'evals/detection.py', 'candidate_reporting')
    monkeypatch.setattr(candidate, 'CORPUS', corpus)
    monkeypatch.setattr(candidate, 'OUT', tmp_path / 'candidate')
    monkeypatch.setattr(candidate, 'BOOT_REPS', 100)
    assert candidate.run(judge_kind, 0) == 0
    new = json.loads((candidate.OUT / f'results-{judge_kind}.json').read_text())
    assert new['rogan_gladen']['corrected'] is None
    assert new['rogan_gladen']['status'] == 'not_estimated'
    assert new['measurement_schema_version'] == 2
    assert 'tnr_qualitative' not in new
    assert 'tnr_numeric' not in new
    assert new['corpus_item_counts']['total_items'] == len(items)
    if judge_kind == 'mock':
        assert new['unseeded_qualitative']['flagged'] >= 1
        assert new['unseeded_qualitative']['reference_labels_independently_adjudicated'] is False
    report = (candidate.OUT / f'report-{judge_kind}.md').read_text()
    assert 'TNR' not in report
    assert 'In production the same correction' not in report
    assert 'No corrected-prevalence estimate' in report
