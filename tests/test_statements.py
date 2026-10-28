from pathlib import Path

import pytest

from madhippu import statements as ar
from madhippu.facts import check

# Synthetic annual-report pages: invented companies and figures, laid out the
# way PDFium extracts real reports' text layers, one layout quirk per page.
# Each section adds up, as in a real report.
FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
CRORE = 10**7


def pages(name):
    return (FIXTURES / name).read_text(encoding="utf-8").split("\f")


def only_statement(name):
    [statement] = ar.statements(pages(name))
    return statement


def clean(st):
    """No warnings, and every arithmetic identity holds."""
    return st.warnings == [] and check(st.facts()) == []


# --- Ind-AS balance sheets ----------------------------------------------------

BS = "indas_consolidated_balance_sheet.txt"


def test_balance_sheet():
    st = only_statement(BS)

    assert (st.kind, st.consolidated, st.columns, st.unit) == (
        "balance_sheet", True, ["2024-03-31", "2023-03-31"], CRORE)
    assert st.signed_off == "2024-04-18"
    assert clean(st)
    assert st.values["Assets"] == [59_358, 56_839]
    # The same label in two sections: by section, not label alone.
    assert st.values["NoncurrentInvestments"] == [145, 162]
    assert st.values["CurrentInvestments"] == [12_480, 14_215]
    assert st.values["TradeReceivablesCurrent"] == [18_322 + 3_671, 16_904 + 3_402]  # billed + unbilled


def test_lines_results_xbrl_groups_differently_get_their_own_names():
    st = only_statement(BS)

    assert st.values["LeaseLiabilitiesNoncurrent"] == [2_018, 1_861]
    assert st.values["RightOfUseAssets"] == [2_140, 1_985]
    # Results XBRL's OtherIntangibleAssets includes right-of-use assets.
    assert "OtherIntangibleAssets" not in st.values
    assert st.values["IntangibleAssetsOtherThanGoodwill"] == [233, 318]


def test_facts_are_point_in_time_rupees():
    facts = {(f.item, f.period_end): f for f in only_statement(BS).facts()}

    assets = facts[("Assets", "2024-03-31")]
    assert assets.period_start == assets.period_end
    assert assets.value == 593_580_000_000 and assets.unit == "INR"


def test_first_ind_as_year_has_three_columns():
    # The transition balance sheet's date in its own column, the title naming
    # all three dates, and the signatures above the table.
    st = only_statement("indas_first_year_three_columns.txt")

    assert st.columns == ["2017-03-31", "2016-03-31", "2015-04-01"]
    assert st.values["Assets"] == [59_358, 56_839, 48_488]
    assert st.values["BorrowingsNoncurrent"] == [35, 41, 57]  # "Long-term borrowings"
    assert st.signed_off == "2017-04-20"
    assert clean(st)


def test_statement_continued_on_next_page_with_wrapped_label():
    st = only_statement("indas_balance_sheet_two_pages.txt")

    assert st.values["EquityAndLiabilities"] == [59_358, 56_839]  # on the second page
    # "Equity attributable to shareholders of the / Company / 37,017 35,682"
    assert st.values["EquityAttributableToOwnersOfParent"] == [37_017, 35_682]
    assert clean(st)


def test_lost_thousands_comma_is_repaired_only_because_the_total_confirms_it():
    # "Lease liabilities 2 018 1,861": a note 2 and 18, or 2,018? Only 2,018
    # makes the non-current liabilities add up to their printed 3,049.
    st = only_statement("indas_balance_sheet_lost_thousands_comma.txt")

    assert st.values["LeaseLiabilitiesNoncurrent"] == [2_018, 1_861]
    assert st.values["NoncurrentLiabilities"] == [3_049, 2_956]
    [warning] = st.warnings
    assert "lease liabilities" in warning and "[2018.0, 1861.0]" in warning
    assert check(st.facts()) == []


def test_day_first_dates():
    st = only_statement("indas_balance_sheet_lost_thousands_comma.txt")

    assert st.columns == ["2026-03-31", "2025-03-31"]
    assert st.signed_off == "2026-04-14"


def _with_wrong_figure():
    page = "".join(pages(BS)).replace("Lease liabilities 5 2,018 1,861", "Lease liabilities 5 2,018 1,961")
    [st] = ar.balance_sheets([page])
    return st


