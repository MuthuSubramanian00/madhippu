"""Read a PDF's text layer, page by page, with PDFium.

No OCR: a scanned page has no text layer and comes back (nearly) empty.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pypdfium2 as pdfium

# A page with fewer extracted characters than this is treated as having no
# text layer (a scan, or an image-only page such as a cover).
MIN_TEXT_CHARS = 40

# PDFium is not thread-safe.
_PDFIUM_LOCK = threading.Lock()

# Symbol-encoded fonts (common in older annual reports) put each character
# at U+F000 plus its ASCII code, in Unicode's private use area: "Con" comes
# out as "". Pages written mostly that way are decoded;
# pages with only the odd private-use glyph (a Wingdings bullet) are left.
_SYMBOL_FONT = {cp: cp - 0xF000 for cp in range(0xF020, 0xF07F)}


def _decode_symbol_font(text: str) -> str:
    visible = [c for c in text if not c.isspace()]
    encoded = sum(1 for c in visible if ord(c) in _SYMBOL_FONT)
    if visible and encoded / len(visible) > 0.5:
        return text.translate(_SYMBOL_FONT)
    return text


def pages(path: Path) -> list[str]:
    """The text of each page, in order."""
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            out = []
            for index in range(len(pdf)):
                page = pdf[index]
                textpage = page.get_textpage()
                text = _decode_symbol_font(textpage.get_text_range())
                out.append(text.replace("\r\n", "\n").replace("\f", " "))
                textpage.close()
                page.close()
            return out
        finally:
            pdf.close()


def has_text(page: str) -> bool:
    return len(page.strip()) >= MIN_TEXT_CHARS
