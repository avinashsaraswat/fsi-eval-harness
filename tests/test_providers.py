"""Provider seam: cache behavior, client selection, config invariants."""

import pytest

from harness import config
from harness.generate import cached_call
from harness.judge import ApiJudge
from harness.providers import GeminiClient, make_client
from harness.schema import Brief, QualitativeClaim, Verdict


class CountingClient:
    """Stub provider: canned response, counts real calls."""
    name = "stub"

    def __init__(self, text="hello"):
        self.text = text
        self.calls = 0

    def complete(self, *, model, system, user, max_tokens):
        self.calls += 1
        return self.text, {"input_tokens": 10, "output_tokens": 5}


def test_cached_call_hits_cache_on_second_call(tmp_path, monkeypatch):
    import harness.generate as gen
    monkeypatch.setattr(gen, "CACHE_DIR", tmp_path)
    client = CountingClient()
    kw = dict(model="stub-model", system="s", user="u", max_tokens=100)

    text1, u1 = cached_call(client, **kw)
    text2, u2 = cached_call(client, **kw)

    assert client.calls == 1
    assert text1 == text2 == "hello"
    assert u1["cache_hit"] is False and u2["cache_hit"] is True


def test_cache_key_ignores_provider_identity(tmp_path, monkeypatch):
    """Same (model, system, user, max_tokens) replays from cache under ANY client —
    the model id, not the client, disambiguates cache entries."""
    import harness.generate as gen
    monkeypatch.setattr(gen, "CACHE_DIR", tmp_path)
    kw = dict(model="stub-model", system="s", user="u", max_tokens=100)

    cached_call(CountingClient("original"), **kw)
    other = CountingClient("should-not-be-called")
    text, usage = cached_call(other, **kw)

    assert text == "original" and usage["cache_hit"] is True
    assert other.calls == 0


def test_make_client_selects_and_rejects():
    assert isinstance(make_client("gemini"), GeminiClient)
    with pytest.raises(ValueError):
        make_client("openai")


def test_clients_are_lazy_no_key_needed_to_construct(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    GeminiClient()  # must not raise: cache replay must not require a key


def test_default_models_are_priced():
    for gen_model, judge_model in config.DEFAULT_MODELS.values():
        assert gen_model in config.PRICES, f"{gen_model} missing from PRICES"
        assert judge_model in config.PRICES, f"{judge_model} missing from PRICES"


def test_resolve_models_per_provider(monkeypatch):
    monkeypatch.delenv("HARNESS_GEN_MODEL", raising=False)
    monkeypatch.delenv("HARNESS_JUDGE_MODEL", raising=False)
    assert config.resolve_models("gemini") == config.DEFAULT_MODELS["gemini"]
    monkeypatch.setenv("HARNESS_GEN_MODEL", "override-model")
    assert config.resolve_models("gemini")[0] == "override-model"


def test_api_judge_parses_via_stub_client(tmp_path, monkeypatch):
    import harness.generate as gen
    monkeypatch.setattr(gen, "CACHE_DIR", tmp_path)
    canned = '[{"claim_id": "c2", "supported": false, "reason": "overreach"}]'
    judge = ApiJudge(CountingClient(canned), model="stub-model")
    brief = Brief(ticker="TST", narrative="n", claims=[
        QualitativeClaim(id="c2", text="q", quality="tone", supporting_claim_ids=["c1"]),
    ])
    verdicts = [Verdict(claim_id="c1", status="pass", reason="ok")]
    out = judge.judge(brief, verdicts)
    assert len(out) == 1 and out[0].supported is False
