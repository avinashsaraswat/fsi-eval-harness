#!/usr/bin/env python3
"""Spearman correlation between harness self-grade and expert grade over gold records.

Usage: python evals/correlation.py [--selftest]
Prints "insufficient expert grades (n<5)" until at least 5 records carry expert dims.
"""

from __future__ import annotations

import sys
from pathlib import Path

GOLD = Path(__file__).resolve().parent / "gold" / "briefs"
MIN_N = 5


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):                       # mean ranks for ties
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            mean_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = mean_rank
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return cov / (sx * sy)


def _load_pairs() -> list[tuple[float, float, float]]:
    """Per fully-graded record: (self_grade, expert_only, expert_grade).

    expert_only = mean(materiality, clarity) — the two dimensions the harness
    cannot see. Spearman(self_grade, expert_only) is the honest eval-of-the-eval:
    does deterministic reconciliation quality predict expert-perceived quality?

    expert_grade = mean(all four) is kept for reference only. It is self-correlated
    with self_grade by construction — it embeds the same two auto dims that make up
    self_grade (expert_grade == 0.5*self_grade + 0.5*expert_only), so correlating it
    against self_grade inflates rho and cannot cleanly fail.
    """
    import yaml  # PyYAML arrives with the venv only if installed; graceful message otherwise
    rows = []
    for p in sorted(GOLD.glob("*.yaml")):
        rec = yaml.safe_load(p.read_text())
        e = rec.get("expert") or {}
        if rec.get("self_grade") is None or e.get("materiality_judgment") is None or e.get("clarity") is None:
            continue
        auto = rec.get("auto") or {}
        expert_only = (e["materiality_judgment"] + e["clarity"]) / 2
        expert_grade = (auto.get("factual_precision", 0) + auto.get("completeness", 0)
                        + e["materiality_judgment"] + e["clarity"]) / 4
        rows.append((float(rec["self_grade"]), round(expert_only, 3), round(expert_grade, 3)))
    return rows


def main() -> int:
    if "--selftest" in sys.argv:
        rho = spearman([1, 2, 3, 4, 5], [1.1, 1.9, 3.2, 3.9, 5.0])
        assert abs(rho - 1.0) < 1e-9, rho
        rho2 = spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
        assert abs(rho2 + 1.0) < 1e-9, rho2
        rho3 = spearman([1, 2, 2, 3, 4], [1, 2, 2, 3, 5])  # ties handled
        assert rho3 > 0.9, rho3
        print("selftest OK (rho=+1 / -1 / ties handled)")
        return 0
    try:
        rows = _load_pairs()
    except ModuleNotFoundError:
        print("PyYAML not installed in this interpreter; run via .venv/bin/python", file=sys.stderr)
        return 2
    if len(rows) < MIN_N:
        print(f"insufficient expert grades ({len(rows)}/{MIN_N}) — grade records in evals/gold/briefs/")
        return 0
    xs = [r[0] for r in rows]
    expert_only = [r[1] for r in rows]
    expert_grade = [r[2] for r in rows]
    print(f"n={len(rows)}")
    print(f"  eval-of-the-eval  Spearman(self_grade, expert-only [materiality+clarity]) = {spearman(xs, expert_only):+.3f}")
    print(f"  reference (inflated · shares the auto dims)  Spearman(self_grade, all-four expert_grade) = {spearman(xs, expert_grade):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
