#!/usr/bin/env python3
"""Judge-detection measurement over the blinded error-injected corpus (D5).

Replaces the saturated correlation eval (see correlation.py's nan finding, 2026-07-26):
instead of asking whether self_grade tracks expert grades — it can't, self_grade has zero
variance on shipped output — this measures whether the pipeline DETECTS planted errors.

  TPR (sensitivity)  per error class: share of injected items where the expected surface
                     flags the planted target
  TNR (specificity)  claim-level, on the clean items only: share of clean claims not flagged
  CIs                Wilson (item-level) + cluster bootstrap resampling the 8 base filers
                     (items derived from one filer share a brief and are not independent)
  Rogan-Gladen       corrected prevalence pi = (p_obs + Sp - 1) / (Se + Sp - 1), validated
                     against the corpus's own known injection prevalence
  sizing             n per class is JUSTIFIED from the floor: smallest n where a true-90%
                     detector's Wilson lower bound clears the 80% floor

Usage:
  python evals/detection.py                 # deterministic surfaces + rule-based mock judge, $0
  python evals/detection.py --judge real    # + LLM judge (budgeted, cached)
  python evals/detection.py --judge none    # deterministic surfaces only
  python evals/detection.py --selftest      # stats math self-checks, no corpus needed
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

CORPUS = REPO / "evals" / "corpus"
OUT = REPO / "out" / "detection"

TARGET, FLOOR, Z = 0.90, 0.80, 1.96
BOOT_REPS, BOOT_SEED = 10_000, 20260728


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = Z) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def cluster_bootstrap(outcomes: list[tuple[str, int]], reps: int = BOOT_REPS,
                      seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% percentile CI for a pooled proportion, resampling CLUSTERS (base filers).
    `outcomes` = (cluster_id, 0/1) per item. Coarse with 8 clusters — reported alongside
    Wilson, never alone."""
    by: dict[str, list[int]] = defaultdict(list)
    for cid, hit in outcomes:
        by[cid].append(hit)
    clusters = sorted(by)
    if not clusters:
        return (0.0, 1.0)
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        picked = [by[rng.choice(clusters)] for _ in clusters]
        hits = sum(sum(c) for c in picked)
        n = sum(len(c) for c in picked)
        stats.append(hits / n if n else 0.0)
    stats.sort()
    return (stats[int(0.025 * reps)], stats[int(0.975 * reps) - 1])


def rogan_gladen(p_obs: float, se: float, sp: float) -> float:
    denom = se + sp - 1
    if denom <= 0:
        return float("nan")  # an uninformative detector admits no correction
    return max(0.0, min(1.0, (p_obs + sp - 1) / denom))


def min_n_for_floor(target: float = TARGET, floor: float = FLOOR) -> int:
    """Smallest n where a detector at the 90% target yields a Wilson lower bound clearing
    the 80% floor — the D5 'size n from the floor' rule."""
    for n in range(5, 2001):
        if wilson(round(target * n), n)[0] >= floor:
            return n
    return -1


def _selftest() -> int:
    lo, hi = wilson(38, 40)
    assert 0.81 < lo < 0.87 and hi > 0.98, (lo, hi)
    assert wilson(0, 0) == (0.0, 1.0)
    assert abs(rogan_gladen(0.5, 1.0, 1.0) - 0.5) < 1e-12
    # se=.9, sp=.95, true pi=.4 -> p_obs = .4*.9 + .6*.05 = .39 -> corrected back to .4
    assert abs(rogan_gladen(0.39, 0.9, 0.95) - 0.4) < 1e-9
    assert rogan_gladen(0.5, 0.5, 0.5) != rogan_gladen(0.5, 0.9, 0.9)  # nan vs value
    n_min = min_n_for_floor()
    assert 30 <= n_min <= 80, n_min
    ci = cluster_bootstrap([("a", 1), ("a", 1), ("b", 1), ("b", 0)], reps=2000)
    assert 0.0 <= ci[0] <= ci[1] <= 1.0
    print(f"selftest OK (min n for 90% detector to clear 80% floor: {n_min})")
    return 0


# ---------------------------------------------------------------------------
# corpus run
# ---------------------------------------------------------------------------

