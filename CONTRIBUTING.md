# Contributing

Thank you for your interest. Reports are very welcome; code contributions
are not accepted yet.

## Reporting a statement that didn't parse

Open an issue with the "Statement didn't parse" template and include:

- the company, the financial year of the report, and where you got it
  (a link to the exchange or company page, not the file itself);
- the page number of the statement;
- madhippu's version and its warnings for that statement.

Please don't attach whole annual reports. If a page's layout matters, a
screenshot of that one page is enough.

## Code contributions

Pull requests from outside contributors aren't being merged until the
project decides how contributions are licensed (a contributor licence
agreement or a Developer Certificate of Origin sign-off). This file will say
how once that's settled. Until then, describing a fix in an issue is the
best way to help.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Test fixtures are synthetic: a page or two of text as PDFium would extract
it (`madhippu.pdf.pages`), pages separated by form feeds, reproducing one
layout quirk with an invented company and invented figures that add up. Never
commit text from a real report, and no real person's or company's names.
