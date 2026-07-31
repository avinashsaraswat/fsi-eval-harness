"""Central configuration: tickers, concept whitelist, tolerances, models, prices."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "edgar"
CACHE_DIR = REPO_ROOT / "cache"
OUT_DIR = REPO_ROOT / "out"

# 8 filers, 4 sectors; no banks/insurers (non-standard income-statement tagging).
# Data-driven, not sacred: swap any ticker whose M2 coverage matrix comes in weak.
# (XOM swapped -> CVX 2026-07-16: ticker now maps to a successor holding-co CIK with no 10-Q/K yet.)
TICKERS = ["AAPL", "MSFT", "NVDA", "COST", "KO", "JNJ", "CAT", "CVX"]

# Supported default provider: the Gemini API. The Anthropic path stays available
# behind the same seam (provider adapter in providers.py) — it replays the
# prototyping-era cache at $0 and is selectable via --provider / HARNESS_PROVIDER.
PROVIDER = os.environ.get("HARNESS_PROVIDER", "gemini")

# Per-provider (generation, judge) defaults. Both Gemini defaults sit on the GA
# flagship rather than a -preview id: a shipped default should not churn.
DEFAULT_MODELS = {
    "gemini": ("gemini-3.6-flash", "gemini-3.6-flash"),
    "anthropic": ("claude-opus-4-8", "claude-haiku-4-5-20251001"),
}

def resolve_models(provider: str) -> tuple[str, str]:
    """(gen_model, judge_model) for a provider; HARNESS_*_MODEL env vars win."""
    d = DEFAULT_MODELS.get(provider, DEFAULT_MODELS["gemini"])
    return (os.environ.get("HARNESS_GEN_MODEL", d[0]),
            os.environ.get("HARNESS_JUDGE_MODEL", d[1]))


GEN_MODEL, JUDGE_MODEL = resolve_models(PROVIDER)

# USD per MTok (input, output) — powers the cost ledger and --max-spend guard.
PRICES = {
    "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.1-pro-preview": (2.0, 12.0),   # <=200K-token tier; harness prompts are far below it
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

MAX_RETRIES = 2          # 3 total attempts
MIN_CLAIMS = 5           # reconciled/partial requires at least this many verified numeric claims
DEFAULT_MAX_SPEND = 10.0  # USD, hard abort

# Tolerances
REL_TOL_REPORTED = 0.005      # 0.5% relative for reported values ($94.9B-style rounding)
ABS_TOL_EPS = 0.005           # diluted EPS absolute
ABS_TOL_DERIVED_PCT = 0.5     # derived percentages, absolute percentage points

# Period classification by duration days.
# Bands accommodate 52/53-week retail calendars (Costco: 12/16-week quarters,
# 24/36-week YTDs -> 84/112/168/252 days).
PERIOD_BANDS = [
    ((80, 120), "Q"),
    ((160, 195), "H1"),
    ((245, 295), "9M"),
    ((350, 380), "FY"),
]


@dataclass(frozen=True)
class Concept:
    key: str
    tags: tuple[str, ...]        # priority synonym chain; first tag with data for the target period wins
    period_type: str             # "instant" | "duration"
    unit: str                    # expected XBRL unit
    optional: bool = False       # missing is fine (e.g. R&D outside tech)
    ytd_ok: bool = False         # 10-Q may report YTD only (cash-flow concepts); accept H1/9M when Q absent


CONCEPTS: dict[str, Concept] = {
    c.key: c
    for c in [
        Concept("revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"), "duration", "USD"),
        Concept("net_income", ("NetIncomeLoss", "ProfitLoss"), "duration", "USD"),
        Concept("eps_diluted", ("EarningsPerShareDiluted",), "duration", "USD/shares"),
        Concept("operating_income", ("OperatingIncomeLoss",), "duration", "USD", optional=True),
        Concept("gross_profit", ("GrossProfit",), "duration", "USD", optional=True),
        Concept("operating_cash_flow", ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), "duration", "USD", ytd_ok=True),
        Concept("assets", ("Assets",), "instant", "USD"),
        Concept("stockholders_equity", ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), "instant", "USD"),
        Concept("cash_and_equivalents", ("CashAndCashEquivalentsAtCarryingValue",), "instant", "USD", optional=True),
        Concept("rd_expense", ("ResearchAndDevelopmentExpense",), "duration", "USD", optional=True),
        Concept("cost_of_revenue", ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfSales"), "duration", "USD", optional=True),
        Concept("shares_outstanding_diluted", ("WeightedAverageNumberOfDilutedSharesOutstanding",), "duration", "shares", optional=True),
    ]
}


def sec_user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')
    if not ua:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. SEC fair-access policy requires a descriptive "
            "User-Agent with a contact email (see .env.example)."
        )
    return ua
