"""Parse an annual report and describe the result as plain data.

`parse()` reads a PDF; `parse_pages()` takes page texts already extracted.
`to_dict()` gives the versioned JSON shape documented in docs/schema.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from madhippu import __version__, pdf, statements
from madhippu.facts import Fact, check

SCHEMA = "madhippu/1"

DISCLAIMER = (
    "Extracted by software (madhippu) and may be wrong or incomplete. Check "
    "every figure against the source document before relying on it. Not "
    "investment advice. Provided without warranty; you are responsible for "
    "your decisions. See DISCLAIMER.md."
)

UNIT_NAMES = {10**7: "crore", 10**6: "million", 10**5: "lakh"}


@dataclass
class Report:
    file: str | None
    sha256: str | None
    page_count: int
    pages_without_text: list[int]  # 1-based; scans need OCR, which madhippu doesn't do
    statements: list[statements.Statement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_pages(pages: list[str], file: str | None = None, sha256: str | None = None) -> Report:
    report = Report(
        file=file,
        sha256=sha256,
        page_count=len(pages),
        pages_without_text=[n for n, p in enumerate(pages, 1) if not pdf.has_text(p)],
    )
    report.statements = sorted(statements.statements(pages), key=lambda st: st.page)
    if report.pages_without_text:
        listed = ", ".join(map(str, report.pages_without_text[:10]))
        more = len(report.pages_without_text) - 10
        report.warnings.append(
            f"no text layer on page{'s' if len(report.pages_without_text) > 1 else ''} {listed}"
            + (f" and {more} more" if more > 0 else "")
            + " (scans or images); a statement on them can't be read"
        )
    if not report.statements:
        report.warnings.append("no financial statements found")
    return report


def parse(path: Path) -> Report:
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return parse_pages(pdf.pages(path), file=path.name, sha256=digest)


def facts(st: statements.Statement) -> list[Fact]:
    """The statement's facts. Amounts are in rupees; when the statement
    states no unit, they're left as printed, with unit "unknown"."""
    if st.unit:
        return st.facts()
    out = []
    for item, values in st.values.items():
        for col, value in enumerate(values):
            if value is not None:
                start, end = st.period(col)
                out.append(Fact(item, start, end, value, "unknown", None))
    return out


def _statement_dict(st: statements.Statement) -> dict:
    found = facts(st)
    return {
        "kind": st.kind,
        "basis": "consolidated" if st.consolidated else "standalone",
        "page": st.page,
        "unit": UNIT_NAMES.get(st.unit, "unknown"),
        "signed_off": st.signed_off,
        "periods": [dict(zip(("start", "end"), st.period(c))) for c in range(len(st.columns))],
        "facts": [
            {"item": f.item, "period_start": f.period_start, "period_end": f.period_end,
             "value": f.value, "unit": f.unit, "decimals": f.decimals}
            for f in found
        ],
        "warnings": st.warnings,
        "failed_checks": check(found) if st.unit else [],
    }


def to_dict(report: Report) -> dict:
    return {
        "schema": SCHEMA,
        "madhippu_version": __version__,
        "disclaimer": DISCLAIMER,
        "source": {
            "file": report.file,
            "sha256": report.sha256,
            "pages": report.page_count,
            "pages_without_text": report.pages_without_text,
        },
        "statements": [_statement_dict(st) for st in report.statements],
        "warnings": report.warnings,
    }
