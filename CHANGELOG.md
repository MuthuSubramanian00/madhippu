# Changelog

madhippu follows [semantic versioning](https://semver.org). The JSON output
has its own version (`schema`), described in [docs/schema.md](docs/schema.md).

## 0.1.1 (2026-10-29)

Reads KEC International's and Ather Energy's FY26 reports, which 0.1.0
found nothing in, and M&M's FY11 and FY12 standalone statements. The rules
are general, so other reports laid out the same way should benefit.

- The rupee sign written as a backtick before "in Crore" ("` in Crore") is
  a unit line, so two statements on one PDF page are split there.
- Section headings numbered "(1)", "(2)".
- An unlabelled subtotal under a sub-heading ("(f) Financial assets") is a
  subtotal, not another line; unlabelled subtotals are never read as the
  section's total.
- Two rows that text extraction ran into one line are split; "*-" (less
  than the rounding norm) reads as nil.
- "Profit after exceptional items and before tax", "Tax expenses";
  arithmetic hints after a label ("(I+II)", "(A+B+C)") are ignored.
- A statement ends on its page when its last line is a heading over the
  rows that hold its figures ("Earnings per equity share" over "Basic" and
  "Diluted"). Profit and loss statements no longer run into the next page.
- A statement that starts below another table (the cash flows below the
  changes in equity) starts at its own title.
- A section total printed beside the section's last line, year by year, is
  dropped from that line.
- More cash flow wordings: taxes "Refund/ (Payment)", "Capital
  expenditure on property, plant and equipment", "Finance costs paid",
  "Net (decrease)/increase in cash", "Effect of exchange differences on
  restatement".
- The net change in cash before exchange effects must equal operating plus
  investing plus financing; a figure printed after the exchange effect,
  which includes it, is left out with a warning instead of being
  mislabelled.
- A damaged text layer, one that spaces letters apart ("a ss ets", "1, 35,
  7 05"), is detected and named in a warning, with a suggestion to use the
  copy filed with the stock exchange; the pages are listed in the JSON
  (`source.pages_letter_spaced`) and the Excel "About" sheet.
- A loss-making year's lines ("Loss before tax", "Total comprehensive
  loss", "Loss per equity share") are read as the profit lines.
- Units stated inside a line ("CIN: ... (Amounts in ₹ crores, unless
  otherwise stated)"), or only below the table when text extraction puts
  the title there; a two-page spread's page numbers ("114 115"); "TOTAL -
  NON CURRENT ASSETS"; "VIIITax expense"; "Total expenses (IV)"; a
  continuation's title and header between a label and its values.
- "Revenue from operations (Net)" after "(Gross)" and "Less: Excise duty"
  is the revenue; "Add : Exceptional items"; an earnings-per-share heading
  with a note number ends the statement.
- A plain "Diluted" line is diluted earnings per share when it's the
  statement's only diluted line.
- Fixes on reports already supported: current assets and liabilities
  where "assets held for sale" follow an unlabelled subtotal (Infosys);
  exceptional items on M&M's FY12 to FY17 statements, which had picked
  up lines from the next page; gross revenue reported instead of net (M&M
  FY12); signature lines ("Membership No") no longer produce warnings.

## 0.1.0 (2026-10-28)

First release.

- Reads balance sheets, profit and loss statements and cash flows,
  consolidated and standalone, from an annual report's text layer: Ind-AS,
  revised Schedule VI and old Schedule VI layouts.
- Tested on every NSE annual report of TCS, Infosys and Mahindra & Mahindra
  from FY10 to FY26.
- `madhippu` command: JSON (schema `madhippu/1`) and Excel output, with the
  disclaimer in both.
- A page in the browser for people who don't use a terminal: `madhippu`
  with no arguments, or the downloadable app for Windows, macOS and Linux.
  It runs on the user's own computer (127.0.0.1) and uploads nothing.
- Python API: `madhippu.parse`, `madhippu.parse_pages`, `madhippu.to_dict`.
- Tests on synthetic pages: invented companies and figures, one layout
  quirk per page; no real report text.
