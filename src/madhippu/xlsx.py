"""Write a parsed report as an Excel workbook: an "About" sheet, then one
sheet per statement with items down and periods across, in the statement's
own unit (crore, lakh…) as printed.

Every sheet starts with the disclaimer, so it stays with the numbers when a
sheet is copied elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from madhippu import __version__
from madhippu.report import DISCLAIMER, SCHEMA, UNIT_NAMES, Report

SHORT = {"balance_sheet": "BS", "profit_and_loss": "P&L", "cash_flow": "Cash flow"}
TITLES = {"balance_sheet": "Balance sheet", "profit_and_loss": "Profit and loss",
          "cash_flow": "Cash flows"}
BOLD = Font(bold=True)


def _put(ws, row: int, col: int, value, bold: bool = False):
    cell = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str):
        # Text from a PDF must never become a formula ("=HYPERLINK(...)").
        cell.data_type = "s"
    if bold:
        cell.font = BOLD
    return cell


def _about(ws, report: Report) -> None:
    ws.title = "About"
    rows = [
        ("madhippu output", None),
        ("Disclaimer", DISCLAIMER),
        ("Source file", report.file),
        ("SHA-256", report.sha256),
        ("Pages", report.page_count),
        ("Pages without text", ", ".join(map(str, report.pages_without_text)) or "none"),
        ("Pages with letters spaced apart", ", ".join(map(str, report.pages_letter_spaced)) or "none"),
        ("madhippu version", __version__),
        ("Schema", SCHEMA),
    ]
    for r, (key, value) in enumerate(rows, 1):
        _put(ws, r, 1, key, bold=True)
        _put(ws, r, 2, value)
    r = len(rows) + 2
    _put(ws, r, 1, "Warnings", bold=True)
    for r, warning in enumerate(report.warnings or ["none"], r):
        _put(ws, r, 2, warning)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 100


def _statement(ws, st, report: Report) -> None:
    unit = UNIT_NAMES.get(st.unit)
    basis = "Consolidated" if st.consolidated else "Standalone"
    _put(ws, 1, 1, DISCLAIMER, bold=True)
    _put(ws, 2, 1, f"{basis} {TITLES[st.kind].lower()}, page {st.page} of {report.file or 'the report'}"
                   + (f", signed off {st.signed_off}" if st.signed_off else ""))
    _put(ws, 3, 1, f"Amounts in ₹ {unit}; per-share figures in ₹; share counts in numbers."
                   if unit else "No unit stated on the page: amounts as printed.")

    header = 5
    _put(ws, header, 1, "Item", bold=True)
    _put(ws, header, 2, "Unit", bold=True)
    for c in range(len(st.columns)):
        start, end = st.period(c)
        _put(ws, header, 3 + c, end if start == end else f"{start} to {end}", bold=True)

    r = header
    for item, values in st.values.items():
        r += 1
        _put(ws, r, 1, item)
        if item in st.per_share:
            _put(ws, r, 2, "₹/share")
        elif item in st.counts:
            _put(ws, r, 2, "shares")
        else:
            _put(ws, r, 2, f"₹ {unit}" if unit else "as printed")
        for c, value in enumerate(values):
            if value is not None:  # None: dropped because its section didn't add up
                _put(ws, r, 3 + c, value)

    if st.warnings:
        r += 2
        _put(ws, r, 1, "Warnings", bold=True)
        for warning in st.warnings:
            r += 1
            _put(ws, r, 1, warning)
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 12
    for c in range(len(st.columns)):
        ws.column_dimensions[chr(ord("C") + c)].width = 16
    ws.freeze_panes = ws.cell(row=header + 1, column=3)


def write(report: Report, path: Path) -> None:
    wb = Workbook()
    _about(wb.active, report)
    used: set[str] = set()
    for st in report.statements:
        basis = "Consolidated" if st.consolidated else "Standalone"
        name = f"{basis} {SHORT[st.kind]} p{st.page}"
        while name in used:
            name += "+"
        used.add(name)
        _statement(wb.create_sheet(name[:31]), st, report)
    wb.save(path)