def run(judge_kind: str, max_spend: float, provider: str | None = None) -> int:
    import yaml

    from harness.edgar import company_facts, filing_by_accession, latest_filing
    from harness.facts import build_fact_index
    from harness.schema import Brief
    from harness.verify import narrative_coverage_audit, verify_claim

    if not (CORPUS / "labels.yaml").exists():
        print("no corpus — run `python -m harness corpus` first", file=sys.stderr)
        return 1
    labels = {d["item_id"]: d for d in yaml.safe_load((CORPUS / "labels.yaml").read_text())}
    item_files = sorted((CORPUS / "items").glob("*.yaml"))

    judge = None
    ledger = None
    if judge_kind == "mock":
        from harness.judge import MockJudge
        judge = MockJudge()
    elif judge_kind == "real":
        import os

        from harness.config import PROVIDER, resolve_models
        from harness.judge import ApiJudge
        from harness.metrics import Ledger
        from harness.providers import make_client
        prov = provider or PROVIDER
        key_env = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[prov]
        if not os.environ.get(key_env):
            print(f"{key_env} not set — needed for --judge real.", file=sys.stderr)
            return 2
        _gen_model, judge_model = resolve_models(prov)
        ledger = Ledger(max_spend=max_spend)
        judge = ApiJudge(make_client(prov), model=judge_model, ledger=ledger)

    indexes: dict[tuple[str, str | None], object] = {}
    per_class: dict[str, list[dict]] = defaultdict(list)
    clean_numeric = [0, 0]   # [flagged, total] over clean items
    clean_qual = [0, 0]

    for f in item_files:
        rec = yaml.safe_load(f.read_text())
        lb = labels.get(rec["item_id"])
        if lb is None:
            print(f"  unlabeled item {rec['item_id']} — skipped", file=sys.stderr)
            continue
        t = rec["ticker"]
        # Pin to the accession the item was built against. Using latest_filing here
        # silently re-based the corpus on whatever the company had filed most recently,
        # so published numbers stopped reproducing as soon as a new 10-Q landed.
        acc = rec.get("accession")
        key = (t, acc)
        if key not in indexes:
            ref = filing_by_accession(t, acc) if acc else latest_filing(t)
            if acc is None:
                print(
                    f"  {t}: item {rec['item_id']} records no accession — "
                    "falling back to latest filing, this run is not reproducible",
                    file=sys.stderr,
                )
            indexes[key], _ = build_fact_index(company_facts(t), ref)
        index = indexes[key]
        brief = Brief.model_validate(rec["brief"])

        verdicts = [verify_claim(c, index) for c in brief.numeric_claims()]
        _pct, uncovered, _m = narrative_coverage_audit(brief, index)
        numeric_flags = {v.claim_id: v.reason for v in verdicts if v.status != "pass"}

        judge_flags: dict[str, str] = {}
        if judge is not None and lb["judge_needed"]:
            jvs = judge.judge(brief, verdicts)
            judge_flags = {jv.claim_id: jv.reason for jv in jvs if not jv.supported}

        cls = lb["error_class"]
        target = lb.get("target_claim_id")
        if cls == "clean":
            clean_numeric[0] += len(numeric_flags)
            clean_numeric[1] += len(brief.numeric_claims())
            if judge is not None:
                clean_qual[0] += len(judge_flags)
                clean_qual[1] += len(brief.qualitative_claims())
            hit = bool(numeric_flags or uncovered or judge_flags)  # any flag on clean = FP
            per_class[cls].append({"item": rec["item_id"], "ticker": t, "false_flag": hit})
            continue

        if cls in ("wrong_value", "wrong_period", "wrong_tag"):
            detected = target in numeric_flags
            expected_reason = lb["expected_surface"].split(":")[1]
            reason_ok = numeric_flags.get(target) == expected_reason
        elif cls == "omission":
            present_ids = {c.id for c in brief.claims}
            dangling = [q.id for q in brief.qualitative_claims()
                        if q.id in judge_flags
                        and any(sid not in present_ids for sid in q.supporting_claim_ids)]
            detected = bool(uncovered or dangling)
            reason_ok = detected
        else:  # fabrication
            detected = target in judge_flags
            reason_ok = detected
        per_class[cls].append({"item": rec["item_id"], "ticker": t, "detected": detected,
                               "reason_ok": reason_ok, "species": lb.get("species"),
                               "target": target or lb.get("target_concept")})

    # ---- aggregate -------------------------------------------------------
    n_min = min_n_for_floor()
    # indexes is keyed by (ticker, accession); clusters are filers, so count tickers.
    n_filers = len({t for t, _ in indexes})
    lines = [f"# Judge-detection report — judge: {judge_kind}", "",
             f"Corpus: {sum(len(v) for v in per_class.values())} items over {n_filers} filers. "
             f"Floor {FLOOR:.0%} / target {TARGET:.0%}; Wilson 95% + cluster bootstrap "
             f"({n_filers} clusters, {BOOT_REPS} reps, seed {BOOT_SEED}).",
             "",
             f"**Sizing rule:** a true-{TARGET:.0%} detector needs **n ≥ {n_min}** per class for its "
             f"Wilson lower bound to clear the {FLOOR:.0%} floor.",
             "", "| class | n | detected | TPR | Wilson 95% | bootstrap 95% | vs floor/target |",
             "|---|---|---|---|---|---|---|"]
    results: dict = {"judge": judge_kind, "min_n_for_floor": n_min, "classes": {}}

    inj_hits, inj_total = 0, 0
    for cls in ("wrong_value", "wrong_period", "wrong_tag", "omission", "fabrication"):
        rows = per_class.get(cls, [])
        if not rows:
            continue
        k = sum(r["detected"] for r in rows)
        n = len(rows)
        inj_hits += k
        inj_total += n
        lo, hi = wilson(k, n)
        blo, bhi = cluster_bootstrap([(r["ticker"], int(r["detected"])) for r in rows])
        verdict = ("meets target" if lo >= TARGET else
                   "meets floor" if lo >= FLOOR else
                   "below floor" if n >= n_min or hi < FLOOR else "underpowered")
        lines.append(f"| {cls} | {n} | {k} | {k / n:.1%} | [{lo:.1%}, {hi:.1%}] | "
                     f"[{blo:.1%}, {bhi:.1%}] | {verdict} |")
        results["classes"][cls] = {"n": n, "detected": k, "tpr": round(k / n, 4),
                                   "wilson95": [round(lo, 4), round(hi, 4)],
                                   "bootstrap95": [round(blo, 4), round(bhi, 4)],
                                   "verdict": verdict}
        if cls == "fabrication":
            by_species: dict[str, list] = defaultdict(list)
            for r in rows:
                by_species[r["species"]].append(r["detected"])
            results["classes"][cls]["by_species"] = {
                s: {"n": len(v), "detected": sum(v)} for s, v in sorted(by_species.items())}

    fn_num, n_num = clean_numeric
    tnr_num = 1 - fn_num / n_num if n_num else float("nan")
    lines += ["", "## Specificity (clean items only — claim level)", ""]
    lines.append(f"- numeric claims: {n_num - fn_num}/{n_num} unflagged — TNR {tnr_num:.1%}, "
                 f"Wilson [{wilson(n_num - fn_num, n_num)[0]:.1%}, {wilson(n_num - fn_num, n_num)[1]:.1%}]. "
                 f"*Conditioned on reconciled output — clean briefs are the harness's own survivors.*")
    results["tnr_numeric"] = {"n": n_num, "false_flags": fn_num,
                              "tnr": round(tnr_num, 4) if n_num else None}
    tnr_q = None
    if judge is not None and clean_qual[1]:
        fq, nq = clean_qual
        tnr_q = 1 - fq / nq
        lines.append(f"- qualitative claims ({judge_kind} judge): {nq - fq}/{nq} unflagged — "
                     f"TNR {tnr_q:.1%}, Wilson [{wilson(nq - fq, nq)[0]:.1%}, {wilson(nq - fq, nq)[1]:.1%}]")
        results["tnr_qualitative"] = {"n": nq, "false_flags": fq, "tnr": round(tnr_q, 4)}

    if inj_total:
        se = inj_hits / inj_total
        sp = tnr_num if tnr_q is None else (tnr_num + tnr_q) / 2
        clean_n = len(per_class.get("clean", []))
        clean_fp = sum(r["false_flag"] for r in per_class.get("clean", []))
        total_items = inj_total + clean_n
        true_prev = inj_total / total_items
        p_obs = (inj_hits + clean_fp) / total_items
        corrected = rogan_gladen(p_obs, se, sp)
        lines += ["", "## Rogan-Gladen check", "",
                  f"Pooled Se {se:.1%}, Sp {sp:.1%}. Observed item flag rate {p_obs:.1%} on a corpus with "
                  f"true injection prevalence {true_prev:.1%} → corrected estimate **{corrected:.1%}**. "
                  f"In production the same correction turns the pipeline's observed flag rate into a "
                  f"true-error-prevalence estimate.",]
        results["rogan_gladen"] = {"se": round(se, 4), "sp": round(sp, 4),
                                   "p_obs": round(p_obs, 4), "true_prevalence": round(true_prev, 4),
                                   "corrected": round(corrected, 4)}

    lines += ["", "## Reading the omission row", "",
              "Omission TPR measures a *designed blindness*: reconciliation checks what is present "
              "and cannot see what is absent. Any non-zero detection comes from dangling citations "
              "(a kept qualitative claim citing a removed numeric) — an incidental path, not a "
              "completeness check. This is the paper's negative-result core, stated as a measured "
              "number rather than an assertion.", ""]
    if ledger is not None:
        lines.append(f"*Judge spend this run: ${ledger.spend:.4f} "
                     f"(cache hits: {getattr(ledger, 'cache_hits', '?')})*")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"report-{judge_kind}.md").write_text("\n".join(lines) + "\n")
    (OUT / f"results-{judge_kind}.json").write_text(json.dumps(results, indent=1) + "\n")
    print("\n".join(lines))
    print(f"\nartifacts: {OUT}/report-{judge_kind}.md · results-{judge_kind}.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TPR/TNR detection measurement over the injected corpus")
    ap.add_argument("--judge", choices=["mock", "real", "none"], default="mock")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default=None,
                    help="judge provider for --judge real (default: HARNESS_PROVIDER env or gemini)")
    ap.add_argument("--max-spend", type=float, default=3.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    return run(args.judge, args.max_spend, provider=args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
