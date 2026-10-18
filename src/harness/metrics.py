"""Cost ledger (checks --max-spend before a new call) and per-brief/aggregate metrics."""

from __future__ import annotations

from collections import Counter
import math

from .config import DEFAULT_MAX_SPEND, MIN_CLAIMS, PRICES
from .schema import Brief, BriefMetrics, DropEntry, JudgeVerdict, Verdict


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, max_spend: float = DEFAULT_MAX_SPEND):
        if not math.isfinite(max_spend) or max_spend < 0:
            raise ValueError("max_spend must be finite and nonnegative")
        self.max_spend = max_spend
        self.tokens_in = 0
        self.tokens_out = 0
        self.spend = 0.0
        self.live_calls = 0   # billable API calls that actually hit the wire
        self.cache_hits = 0   # calls served from the disk cache ($0, 0 tokens)

    def add(self, model: str, tokens_in: int, tokens_out: int) -> None:
        pin, pout = PRICES.get(model, (5.0, 25.0))  # unknown model: conservative ceiling price
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.spend += tokens_in / 1e6 * pin + tokens_out / 1e6 * pout
        self.live_calls += 1

    def note_cache_hit(self) -> None:
        self.cache_hits += 1

    def precheck(self) -> None:
        if self.spend >= self.max_spend:
            raise BudgetExceeded(f"spend ${self.spend:.2f} >= --max-spend ${self.max_spend:.2f}")


def brief_metrics(ticker: str, status: str, attempts: int, brief: Brief,
                  verdicts: list[Verdict], judge_verdicts: list[JudgeVerdict],
                  drops: list[DropEntry], narrative_coverage_pct: float,
                  retries_to_reconcile: int | None) -> BriefMetrics:
    numeric = brief.numeric_claims()
    quals = brief.qualitative_claims()
    routed_det = len(numeric)
    routed_llm = len(quals)
    dropped = Counter(d.reason_code for d in drops if d.action == "dropped")
    return BriefMetrics(
        ticker=ticker,
        status=status,  # type: ignore[arg-type]
        attempts=attempts,
        claims_total=len(brief.claims),
        numeric_claims=len(numeric),
        qualitative_claims=len(quals),
        routed_deterministic=routed_det,
        routed_llm_judge=routed_llm,
        routing_share_deterministic=round(routed_det / (routed_det + routed_llm), 4) if (routed_det + routed_llm) else 0.0,
        verified_pass=sum(1 for v in verdicts if v.status == "pass"),
        failed_final=sum(1 for v in verdicts if v.status != "pass"),
        dropped_by_reason=dict(dropped),
        retries_to_reconcile=retries_to_reconcile,
        narrative_coverage_pct=narrative_coverage_pct,
    )


def decide_status(passing: int, any_failing: bool, coverage_ok: bool) -> str:
    if not any_failing and coverage_ok and passing >= MIN_CLAIMS:
        return "RECONCILED"
    if passing >= MIN_CLAIMS:
        return "PARTIAL"
    return "FAILED"


def aggregate(rows: list[BriefMetrics], ledger: Ledger) -> dict:
    n = len(rows)
    reconciled = sum(1 for r in rows if r.status == "RECONCILED")
    det = sum(r.routed_deterministic for r in rows)
    llm = sum(r.routed_llm_judge for r in rows)
    drops: Counter = Counter()
    for r in rows:
        drops.update(r.dropped_by_reason)
    retries = [r.retries_to_reconcile for r in rows if r.retries_to_reconcile is not None]
    return {
        "briefs": n,
        "reconciled": reconciled,
        "partial": sum(1 for r in rows if r.status == "PARTIAL"),
        "failed": sum(1 for r in rows if r.status == "FAILED"),
        "reconcile_rate": round(reconciled / n, 4) if n else 0.0,
        "mean_retries_to_reconcile": round(sum(retries) / len(retries), 2) if retries else None,
        "pooled_routing_share_deterministic": round(det / (det + llm), 4) if (det + llm) else 0.0,
        "drops_by_reason": dict(drops),
        "tokens_in": ledger.tokens_in,
        "tokens_out": ledger.tokens_out,
        "total_spend_usd": round(ledger.spend, 4),
        "live_calls": ledger.live_calls,
        "cache_hits": ledger.cache_hits,
    }
