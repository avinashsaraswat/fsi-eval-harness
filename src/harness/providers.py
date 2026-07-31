"""Provider clients: one completion shape over the Gemini and Anthropic APIs.

The supported default provider is the Gemini API (`gemini-3.6-flash` for both
generation and judging). The Anthropic client stays behind the same seam: it
replays the prototyping-era cache at $0 and remains selectable via
`--provider anthropic` / `HARNESS_PROVIDER=anthropic`.

Clients are lazy: no SDK import and no key needed until the first real call, so
fully-cached replays run without any provider credentials. Nothing outside this
module talks to a vendor SDK.
"""

from __future__ import annotations

from typing import Protocol


class ProviderClient(Protocol):
    name: str

    def complete(self, *, model: str, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        """Returns (text, usage) with usage = {input_tokens, output_tokens}."""
        ...


class AnthropicClient:
    name = "anthropic"

    def __init__(self):
        self._client = None  # lazy: cache replay must not require a key

    def complete(self, *, model: str, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        resp = self._client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                            messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, {"input_tokens": resp.usage.input_tokens,
                      "output_tokens": resp.usage.output_tokens}


class GeminiClient:
    name = "gemini"

    def __init__(self):
        self._client = None  # lazy: cache replay must not require a key

    def complete(self, *, model: str, system: str, user: str, max_tokens: int) -> tuple[str, dict]:
        if self._client is None:
            from google import genai
            self._client = genai.Client()  # reads GEMINI_API_KEY
        from google.genai import types
        # Gemini counts thinking tokens against max_output_tokens (first live run: 7996 of
        # 8000 spent thinking, 757 chars of fragments emitted). Treat the caller's max_tokens
        # as the FINAL-output budget: request low thinking and add headroom for it. The cache
        # key still uses the caller's max_tokens, so keys stay stable across providers.
        cfg = dict(system_instruction=system,
                   max_output_tokens=max_tokens + 8192,
                   # Every harness call expects a JSON object/array back (generator brief,
                   # judge verdicts); constrained decoding stops prose leaking into the text.
                   response_mime_type="application/json")
        try:
            config = types.GenerateContentConfig(
                **cfg, thinking_config=types.ThinkingConfig(thinking_level="low"))
        except Exception:  # SDK/model without thinking_level: run with headroom alone
            config = types.GenerateContentConfig(**cfg)
        resp = self._client.models.generate_content(model=model, contents=user, config=config)
        # Build text from non-thought parts; resp.text can interleave thought summaries.
        parts = resp.candidates[0].content.parts if resp.candidates else []
        text = "".join(p.text for p in parts
                       if getattr(p, "text", None) and not getattr(p, "thought", False))
        if not text:
            text = resp.text or ""
        um = resp.usage_metadata
        # Thinking tokens are billed as output; count them so the ledger stays honest.
        thoughts = getattr(um, "thoughts_token_count", 0) or 0
        return text, {"input_tokens": um.prompt_token_count or 0,
                      "output_tokens": (um.candidates_token_count or 0) + thoughts}


def make_client(provider: str) -> ProviderClient:
    if provider == "gemini":
        return GeminiClient()
    if provider == "anthropic":
        return AnthropicClient()
    raise ValueError(f"unknown provider: {provider!r} (expected 'gemini' or 'anthropic')")
