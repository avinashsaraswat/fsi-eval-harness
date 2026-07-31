"""SEC EDGAR client: ticker->CIK, submissions, XBRL company-facts. Disk-cached, throttled."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from .config import DATA_DIR, sec_user_agent
from .schema import FilingRef

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_THROTTLE_S = 0.15
_last_request_at = 0.0


def _get_json(url: str, *, refresh: bool = False) -> Any:
    """Fetch JSON with disk cache keyed by URL hash; throttled, fair-access UA."""
    global _last_request_at
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DATA_DIR / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text())

    wait = _THROTTLE_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    resp = httpx.get(url, headers={"User-Agent": sec_user_agent()}, timeout=30, follow_redirects=True)
    _last_request_at = time.monotonic()
    resp.raise_for_status()
    data = resp.json()
    cache_path.write_text(json.dumps(data))
    return data


def ticker_to_cik(ticker: str) -> str:
    """Zero-padded 10-digit CIK for a ticker."""
    table = _get_json(TICKERS_URL)
    for row in table.values():
        if row["ticker"].upper() == ticker.upper():
            return f"{int(row['cik_str']):010d}"
    raise KeyError(f"ticker {ticker!r} not found in SEC company_tickers.json")


def latest_filing(ticker: str) -> FilingRef:
    """Newest 10-Q or 10-K (amendments skipped) for the ticker."""
    cik = ticker_to_cik(ticker)
    subs = _get_json(SUBMISSIONS_URL.format(cik=cik))
    recent = subs["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form in ("10-Q", "10-K"):
            return FilingRef(
                ticker=ticker.upper(),
                cik=cik,
                form=form,
                accession=recent["accessionNumber"][i],
                filing_date=recent["filingDate"][i],
                report_date=recent["reportDate"][i],
                company_name=subs.get("name", ticker.upper()),
            )
    raise LookupError(f"no 10-Q/10-K found in recent filings for {ticker}")


def company_facts(ticker: str) -> dict:
    """Full XBRL company-facts JSON for the ticker."""
    cik = ticker_to_cik(ticker)
    return _get_json(COMPANYFACTS_URL.format(cik=cik))
