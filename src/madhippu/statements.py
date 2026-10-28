"""Parse financial statements from an annual report's text layer.

Deterministic, no AI. Each statement (balance sheet, profit and loss, cash
flows) is described by a `Layout`: the lines that identify its page, its
sections and their totals, and how its labels map onto item names. Parsing
is the same for all of them:

1. Find the page by structure (the rows it must contain), joining the next
   page when the statement continues there.
2. Split each line into label, note reference and one value per column;
   labels that wrap over several lines are joined.
3. Check that every section's lines add up to its printed total (and every
   running subtotal to the lines before it). Where one doesn't, a line may
   have been misread — the text layer sometimes loses a thousands comma,
   turning "9,453" into "9 453" — so the other readings of doubtful lines
   are tried, and one is accepted only if it alone makes the totals agree.
   Otherwise the section's lines are dropped for that year and its total
   kept.
4. Map labels to items. SEBI taxonomy names are used only where results
   XBRL means the same thing; lines it groups differently (it folds
   right-of-use assets into intangibles, lease liabilities into other
   financial liabilities, dividends to minority shareholders into dividends
   paid) get names of their own, so the two sources never disagree under
   one name.

Layouts: Ind-AS (FY17 onward), Indian GAAP under revised Schedule VI (FY12 to
FY16 reports) and under the old Schedule VI (to FY11), for the balance sheet
and profit and loss; one cash flow layout serves all of them. Written against
TCS's, Infosys's and Mahindra & Mahindra's reports, FY10 to FY26, and KEC
International's and Ather Energy's FY26; another
company's wording may need rules added, and a statement that can't be read
yields warnings rather than guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from fnmatch import fnmatchcase

from madhippu.facts import Fact

UNITS = {"crore": 10**7, "crores": 10**7, "lakh": 10**5, "lakhs": 10**5,
         "million": 10**6, "millions": 10**6}
# "(` crore)": the rupee sign comes out as a backtick (or "H") in many fonts.
_UNIT = re.compile(
    r"\(\s*(?:`|₹|H|Rs\.?)\s*(?:in\s+)?(crores?|lakhs?|millions?)\s*\)"
    r"|\bRs\.?\s+in\s+(crores?|lakhs?|millions?)\b"  # "Rs. in crores", unbracketed
    r"|(?:^|\s)Rupees\s+(?:in\s+)?(crores?|lakhs?|millions?)\s*$"  # "... March, 2015 Rupees crores"
    r"|(?:^|\s)(?:`|₹)\s*(?:in\s+)?(crores?|lakhs?|millions?)\s*$"  # "` crores", "` in Crore" on its own
    r"|\bin\s+(?:`|₹)\s*(crores?|lakhs?|millions?)\b",  # "(In ₹ crore, except per share data)"
    re.I | re.M,
)


_AMOUNTS_NOTE = re.compile(r"\((?:all\s+)?amounts?\b[^()]*\)\s*$", re.I)


def _is_unit_line(line: str) -> bool:
    """A statement's unit line: short ("` in Crore"), or a bracketed note
    closing the line ("CIN: ... (Amounts in ₹ crores, unless otherwise
    stated)"); not a unit inside a table's long header."""
    return _UNIT.search(line) is not None and (len(line.split()) <= 4 or _AMOUNTS_NOTE.search(line) is not None)


def _unit(text: str) -> int | None:
    m = _UNIT.search(text)
    return UNITS[next(g for g in m.groups() if g).lower()] if m else None
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# "March 31, 2024" or "31 March 2026".
# "March 31, 2024", "31 March 2024", "31st March, 2024"
_DATE = re.compile(rf"\b(?:({_MONTHS})\s+(\d{{1,2}}),\s*|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS}),?\s+)(\d{{4}})")
_SIGN_OFF = re.compile(rf"\b[A-Z][a-z]+,\s+(?:({_MONTHS})\s+(\d{{1,2}}),\s*|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS}),?\s+)(\d{{4}})")
_NIL = {"-", "–", "—", "-*", "–*", "*-", "*–"}  # "-*", "*-": less than half a unit
_VALUE = re.compile(r"^-?[\d,]*\d(?:\.\d+)?$|^\(-?[\d,]*\d(?:\.\d+)?\)$")
# "8(a)", "17", "2.13", "4,6" (a number's last group has three digits), schedule "N"
_NOTE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,2}(?:\s?\([A-Za-z]{1,3}\))?|\d{1,2}(?:,\d{1,2})+|[A-Z])$")
_MARKER = re.compile(r"^(?:\((?:[a-z]|[ivx]+[a-z]?|\d)\)\s*)+", re.I)  # "(a)", "(iv)", "(ia)", "(1)"
_ROMAN = re.compile(r"^[IVX]+(?:\b\.?\s*|(?=[A-Z][a-z]))")  # "IV. Expenses", "III TOTAL INCOME", "IX.TOTAL", "VIIITax"
_LETTERED = re.compile(r"^[A-Za-z]\s?[.)]\s*(?=[A-Za-z(])")  # "A. Cash Flow from...", "b) Loans"
_NUMBERED = re.compile(r"^\d{1,2}\s+(?=[A-Za-z(])")  # "10 CURRENT ASSETS, LOANS AND ADVANCES"
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# "As at March 31, As at April 1," over a line "2017 2016 2015".
_MONTH_DAY = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?!\d|,?\s*\d)")
_REFER = re.compile(r"\(refer note[^()]*(?:\([^()]*\)[^()]*)*\)", re.I)


def _is_value(token: str) -> bool:
    return token in _NIL or _VALUE.match(token) is not None


@dataclass(frozen=True)
class Rule:
    """Map a label (exact, or a pattern with "*" wildcards) in a section
    (None: statement level) to an item, with a sign matching the item's
    convention."""
    section: str | None
    label: str
    item: str
    sign: int = 1
    add: bool = False  # several lines add up to the item

    def matches(self, section: str | None, label: str, kind: str = "line") -> bool:
        # Statement-level rules (section None) apply to totals and
        # statement-level lines, never to a line inside a section.
        if self.section is None and kind == "line":
            return False
        if self.section is not None and self.section != section:
            return False
        if not self.label and kind != "total":
            return False  # an unlabelled section total, never a subtotal within it
        if "*" in self.label:
            return fnmatchcase(label, self.label)
        return label == self.label


@dataclass(frozen=True)
class Layout:
    kind: str
    title: str  # words in the statement's title, e.g. "balance sheet"
    required: tuple[str, ...]  # label prefixes all present on the first page
    last: tuple[str, ...]  # label prefixes of the statement's final line
    sections: dict[str, str]  # heading (no values) -> section
    totals: tuple[tuple[str, str], ...]  # (label prefix, section) closing a section
    rules: tuple[Rule, ...]
    instant: bool  # balance sheet dates, or ends of years
    # The title must head the page: lines like "Net cash from operating
    # activities" also appear in the management discussion's summaries.
    title_required: bool = False
    resets: tuple[str, ...] = ()  # headings that close any section
    initial: str | None = None  # section open at the first line
    subtotals: tuple[str, ...] = ()  # running subtotals within a section
    globals: tuple[str, ...] = ()  # statement-level lines: close any section
    summed: dict[str, tuple[str, ...]] = field(default_factory=dict)  # heading -> child prefixes
    headings: tuple[str, ...] = ()  # sub-headings within a section, e.g. "fixed assets"
    per_share: frozenset[str] = frozenset()  # items in rupees, not the statement's unit
    counts: frozenset[str] = frozenset()  # items that are numbers of shares
    labelled_totals: bool = False  # every section closes with a labelled total


@dataclass
class Row:
    section: str | None
    heading: str | None
    label: str  # normalised, wrapped lines joined
    own: str  # normalised label of the values line alone
    values: list[float | None]
    alternatives: list[list[float]] = field(default_factory=list)
    kind: str = "line"  # line, total, subtotal or global


@dataclass
class Statement:
    kind: str
    consolidated: bool
    page: int  # 1-based
    unit: int  # multiplier to rupees
    columns: list[str]  # balance sheet dates, or ends of years
    instant: bool = True
    # None: the line was dropped as unreliable for that column.
    values: dict[str, list[float | None]] = field(default_factory=dict)
    signed_off: str | None = None  # date the statements were approved
    warnings: list[str] = field(default_factory=list)
    vocabulary: frozenset[str] = frozenset()  # items this layout can report
    per_share: frozenset[str] = frozenset()
    counts: frozenset[str] = frozenset()

    def period(self, column: int) -> tuple[str, str]:
        end = self.columns[column]
        if self.instant:
            return end, end
        start = date.fromisoformat(end).replace(year=int(end[:4]) - 1) + timedelta(days=1)
        return start.isoformat(), end

    def omitted(self, column: int) -> set[str]:
        """Items this statement could show but doesn't, for one column: a newer
        presentation that has dropped or regrouped a line."""
        dropped = {item for item, values in self.values.items() if values[column] is None}
        if len(dropped) == len(self.values):
            return set()  # nothing read for this column: no evidence of a regrouping
        return set(self.vocabulary) - set(self.values) - dropped

    def facts(self) -> list[Fact]:
        facts = []
        for item, column_values in sorted(self.values.items()):
            for col, value in enumerate(column_values):
                if value is None:
                    continue
                start, end = self.period(col)
                if item in self.per_share:
                    facts.append(Fact(item, start, end, value, "INR/shares", None))
                elif item in self.counts:
                    facts.append(Fact(item, start, end, value, "shares", 0))
                else:
                    decimals = -7 if self.unit == 10**7 else -5
                    facts.append(Fact(item, start, end, value * self.unit, "INR", decimals))
        return facts


