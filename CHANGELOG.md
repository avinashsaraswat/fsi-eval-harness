# Changelog

## 0.2.0

- The LLM judge and generator run on Gemini only. The second provider adapter is removed, along
  with its `--provider` choice, its API-key setting and its SDK dependency. `--provider` now
  accepts only `gemini`.
- `docs/DETECTION.md` and the README report one judge configuration, `gemini-3.6-flash`, with the
  numbers already published for it on 2026-07-31. The earlier baseline from a non-Google judge is
  removed.
- README fixes: the no-key quickstart creates a virtual environment and installs the `dev` extra,
  so `pytest` is available. The `run --mock` comment now says it makes no model calls but needs
  `SEC_USER_AGENT` and network access.
- README: new "About the test data" section on where the fixed test corpus came from.
- `tests/conftest.py`: the fixture docstring now says the figures are Apple's fiscal Q2 2025
  values under a placeholder name, used as a fixed test vector. No test logic changed.
- Version 0.2.0 in `pyproject.toml` and `CITATION.cff`.
- First run without SEC_USER_AGENT, with the example placeholder, or without a contact email now stops with a one-line message instead of a traceback.
- Withdraw the mixed-unit prevalence estimate and unadjudicated qualitative specificity claim. Measurement JSON now uses schema version 2 with descriptive baseline counts.
- Validate judge response coverage, claim IDs and supporting citations. Reject incomplete or malformed verdicts.
- Check reported units and period types. Accept an exact zero against zero and reject constant-only derivations.
- Check small explicit currency, percentage and basis-point tokens. Withhold partial briefs when unsafe prose remains.
- Honor a zero spend threshold, reject invalid budgets and return failure after a budget abort. Remove stale output after failed reruns.
- Refuse to rebuild a corpus into a nonempty directory. Add offline regression tests and document remaining limitations.

## 0.1.1 (2026-09-09)

Commit `f2d7367`: names the author, pins each corpus item to the filing accession it was built
against, and adds `CITATION.cff` and the copyright line in `LICENSE`.
