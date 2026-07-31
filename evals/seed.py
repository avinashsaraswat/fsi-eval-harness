#!/usr/bin/env python3
"""Seed gold-set grading records from a run's artifacts: evals/seed.py <run_id> [--limit N]."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "gold" / "briefs"


def scale(x: float, floor: float = 0.6) -> float:
    """Map [floor..1.0] -> [1..5]."""
    return round(max(1.0, min(5.0, 1 + 4 * (x - floor) / (1 - floor))), 2)


def seed(run_id: str, limit: int = 20) -> int:
    run_dir = ROOT / "out" / run_id
    if not run_dir.exists():
        print(f"no such run: {run_dir}", file=sys.stderr)
        return 1
    GOLD.mkdir(parents=True, exist_ok=True)
    n = 0
    for mpath in sorted(run_dir.glob("*/metrics.json")):
        if n >= limit:
            break
        m = json.loads(mpath.read_text())
        if m["status"] == "FAILED":
            continue
        rec_path = GOLD / f"{run_id}-{m['ticker']}.yaml"
        if rec_path.exists():
            continue  # never overwrite a possibly-graded record
        fp = scale(m["verified_pass"] / m["numeric_claims"] if m["numeric_claims"] else 0)
        comp = scale(m["narrative_coverage_pct"] / 100)
        self_grade = round((fp + comp) / 2, 2)
        rec_path.write_text(f"""run_id: "{run_id}"
ticker: "{m['ticker']}"
brief_path: "out/{run_id}/{m['ticker']}/brief.md"
generated_by: "{'mock' if run_id.endswith('mock') or 'mock' in run_id else 'real'}"
status: "{m['status']}"
auto:
  factual_precision: {fp}
  completeness: {comp}
expert:
  materiality_judgment: null
  clarity: null
  notes: ""
self_grade: {self_grade}
expert_grade: null
""")
        n += 1
    print(f"seeded {n} record(s) into evals/gold/briefs/ (existing records untouched)")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = 20
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1]); del args[i:i + 2]
    raise SystemExit(seed(args[0], limit) if args else (print("usage: seed.py <run_id>") or 1))