def test_section_that_does_not_add_up_drops_its_lines_for_that_year():
    st = _with_wrong_figure()

    assert st.values["LeaseLiabilitiesNoncurrent"] == [2_018, None]
    assert st.values["DeferredTaxLiabilitiesNet"] == [402, None]
    assert st.values["NoncurrentLiabilities"] == [3_049, 2_956]  # totals kept
    assert st.values["LeaseLiabilitiesCurrent"] == [620, 598]  # other sections unaffected
    [warning] = st.warnings
    assert "total non-current liabilities (2023-03-31)" in warning and "dropped" in warning


def test_omitted_lines_are_known_per_column():
    # No borrowings line: a newer report showing none, or regrouping them.
    assert "BorrowingsNoncurrent" in only_statement(BS).omitted(1)
    # Dropped lines aren't "omitted": the statement did show them.
    assert "LeaseLiabilitiesNoncurrent" not in _with_wrong_figure().omitted(1)


def test_dates_split_from_their_years_and_note_numbers_with_decimals():
    # "As at March 31, As at April 1," over "2017 2016 2015"; rows like
    # "Property, plant and equipment 2.3 3,442 3,299 2,939".
    st = only_statement("indas_balance_sheet_dates_split_from_years.txt")

    assert st.columns == ["2017-03-31", "2016-03-31", "2015-04-01"]
    assert (st.consolidated, clean(st)) == (False, True)
    assert st.values["PropertyPlantAndEquipment"] == [3_442, 3_299, 2_939]
    assert st.values["Assets"] == [31_953, 29_092, 24_727]


def test_column_years_in_scrambled_text_order_are_newest_first():
    # The text layer reads the header years as "2015 2017 2016".
    st = only_statement("indas_balance_sheet_scrambled_header_years.txt")

    assert st.columns == ["2017-03-31", "2016-03-31", "2015-04-01"]
    assert st.values["Assets"] == [33_724, 30_992, 26_218] and clean(st)


def test_side_menu_naming_both_bases_is_not_a_consolidated_title():
    # Every page has a menu: "... STANDALONE ACCOUNTS CONSOLIDATED ACCOUNTS".
    st = only_statement("indas_balance_sheet_side_menu.txt")

    assert st.consolidated is False and clean(st)
    assert st.values["Equity"] == [8_625.63, 7_070.09]


def test_two_statements_side_by_side_on_one_page():
    # The profit and loss beside the balance sheet; labels run into dot
    # leaders, dates are "31st March, 2024", and the balance sheet's section
    # totals are unlabelled.
    bs, pl = ar.statements(pages("indas_profit_and_loss_beside_balance_sheet.txt"))

    assert (bs.kind, pl.kind) == ("balance_sheet", "profit_and_loss")
    assert bs.columns == pl.columns == ["2024-03-31", "2023-03-31"]
    assert clean(bs) and clean(pl)
    assert bs.values["NoncurrentLiabilities"] == [1_389.00, 1_595.78]  # under "Financial liabilities"
    assert bs.values["Equity"] == [13_069.17, 10_839.18]
    # "Income from Operations" is revenue plus investment income, as filed.
    assert pl.values["RevenueFromOperations"] == [50_609.71, 43_322.32]
    assert pl.values["ExceptionalItemsBeforeTax"] == [0, -714.77]


@pytest.mark.parametrize(
    "line, columns, expected",
    [
        ("Investments 8(a) 31,481 36,897", 2, ("Investments", [31_481, 36_897])),
        ("Deferred tax assets (net) 17 3,403 3,307", 2, ("Deferred tax assets (net)", [3_403, 3_307])),
        ("Unbilled 16 199", 2, ("Unbilled", [16, 199])),  # no note: both are values
        ("(b) Deferred tax liabilities (net) 6 (a) 308.80 235.48", 2,
         ("(b) Deferred tax liabilities (net)", [308.80, 235.48])),
        ("Borrowings 19(B) - 181", 2, ("Borrowings", [0, 181])),  # a dash is nil
        ("Exchange difference (1,234) 56", 2, ("Exchange difference", [-1_234, 56])),
        ("Total 1,234 2 3", 2, None),  # three values, two columns
        ("Financial assets", 2, None),
    ],
)
def test_split_line(line, columns, expected):
    assert ar.split_line(line, columns) == expected


