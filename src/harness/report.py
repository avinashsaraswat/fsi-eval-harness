"""Run artifacts: brief.md, droplog (jsonl + md), metrics.json, README metrics section."""

from __future__ import annotations

import json
from pathlib import Path

from tabulate import tabulate

from .config import OUT_DIR, REPO_ROOT
from .loop import BriefResult
from .metrics import Ledger, aggregate

README_BEGIN = "<!-- metrics:begin -->"
README_END = "<!-- metrics:end -->"


def write_result(run_id: str, r: BriefResult) -> Path:
    d = OUT_DIR / run_id / r.ticker
    d.mkdir(parents=True, exist_ok=True)

    if r.brief is not None:
        lines = [r.brief.narrative, "\n\n## Claims & verdicts\n"]
        vmap = {v.claim_id: v for v in r.verdicts}
        rows = []
        for c in r.brief.numeric_claims():
            v = vmap.get(c.id)
            rows.append([c.id, c.concept, f"{c.value:,.4g}", c.basis,
                         v.status if v else "—", v.reason if v else "—"])
        lines.append(tabulate(rows, headers=["claim", "concept", "value", "basis", "verdict", "reason"],
                              disable_numparse=True))
        if r.judge_verdicts:
            lines.append("\n\n## Judge (qualitative layer)\n")
            lines.append(tabulate([[j.claim_id, "✓" if j.supported else "✗", j.reason]
                                   for j in r.judge_verdicts], headers=["claim", "supported", "reason"]))
        (d / "brief.md").write_text("\n".join(lines))

    (d / "droplog.jsonl").write_text("\n".join(e.model_dump_json() for e in r.drops))
    (d / "droplog.md").write_text(tabulate(
        [[e.attempt, e.stage, e.item_ref, e.action, e.reason_code, e.detail[:80]] for e in r.drops],
        headers=["att", "stage", "item", "action", "reason", "detail"], tablefmt="github"))
    (d / "metrics.json").write_text(r.metrics.model_dump_json(indent=1))
    return d


def write_run_summary(run_id: str, results: list[BriefResult], ledger: Ledger, mode: str) -> str:
    agg = aggregate([r.metrics for r in results], ledger)
    rows = [[r.ticker, r.filing.form, r.status, r.metrics.attempts,
             f"{r.metrics.verified_pass}/{r.metrics.numeric_claims}",
             f"{r.metrics.routing_share_deterministic:.0%}",
             r.metrics.narrative_coverage_pct,
             sum(r.metrics.dropped_by_reason.values())] for r in results]
    table = tabulate(rows, headers=["ticker", "form", "status", "attempts", "numeric pass",
                                    "det-routing", "coverage %", "drops"], tablefmt="github")
    # Honest spend framing: a "real" run whose calls all hit the disk cache spends $0 — but
    # that is NOT the cold-run cost, and a bare "(real) … $0.00" reads as free. Label it.
    served_from_cache = mode == "real" and ledger.live_calls == 0 and ledger.cache_hits > 0
    mode_label = f"{mode} · cache-served" if served_from_cache else mode
    if served_from_cache:
        spend_line = (
            f"- Spend: **$0.00** — cache-served: all {ledger.cache_hits} model calls hit the local "
            f"cache, 0 live. This is a re-run over cached generations, **not** cold-run cost.\n"
        )
    else:
        cached = f", {ledger.cache_hits} cached" if ledger.cache_hits else ""
        spend_line = (
            f"- Spend: **${agg['total_spend_usd']:.2f}** "
            f"({agg['tokens_in']:,} in / {agg['tokens_out']:,} out tokens; "
            f"{ledger.live_calls} live call{'s' if ledger.live_calls != 1 else ''}{cached})\n"
        )
    summary = (
        f"### Run `{run_id}` ({mode_label})\n\n{table}\n\n"
        f"- Reconcile rate: **{agg['reconcile_rate']:.0%}** ({agg['reconciled']}/{agg['briefs']}; "
        f"{agg['partial']} partial, {agg['failed']} failed) · mean retries-to-reconcile: {agg['mean_retries_to_reconcile']}\n"
        f"- Pooled deterministic routing share: **{agg['pooled_routing_share_deterministic']:.0%}**\n"
        f"- Drops by reason: `{json.dumps(agg['drops_by_reason'])}`\n"
        + spend_line
    )
    out = OUT_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary)
    (out / "aggregate.json").write_text(json.dumps(agg, indent=1))
    return summary


def update_readme(summary: str) -> None:
    """Replace the metrics block in README.md (idempotent)."""
    readme = REPO_ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text()
    if README_BEGIN not in text:
        return
    head, rest = text.split(README_BEGIN, 1)
    _, tail = rest.split(README_END, 1)
    readme.write_text(head + README_BEGIN + "\n" + summary + "\n" + README_END + tail)
