# JSON output: schema `madhippu/1`

`madhippu report.pdf --json out.json` (or `madhippu.to_dict(report)` in
Python) produces one object:

```json
{
  "schema": "madhippu/1",
  "madhippu_version": "0.1.0",
  "disclaimer": "Extracted by software (madhippu) and may be wrong ...",
  "source": {
    "file": "report.pdf",
    "sha256": "4fc4a071...",
    "pages": 384,
    "pages_without_text": [2, 384]
  },
  "statements": [
    {
      "kind": "balance_sheet",
      "basis": "consolidated",
      "page": 180,
      "unit": "crore",
      "signed_off": "2024-04-12",
      "periods": [
        {"start": "2024-03-31", "end": "2024-03-31"},
        {"start": "2023-03-31", "end": "2023-03-31"}
      ],
      "facts": [
        {"item": "Assets", "period_start": "2024-03-31", "period_end": "2024-03-31",
         "value": 1464490000000, "unit": "INR", "decimals": -7}
      ],
      "warnings": [],
      "failed_checks": []
    }
  ],
  "warnings": []
}
```

## Top level

| Field | Meaning |
|---|---|
| `schema` | `madhippu/1`. Changes only when a field is removed or changes meaning; new fields can appear within a version. |
| `madhippu_version` | The release that produced the output. Parsing improves between releases, so keep this with the numbers. |
| `disclaimer` | Always present. Keep it with the data if you pass the data on. |
| `source` | The file's name, its SHA-256, its page count, and the pages with no text layer (scans or images, which madhippu can't read). |
| `statements` | Each statement found, in page order. |
| `warnings` | About the document as a whole: pages without text, or no statements found. |

## Statements

| Field | Meaning |
|---|---|
| `kind` | `balance_sheet`, `profit_and_loss` or `cash_flow`. |
| `basis` | `consolidated` or `standalone`. |
| `page` | 1-based page number in the PDF where the statement starts (it may continue on the next page). |
| `unit` | The unit printed on the page: `crore`, `lakh`, `million`, or `unknown` when the page states none. |
| `signed_off` | The date the statements were approved, from the signatures; `null` when not found. |
| `periods` | One per column, newest first. Balance sheets have `start` equal to `end`; others cover the year. |
| `facts` | The figures read (see below). |
| `warnings` | What was dropped or repaired, and why. A figure missing for a period is usually explained here. |
| `failed_checks` | Accounting identities the figures break (e.g. assets ≠ equity and liabilities). Empty when all hold. |

## Facts

| Field | Meaning |
|---|---|
| `item` | The line item. Names follow SEBI's XBRL taxonomy for financial results (`RevenueFromOperations`, `Assets`…) where the line means the same thing; lines the taxonomy groups differently get names of their own (`RightOfUseAssets`, `LeaseLiabilitiesNoncurrent`…). |
| `period_start`, `period_end` | ISO dates. |
| `value` | In rupees (`unit` `INR`), rupees per share (`INR/shares`, earnings per share), or a number of shares (`shares`). Converted from the printed unit: 9,376 crore becomes 93,760,000,000. If the page states no unit, the value is as printed and `unit` is `unknown`. |
| `unit` | `INR`, `INR/shares`, `shares` or `unknown`. |
| `decimals` | The printed precision as a power of ten: `-7` means rounded to the nearest crore. `null` for per-share figures. |

Signs follow the taxonomy: expenses and payments are positive amounts; a
loss or an outflow total is negative.

A line whose section didn't add up to its printed total is left out for
that period, never guessed; the statement's `warnings` say so.