def test_older_layouts_are_not_misread():
    # A revised Schedule VI page cut short: unlabelled subtotals and two
    # "TOTAL" lines, but no reserves and surplus, so not a balance sheet.
    page = "\n".join([
        "Consolidated Balance Sheet as at March 31, 2014", "(` crores)",
        "As at March 31, 2014 As at March 31, 2013", "I. EQUITY AND LIABILITIES",
        "(a) Share capital 3 97.52 97.52", "24425.06 19178.81", "TOTAL 33331.21 25899.07",
        "II. ASSETS", "Current assets", "(b) Inventories 17 7.55 10.50", "TOTAL 33331.21 25899.07",
    ])
    assert ar.balance_sheets([page]) == []


# --- Ind-AS profit and loss -----------------------------------------------------

def test_profit_and_loss():
    st = only_statement("indas_consolidated_profit_and_loss.txt")

    assert (st.kind, st.consolidated, st.columns) == ("profit_and_loss", True, ["2024-03-31", "2023-03-31"])
    assert clean(st)
    v = st.values
    assert v["RevenueFromOperations"] == [96_420, 89_705]
    assert v["Expenses"] == [72_860, 68_914]
    assert v["ProfitLossForPeriod"] == [18_628, 16_321]
    assert v["ProfitOrLossAttributableToOwnersOfParent"] == [18_552, 16_259]
    # "Provision for settlement of a legal claim 380" is a charge: profit
    # before tax is 25,372 - 380. The taxonomy adds exceptional items.
    assert v["ExceptionalItemsBeforeTax"] == [-380, 0]


def test_per_share_and_share_counts_are_not_scaled_to_crore():
    st = only_statement("indas_consolidated_profit_and_loss.txt")
    facts = {f.item: f for f in st.facts() if f.period_end == "2024-03-31"}

    assert facts["RevenueFromOperations"].value == 964_200_000_000
    eps = facts["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"]
    assert (eps.value, eps.unit) == (126.89, "INR/shares")
    assert facts["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations"].value == 126.89
    shares = facts["WeightedAverageNumberOfEquityShares"]
    assert (shares.value, shares.unit) == (1_462_050_000, "shares")
    assert (facts["RevenueFromOperations"].period_start, facts["RevenueFromOperations"].period_end) == (
        "2023-04-01", "2024-03-31")


def test_two_page_spread_resumes_after_the_footer():
    # One PDF page holding two printed pages: the left half ends with "Notes
    # forming part..." and signatures, the right half repeats the header.
    st = only_statement("indas_standalone_profit_and_loss_spread.txt")

    assert (st.consolidated, clean(st)) == (False, True)
    assert st.values["OtherComprehensiveIncomeNetOfTaxes"] == [70, 175]  # right half
    assert st.values["ProfitLossForPeriod"] == [13_335, 12_125]


def test_profit_for_the_period_and_tax_without_a_total_line():
    st = only_statement("indas_profit_for_the_period_without_tax_total.txt")

    assert st.values["ProfitLossForPeriod"] == [5_657, 5_204]
    assert st.values["TaxExpense"] == [2_099, 2_012]  # current + deferred
    assert st.values["DepreciationDepletionAndAmortisationExpense"] == [546, 457]  # "2.3 & 2.4"
    assert st.values["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations"] == [60.03, 55.26]
    assert check(st.facts()) == []


def test_discontinued_operations_and_associates_follow_the_filings():
    # The year's profit is the third profit line, and profit before tax
    # leaves out the share of associates, as results XBRL does. "Less:
    # Amounts capitalised" is subtracted after an unlabelled subtotal.
    st = only_statement("indas_profit_and_loss_discontinued_operations.txt")

    assert clean(st)
    v = st.values
    assert v["Expenses"] == [35_040.79, 35_789.10]
    assert v["ProfitBeforeTax"] == [2_035.53, 1_830.58]
    assert v["ProfitLossForPeriodFromContinuingOperations"] == [1_212.62, 842.78]
    assert v["ProfitLossForPeriod"] == [756.18, -160.49]
    assert v["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"] == [8.17, 0.58]
    assert v["BasicEarningsLossPerShareFromContinuingOperations"] == [15.09, 10.83]


# --- Ind-AS cash flows ------------------------------------------------------------