# --- Layouts -------------------------------------------------------------

BALANCE_SHEET = Layout(
    kind="balance_sheet",
    title="balance sheet",
    # Half totals may be unlabelled (M&M), and equity may be on the next page
    # (Infosys).
    required=("total assets",),
    last=("total equity and liabilities",),
    sections={
        "non-current assets": "nca", "current assets": "ca", "equity": "eq",
        "non-current liabilities": "ncl", "current liabilities": "cl",
    },
    resets=("assets", "equity and liabilities", "liabilities"),
    totals=(
        ("total non-current assets", "nca"), ("total current assets", "ca"),
        ("total equity", "eq"),
        ("total non-current liabilities", "ncl"), ("total current liabilities", "cl"),
    ),
    subtotals=("equity attributable to",),
    globals=("total assets", "total equity and liabilities"),
    summed={
        "trade receivables": ("billed", "unbilled"),
        "trade payables": ("dues of", "total outstanding dues of"),
    },
    instant=True,
    rules=(
        Rule("nca", "property, plant and equipment", "PropertyPlantAndEquipment"),
        Rule("nca", "capital work-in-progress", "CapitalWorkInProgress"),
        Rule("nca", "right-of-use assets", "RightOfUseAssets"),
        Rule("nca", "goodwill", "Goodwill"),
        Rule("nca", "other intangible assets", "IntangibleAssetsOtherThanGoodwill"),
        Rule("nca", "intangible assets", "IntangibleAssetsOtherThanGoodwill"),
        Rule("nca", "investments", "NoncurrentInvestments"),
        Rule("nca", "trade receivables", "TradeReceivablesNoncurrent"),
        Rule("nca", "loans", "LoansNoncurrent"),
        Rule("nca", "other financial assets", "OtherNoncurrentFinancialAssets"),
        Rule("nca", "deferred tax assets (net)", "DeferredTaxAssetsNet"),
        Rule("nca", "income tax assets (net)", "NoncurrentTaxAssets"),
        Rule(None, "total non-current assets", "NoncurrentAssets"),
        # XBRL's "Other..." items are residual buckets whose scope varies by
        # company (some fold in lease liabilities), so those lines stay unmapped.
        Rule("nca", "", "NoncurrentAssets"),  # M&M leaves the section totals unlabelled
        Rule("ca", "inventories", "Inventories"),
        Rule("ca", "investments", "CurrentInvestments"),
        Rule("ca", "trade receivables", "TradeReceivablesCurrent"),
        Rule("ca", "unbilled revenue", "UnbilledRevenueCurrent"),
        Rule("ca", "cash and cash equivalents", "CashAndCashEquivalents"),
        Rule("ca", "other balances with banks", "BankBalanceOtherThanCashAndCashEquivalents"),
        Rule("ca", "bank balances other than cash and cash equivalents", "BankBalanceOtherThanCashAndCashEquivalents"),
        Rule("ca", "loans", "LoansCurrent"),
        Rule("ca", "other financial assets", "OtherCurrentFinancialAssets"),
        Rule("ca", "income tax assets (net)", "CurrentTaxAssets"),
        Rule("ca", "other assets", "OtherCurrentAssets"),
        Rule("ca", "other current assets", "OtherCurrentAssets"),
        Rule(None, "total current assets", "CurrentAssets"),
        Rule("ca", "", "CurrentAssets"),
        Rule(None, "total assets", "Assets"),
        Rule("eq", "share capital", "EquityShareCapital"),
        Rule("eq", "equity share capital", "EquityShareCapital"),
        Rule("eq", "other equity", "OtherEquity"),
        Rule("eq", "equity attributable to shareholders of the company", "EquityAttributableToOwnersOfParent"),
        Rule("eq", "equity attributable to equity holders of the company", "EquityAttributableToOwnersOfParent"),
        Rule("eq", "equity attributable to owners of the company", "EquityAttributableToOwnersOfParent"),
        Rule("eq", "", "Equity"),
        Rule("eq", "non-controlling interests", "NonControllingInterest"),
        Rule(None, "total equity", "Equity"),
        Rule("ncl", "borrowings", "BorrowingsNoncurrent"),
        Rule("ncl", "long-term borrowings", "BorrowingsNoncurrent"),
        Rule("ncl", "lease liabilities", "LeaseLiabilitiesNoncurrent"),
        Rule("ncl", "provisions", "ProvisionsNoncurrent"),
        Rule("ncl", "deferred tax liabilities (net)", "DeferredTaxLiabilitiesNet"),
        Rule(None, "total non-current liabilities", "NoncurrentLiabilities"),
        Rule("ncl", "", "NoncurrentLiabilities"),
        Rule("cl", "borrowings", "BorrowingsCurrent"),
        Rule("cl", "short-term borrowings", "BorrowingsCurrent"),
        Rule("cl", "lease liabilities", "LeaseLiabilitiesCurrent"),
        Rule("cl", "trade payables*", "TradePayablesCurrent"),  # "(includes dues of ...)"
        Rule("cl", "provisions", "ProvisionsCurrent"),
        Rule("cl", "income tax liabilities (net)", "CurrentTaxLiabilities"),
        Rule("cl", "income tax liabilities(net)", "CurrentTaxLiabilities"),
        Rule("cl", "current income tax liabilities (net)", "CurrentTaxLiabilities"),
        Rule("cl", "current tax liabilities (net)", "CurrentTaxLiabilities"),
        Rule(None, "total current liabilities", "CurrentLiabilities"),
        Rule("cl", "", "CurrentLiabilities"),
        Rule(None, "total equity and liabilities", "EquityAndLiabilities"),
    ),
)

PROFIT_AND_LOSS = Layout(
    kind="profit_and_loss",
    title="profit and loss",
    required=("total income", "total expenses", "profit before tax|profit after exceptional items and before tax",
              "profit for the year|profit for the period"),
    last=("earnings per equity share", "weighted average number of equity shares"),
    initial="income",
    sections={
        "income": "income", "expenses": "expenses", "exceptional item": "exceptional", "exceptional items": "exceptional",
        "tax expense": "tax", "tax expenses": "tax",
        "profit for the year attributable to": "profit_to",
        "profit for the period attributable to": "profit_to",
        "other comprehensive income for the year attributable to": "oci_to",
        "other comprehensive income for the period attributable to": "oci_to",
        "total comprehensive income for the year attributable to": "tci_to",
        "total comprehensive income for the period attributable to": "tci_to",
        "other comprehensive*": "oci",  # "(OCI)", "(loss) / income"
    },
    totals=(
        ("total income", "income"), ("total expenses", "expenses"),
        ("total tax expense", "tax"), ("total other comprehensive", "oci"),
    ),
    globals=("profit before", "profit after exceptional items and before tax", "profit for the year", "profit for the period", "profit after tax",
             "total comprehensive income for the year", "total comprehensive income for the period",
             "earnings per equity share", "weighted average number", "weighted average equity shares"),
    instant=False,
    rules=(
        Rule("income", "revenue from operations", "RevenueFromOperations"),
        Rule("income", "revenue", "RevenueFromOperations"),
        Rule("income", "income from operations", "IncomeFromOperations"),  # see _income_from_operations
        Rule("income", "other income*", "OtherIncome"),
        Rule(None, "total income", "Income"),
        Rule("expenses", "employee benefit* expense*", "EmployeeBenefitExpense"),
        Rule("expenses", "finance costs", "FinanceCosts"),
        Rule("expenses", "depreciation and amorti[sz]ation expense*", "DepreciationDepletionAndAmortisationExpense"),
        Rule("expenses", "depreciation, amorti[sz]ation and impairment*", "DepreciationDepletionAndAmortisationExpense"),
        Rule(None, "total expenses", "Expenses"),
        Rule(None, "profit before exceptional item*", "ProfitBeforeExceptionalItemsAndTax"),
        # A year without exceptional items: "Profit before share of profit of associates ... and tax".
        Rule(None, "profit before share of profit*", "ProfitBeforeExceptionalItemsAndTax"),
        Rule("exceptional", "*", "ExceptionalItemsBeforeTax", add=True),  # sign settled by arithmetic
        Rule(None, "exceptional items*", "ExceptionalItemsBeforeTax"),  # one line: "Exceptional items (net)"
        Rule(None, "share of profit*of associates and joint ventures*",
             "ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod"),
        Rule(None, "profit before tax from discontinued operations*", "ProfitLossFromDiscontinuedOperationsBeforeTax"),
        Rule(None, "profit before tax*", "ProfitBeforeTax"),  # "(III-IV)"
        Rule(None, "profit after exceptional items and before tax*", "ProfitBeforeTax"),
        Rule("tax", "current tax", "CurrentTax"),
        Rule("tax", "deferred tax", "DeferredTax"),
        Rule(None, "total tax expense", "TaxExpense"),
        # With discontinued operations (M&M FY21), the year's profit is the third line.
        Rule(None, "profit for the year from continuing operations*", "ProfitLossForPeriodFromContinuingOperations"),
        Rule(None, "profit after tax from discontinued operations*", "ProfitLossFromDiscontinuedOperationsAfterTax"),
        Rule(None, "profit after tax from continuing and discontinued operations*", "ProfitLossForPeriod"),
        Rule(None, "profit for the year*", "ProfitLossForPeriod"),
        Rule(None, "profit for the period*", "ProfitLossForPeriod"),
        Rule(None, "total other comprehensive*", "OtherComprehensiveIncomeNetOfTaxes"),
        Rule(None, "total comprehensive income for the year", "ComprehensiveIncomeForThePeriod"),
        Rule(None, "total comprehensive income for the period", "ComprehensiveIncomeForThePeriod"),
        Rule("profit_to", "shareholders of the company", "ProfitOrLossAttributableToOwnersOfParent"),
        Rule("profit_to", "equity holders of the company", "ProfitOrLossAttributableToOwnersOfParent"),
        Rule("profit_to", "non-controlling interests", "ProfitOrLossAttributableToNonControllingInterests"),
        Rule(None, "earnings per equity share (for continuing operations)*",
             "BasicEarningsLossPerShareFromContinuingOperations"),
        Rule(None, "earnings per equity share (for discontinued operations)*",
             "BasicEarningsLossPerShareFromDiscontinuedOperations"),
        Rule(None, "earnings per equity share*", "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"),
        Rule(None, "diluted (*)", "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations"),
        Rule(None, "diluted", "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations"),
        Rule(None, "weighted average number of equity shares*", "WeightedAverageNumberOfEquityShares"),
        Rule(None, "weighted average equity shares*", "WeightedAverageNumberOfEquityShares"),
    ),
    per_share=frozenset({"BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                         "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                         "BasicEarningsLossPerShareFromContinuingOperations",
                         "BasicEarningsLossPerShareFromDiscontinuedOperations"}),
    counts=frozenset({"WeightedAverageNumberOfEquityShares"}),
)

