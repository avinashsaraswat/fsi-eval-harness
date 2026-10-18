"""CLI: python -m harness {run,fetch,coverage}."""

from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv

from .config import ConfigError


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="harness", description="FSI eval-harness: signal -> verify -> regenerate")
    sub = p.add_subparsers(dest="cmd", required=True)

    prun = sub.add_parser("run", help="run the loop over tickers")
    mode = prun.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="deterministic generator + judge, $0")
    mode.add_argument("--real", action="store_true",
                      help="API generator + judge (default provider: gemini; needs its key)")
    prun.add_argument("--provider", choices=["gemini"], default=None,
                      help="LLM provider (default: HARNESS_PROVIDER env or gemini)")
    prun.add_argument("--tickers", nargs="+", default=None)
    prun.add_argument("--max-spend", type=float, default=None)
    prun.add_argument("--run-id", default=None)

    pfetch = sub.add_parser("fetch", help="fetch + show target filing and 3 sample facts")
    pfetch.add_argument("ticker")

    pcov = sub.add_parser("coverage", help="concept-coverage matrix across tickers")
    pcov.add_argument("--tickers", nargs="+", default=None)

    padv = sub.add_parser("adversarial",
                          help="inject a citation error into a real cached brief; show the verifier catch (red->green)")
    padv.add_argument("--ticker", default="AAPL")
    padv.add_argument("--provider", choices=["gemini"], default=None,
                      help="provider whose cached brief to replay (default: HARNESS_PROVIDER env or gemini)")
    padv.add_argument("--allow-spend", action="store_true",
                      help="permit one live generation if the brief is not cached (default: $0, cache-only)")

    pcorpus = sub.add_parser("corpus",
                             help="build the blinded error-injected detection corpus from cached real briefs ($0)")
    pcorpus.add_argument("--tickers", nargs="+", default=None)
    pcorpus.add_argument("--provider", choices=["gemini"], default=None,
                         help="provider whose cached generation chain to replay (default: HARNESS_PROVIDER env or gemini)")
    pcorpus.add_argument("--out", default="evals/corpus")

    args = p.parse_args(argv)
    try:
        if args.cmd == "fetch":
            return _fetch(args.ticker)
        if args.cmd == "coverage":
            return _coverage(args.tickers)
        if args.cmd == "adversarial":
            return _adversarial(args)
        if args.cmd == "corpus":
            return _corpus(args)
        return _run(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _fetch(ticker: str) -> int:
    from .edgar import company_facts, latest_filing
    from .facts import build_fact_index
    filing = latest_filing(ticker)
    print(f"{filing.company_name} ({filing.ticker}) — {filing.form} {filing.accession} "
          f"period={filing.report_date} filed={filing.filing_date}")
    index, _cov = build_fact_index(company_facts(ticker), filing)
    for f in index.facts[:3]:
        print(f"  {f.fact_id} {f.concept} [{f.gaap_tag}] {f.period_label} = {f.value:,.4g} {f.unit}")
    return 0


def _coverage(tickers: list[str] | None) -> int:
    from tabulate import tabulate

    from .config import CONCEPTS, TICKERS
    from .edgar import company_facts, latest_filing
    from .facts import build_fact_index
    tickers = tickers or TICKERS
    matrix = {}
    for t in tickers:
        filing = latest_filing(t)
        _idx, cov = build_fact_index(company_facts(t), filing)
        matrix[t] = cov
    rows = [[key] + [("—" if matrix[t][key] == "MISSING" else "✓") for t in tickers]
            for key in CONCEPTS]
    print(tabulate(rows, headers=["concept"] + tickers, tablefmt="github"))
    weak = [t for t in tickers
            if sum(1 for k, c in CONCEPTS.items() if not c.optional and matrix[t][k] == "MISSING")]
    print("\nTickers missing NON-optional concepts (swap candidates):", weak or "none")
    return 0


def _resolve_provider(args):
    """(provider, gen_model, judge_model, client) from --provider / HARNESS_PROVIDER."""
    from .config import PROVIDER, resolve_models
    from .providers import make_client
    provider = getattr(args, "provider", None) or PROVIDER
    gen_model, judge_model = resolve_models(provider)
    return provider, gen_model, judge_model, make_client(provider)


_KEY_ENV = {"gemini": "GEMINI_API_KEY"}


def _key_missing(provider: str) -> bool:
    import os
    return not os.environ.get(_KEY_ENV[provider])


def _adversarial(args) -> int:
    provider, gen_model, _judge_model, client = _resolve_provider(args)
    if args.allow_spend and _key_missing(provider):
        print(f"{_KEY_ENV[provider]} not set — needed for --allow-spend.", file=sys.stderr)
        return 2
    from .adversarial import demonstrate
    return demonstrate(args.ticker, args.allow_spend, client=client, model=gen_model)


def _corpus(args) -> int:
    """Build the blinded detection corpus. Cache replay only — a ticker whose generation
    chain is not fully cached is skipped, never regenerated (no surprise spend)."""
    from pathlib import Path

    import yaml

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"Corpus output is not empty: {out}. Choose a new --out directory.", file=sys.stderr)
        return 2

    # Cache replay needs no API key: clients are lazy and max_spend=0 aborts any miss.
    _provider, gen_model, judge_model, client = _resolve_provider(args)

    from .config import TICKERS
    from .edgar import company_facts, latest_filing
    from .facts import build_fact_index, render_fact_sheet
    from .generate import ApiGenerator
    from .inject import build_items_for_brief
    from .judge import ApiJudge
    from .loop import run_ticker
    from .metrics import BudgetExceeded, Ledger

    tickers = args.tickers or TICKERS
    out = Path(args.out)
    (out / "items").mkdir(parents=True, exist_ok=True)
    (out / "factsheets").mkdir(parents=True, exist_ok=True)

    all_items, all_labels, skipped = [], [], []
    for t in tickers:
        ledger = Ledger(max_spend=0.0)  # replay must be free; a cache miss aborts this ticker
        try:
            r = run_ticker(t, ApiGenerator(client, model=gen_model, ledger=ledger),
                           ApiJudge(client, model=judge_model, ledger=ledger),
                           run_id="corpus-replay", ledger=ledger)
        except BudgetExceeded:
            skipped.append((t, "generation chain not fully cached"))
            continue
        if r.status != "RECONCILED" or r.brief is None:
            skipped.append((t, f"replayed status {r.status}, corpus needs a clean base"))
            continue
        index, _cov = build_fact_index(company_facts(t), r.filing)
        items, labels = build_items_for_brief(r.brief, index, r.filing)
        all_items.extend(items)
        all_labels.extend(labels)
        (out / "factsheets" / f"{t}.md").write_text(render_fact_sheet(index, r.filing))
        print(f"{t}: {len(items)} items ({r.metrics.numeric_claims} numeric / "
              f"{r.metrics.qualitative_claims} qualitative claims in base)")

    if not all_items:
        print("no corpus items built — nothing cached?", file=sys.stderr)
        return 1

    for item in all_items:
        (out / "items" / f"{item.item_id}.yaml").write_text(
            yaml.safe_dump(item.model_dump(), sort_keys=False, allow_unicode=True, width=100))
    labels_sorted = sorted((lb.model_dump() for lb in all_labels), key=lambda d: d["item_id"])
    (out / "labels.yaml").write_text(
        yaml.safe_dump(labels_sorted, sort_keys=False, allow_unicode=True, width=100))

    by_class: dict[str, int] = {}
    for lb in all_labels:
        by_class[lb.error_class] = by_class.get(lb.error_class, 0) + 1
    counts = "\n".join(f"| {k} | {v} |" for k, v in sorted(by_class.items()))
    (out / "MANIFEST.md").write_text(
        "# Detection corpus — blinded mixed set (clean + error-injected)\n\n"
        "Built by `python -m harness corpus` from cached briefs. Seeded variants contain one planted\n"
        "error each; unseeded baselines have none added. Item ids are stable identifiers, not content checksums. **Labels live ONLY in\n"
        "`labels.yaml` — an expert labeling blind must not open it.**\n\n"
        f"| class | items |\n|---|---|\n{counts}\n| **total** | **{len(all_items)}** |\n\n"
        "## Blind labeling protocol (expert)\n\n"
        "1. Take `items/*.yaml` and the matching `factsheets/<ticker>.md` (XBRL ground truth).\n"
        "2. For each item, record: is the brief clean or does it contain an error? If an error —\n"
        "   which claim/sentence, and what kind (wrong number · wrong period · wrong tag ·\n"
        "   material omission · unsupported assertion)?\n"
        "3. Return the label sheet; score against `labels.yaml` (inter-rater vs machine —\n"
        "   never the eval-of-the-eval).\n\n"
        "## Machine detection\n\n"
        "Run `python evals/detection.py` (deterministic surfaces, $0) or\n"
        "`python evals/detection.py --judge real` (adds the LLM judge; budgeted).\n")
    print(f"\ncorpus: {len(all_items)} items -> {out}/items · labels -> {out}/labels.yaml")
    for t, why in skipped:
        print(f"  skipped {t}: {why}", file=sys.stderr)
    return 0


