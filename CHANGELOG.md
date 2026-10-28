# Changelog

madhippu follows [semantic versioning](https://semver.org). The JSON output
has its own version (`schema`), described in [docs/schema.md](docs/schema.md).

## 0.1.0 (unreleased)

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
