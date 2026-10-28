"""madhippu REPORT.pdf [--json FILE] [--xlsx FILE]

With neither option, JSON goes to standard output. A summary goes to
standard error. Exit status: 0 if any statement was read, 1 if none, 2 if
the file couldn't be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from madhippu import __version__
from madhippu.report import parse, to_dict

TITLES = {"balance_sheet": "balance sheet", "profit_and_loss": "profit and loss",
          "cash_flow": "cash flows"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="madhippu",
        description="Read the financial statements in an Indian annual report (PDF) "
                    "into JSON or Excel. Not investment advice: check every figure "
                    "against the report.",
    )
    parser.add_argument("pdf", type=Path, help="the annual report")
    parser.add_argument("--json", type=Path, metavar="FILE", help="write JSON here ('-' for stdout)")
    parser.add_argument("--xlsx", type=Path, metavar="FILE", help="write an Excel workbook here")
    parser.add_argument("--version", action="version", version=f"madhippu {__version__}")
    args = parser.parse_args(argv)

    try:
        report = parse(args.pdf)
    except Exception as e:  # missing file, not a PDF, damaged PDF
        print(f"madhippu: can't read {args.pdf}: {e}", file=sys.stderr)
        return 2

    data = to_dict(report)
    if args.json is None and args.xlsx is None:
        args.json = Path("-")
    if args.json is not None:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if str(args.json) == "-":
            sys.stdout.write(text)
        else:
            args.json.write_text(text, encoding="utf-8")
    if args.xlsx is not None:
        from madhippu import xlsx  # openpyxl is only needed here
        xlsx.write(report, args.xlsx)

    for st in data["statements"]:
        years = ", ".join(p["end"] for p in st["periods"])
        problems = len(st["warnings"]) + len(st["failed_checks"])
        print(f"page {st['page']}: {st['basis']} {TITLES[st['kind']]} ({years}), "
              f"{len(st['facts'])} figures" + (f", {problems} warning{'s' if problems > 1 else ''}" if problems else ""),
              file=sys.stderr)
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print("Not investment advice. Check figures against the report before relying on them.",
          file=sys.stderr)
    return 0 if report.statements else 1


if __name__ == "__main__":
    sys.exit(main())