def _run(args) -> int:
    from .config import DEFAULT_MAX_SPEND, TICKERS
    from .loop import run_ticker
    from .metrics import BudgetExceeded, Ledger
    from .report import update_readme, write_result, write_run_summary

    tickers = args.tickers or TICKERS
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S") + ("-mock" if args.mock else "-real")
    ledger = Ledger(max_spend=DEFAULT_MAX_SPEND if args.max_spend is None else args.max_spend)

    if args.mock:
        from .generate import MockGenerator
        from .judge import MockJudge
        generator, judge = MockGenerator(), MockJudge()
    else:
        provider, gen_model, judge_model, client = _resolve_provider(args)
        if _key_missing(provider):
            print(f"{_KEY_ENV[provider]} not set — export it (or use --mock).", file=sys.stderr)
            return 2
        from .generate import ApiGenerator
        from .judge import ApiJudge
        generator = ApiGenerator(client, model=gen_model, ledger=ledger)
        judge = ApiJudge(client, model=judge_model, ledger=ledger)

    results = []
    aborted = False
    for t in tickers:
        try:
            r = run_ticker(t, generator, judge, run_id, ledger=ledger)
        except BudgetExceeded as exc:
            print(f"BUDGET ABORT before {t}: {exc}", file=sys.stderr)
            aborted = True
            break
        results.append(r)
        write_result(run_id, r)
        print(f"{t}: {r.status} (attempts={r.metrics.attempts}, "
              f"pass={r.metrics.verified_pass}/{r.metrics.numeric_claims}, "
              f"coverage={r.metrics.narrative_coverage_pct}%)")

    if results:
        summary = write_run_summary(run_id, results, ledger, "mock" if args.mock else "real")
        if not args.mock:
            update_readme(summary)  # mock runs never overwrite the README's real-run headline block
        print(f"\nartifacts: out/{run_id}/  ·  spend ${ledger.spend:.2f}")
    return 0 if not aborted and results and all(r.status != "FAILED" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