def test_cash_flow_over_two_pages_with_payments_signed_like_xbrl():
    statements = ar.statements(pages("indas_consolidated_cash_flows_two_pages.txt"))

    [st] = statements  # the second page isn't a statement of its own
    assert clean(st)
    v = st.values
    assert v["CashFlowsFromUsedInOperatingActivities"] == [17_929, 16_143]
    assert v["CashFlowsFromUsedInInvestingActivities"] == [2_070, -2_132]  # on the second page
    assert v["CashFlowsFromUsedInFinancingActivities"] == [-19_397, -19_160]
    # Printed as (880); results XBRL records payments as positive amounts.
    assert v["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"] == [880, 1_013]
    assert v["PaymentsOfLeaseLiabilitiesClassifiedAsFinancingActivities"] == [646, 606]
    # XBRL's dividends paid include minority shareholders'; kept apart here.
    assert v["DividendsPaidToShareholdersOfTheCompany"] == [10_055, 16_540]
    assert v["DividendsPaidToNonControllingInterests"] == [32, 25]
    assert v["BuybackOfEquityShares"] == [6_800, 0]


def test_cash_flow_from_singular_and_a_repeated_header_with_years():
    # "Cash flow from operating activities"; the second page repeats
    # "Particulars Note no. Year ended March 31," over "2019 2018".
    st = only_statement("indas_cash_flows_singular_heading_repeated_header.txt")

    assert clean(st)
    assert st.values["CashFlowsFromUsedInOperatingActivities"] == [5_302, 4_766]
    assert st.values["PurchaseOfFixedAssets"] == [922, 737]
    assert st.values["BuybackOfEquityShares"] == [325, 5_218]


def test_unlabelled_sum_in_a_cash_flow_section_is_a_subtotal():
    # "Cash generated from operations" printed without its label, before
    # taxes paid and "Net cash generated by operating activities".
    st = only_statement("indas_cash_flows_unlabelled_subtotal.txt")

    assert clean(st)
    assert st.values["CashFlowsFromUsedInOperatingActivities"] == [3_073, 3_570]
    assert st.values["IncomeTaxesPaidRefundClassifiedAsOperatingActivities"] == [2_596, 1_452]


def test_statement_beside_another_table_is_read_from_its_own_unit_line():
    # The cash flows start in the right half of a page whose left half is a
    # table of reserves.
    st = only_statement("indas_cash_flows_beside_another_table.txt")

    assert st.consolidated and check(st.facts()) == []
    assert st.values["CashFlowsFromUsedInOperatingActivities"] == [-2_495.12, -3_368.57]  # "Cash used in operations"
    assert st.values["CashFlowsFromUsedInFinancingActivities"] == [5_617.63, 7_521.40]


def test_lettered_cash_flow_sections():
    # "A. CASH FLOW FROM OPERATING ACTIVITIES :"; the 2009 operating column
    # includes a merged subsidiary's profit and doesn't add up, so it's dropped.
    st = only_statement("old_cash_flows_lettered_sections.txt")

    assert st.values["CashFlowsFromUsedInOperatingActivities"] == [1_154.93, 789.31]
    assert st.values["IncomeTaxesPaidRefundClassifiedAsOperatingActivities"] == [355.69, None]
    assert st.values["PurchaseOfFixedAssets"] == [483.53, 464.87]
    [warning] = st.warnings
    assert "(2009-03-31)" in warning and "dropped" in warning


# --- Indian GAAP: revised Schedule VI (FY12-FY16 reports) -------------------------

def test_schedule_vi_balance_sheet_unlabelled_totals():
    st = only_statement("schedule_vi_consolidated_balance_sheet.txt")

    assert (st.consolidated, st.columns) == (True, ["2014-03-31", "2013-03-31"])
    assert clean(st)
    v = st.values
    assert v["EquityAttributableToOwnersOfParent"] == [24_425.06, 19_178.81]  # shareholders' funds
    assert v["NonControllingInterest"] == [351.49, 345.19]
    assert v["NoncurrentLiabilities"] == [775.14, 583.96]
    # "(a) Fixed assets 12" over tangible, intangible and CWIP: a subtotal
    # inside non-current assets, not their total.
    assert v["FixedAssets"] == [5_184.96, 4_042.12]
    assert v["NoncurrentAssets"] == [12_034.16, 10_311.17]
    # "TOTAL" twice: the first closes equity and liabilities, the second assets.
    assert v["EquityAndLiabilities"] == v["Assets"] == [33_331.21, 25_899.07]
    # Not cash and cash equivalents: it includes longer bank deposits.
    assert v["CashAndBankBalances"] == [7_169.83, 3_360.58] and "CashAndCashEquivalents" not in v


