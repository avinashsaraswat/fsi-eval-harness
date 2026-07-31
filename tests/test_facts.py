from harness.facts import build_fact_index, classify_duration, render_fact_sheet


def test_classify_duration_bands():
    assert classify_duration(90) == "Q"
    assert classify_duration(181) == "H1"
    assert classify_duration(273) == "9M"
    assert classify_duration(365) == "FY"
    assert classify_duration(140) is None


def test_index_resolves_whitelist(raw_facts, filing):
    index, coverage = build_fact_index(raw_facts, filing)
    rev = index.lookup("revenue", "2024-12-29", "2025-03-29")
    assert rev is not None and rev.value == 95_360_000_000.0
    assert rev.gaap_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    # H1 YTD must not have been selected as the current revenue period
    assert all(not (f.concept == "revenue" and f.duration_days and f.duration_days > 100
                    and f.end == "2025-03-29") for f in index.facts)


def test_prior_year_comparative_indexed(raw_facts, filing):
    index, _ = build_fact_index(raw_facts, filing)
    prior = index.lookup("revenue", "2023-12-31", "2024-03-30")
    assert prior is not None and prior.value == 90_750_000_000.0
    assert prior.fallback_accession is True  # came from the prior filing, flagged


def test_ytd_ok_fallback_for_cash_flow(raw_facts, filing):
    index, coverage = build_fact_index(raw_facts, filing)
    assert coverage["operating_cash_flow"] != "MISSING"
    ocf = [f for f in index.facts if f.concept == "operating_cash_flow" and f.end == filing.report_date]
    assert len(ocf) == 1 and ocf[0].duration_days > 100  # the H1 fact was accepted


def test_optional_missing_is_reported(raw_facts, filing):
    _, coverage = build_fact_index(raw_facts, filing)
    assert coverage["rd_expense"] == "MISSING"
    assert coverage["assets"] == "Assets"


def test_instant_concepts(raw_facts, filing):
    index, _ = build_fact_index(raw_facts, filing)
    assets = index.lookup("assets", None, "2025-03-29")
    assert assets is not None and assets.period_type == "instant" and assets.value == 331_200_000_000.0


def test_fact_sheet_renders(raw_facts, filing):
    index, _ = build_fact_index(raw_facts, filing)
    sheet = render_fact_sheet(index, filing)
    assert "Test Corp" in sheet and "f1" in sheet and "95,360,000,000.00" in sheet
