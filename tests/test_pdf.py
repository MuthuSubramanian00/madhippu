from madhippu import pdf


def _symbol_font(text: str) -> str:
    return "".join(chr(0xF000 + ord(c)) if c != "\n" else c for c in text)


def test_symbol_font_text_is_decoded():
    # Older annual reports: "Consolidated Balance Sheet" comes out of the
    # text layer as private-use characters U+F043 U+F06F U+F06E ...
    page = _symbol_font("Consolidated Balance Sheet\nTotal assets 1,03,252 89,096")
    assert pdf._decode_symbol_font(page) == "Consolidated Balance Sheet\nTotal assets 1,03,252 89,096"


def test_odd_private_use_glyph_in_ordinary_text_is_left_alone():
    page = " Revenue grew 8.6%  in constant currency"  # Wingdings bullets
    assert pdf._decode_symbol_font(page) == page
