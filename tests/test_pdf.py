import madhippu
from madhippu import pdf

from .pdfs import make_letter_spaced_pdf


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


DAMAGED = ["Fin|ancial a|ss|ets 1,|35,|7|05", "Def|erred t|ax a|ss|ets 2,|003"] * 4


def test_letters_spaced_apart_flag_a_damaged_text_layer(tmp_path):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(make_letter_spaced_pdf(DAMAGED))

    texts, spaced = pdf.read(path)

    assert texts[0].splitlines()[0] == "Fin ancial a ss ets 1, 35, 7 05"
    assert spaced == [1]


def test_whole_words_are_not_flagged(tmp_path):
    path = tmp_path / "fine.pdf"
    path.write_bytes(make_letter_spaced_pdf([line.replace("|", "") for line in DAMAGED]))

    assert pdf.read(path)[1] == []


def test_report_warns_of_a_damaged_text_layer(tmp_path):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(make_letter_spaced_pdf(DAMAGED))

    report = madhippu.parse(path)

    assert report.pages_letter_spaced == [1]
    assert any("text layer is damaged on page 1" in w and "stock exchange" in w for w in report.warnings)
    assert madhippu.to_dict(report)["source"]["pages_letter_spaced"] == [1]
