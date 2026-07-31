"""Shared synthetic fixture: one fake 10-Q filer ("TEST") with realistic company-facts shape."""

import pytest

from harness.schema import FilingRef

REPORT = "2025-03-29"     # current quarter end
Q_START = "2024-12-29"    # 90 days
YTD_START = "2024-09-29"  # 181 days -> H1
PRIOR_END = "2024-03-30"
PRIOR_START = "2023-12-31"
ACCN = "0000000001-25-000001"
PRIOR_ACCN = "0000000001-24-000001"


def _dur(start, end, val, accn=ACCN, filed="2025-05-01", fy=2025, fp="Q2", form="10-Q"):
    return {"start": start, "end": end, "val": val, "accn": accn, "filed": filed,
            "fy": fy, "fp": fp, "form": form}


def _inst(end, val, accn=ACCN, filed="2025-05-01", fy=2025, fp="Q2", form="10-Q"):
    return {"end": end, "val": val, "accn": accn, "filed": filed, "fy": fy, "fp": fp, "form": form}


@pytest.fixture
def filing():
    return FilingRef(ticker="TEST", cik="0000000001", form="10-Q", accession=ACCN,
                     filing_date="2025-05-01", report_date=REPORT, company_name="Test Corp")


@pytest.fixture
def raw_facts():
    return {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _dur(Q_START, REPORT, 95_360_000_000.0),
            _dur(YTD_START, REPORT, 190_100_000_000.0),  # H1 — must NOT win over Q
            _dur(PRIOR_START, PRIOR_END, 90_750_000_000.0, accn=PRIOR_ACCN, filed="2024-05-02", fy=2024),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _dur(Q_START, REPORT, 24_780_000_000.0),
            _dur(PRIOR_START, PRIOR_END, 23_640_000_000.0, accn=PRIOR_ACCN, filed="2024-05-02", fy=2024),
        ]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            _dur(Q_START, REPORT, 1.65),
            _dur(PRIOR_START, PRIOR_END, 1.53, accn=PRIOR_ACCN, filed="2024-05-02", fy=2024),
        ]}},
        "OperatingIncomeLoss": {"units": {"USD": [_dur(Q_START, REPORT, 29_590_000_000.0)]}},
        "GrossProfit": {"units": {"USD": [_dur(Q_START, REPORT, 44_870_000_000.0)]}},
        # cash flow is YTD-only, like real 10-Qs — exercises ytd_ok
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _dur(YTD_START, REPORT, 53_900_000_000.0),
        ]}},
        "Assets": {"units": {"USD": [
            _inst(REPORT, 331_200_000_000.0),
            _inst(PRIOR_END, 337_400_000_000.0, accn=PRIOR_ACCN, filed="2024-05-02", fy=2024),
        ]}},
        "StockholdersEquity": {"units": {"USD": [_inst(REPORT, 66_800_000_000.0)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_inst(REPORT, 28_160_000_000.0)]}},
        # rd_expense, cost_of_revenue, shares deliberately absent (optional concepts)
    }}}
