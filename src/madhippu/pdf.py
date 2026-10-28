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


# A damaged text layer can space a word's letters apart ("Financial a ss
# ets", "1, 35, 7 05"): PDFium inserts a space wherever the letters are
# drawn apart, though the gap is far narrower than the page's word spaces. A
# page with this many such spaces between letters or digits is flagged.
LETTER_SPACED_MIN = 20
LETTER_GAP = 0.10  # of the font's height; word spaces are about twice that


def _letter_spaced(textpage, text: str) -> bool:
    found = 0
    for i, c in enumerate(text):
        if c != " " or i == 0 or i + 1 >= len(text) or not (text[i - 1].isalnum() and text[i + 1].isalnum()):
            continue
        if not pdfium.raw.FPDFText_IsGenerated(textpage, i):
            continue  # a space written in the file
        a, b = pdfium.raw.FS_RECTF(), pdfium.raw.FS_RECTF()
        pdfium.raw.FPDFText_GetLooseCharBox(textpage, i - 1, a)
        pdfium.raw.FPDFText_GetLooseCharBox(textpage, i + 1, b)
        height = max(a.top - a.bottom, b.top - b.bottom)
        if height > 0 and 0 <= (b.left - a.right) / height < LETTER_GAP:
            found += 1
            if found >= LETTER_SPACED_MIN:
                return True
    return False


def read(path: Path) -> tuple[list[str], list[int]]:
    """The text of each page, in order, and the (1-based) pages whose text
    layer spaces letters apart."""
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            out, spaced = [], []
            for index in range(len(pdf)):
                page = pdf[index]
                textpage = page.get_textpage()
                raw_text = textpage.get_text_range()
                if _letter_spaced(textpage, raw_text):
                    spaced.append(index + 1)
                text = _decode_symbol_font(raw_text)
                out.append(text.replace("\r\n", "\n").replace("\f", " "))
                textpage.close()
                page.close()
            return out, spaced
        finally:
            pdf.close()


def pages(path: Path) -> list[str]:
    """The text of each page, in order."""
    return read(path)[0]


def has_text(page: str) -> bool:
    return len(page.strip()) >= MIN_TEXT_CHARS