CASH_FLOW = Layout(
    kind="cash_flow",
    title="cash flow",
    required=("net cash",),
    title_required=True,
    last=("cash and cash equivalents at*end of the year",),  # [0]: complete on this page
    sections={
        "cash flows from operating activities": "operating",
        "cash flows from investing activities": "investing",
        "cash flows from financing activities": "financing",
        "net cash flows from operating activities": "operating",
        "net cash flows from investing activities": "investing",
        "net cash flows from financing activities": "financing",
        "cash flow from operating activities": "operating",
        "cash flow from investing activities": "investing",
        "cash flow from financing activities": "financing",
    },
    # "activit": TCS's FY17 statement spells it "activites".
    totals=(
        ("net cash*operating activit", "operating"),
        ("net cash*investing activit", "investing"),
        ("net cash*financing activit", "financing"),
    ),
    subtotals=("operating profit before working capital changes", "cash*generated from*operations",
               "cash*used in operations"),
    # Not "Net decrease in cash credit facilities", a financing line.
    globals=("net change in cash", "net*increase*in cash and cash", "net*decrease*in cash and cash",
             "cash and cash equivalents at the"),
    summed={},
    instant=False,
    labelled_totals=True,  # "Net cash from...": an unlabelled sum above it is a subtotal
    rules=(
        Rule("operating", "depreciation and amortisation expense", "AdjustmentsForDepreciationAndAmortisationExpense"),
        Rule(None, "cash*generated from*operations", "CashFlowsFromUsedInOperations"),  # "from / (used in)"
        Rule(None, "cash*used in operations", "CashFlowsFromUsedInOperations"),
        Rule("operating", "taxes paid*", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities", -1),
        Rule("operating", "income taxes paid*", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities", -1),
        Rule("operating", "income tax* refund*(paid)*", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities", -1),
        # "Refund/ (Payment) of taxes (net)", "(Payment) / Refund of taxes (net)"
        Rule("operating", "*(payment)*of taxes*", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities", -1),
        Rule(None, "net cash*operating activit*", "CashFlowsFromUsedInOperatingActivities"),
        Rule("investing", "payment* for purchase of property, plant and equipment",
             "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", -1),
        Rule("investing", "payment* for purchase of intangible assets", "PaymentsForIntangibleAssets", -1),
        Rule("investing", "payment including advances for acquiring right-of-use assets",
             "PaymentsForRightOfUseAssets", -1),
        Rule("investing", "purchase of fixed assets*", "PurchaseOfFixedAssets", -1),
        Rule("investing", "proceeds from sale of fixed assets*", "ProceedsFromSaleOfFixedAssets"),
        Rule("investing", "sale of fixed assets*", "ProceedsFromSaleOfFixedAssets"),
        # Infosys and M&M wordings; these include intangibles, so they're fixed assets.
        Rule("investing", "payment towards capital expenditure*", "PurchaseOfFixedAssets", -1),
        Rule("investing", "expenditure on property, plant and equipment*", "PurchaseOfFixedAssets", -1),
        Rule("investing", "capital expenditure on property, plant and equipment*", "PurchaseOfFixedAssets", -1),
        Rule("investing", "payment* to acquire property, plant * equipment*", "PurchaseOfFixedAssets", -1),
        Rule("investing", "proceeds from sale of property, plant * equipment*", "ProceedsFromSaleOfFixedAssets"),
        Rule("investing", "acquisition of subsidiaries*", "AcquisitionOfSubsidiariesNetOfCashAcquired", -1),
        Rule("investing", "dividends received", "DividendsReceivedClassifiedAsInvestingActivities"),
        Rule("investing", "proceeds from disposal of property, plant and equipment",
             "ProceedsFromSalesOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
        Rule("investing", "purchase of subsidiar*",
             "AcquisitionOfSubsidiariesNetOfCashAcquired", -1),
        Rule("investing", "payment for business acquisition*",
             "AcquisitionOfSubsidiariesNetOfCashAcquired", -1),
        Rule("investing", "acquisition of subsidiar*",
             "AcquisitionOfSubsidiariesNetOfCashAcquired", -1),
        Rule("investing", "interest received", "InterestReceivedClassifiedAsInvestingActivities"),
        Rule("investing", "dividend received", "DividendsReceivedClassifiedAsInvestingActivities"),
        Rule(None, "net cash*investing activit*", "CashFlowsFromUsedInInvestingActivities"),
        Rule("financing", "repayment of lease liabilities",
             "PaymentsOfLeaseLiabilitiesClassifiedAsFinancingActivities", -1),
        Rule("financing", "interest paid", "InterestPaidClassifiedAsFinancingActivities", -1),
        Rule("financing", "finance costs paid", "InterestPaidClassifiedAsFinancingActivities", -1),
        Rule("financing", "dividend paid to non-controlling interests*", "DividendsPaidToNonControllingInterests", -1),
        Rule("financing", "dividends paid to non-controlling interests*", "DividendsPaidToNonControllingInterests", -1),
        Rule("financing", "dividend* paid to minority*", "DividendsPaidToNonControllingInterests", -1),
        Rule("financing", "dividend paid*", "DividendsPaidToShareholdersOfTheCompany", -1),
        Rule("financing", "dividends paid*", "DividendsPaidToShareholdersOfTheCompany", -1),
        Rule("financing", "payment of dividends*", "DividendsPaidToShareholdersOfTheCompany", -1),
        Rule("financing", "buyback of equity shares*", "BuybackOfEquityShares", -1),
        Rule("financing", "repayment of finance lease obligations",
             "PaymentsOfFinanceLeaseLiabilitiesClassifiedAsFinancingActivities", -1),
        Rule("financing", "buy-back of equity shares", "BuybackOfEquityShares", -1),
        Rule("financing", "tax on buy-back of equity shares", "TaxOnBuybackOfEquityShares", -1),
        Rule(None, "net cash*financing activit*", "CashFlowsFromUsedInFinancingActivities"),
        Rule(None, "net change in cash and cash equivalents",
             "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges"),
        Rule(None, "net increase / (decrease) in cash and cash equivalents",
             "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges"),
        Rule(None, "net (decrease)*increase in cash and cash equivalents",
             "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges"),
        Rule(None, "exchange difference on translation of foreign currency cash and cash equivalents",
             "EffectOfExchangeRateChangesOnCashAndCashEquivalents"),
        Rule(None, "effect of exchange differences on restatement of foreign currency cash and cash equivalents",
             "EffectOfExchangeRateChangesOnCashAndCashEquivalents"),
    ),
)

# Indian GAAP, revised Schedule VI (annual reports for FY12 to FY16):
# sections close with unlabelled totals, and "TOTAL" closes each half.
BALANCE_SHEET_SCHEDULE_VI = Layout(
    kind="balance_sheet",
    title="balance sheet",
    required=("reserves and surplus", "long-term provisions|short-term provisions|long-term borrowings"),
    last=("total", "total assets"),  # "TOTAL" closes each half
    sections={
        "shareholders' funds": "eq", "non-current liabilities": "ncl", "current liabilities": "cl",
        "non-current assets": "nca", "current assets": "ca",
    },
    resets=("equity and liabilities", "assets"),
    headings=("fixed assets",),
    totals=(),
    instant=True,
    rules=(
        Rule("eq", "share capital", "EquityShareCapital"),
        Rule("eq", "reserves and surplus", "ReservesAndSurplus"),
        Rule("eq", "", "EquityAttributableToOwnersOfParent"),  # shareholders' funds
        Rule(None, "minority interest", "NonControllingInterest"),
        Rule("ncl", "long-term borrowings", "BorrowingsNoncurrent"),
        Rule("ncl", "deferred tax liabilities (net)", "DeferredTaxLiabilitiesNet"),
        Rule("ncl", "long-term provisions", "ProvisionsNoncurrent"),
        Rule("ncl", "", "NoncurrentLiabilities"),
        Rule("cl", "short-term borrowings", "BorrowingsCurrent"),
        Rule("cl", "trade payables*", "TradePayablesCurrent"),  # "(includes dues of ...)"
        Rule("cl", "short-term provisions", "ProvisionsCurrent"),
        Rule("cl", "", "CurrentLiabilities"),
        Rule(None, "total equity and liabilities", "EquityAndLiabilities"),
        Rule("nca", "tangible assets", "PropertyPlantAndEquipment"),
        Rule("nca", "intangible assets", "IntangibleAssetsOtherThanGoodwill"),
        Rule("nca", "capital work-in-progress", "CapitalWorkInProgress"),
        Rule("nca", "fixed assets", "FixedAssets"),
        Rule("nca", "non-current investments", "NoncurrentInvestments"),
        Rule("nca", "deferred tax assets (net)", "DeferredTaxAssetsNet"),
        Rule("nca", "goodwill*", "Goodwill"),
        Rule("nca", "", "NoncurrentAssets"),
        Rule("ca", "current investments", "CurrentInvestments"),
        Rule("ca", "inventories", "Inventories"),
        Rule("ca", "unbilled revenue", "UnbilledRevenueCurrent"),
        Rule("ca", "trade receivables", "TradeReceivablesCurrent"),
        Rule("ca", "cash and bank balances", "CashAndBankBalances"),
        Rule("ca", "", "CurrentAssets"),
        Rule(None, "total assets", "Assets"),
    ),
)

# Indian GAAP, old Schedule VI (annual reports up to FY11): sources and
# application of funds, with current liabilities netted off current assets.
BALANCE_SHEET_OLD = Layout(
    kind="balance_sheet",
    title="balance sheet",
    required=("reserves and surplus", "total funds employed|net current assets"),
    last=("total assets (net)",),
    sections={
        "shareholders' funds": "eq", "loan funds": "loans",
        "current assets, loans and advances": "ca", "current liabilities and provisions": "cl",
    },
    resets=("sources of funds", "application of funds"),
    headings=("fixed assets",),
    totals=(),
    globals=("minority interest", "deferred tax", "total funds employed", "goodwill", "investments",
             "net current assets", "total assets"),
    instant=True,
    rules=(
        Rule("eq", "share capital", "EquityShareCapital"),
        Rule("eq", "reserves and surplus", "ReservesAndSurplus"),
        Rule("eq", "", "EquityAttributableToOwnersOfParent"),
        Rule(None, "minority interest", "NonControllingInterest"),
        Rule("loans", "secured loans", "SecuredLoans"),
        Rule("loans", "unsecured loans", "UnsecuredLoans"),
        Rule("loans", "", "Borrowings"),  # loan funds
        Rule(None, "deferred tax liabilities (net)", "DeferredTaxLiabilitiesNet"),
        Rule(None, "total funds employed", "TotalFundsEmployed"),
        Rule(None, "net block", "NetBlock"),
        Rule(None, "capital work-in-progress", "CapitalWorkInProgress"),
        Rule(None, "goodwill*", "Goodwill"),
        Rule(None, "investments", "Investments"),
        Rule(None, "deferred tax assets (net)", "DeferredTaxAssetsNet"),
        Rule("ca", "inventories", "Inventories"),
        Rule("ca", "unbilled revenues", "UnbilledRevenueCurrent"),
        Rule("ca", "sundry debtors", "TradeReceivablesCurrent"),
        Rule("ca", "cash and bank balances", "CashAndBankBalances"),
        Rule("ca", "loans and advances", "LoansAndAdvances"),
        Rule("ca", "", "CurrentAssetsLoansAndAdvances"),
        Rule("cl", "", "CurrentLiabilitiesAndProvisions"),
        Rule(None, "net current assets*", "NetCurrentAssets"),
    ),
)

# Indian GAAP, revised Schedule VI (FY12 to FY16). "Profit for the year" is
# after minority interest here, unlike Ind-AS.
PROFIT_AND_LOSS_SCHEDULE_VI = Layout(
    kind="profit_and_loss",
    title="profit and loss",
    required=("total revenue", "total expenses", "profit before tax"),
    last=("earnings per equity share", "weighted average number of equity shares"),
    initial="income",
    sections={"expenses": "expenses", "tax expense": "tax",
              "exceptional item": "exceptional", "exceptional items": "exceptional"},
    totals=(("total revenue", "income"), ("total expenses", "expenses")),
    globals=("profit before", "profit for the year", "minority interest", "share of",
             "earnings per", "weighted average number"),
    instant=False,
    rules=(
        Rule("income", "revenue from operations*", "RevenueFromOperations"),
        Rule("income", "other income*", "OtherIncome"),
        Rule(None, "total revenue", "Income"),
        Rule("expenses", "employee benefit* expense*", "EmployeeBenefitExpense"),
        Rule("expenses", "finance costs", "FinanceCosts"),
        Rule("expenses", "depreciation and amorti[sz]ation expense*", "DepreciationDepletionAndAmortisationExpense"),
        Rule("expenses", "depreciation, amorti[sz]ation and impairment*", "DepreciationDepletionAndAmortisationExpense"),
        Rule(None, "total expenses", "Expenses"),
        Rule(None, "profit before exceptional item*", "ProfitBeforeExceptionalItemsAndTax"),
        Rule("exceptional", "*", "ExceptionalItemsBeforeTax", add=True),
        Rule(None, "exceptional item*", "ExceptionalItemsBeforeTax"),  # one line: "Add : Exceptional Items"
        Rule(None, "profit before tax*", "ProfitBeforeTax"),
        # Tax also includes MAT credit and fringe benefit tax: total only.
        Rule("tax", "", "TaxExpense"),
        Rule(None, "profit for the year before minority interest*", "ProfitLossForPeriod"),
        Rule(None, "minority interest", "ProfitOrLossAttributableToNonControllingInterests"),
        Rule(None, "profit for the year", "ProfitOrLossAttributableToOwnersOfParent"),
        Rule(None, "earnings per*share*", "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"),
        Rule(None, "weighted average number of equity shares*", "WeightedAverageNumberOfEquityShares"),
    ),
    per_share=PROFIT_AND_LOSS.per_share,
    counts=PROFIT_AND_LOSS.counts,
)

# Indian GAAP, old Schedule VI (up to FY11): income and expenditure each close
# with an unlabelled total; TCS showed revenue as services plus equipment sales.
PROFIT_AND_LOSS_OLD = Layout(
    kind="profit_and_loss",
    title="profit and loss",
    required=("profit before taxes",),
    last=("earnings per*share",),
    initial="income",
    sections={"income": "income", "expenditure": "expenses", "provision for taxes": "tax",
              "appropriations": "appropriations"},
    totals=(),
    globals=("profit before taxes", "profit for the year before", "minority interest", "share of",
             "net profit for the year", "balance brought forward", "amount available"),
    instant=False,
    rules=(
        Rule("income", "information technology and consultancy services", "RevenueFromOperations", add=True),
        Rule("income", "sale of equipment and software licen*", "RevenueFromOperations", add=True),
        Rule("income", "other income*", "OtherIncome"),
        Rule("income", "", "Income"),
        Rule("expenses", "employee costs", "EmployeeBenefitExpense"),
        Rule("expenses", "interest", "FinanceCosts"),
        Rule("expenses", "depreciation*", "DepreciationDepletionAndAmortisationExpense"),
        Rule("expenses", "", "Expenses"),
        Rule(None, "profit before taxes", "ProfitBeforeTax"),
        Rule("tax", "", "TaxExpense"),  # includes MAT credit and fringe benefit tax
        Rule(None, "profit for the year before minority interest*", "ProfitLossForPeriod"),
        Rule(None, "minority interest", "ProfitOrLossAttributableToNonControllingInterests"),
        Rule(None, "net profit for the year", "ProfitOrLossAttributableToOwnersOfParent"),
        Rule(None, "earnings per*share*", "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"),
    ),
    per_share=PROFIT_AND_LOSS.per_share,
)

LAYOUTS = (BALANCE_SHEET, BALANCE_SHEET_SCHEDULE_VI, BALANCE_SHEET_OLD,
           PROFIT_AND_LOSS, PROFIT_AND_LOSS_SCHEDULE_VI, PROFIT_AND_LOSS_OLD, CASH_FLOW)
# Every balance sheet item a report may present: a newer report's balance
# sheet replaces all of them for the dates it covers.
BALANCE_SHEET_ITEMS = frozenset(
    r.item for layout in LAYOUTS if layout.kind == "balance_sheet" for r in layout.rules
)


# --- Lines ---------------------------------------------------------------

_LIGATURE = re.compile(r"(?<=[a-z])(f[il]) (?=[a-z])")  # "Profi t", "benefi t": a split ligature
# How a total is worked out, after its label: "(I+II)", "(V - VI)", "(A+B+C)".
_FORMULA = re.compile(r"\s*\((?:[ivx]+|[a-z])(?:\s*[+-]\s*(?:[ivx]+|[a-z]))+\)$")


def normalise(label: str) -> str:
    label = _LIGATURE.sub(r"\1", label)
    label = _MARKER.sub("", _LETTERED.sub("", _NUMBERED.sub("", _ROMAN.sub("", label.strip()))).lower())
    label = _REFER.sub("", label.replace("’", "'").replace("–", "-").replace("\ufffe", "-"))
    label = re.sub(r"\s+", " ", label)
    label = re.sub(r"^total\s*-\s*", "total ", label)  # "TOTAL - NON CURRENT ASSETS"
    label = re.sub(r"^add\s*:\s*", "", label)  # "Add : Exceptional Items"
    label = re.sub(r"(?<=[a-z]) - (?=[a-z])", "-", label).strip(" :#*")  # "long - term"
    label = re.sub(r"\b(long|short) term\b", r"\1-term", label)  # "Long Term Borrowings"
    label = re.sub(r"\bnon current\b", "non-current", label)
    label = re.sub(r"\s*/\s*\(loss\)", "", label)  # "Profit/(loss) for the year"
    label = _FORMULA.sub("", label)
    if label.startswith("total"):
        label = re.sub(r"\s*\([ivx]+\)$", "", label)  # "Total expenses (IV)"
    # A loss-making year's lines name the profit lines they stand for:
    # "Loss before tax", "Loss for the year", "Operating loss before...".
    label = re.sub(r"^(operating |net )?loss (before|for|after)\b", r"\1profit \2", label)
    label = re.sub(r"\bcomprehensive loss\b", "comprehensive income", label)
    label = re.sub(r"^loss per (equity )?share", r"earnings per \1share", label)
    return re.sub(r"[#*]+$", "", label).strip(" :")


def _number(token: str) -> float:
    if token in _NIL:
        return 0.0  # a dash in published accounts means nil
    negative = token.startswith("(") and token.endswith(")")
    value = float(token.strip("()").replace(",", ""))
    return -value if negative else value


def _note_tokens(tokens: list[str]) -> int:
    """How many trailing tokens of a label are a note reference: "8(a)",
    "17", or "6 (a)"."""
    if tokens and _NOTE.match(tokens[-1]):
        if len(tokens) >= 3 and tokens[-2] in ("&", "and") and _NOTE.match(tokens[-3]):
            return 3  # "2.3 & 2.4"
        return 1
    if len(tokens) >= 2 and re.match(r"^\(\w{1,3}\)$", tokens[-1]) and _NOTE.match(tokens[-2]):
        return 2
    return 0


def split_line(line: str, columns: int) -> tuple[str, list[float]] | None:
    """'Investments 8(a) 31,481 36,897' -> ('Investments', [31481, 36897]).
    None if the line doesn't end in exactly `columns` values."""
    tokens = _REFER.sub("", line).split()
    if len(tokens) <= columns or not all(_is_value(t) for t in tokens[-columns:]):
        return None
    before = tokens[-columns - 1]
    if _is_value(before) and not _NOTE.match(before):
        return None  # more values than columns: not a row of this table
    label_tokens = tokens[:-columns]
    label_tokens = label_tokens[: len(label_tokens) - _note_tokens(label_tokens)]
    if not label_tokens:
        return None
    return " ".join(label_tokens), [_number(t) for t in tokens[-columns:]]


_ROW_START = re.compile(r"^(?:\((?:[a-z]|[ivx]+)\)|[-–])$")


def split_rows(line: str, columns: int) -> list[str]:
    """Two printed rows that text extraction ran into one line: "(i)
    Investments 10 *- *- (ii) Trade receivables 11 31.45 214.93". Split
    where a row's values are followed by the start of another row: a marker
    ("(ii)", "(b)") or a dash bullet, then a word."""
    tokens = line.split()
    for i in range(columns + 1, len(tokens) - 1):
        if _ROW_START.match(tokens[i]) and tokens[i + 1][:1].isalpha() \
                and split_line(" ".join(tokens[:i]), columns):
            return [" ".join(tokens[:i])] + split_rows(" ".join(tokens[i:]), columns)
    return [line]


def drop_running_totals(lines: list[str], columns: int) -> list[str]:
    """A section's last line printed with the section's total beside it,
    year by year: "Dividend paid (146.38) 510.97 (102.79) (32.12)", the
    total repeated on its own line below ("Net cash ... (C) 510.97
    (32.12)"). Keep the line's own values."""
    out = []
    for i, line in enumerate(lines):
        tokens = line.split()
        wide = tokens[-2 * columns:]
        if len(tokens) > 2 * columns and all(_is_value(t) for t in wide) \
                and not _is_value(tokens[-2 * columns - 1]):
            totals = [_number(t) for t in wide[1::2]]
            if any(_values_only(after, columns) == totals or (split_line(after, columns) or ("", None))[1] == totals
                   for after in lines[i + 1:i + 4]):
                line = " ".join(tokens[:-2 * columns] + wide[0::2])
        out.append(line)
    return out


def _merge(a: str, b: str) -> str | None:
    """'9' + '453' -> '9453': a thousands group split off by a lost comma."""
    if re.fullmatch(r"\(?\d{1,3}(?:,\d{2,3})*", a) and re.fullmatch(r"\d{3}(?:\.\d+)?\)?", b):
        return a + b
    return None


def alternatives(line: str, columns: int) -> list[list[float]]:
    """Other ways to read a line's values if a thousands comma was lost:
    "Lease liabilities 9 453 7,838" may be 9,453 and 7,838 (no note), and
    "X 8(a) 1 234 567" may be 1,234 and 567."""
    tokens = _REFER.sub("", line).split()
    found: list[list[float]] = []
    for extra in (1, 2):  # one merge, with or without a note before the values
        tail = tokens[-(columns + extra):]
        if len(tokens) <= columns + extra or not all(_is_value(t) for t in tail):
            continue
        for i in range(len(tail) - 1):
            merged = _merge(tail[i], tail[i + 1])
            if merged is None:
                continue
            candidate = tail[:i] + [merged] + tail[i + 2:]
            if len(candidate) == columns + 1 and _NOTE.match(candidate[0]):
                candidate = candidate[1:]
            if len(candidate) == columns:
                values = [_number(t) for t in candidate]
                if values not in found:
                    found.append(values)
    return found


def _values_only(line: str, n: int) -> list[float] | None:
    """A line of values whose label was printed on the line(s) above, e.g.
    '86,433 84,126' or '6(h) 10,082 7,962'."""
    tokens = line.split()
    if len(tokens) == n + 1 and _NOTE.match(tokens[0]):
        tokens = tokens[1:]
    if len(tokens) == n and all(_is_value(t) for t in tokens):
        return [_number(t) for t in tokens]
    return None


def _date(match: re.Match) -> str:
    month_name, day = match.group(1) or match.group(4), match.group(2) or match.group(3)
    month = _MONTHS.split("|").index(month_name) + 1
    return date(int(match.group(5)), month, int(day)).isoformat()


def _is_period_date(iso: str) -> bool:
    """Month-end, or the first of a month (Ind-AS transition date)."""
    d = date.fromisoformat(iso)
    return d.day == 1 or (d + timedelta(days=1)).day == 1


_HEADER_WORDS = {"note", "notes", "no.", "schedule", "particulars", "year", "ended", "as", "at",
                 "rs.", "rs", "in", "crores", "crore", "`", "(`", "crores)", "crore)", "(", ")"}


def _is_year_header(line: str) -> bool:
    """A line of column years: "Note 2014 2013", "2009", "Schedule 2010",
    "Note 2018 2017*" (restated)."""
    tokens = line.lower().replace("*", "").split()
    return any(_YEAR.fullmatch(t) for t in tokens) and all(
        _YEAR.fullmatch(t) or t in _HEADER_WORDS for t in tokens
    )


def _is_column_header(line: str) -> bool:
    """"Note 2017 2016": years heading the value columns, not a row."""
    tokens = line.split()
    return bool(tokens) and tokens[0].lower() in ("note", "notes", "particulars", "schedule") and all(
        _YEAR.fullmatch(t) or t.lower() in ("no.", "no") for t in tokens[1:]
    )


_FURNITURE = re.compile(
    r"annual report \d{4}-\d{2}|financial statements|"
    r"^membership n(?:o|umber)\b|^(?:firm registration|din)\b|"  # the signatures
    r"(statement of (cash flows?|profit and loss)|balance sheet)(\s*\((contd\.?|continued)\))?(\s*(\||i)?\s*\d{1,3})?$", re.I)


def _is_page_furniture(line: str) -> bool:
    """Running headers and footers: "Standalone Statement of Cash Flows",
    "Integrated Annual Report 2020-21 Standalone Financial Statements | 273",
    a two-page spread's page numbers "114 115"."""
    spread = re.fullmatch(r"(\d{1,3}) (\d{1,3})", line.strip())
    return _FURNITURE.search(line.strip()) is not None or (
        spread is not None and int(spread[2]) == int(spread[1]) + 1)


def _is_repeated_header(line: str) -> bool:
    """A column header repeated mid-statement: "(` crore)", "Year ended"."""
    label = normalise(_UNIT.sub("", line))
    return _UNIT.search(line) is not None and not label or label in (
        "year ended", "as at", "note", "note year ended", "note as at", "year ended year ended",
        "for the year ended",
    ) or _is_unit_line(line)


def _open_brackets(text: str) -> int:
    return text.count("(") - text.count(")")


def _heading_with_note(line: str) -> str | None:
    """'(a) Fixed assets 12' -> '(a) Fixed assets': a heading whose only
    number is its note reference."""
    tokens = _REFER.sub("", line).split()
    if len(tokens) >= 2 and _NOTE.match(tokens[-1]) and not any(_is_value(t) for t in tokens[:-1]):
        return " ".join(tokens[:-1])
    return None


def _has_values(line: str) -> bool:
    tokens = line.split()
    return len(tokens) > 1 and _is_value(tokens[-1])


def _row_labels(page: str) -> set[str]:
    return {normalise(split[0]) for line in page.splitlines()
            if (split := split_line(line, 2) or split_line(line, 3))}


def _has_rows(page: str, prefixes: tuple[str, ...]) -> bool:
    """Every prefix (or one of its "|"-separated wordings) starts a row label."""
    labels = _row_labels(page)
    return all(
        any(fnmatchcase(label, wording + "*") for wording in p.split("|") for label in labels)
        for p in prefixes
    )


def _ends_here(page: str, layout: Layout) -> bool:
    """The statement's last line is on this page: as a row, or as a heading
    over the rows that hold its figures ("Earnings per equity share" over
    "Basic" and "Diluted")."""
    if _has_rows(page, layout.last[:1]):
        return True
    lines = [l for l in page.splitlines() if l.strip()]
    return any(
        (_heading_with_note(line) or not _has_values(line))
        and any(fnmatchcase(normalise(_heading_with_note(line) or line), w + "*") for w in layout.last[0].split("|"))
        and any(_has_values(after) for after in lines[i + 1:i + 3])
        for i, line in enumerate(lines)
    )


# --- Statements ----------------------------------------------------------

# Lines after which a statement page holds only notes and signatures.
_END_MARKERS = ("notes forming part", "as per our report", "for and on behalf of the board",
                "the accompanying notes", "notes to accounts", "significant accounting policies")


def _section_for(layout: Layout, label: str) -> str | None:
    """A heading's section: exact wording first, then "*" prefixes."""
    if label in layout.sections:
        return layout.sections[label]
    for heading, section in layout.sections.items():
        if heading.endswith("*") and label.startswith(heading[:-1]):
            return section
    return None


def _total_section(layout: Layout, label: str) -> str | None:
    for pattern, section in layout.totals:
        if fnmatchcase(label, pattern + "*"):
            return section
    return None


_LESS = re.compile(r"^less\s*:?-?\s*")


def _starts(label: str, prefix: str) -> bool:
    """`label` starts with `prefix`, which may hold "*" wildcards."""
    return fnmatchcase(label, prefix + "*")


def _rows(layout: Layout, lines: list[str], n: int, statement: Statement) -> list[Row]:
    """Each line with values, with its section and kind, joining labels that
    wrap over several lines."""
    rows: list[Row] = []
    section, heading = layout.initial, None
    part = None  # the balance sheet half: "assets", or "equity and liabilities"
    group_at = 0  # index of the first row under the current heading
    pending: list[str] = []  # lines that may be the start of a wrapped label
    skipping = False  # between a footer ("Notes forming part...") and the next header
    for line in lines:
        if skipping:
            # A two-page spread printed as one PDF page: the statement resumes
            # in the right half under a repeated header.
            skipping = _UNIT.search(line) is None
            continue
        if pending and _open_brackets(" ".join(pending)) > 0:
            # Still inside a bracketed note in a label: "Trade payables
            # (includes dues of micro and small enterprises ` 18.46 /
            # crores (March 31, 2015: ` 9.90 crores))".
            joined_text = " ".join(pending + [line])
            if _open_brackets(joined_text) > 0 or split_line(joined_text, n) is None:
                pending = [joined_text] if _open_brackets(joined_text) <= 0 else pending + [line]
                continue  # the values follow on a line of their own
            pending, line = [], joined_text
        split = split_line(line, n)
        unlabelled = False
        if split is None and _is_page_furniture(line):
            continue
        if split is None:
            only = _values_only(line, n)
            if only is not None and [int(v) for v in only] == [int(c[:4]) for c in statement.columns]:
                pending = []  # "Particulars Note Year ended March 31," / "2019 2018" repeated
                continue
            if only is not None:
                if pending:
                    split, pending = (" ".join(pending), only), []
                elif heading and section and _adds_up(rows[group_at:], section, only) and (
                    heading in layout.summed or heading in layout.headings
                    or any(r.kind == "line" and r.section == section for r in rows[:group_at])
                ):  # ("Financial liabilities" opening a section heads no subtotal of its own)
                    # The heading's subtotal ("(a) Fixed assets" over its lines).
                    rows.append(Row(section, heading, heading, heading, only, kind="group"))
                    heading = None
                    continue
                elif layout.labelled_totals and section:
                    # Cash flows close each section with a labelled "Net
                    # cash..."; an unlabelled sum before it totals some of
                    # the lines above ("Adjustments for:"), already counted.
                    rows.append(Row(section, heading, "", "", only, kind="group"))
                    continue
                else:
                    split, unlabelled = ("", only), True  # a section's unlabelled total
            elif _is_repeated_header(line) or _is_page_furniture(line):
                pending = []  # a continuation's title and header: no label starts here
                continue
            elif _DATE.search(line):
                continue  # a continuation page's header, a footer title, or a sign-off
            elif _open_brackets(line) > 0:
                pending.append(line)  # a label whose bracketed note continues below
                continue
            elif _has_values(line) and not _heading_with_note(line):
                statement.warnings.append(f"page {statement.page}: can't read {line!r}")
                pending = []
                continue
            else:
                text = _heading_with_note(line) or line  # "(a) Fixed assets 12": a heading
                label = normalise(text)
                if label.startswith(_END_MARKERS):
                    skipping, pending = True, []
                    continue
                wrapped = normalise(" ".join(pending + [text])) if pending else None
                if _section_for(layout, label):
                    section, heading, pending = _section_for(layout, label), None, []
                elif wrapped and _section_for(layout, wrapped):
                    # "CASH FLOWS FROM OPERATING / ACTIVITIES"
                    section, heading, pending = _section_for(layout, wrapped), None, []
                elif label in layout.resets:
                    section, heading, pending, part = None, None, [], label
                elif label in layout.summed or label in layout.headings or label in (
                    "financial assets", "financial liabilities", "adjustments for", "net change in",
                ):
                    heading, pending, group_at = label, [], len(rows)
                else:
                    pending.append(text)
                continue
        if [int(v) for v in split[1]] == [int(c[:4]) for c in statement.columns]:
            pending = []  # a running header: "Mahindra & Mahindra Limited 95 2013 2012"
            continue
        own = normalise(split[0])
        joined = normalise(" ".join(pending + [split[0]])) if pending else own
        pending = []
        if own == "total" and part:  # older balance sheets: "TOTAL" closes each half
            own = joined = f"total {part}"
        values, readings = split[1], alternatives(line, n)
        if _LESS.match(own) and all(v >= 0 for v in values):
            # "Less: Amounts capitalised 160.74": printed positive, subtracted.
            own, joined = _LESS.sub("", own), _LESS.sub("", joined)
            values, readings = [-v for v in values], [[-v for v in r] for r in readings]
        row = Row(section, heading, joined, own, values, readings)

        # Statement-level lines first: "total equity and liabilities" isn't
        # the equity section's "total equity".
        total_of = None
        if not any(_starts(joined, p) or _starts(own, p) for p in layout.globals):
            total_of = _total_section(layout, joined) or _total_section(layout, own)
            if unlabelled and section:
                total_of = section
        if total_of:
            row.kind, row.section = "total", total_of
            section, heading = None, None
            # "Total expenses" after an unlabelled sum of the section and a
            # "Less: Amounts capitalised": the sum was a subtotal, and the
            # lines after it are still the section's.
            at = len(rows)
            while at and rows[at - 1].kind == "global" and rows[at - 1].section is None:
                at -= 1
            if at < len(rows) and at and rows[at - 1].kind == "total" and rows[at - 1].section == total_of \
                    and not rows[at - 1].label:
                rows[at - 1].kind = "group"
                for i, r in enumerate(rows[at:], at):
                    r.kind, r.section = "line", total_of
                    # An unlabelled sum of the lines under its heading
                    # ("(f) Financial assets" over (i) to (iii)) is their
                    # subtotal, not another line.
                    under = [x for x in rows[at:i] if x.kind == "line" and x.heading == r.heading]
                    if not r.label and r.heading and len(under) >= 2 and all(
                        abs(v - sum(x.values[col] or 0.0 for x in under)) <= 0.5 * len(under)
                        for col, v in enumerate(r.values)
                    ):
                        r.kind = "group"
        elif any(_starts(joined, p) or _starts(own, p) for p in layout.subtotals):
            row.kind = "subtotal"
        elif any(_starts(joined, p) or _starts(own, p) for p in layout.globals):
            row.kind, row.section = "global", None
            section, heading = None, None
        elif section is None:
            row.kind = "global"
        elif _sums_lines_above(rows, section, row.values):
            # "Income from Operations": revenue plus investment income above it.
            row.kind = "group"
        rows.append(row)
        if fnmatchcase(joined, layout.last[-1] + "*") or fnmatchcase(own, layout.last[-1] + "*"):
            break
    return rows


def _sums_lines_above(rows: list[Row], section: str, values: list[float]) -> bool:
    """A labelled line that adds up the section's lines since its start or
    its last subtotal: a subtotal printed like a line."""
    above = []
    for r in reversed(rows):
        if r.section != section or r.kind != "line":
            break
        above.append(r)
    return len(above) >= 2 and any(values) and all(
        abs(v - sum(r.values[col] or 0.0 for r in above)) <= 0.5 * len(above)
        for col, v in enumerate(values)
    )


def _adds_up(rows: list[Row], section: str, values: list[float]) -> bool:
    lines = [r for r in rows if r.kind == "line" and r.section == section]
    if not lines:
        return False
    tolerance = 0.5 * (len(lines) + 1)
    return all(
        abs(v - sum(r.values[col] or 0.0 for r in lines)) <= tolerance for col, v in enumerate(values)
    )


def _mismatches(rows: list[Row], n: int) -> list[tuple[int, int, float, float]]:
    """(row index, column, printed, added) for every total or running subtotal
    that its section's lines don't add up to."""
    found = []
    sums: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for i, row in enumerate(rows):
        if row.kind in ("total", "subtotal") and row.section:
            parts = sums.get(row.section, [0.0] * n)
            tolerance = 0.5 * (counts.get(row.section, 0) + 1)  # half a unit per rounded line
            for col, (printed, added) in enumerate(zip(row.values, parts)):
                if printed is not None and abs(printed - added) > tolerance:
                    found.append((i, col, printed, added))
        elif row.kind == "line" and row.section:
            running = sums.setdefault(row.section, [0.0] * n)
            counts[row.section] = counts.get(row.section, 0) + 1
            for col, v in enumerate(row.values):
                running[col] += v or 0.0
    return found


def _repair(rows: list[Row], n: int, statement: Statement) -> None:
    """Where a section doesn't add up, accept another reading of one of its
    lines if that alone makes every total and subtotal agree."""
    mismatched = {rows[i].section for i, *_ in _mismatches(rows, n)}
    for section in mismatched:
        working = []
        for row in rows:
            if row.section != section or row.kind != "line":
                continue
            for alternative in row.alternatives:
                original, row.values = row.values, alternative
                if not any(rows[i].section == section for i, *_ in _mismatches(rows, n)):
                    working.append((row, alternative))
                row.values = original
        if len(working) == 1:
            row, alternative = working[0]
            statement.warnings.append(
                f"page {statement.page}: read {row.label!r} as {alternative} rather than "
                f"{row.values}: only that makes its section add up (a lost thousands comma)"
            )
            row.values = alternative


def _map(layout: Layout, rows: list[Row], statement: Statement, unreliable: set) -> None:
    # A plain "Diluted" is earnings per share only when it's the statement's
    # one diluted line: not under each of several headings (continuing,
    # discontinued, both), nor a share count below "Diluted (₹)".
    ambiguous = {own for own in ("diluted",) if sum(r.own.startswith(own) for r in rows) > 1}
    gross: dict[str, bool] = {}  # items set from a "(Gross)" line
    for row in rows:
        if row.own in ambiguous:
            continue
        children = layout.summed.get(row.heading, ())
        item = sign = None
        if children and row.own.startswith(children):
            rule = next((r for r in layout.rules if r.matches(row.section, row.heading, row.kind)), None)
            summed = True
        else:
            rule = next((r for r in layout.rules
                         for label in (row.label, row.own) if r.matches(row.section, label, row.kind)), None)
            summed = rule is not None and rule.add
        if rule is None:
            continue
        item, sign = rule.item, rule.sign
        values = [None if v is None else (v * sign if v else 0.0) for v in row.values]
        if row.kind == "line":
            values = [None if (row.section, col) in unreliable else v for col, v in enumerate(values)]
        if item in statement.values and summed:
            statement.values[item] = [
                None if a is None or b is None else round(a + b, 2)
                for a, b in zip(statement.values[item], values)
            ]
        elif item in statement.values and row.own.endswith("(net)") and gross.get(item):
            # "Revenue from operations (Gross)", "Less: Excise duty",
            # "Revenue from operations (Net)": the item is the net figure.
            statement.values[item], gross[item] = values, False
        elif item in statement.values:
            statement.warnings.append(f"page {statement.page}: {row.label!r} appears twice; kept the first")
        else:
            statement.values[item] = values
            gross[item] = row.own.endswith("(gross)")


def _income_from_operations(statement: Statement) -> None:
    """M&M: "Revenue from operations" plus "Income from investment related to
    subsidiaries..." = "Income from Operations", which is what its filings
    call revenue from operations."""
    if "IncomeFromOperations" in statement.values:
        statement.values["RevenueFromOperations"] = statement.values.pop("IncomeFromOperations")


def _associates_outside_profit_before_tax(statement: Statement) -> None:
    """Statements that add the share of associates' profit above "Profit
    before tax" (M&M) print a figure the filings don't: their profit before
    tax, and profit from continuing operations, leave the share out."""
    v = statement.values
    share = v.get("ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod")
    if not share or "ProfitBeforeTax" not in v or "ProfitBeforeExceptionalItemsAndTax" not in v:
        return
    for col, s in enumerate(share):
        before, after = v["ProfitBeforeExceptionalItemsAndTax"][col], v["ProfitBeforeTax"][col]
        exceptional = (v.get("ExceptionalItemsBeforeTax") or [0.0] * len(share))[col] or 0.0
        if None in (s, before, after) or not s:
            continue
        if min(abs(before + s + exceptional - after), abs(before + s - exceptional - after)) <= 0.5 * 4:
            v["ProfitBeforeTax"][col] = round(after - s, 2)
            continuing = v.get("ProfitLossForPeriodFromContinuingOperations")
            if continuing and continuing[col] is not None:
                continuing[col] = round(continuing[col] - s, 2)


def _settle_exceptional_sign(statement: Statement) -> None:
    """Annual reports print exceptional items as charges or credits under a
    heading; the taxonomy adds them to profit. Keep whichever sign makes
    profit before tax add up."""
    v = statement.values
    if "ExceptionalItemsBeforeTax" not in v:
        return
    for col, x in enumerate(v["ExceptionalItemsBeforeTax"]):
        before, after = v.get("ProfitBeforeExceptionalItemsAndTax", [None] * 9)[col], \
            v.get("ProfitBeforeTax", [None] * 9)[col]
        if None in (x, before, after) or x == 0:
            continue
        if abs(before - x - after) < abs(before + x - after):
            v["ExceptionalItemsBeforeTax"][col] = -x


def _total_tax(statement: Statement) -> None:
    """Some statements list current and deferred tax with no total line."""
    v = statement.values
    if "TaxExpense" in v or "CurrentTax" not in v or "DeferredTax" not in v:
        return
    v["TaxExpense"] = [None if None in (c, d) else round(c + d, 2)
                       for c, d in zip(v["CurrentTax"], v["DeferredTax"])]


def _settle_tax_sign(statement: Statement) -> None:
    """Some statements print tax as a deduction, "(2,742.04)"; the taxonomy
    has it positive. Keep whichever sign makes profit add up."""
    v = statement.values
    items = [i for i in ("TaxExpense", "CurrentTax", "DeferredTax") if i in v]
    if "TaxExpense" not in v or "ProfitBeforeTax" not in v or "ProfitLossForPeriod" not in v:
        return
    share = v.get("ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod")
    continuing = v.get("ProfitLossForPeriodFromContinuingOperations")
    for col, tax in enumerate(v["TaxExpense"]):
        before, after = v["ProfitBeforeTax"][col], v["ProfitLossForPeriod"][col]
        if None in (tax, before, after) or tax == 0:
            continue
        # The profit tax is deducted to reach: before discontinued operations,
        # and perhaps before the share of associates.
        afters = [after]
        if continuing and continuing[col] is not None:
            afters.append(continuing[col])
        if share and share[col] is not None:
            afters.append(after - share[col])
        if min(abs(before + tax - a) for a in afters) < min(abs(before - tax - a) for a in afters):
            for item in items:
                if v[item][col] is not None:
                    v[item][col] = -v[item][col]


def _net_change_adds_up(statement: Statement) -> None:
    """The net change in cash before exchange effects is operating plus
    investing plus financing. A year where it isn't was printed after the
    effect of exchange rates, which it includes: drop it for that year."""
    v = statement.values
    net = v.get("IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges")
    parts = [v.get(f"CashFlowsFromUsedIn{s}Activities") for s in ("Operating", "Investing", "Financing")]
    if net is None or any(p is None for p in parts):
        return
    for col, value in enumerate(net):
        if value is None or any(p[col] is None for p in parts):
            continue
        added = sum(p[col] for p in parts)
        if abs(value - added) > 1.5:
            net[col] = None
            statement.warnings.append(
                f"page {statement.page}: net change in cash ({statement.columns[col]}) is {value:,.0f} but "
                f"operating, investing and financing add up to {added:,.0f}; it includes the effect of "
                "exchange rates, so it's left out for that year")


def _body_start(layout: Layout, lines: list[str]) -> int | None:
    """Index of the statement's first section heading or row; the lines
    before it are the header (title, unit, column dates)."""
    for i, line in enumerate(lines):
        label = normalise(line)
        if _section_for(layout, label) or label in layout.resets:
            return i
        if ((split_line(line, 2) or split_line(line, 3)) and not _is_column_header(line)) or (
            _values_only(line, 2) or _values_only(line, 3)
        ) and not _is_year_header(line) and not _is_page_furniture(line):
            # A section heading or label wrapped over the lines before the
            # first row ("I. Revenue from operations / (Net of excise duty)").
            start = i
            while start > 0 and i - start < 2 and _is_label_text(lines[start - 1]):
                start -= 1
            return start
    return None


def _is_label_text(line: str) -> bool:
    return not (_UNIT.search(line) or _DATE.search(line) or _is_year_header(line)
                or _is_repeated_header(line) or _is_page_furniture(line) or _has_values(line)
                or normalise(line) in ("as at", "year ended", "note"))


def _is_consolidated(layout: Layout, head_text: str, page: str) -> bool:
    consolidated = re.compile(r"(?<!un)consolidated")
    # Older reports print the title at the foot of the page.
    if any(layout.title in l.lower() and consolidated.search(l.lower()) for l in page.splitlines()):
        return True
    # A side menu naming every section ("STANDALONE ACCOUNTS / CONSOLIDATED
    # ACCOUNTS") says nothing; the title would have said consolidated.
    return consolidated.search(head_text) is not None and "standalone" not in head_text


def parse_statement(layout: Layout, page: str, page_number: int,
                    year_end: str | None = None, whole_page: str | None = None) -> Statement | None:
    """`year_end` ("-03-31"): the report's year end, for statements whose
    columns are headed by years alone."""
    lines = [l.strip() for l in page.splitlines() if l.strip()]
    body_at = _body_start(layout, lines)
    if body_at is None:
        return None
    header = lines[:body_at]
    head_text = " ".join(header).lower()
    unit = _unit("\n".join(header))
    unit_below = not unit
    if not unit:
        # Text extraction can put the title and unit below the table: take
        # the page's unit lines, when they agree.
        stated = {_unit(l) for l in lines[body_at:] if _is_unit_line(l)}
        unit = stated.pop() if len(stated) == 1 else None
    columns = []
    year_header = any(_is_year_header(l) for l in header)
    # Text above the unit line can be an accounting-policy note with its own
    # dates ("Effective April 1, 2017, the Company adopted...").
    units_at = [i for i, l in enumerate(header) if _UNIT.search(l)]
    dated = header[units_at[-1]:] if units_at and any(
        _DATE.search(l) or _is_year_header(l) for l in header[units_at[-1]:]) else header
    for line in dated:
        if layout.title in _LIGATURE.sub(r"\1", line.lower()) or year_header:
            continue  # the title may list the dates in another form; "Note 2012 2011" rules
        for m in _DATE.finditer(line):
            iso = _date(m)
            if _is_period_date(iso) and iso not in columns:
                columns.append(iso)
    if not columns:
        # "Note 2017 2016", the month and day given once in the title
        # ("... for the years ended March 31, 2017 and 2016") or elsewhere
        # in the report. Sign-off dates ("Mumbai, April 18, 2017") aren't
        # period ends.
        titles = [l for l in (whole_page or page).splitlines() if layout.title in normalise(l)]
        day = next((iso[4:] for l in header + titles for m in _DATE.finditer(l)
                    if _is_period_date(iso := _date(m))), year_end)
        # Newest first, as Indian statements print them; text extraction
        # sometimes scrambles a header's order ("2015 2017 2016").
        years = sorted(dict.fromkeys(
            y for l in dated if _is_year_header(l) for y in _YEAR.findall(l)), reverse=True)
        month_days = [(_MONTHS.split("|").index(m.group(1)) + 1, int(m.group(2)))
                      for l in dated if layout.title not in normalise(l)
                      for m in _MONTH_DAY.finditer(l)]
        if month_days and years and len(month_days) <= len(years):
            # The first date heads the extra years: "As at March 31, | As
            # at April 1," over "2017 2016 2015" is two March 31s and April 1.
            extra = len(years) - len(month_days)
            columns = [date(int(y), *month_days[max(0, i - extra)]).isoformat()
                       for i, y in enumerate(years)]
        elif day and years:
            columns = [f"{y}{day}" for y in years]
    statement = Statement(
        kind=layout.kind,
        # "Unconsolidated" is how some years title the standalone statements.
        consolidated=_is_consolidated(layout, head_text, whole_page or page),
        page=page_number,
        unit=unit or 0,
        columns=columns,
        instant=layout.instant,
        vocabulary=BALANCE_SHEET_ITEMS if layout.instant else frozenset(r.item for r in layout.rules),
        per_share=layout.per_share,
        counts=layout.counts,
    )
    signed = list(_SIGN_OFF.finditer(page))
    statement.signed_off = _date(signed[-1]) if signed else None
    if not unit:
        statement.warnings.append(f"page {page_number}: no unit (crore/lakh) stated")
        return statement
    if len(columns) not in (2, 3):
        if unit_below:
            return None  # a unit stated elsewhere on a page that isn't a statement
        statement.warnings.append(f"page {page_number}: expected 2 or 3 dated columns, found {columns}")
        return statement

    n = len(columns)
    body = drop_running_totals([part for line in lines[body_at:] for part in split_rows(line, n)], n)
    rows = _rows(layout, body, n, statement)
    _repair(rows, n, statement)
    unreliable = set()
    for i, col, printed, added in _mismatches(rows, n):
        unreliable.add((rows[i].section, col))
        statement.warnings.append(
            f"page {page_number}: {rows[i].label or 'total'} ({columns[col]}) is {printed:,.0f} but "
            f"its lines add up to {added:,.0f}; that section's lines dropped for that year"
        )
    _map(layout, rows, statement, unreliable)
    _income_from_operations(statement)
    _associates_outside_profit_before_tax(statement)
    _settle_exceptional_sign(statement)
    _total_tax(statement)
    _settle_tax_sign(statement)
    _net_change_adds_up(statement)
    owners, profit = "ProfitOrLossAttributableToOwnersOfParent", "ProfitLossForPeriod"
    if not statement.consolidated and owners in statement.values and profit not in statement.values:
        # Older standalone statements: "profit for the year" has no minority
        # interest to come after; it is the company's profit.
        statement.values[profit] = statement.values.pop(owners)
    basic = "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"
    if basic in statement.values and any(
        r.label.startswith("earnings per equity share") and "diluted" in r.label for r in rows
    ):
        statement.values["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations"] = \
            list(statement.values[basic])
    return statement


def _until_end_marker(layout: Layout, page: str) -> str:
    """A page up to the "Notes forming part..." footer and signatures after
    its statement, which some reports print on every page of a statement.
    (Others print the signatures above the statement.)"""
    lines = page.splitlines()
    body = _body_start(layout, [l.strip() for l in lines])
    if body is None:
        return page
    seen = 0
    for i, line in enumerate(lines):
        seen += bool(line.strip())
        if seen > body and normalise(line).startswith(_END_MARKERS):
            return "\n".join(lines[:i])
    return page


def _continuation(layout: Layout, page: str) -> str:
    """The next page without its running header and column dates."""
    lines = [l for l in page.splitlines() if l.strip()]
    i = 0
    while i < len(lines) and (
        _is_page_furniture(lines[i]) or _is_repeated_header(lines[i]) or _UNIT.search(lines[i])
        or (_DATE.search(lines[i]) and not split_line(lines[i], 2))
        or normalise(lines[i]) in ("year ended", "as at", "note")
    ):
        i += 1
    return "\n".join(lines[i:])


# Dot leaders between a label and its values: "Revenue from operations..... 25 98,763.42"
_LEADERS = re.compile(r"(?:\s*\.){4,}|…+")


def _parts(page: str, layouts: tuple[Layout, ...]) -> list[str]:
    """A two-page spread printed as one PDF page can hold two statements
    (M&M: the profit and loss beside the balance sheet), each under its own
    unit line. Split it there when each part is a statement of its own;
    otherwise (a statement continuing in the right half) keep it whole."""
    lines = page.splitlines()
    units = [i for i, l in enumerate(lines) if _is_unit_line(l)]
    if len(units) == 1:
        # A statement starting below the end of another table (the changes
        # in equity above the cash flows): it begins at its title, the lines
        # without figures just above its unit line.
        u = start = units[0]
        while start > 0 and u - start < 3 and not _has_values(lines[start - 1]):
            start -= 1
        above, below = "\n".join(lines[:start]), "\n".join(lines[start:])
        if sum(map(_has_values, lines[:start])) >= 5 and any(
            _has_rows(below, l.required) and not _has_rows(above, l.required) for l in layouts
        ):
            return [below]
    if len(units) < 2:
        return [page]
    head = lines[:units[0]]
    parts = ["\n".join(head + lines[a:b]) for a, b in zip(units, units[1:] + [len(lines)])]
    kinds = [{l.kind for l in layouts if _has_rows(p, l.required)} for p in parts]
    statement = [bool(k) for k in kinds]
    if sum(statement) >= 2 and len(set().union(*kinds)) >= 2 and not any(
        a & b for i, a in enumerate(kinds) for b in kinds[i + 1:]
    ):
        return parts  # different statements; one continuing under a repeated header is one
    if any(statement) and not statement[0]:
        # Another table (changes in equity) beside the start of a statement.
        # A statement's own left half has its section headings
        # ("Non-current assets"); a table of changes in equity has none.
        start = units[statement.index(True)]
        headings = {h for l in layouts for h in l.sections}
        before = lines[units[0]:start]
        labels = {normalise(l) for l in before} | {normalise(f"{a} {b}") for a, b in zip(before, before[1:])}
        if sum(map(_has_values, before)) >= 5 and not labels & headings:  # headings may wrap
            return ["\n".join(head + lines[start:])]
    return [page]


def statements(pages: list[str], layouts: tuple[Layout, ...] = LAYOUTS) -> list[Statement]:
    """Every supported statement in an annual report's pages."""
    pages = [_LEADERS.sub(" ", page) for page in pages]
    found: list[Statement] = []
    continued: set[tuple[str, int]] = set()  # (kind, page) used as a continuation
    for layout in layouts:
        # The year end, from a statement that states full dates.
        year_end = next((st.columns[0][4:] for st in found if st.columns), None)
        for number, whole_page in enumerate(pages, 1):
            parts = _parts(whole_page, layouts)
            for at, page in enumerate(parts):
                if (layout.kind, number) in continued or not _has_rows(page, layout.required):
                    continue
                if layout.title_required and layout.title not in whole_page.lower():
                    continue
                last_part = at == len(parts) - 1
                if not _ends_here(page, layout) and number < len(pages) and last_part:
                    page = _until_end_marker(layout, page) + "\n" + _continuation(layout, pages[number])
                    continued.add((layout.kind, number + 1))
                statement = parse_statement(layout, page, number, year_end, whole_page)
                if statement is None or (not statement.unit and not statement.values):
                    continue  # e.g. a summary in the management discussion
                if statement.signed_off is None and number < len(pages):
                    # The signatures can spill onto the next page.
                    signed = list(_SIGN_OFF.finditer(pages[number]))
                    statement.signed_off = _date(signed[0]) if signed else None
                found.append(statement)
    # The same statement twice (a summary reprinted elsewhere): keep the
    # fuller one.
    best: dict[tuple, Statement] = {}
    for st in found:
        key = (st.kind, st.consolidated, tuple(sorted(st.columns)))
        if key not in best or len(st.values) > len(best[key].values):
            best[key] = st
    return [st for st in found if best[(st.kind, st.consolidated, tuple(sorted(st.columns)))] is st]


def balance_sheets(pages: list[str]) -> list[Statement]:
    return statements(pages, (BALANCE_SHEET,))


# Kept for callers that only need the balance sheet's vocabulary.
LABELS = BALANCE_SHEET_ITEMS