def test_schedule_vi_profit_and_loss_profit_is_after_minority_interest():
    # Signatures above the table, "Profi t" (a split ligature) in the title,
    # and columns headed "Note 2012 2011".
    st = only_statement("schedule_vi_consolidated_profit_and_loss.txt")

    assert (st.consolidated, st.columns, st.signed_off) == (True, ["2012-03-31", "2011-03-31"], "2012-04-24")
    assert clean(st)
    v = st.values
    assert v["ProfitLossForPeriod"] == [5_261.72, 4_594.89]  # before minority interest
    assert v["ProfitOrLossAttributableToOwnersOfParent"] == [5_206.74, 4_534.01]  # "PROFIT FOR THE YEAR"
    # Tax includes MAT credit and fringe benefit tax: only the total is kept.
    assert v["TaxExpense"] == [1_699.94, 915.42] and "CurrentTax" not in v
    assert v["EmployeeBenefitExpense"] == [9_285.94, 6_925.27]  # "Employee benefi t expenses"


def test_bracketed_note_in_a_label_spanning_lines():
    # "(b) Trade payables (includes dues of micro and small enterprises ` 9.12
    #  crores (March 31, 2015: ` 4.88 crores)) / 2748.36 2119.80"
    st = only_statement("schedule_vi_balance_sheet_bracketed_label.txt")

    assert (st.consolidated, clean(st)) == (False, True)
    assert st.values["TradePayablesCurrent"] == [2_748.36, 2_119.80]


def test_schedule_vi_without_hyphens_and_with_rupees_crores():
    # "Long Term Borrowings", "Rupees crores", "Balance Sheet as at 31st March, 2015".
    st = only_statement("schedule_vi_balance_sheet_without_hyphens.txt")

    assert (st.consolidated, st.columns, clean(st)) == (False, ["2015-03-31", "2014-03-31"], True)
    assert st.values["BorrowingsNoncurrent"] == [1_257.07, 1_872.21]
    assert st.values["ProvisionsNoncurrent"] == [303.67, 255.17]


# --- Indian GAAP: old Schedule VI (FY10-FY11 reports) -----------------------------

def test_old_format_balance_sheet_sources_and_application_of_funds():
    st = only_statement("old_format_consolidated_balance_sheet.txt")

    assert (st.consolidated, st.columns, st.unit) == (True, ["2010-03-31", "2009-03-31"], CRORE)  # "Rs. in crores"
    assert clean(st)
    v = st.values
    assert v["EquityShareCapital"] == [147.86, 98.93]  # "Share Capital A": schedule letter, not a value
    assert v["EquityAttributableToOwnersOfParent"] == [9_233.37, 7_850.01]
    assert v["Borrowings"] == [51.63, 281.95]  # loan funds
    assert v["TotalFundsEmployed"] == [9_500.20, 8_334.82]
    assert v["NetCurrentAssets"] == [3_697.52, 3_772.07]


def test_old_format_profit_and_loss_with_title_at_the_foot_of_the_page():
    st = only_statement("old_format_consolidated_profit_and_loss.txt")

    # The title ("Consolidated Profit and Loss Account for the year ended
    # March 31, 2010") is printed below the table: it still decides the basis
    # and the columns' day, as the header shows only "Schedule 2010 ... 2009".
    assert (st.consolidated, st.columns) == (True, ["2010-03-31", "2009-03-31"])
    assert clean(st)
    v = st.values
    # Revenue is services (14,542.61) plus equipment sales (471.86).
    assert v["RevenueFromOperations"] == [15_014.47, 13_906.44]
    assert v["ProfitOrLossAttributableToOwnersOfParent"] == [3_500.32, 2_628.20]
    assert v["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"] == [23.67, 17.77]


@pytest.mark.parametrize(
    "label, expected",
    [
        ("III TOTAL INCOME", "total income"),
        ("IX.TOTAL COMPREHENSIVE INCOME FOR THE YEAR", "total comprehensive income for the year"),
        ("10 CURRENT ASSETS, LOANS AND ADVANCES", "current assets, loans and advances"),
        ("(A) (i) Items that will be reclassified", "items that will be reclassified"),
        ("(a) Employee benefi t expenses", "employee benefit expenses"),
        ("Long - term borrowings", "long-term borrowings"),
        ("Purchase of investments#", "purchase of investments"),
        ("Tax on buy-back of equity shares (Refer note 8(m))", "tax on buy-back of equity shares"),
        ("A. CASH FLOW FROM OPERATING ACTIVITIES :", "cash flow from operating activities"),
        ("Long Term Provisions", "long-term provisions"),
        ("Profit/(loss) for the year", "profit for the year"),
        ("(ia) Borrowings", "borrowings"),
    ],
)
def test_normalise(label, expected):
    assert ar.normalise(label) == expected
