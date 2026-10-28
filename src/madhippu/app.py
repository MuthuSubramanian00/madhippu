"""A local web page for people who don't use a terminal: drop an annual
report, see its statements, download Excel or JSON.

`madhippu` with no arguments starts it and opens the browser. It uses only
the standard library's HTTP server, binds to 127.0.0.1 and serves this one
person on this one machine; nothing is uploaded anywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import tempfile
import threading
import webbrowser
from collections import OrderedDict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from madhippu import __version__, pdf
from madhippu.report import DISCLAIMER, UNIT_NAMES, Report, parse_pages, to_dict

MAX_UPLOAD_BYTES = 300 * 1024**2
KEEP_REPORTS = 5  # recent reports kept in memory for downloads and the PDF view

# Item names follow SEBI's taxonomy; these make them read like a report's lines.
_WORDS = [
    ("ProfitLoss", "Profit (loss)"), ("ProfitOrLoss", "Profit (loss)"),
    ("EarningsLoss", "Earnings (loss)"), ("FromUsedIn", "From (used in)"),
    ("IncreaseDecrease", "Increase (decrease)"), ("PaidRefund", "Paid (net of refunds)"),
    ("Noncurrent", "Non-current"), ("NonControlling", "Non-controlling"),
]
_SUFFIX = re.compile(r"ClassifiedAs(Operating|Investing|Financing)Activities$")


def label(item: str) -> str:
    """"LeaseLiabilitiesNoncurrent" -> "Lease liabilities non-current"."""
    item = _SUFFIX.sub("", item)
    for old, new in _WORDS:
        item = item.replace(old, new)
    text = re.sub(r"(?<=[a-z)])(?=[A-Z(])", " ", item)
    return text[:1] + text[1:].lower()


def _day(iso: str) -> str:
    """"2026-03-31" -> "31 Mar 2026"."""
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def view(report: Report) -> list[dict]:
    """The statements as printed: values in the page's own unit, lines in
    the page's order."""
    out = []
    for st in report.statements:
        unit = UNIT_NAMES.get(st.unit)
        out.append({
            "kind": st.kind,
            "basis": "consolidated" if st.consolidated else "standalone",
            "page": st.page,
            "unit": f"₹ {unit}" if unit else "as printed (no unit stated)",
            "period": "as at" if st.instant else "year ended",
            "columns": [_day(end) for end in st.columns],
            "rows": [{"item": item, "label": label(item), "values": values,
                      "per_share": item in st.per_share, "count": item in st.counts}
                     for item, values in st.values.items()],
            "warnings": st.warnings,
        })
    return out


class Reports:
    """The last few parsed reports, with their PDFs, by id."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[Report, bytes]] = OrderedDict()

    def add(self, report: Report, data: bytes) -> str:
        key = report.sha256[:16]
        with self._lock:
            self._items[key] = (report, data)
            self._items.move_to_end(key)
            while len(self._items) > KEEP_REPORTS:
                self._items.popitem(last=False)
        return key

    def get(self, key: str) -> tuple[Report, bytes] | None:
        with self._lock:
            return self._items.get(key)


def parse_bytes(data: bytes, filename: str) -> Report:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.pdf"
        path.write_bytes(data)
        return parse_pages(pdf.pages(path), file=filename,
                           sha256=hashlib.sha256(data).hexdigest())


def _safe_name(filename: str) -> str:
    stem = Path(filename).stem or "report"
    return re.sub(r"[^\w.-]+", "-", stem)[:80]


def make_handler(port: int, token: str, reports: Reports, stop):
    page = (resources.files("madhippu") / "app.html").read_text(encoding="utf-8")
    page = (page.replace("{{TOKEN}}", token).replace("{{VERSION}}", __version__)
                .replace("{{DISCLAIMER}}", DISCLAIMER))

    class Handler(BaseHTTPRequestHandler):
        server_version = f"madhippu/{__version__}"

        def log_message(self, format, *args):  # keep the window quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def _allowed(self) -> bool:
            # Another site open in the browser can reach 127.0.0.1 too: refuse
            # requests addressed to any other host name (DNS rebinding), and
            # POSTs without this run's token (cross-site forms).
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self._send(403, b"forbidden", "text/plain")
                return False
            if self.command == "POST" and self.headers.get("X-Madhippu-Token") != token:
                self._send(403, b"forbidden", "text/plain")
                return False
            return True

        def do_GET(self):
            if not self._allowed():
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, page.encode(), "text/html; charset=utf-8",
                           {"Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; "
                                                       "script-src 'unsafe-inline'; frame-ancestors 'none'"})
                return
            m = re.fullmatch(r"/(report)/([0-9a-f]{16})\.(xlsx|json|pdf)", path)
            found = m and reports.get(m[2])
            if not found:
                self._send(404, b"not found", "text/plain")
                return
            report, data = found
            name = _safe_name(report.file or "report")
            if m[3] == "pdf":
                self._send(200, data, "application/pdf")
                return
            if m[3] == "json":
                body = (json.dumps(to_dict(report), indent=2, ensure_ascii=False) + "\n").encode()
                content_type = "application/json"
            else:
                from madhippu import xlsx
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out.xlsx"
                    xlsx.write(report, out)
                    body = out.read_bytes()
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{name}-madhippu.{m[3]}"
            self._send(200, body, content_type, {
                "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"})

        def do_POST(self):
            if not self._allowed():
                return
            if self.path == "/quit":
                self._json(200, {"ok": True})
                threading.Thread(target=stop, daemon=True).start()
                return
            if self.path != "/parse":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_UPLOAD_BYTES:
                self._json(413, {"error": "That file is empty or larger than 300 MB."})
                return
            data = self.rfile.read(length)
            filename = self.headers.get("X-Filename") or "report.pdf"
            try:
                filename = bytes(filename, "latin-1").decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            if not data.startswith(b"%PDF"):
                self._json(400, {"error": "That doesn't look like a PDF."})
                return
            try:
                report = parse_bytes(data, Path(filename).name)
            except Exception as e:  # damaged or encrypted PDF
                self._json(400, {"error": f"Couldn't read that PDF: {e}"})
                return
            key = reports.add(report, data)
            self._json(200, {"id": key, "file": report.file, "pages": report.page_count,
                             "statements": view(report), "warnings": report.warnings})

    return Handler


def main(open_browser: bool = True, port: int = 0) -> int:
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(port, token, Reports(), server.shutdown)
    url = f"http://127.0.0.1:{port}/"
    print(f"madhippu {__version__} is running at {url}\n"
          "Your browser should open it; if not, copy that address into it.\n"
          "Close this window (or press Ctrl+C) to stop madhippu.", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("madhippu stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
