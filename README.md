# madhippu

Financial statements out of Indian annual reports. Give it an annual-report
PDF; get the balance sheet, profit and loss and cash flows, consolidated and
standalone, as JSON or an Excel workbook.

*Madhippu* is Tamil for value.

- **Checked, not guessed.** Every section must add up to its printed total.
  When one doesn't, its lines are dropped for that year and a warning says
  so; a misread digit is repaired only when the totals confirm the repair.
- **Traceable.** Every statement records its page; every figure, its period,
  unit and printed precision.
- **Comparable.** Item names follow SEBI's XBRL taxonomy for financial
  results where a line means the same thing, so figures line up with the
  quarterly XBRL filings.
- **Deterministic and offline.** No AI, no network calls. Your documents
  never leave your machine.

Handles Ind-AS statements (FY17 onward) and both Indian GAAP layouts:
revised Schedule VI (FY12 to FY16) and the old Schedule VI (to FY11).

> **Not investment advice.** madhippu's output is extracted by software and
> can be wrong. Check every figure you rely on against the report. You are
> responsible for your decisions; madhippu comes with no warranty and no
> liability. Read [DISCLAIMER.md](DISCLAIMER.md), which you accept by using
> madhippu.

## Install

### Without Python

Download the app for your computer from the
[latest release](https://github.com/MuthuSubramanian00/madhippu/releases/latest)
(Windows, Mac with Apple silicon, or Linux), unzip it and double-click
`madhippu`. A page opens in your browser: drop an annual report on it, see
the statements, and download them as Excel. The page runs on your own
computer; nothing is uploaded.

madhippu isn't signed with a paid certificate, so the first time Windows or
macOS warns that it can't verify the developer. The "READ ME FIRST" file in
the download says how to open it anyway.

### With Python

Python 3.10 or newer.

```bash
pipx install git+https://github.com/MuthuSubramanian00/madhippu
```

Or from a checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Use

`madhippu` with no arguments opens the page in your browser, as the app
does. With a file:

```bash
madhippu annual-report.pdf --json out.json --xlsx out.xlsx
```

```text
page 208: standalone balance sheet (2026-03-31, 2025-03-31), 64 figures
page 210: standalone profit and loss (2026-03-31, 2025-03-31), 36 figures, 1 warning
page 217: standalone cash flows (2026-03-31, 2025-03-31), 18 figures
...
```

With neither option, JSON goes to standard output. The workbook has an
"About" sheet and one sheet per statement, in the unit the report prints
(₹ crore, lakh…). The JSON is described in [docs/schema.md](docs/schema.md);
figures there are in rupees.

From Python:

```python
import madhippu

report = madhippu.parse("annual-report.pdf")
for st in report.statements:
    print(st.kind, st.consolidated, st.page, st.values.get("Assets"))
data = madhippu.to_dict(report)  # the JSON shape
```

## What it has been tested on

Every annual report of these companies on NSE, FY10 to FY26, checked on
the author's machine before each release. "Complete" means all six
statements were found: consolidated and standalone balance sheet, profit and
loss and cash flows.

| Company | Complete | Gaps |
|---|---|---|
| TCS | FY10–FY26 | — |
| Infosys | FY14–FY26 | FY10, FY11, FY13: scanned, or not on NSE. FY12: standalone balance sheet and P&L. FY18: consolidated P&L |
| Mahindra & Mahindra | FY13–FY26 | FY10–FY12: some standalone statements and the consolidated P&L |

"Found" isn't "perfect": a statement can still carry warnings for sections
that didn't add up. Another company's wording may need rules added; reports
of what didn't parse are welcome (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Limits

- Text-layer PDFs only. Scanned pages have no text layer; madhippu lists
  them and can't read them (no OCR).
- Annual reports' primary statements only, not notes or schedules.
- Indian rupee reports in crore, lakh or million.

## Tests

```bash
.venv/bin/pytest
```

Tests run offline against synthetic pages in `tests/fixtures/synthetic/`:
invented companies and figures, laid out the way PDFium extracts real
reports, one layout quirk per page (statements over two pages, side by side,
beside another table; split or scrambled column headers; a lost thousands
comma; the older Indian GAAP formats). Every section adds up, as in a real
report. No real report text is in this repository.

## Licence

Copyright (C) 2026 Muthu Subramanian

madhippu is free software: you can redistribute it and/or modify it under
the terms of the [GNU Affero General Public License, version 3](LICENSE)
(AGPL-3.0-only).

In short: you may use, study, change and share madhippu. If you distribute
it, or run a modified version that others use over a network (for example
as a hosted web service), you must make your version's complete source
available to them under the same licence.

PDF text extraction uses [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)
(Apache-2.0/BSD-3-Clause); Excel output uses
[openpyxl](https://openpyxl.readthedocs.io) (MIT).
