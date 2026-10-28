"""Numeric facts, and the arithmetic every statement must satisfy.

A fact is one figure for one period, in rupees (or rupees per share, or a
number of shares). Item names follow SEBI's XBRL taxonomy for financial
results wherever a line means the same thing, so output lines up with the
XBRL filings companies make every quarter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Fact:
    item: str
    period_start: str  # equals period_end for point-in-time items
    period_end: str
    value: float
    unit: str  # "INR", "INR/shares" or "shares"
    decimals: int | None  # precision as printed (-7: nearest crore); None = exact


# total = sum of parts, per period. Checked only when every item is present.
IDENTITIES = [
    ("Income", ["RevenueFromOperations", "OtherIncome"]),
    ("ProfitBeforeTax", ["ProfitBeforeExceptionalItemsAndTax", "ExceptionalItemsBeforeTax"]),
    ("TaxExpense", ["CurrentTax", "DeferredTax"]),
    ("Expenses", [
        "CostOfMaterialsConsumed", "PurchasesOfStockInTrade",
        "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
        "EmployeeBenefitExpense", "FinanceCosts",
        "DepreciationDepletionAndAmortisationExpense", "OtherExpenses",
    ]),
    ("Assets", ["NoncurrentAssets", "CurrentAssets"]),
    ("EquityAndLiabilities", ["Equity", "Liabilities"]),
    ("Assets", ["EquityAndLiabilities"]),
]
# total = first - second
DIFFERENCES = [
    ("ProfitBeforeExceptionalItemsAndTax", "Income", "Expenses"),
]


def _tolerance(facts: list[Fact]) -> float:
    """Each reported figure is rounded to half a unit of its stated precision,
    so an identity can be off by at most the sum of those half-units."""
    return sum(0.5 * 10 ** -f.decimals for f in facts if f.decimals is not None) + 1e-6


def check(facts: list[Fact]) -> list[str]:
    """Describe every identity the facts break; empty when all hold."""
    by_period: dict[tuple[str, str], dict[str, Fact]] = {}
    for f in facts:
        by_period.setdefault((f.period_start, f.period_end), {})[f.item] = f
    warnings = []
    for (start, end), items in sorted(by_period.items()):
        def report(total, terms, expression, expected):
            used = [items[total]] + [items[t] for t in terms]
            gap = items[total].value - expected
            if abs(gap) > _tolerance(used):
                warnings.append(
                    f"{start}..{end}: {total} {items[total].value:,.0f} != "
                    f"{expression} {expected:,.0f} (off by {gap:,.0f})"
                )
        for total, parts in IDENTITIES:
            if total in items and all(p in items for p in parts):
                report(total, parts, " + ".join(parts), sum(items[p].value for p in parts))
        for total, first, second in DIFFERENCES:
            if all(i in items for i in (total, first, second)):
                report(total, [first, second], f"{first} - {second}",
                       items[first].value - items[second].value)
    return warnings
