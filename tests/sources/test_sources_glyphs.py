"""A glyph's name and its character (`glyphs.py`), and `pdf.Pdf.text`
reading a font with no Unicode map by its glyph names.

PDFium maps a glyph name only by Adobe's list, so a TeX font's
`angbracketleft` comes back as the font's code, `h`. The page here draws in
a symbol font no reader knows, its names given by the PDF's `/Encoding` or
by the embedded program's own encoding, as TeX's fonts give them.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from axiomation.sources import glyphs, pdf
from sources.pdfs import write_clashing_fonts_pdf, write_named_glyphs_pdf

# --- A name's character ---


@pytest.mark.parametrize(
    ("name", "character"),
    [
        ("angbracketleft", "⟨"),
        ("bardbl", "‖"),
        ("summationdisplay", "∑"),
        ("integraldisplay", "∫"),
        ("prime", "′"),
        ("mapsto", "↦"),
        # TeX's own list where pypdf's lacks the name or reads it otherwise
        ("lscript", "ℓ"),
        ("greaterorequalslant", "⩾"),
        ("followsequal", "⪰"),
        ("negationslash", "\u0338"),
        # the txfonts' names, each its character's Unicode name
        ("colonequal", "≔"),
        ("nequivalence", "≢"),
        # MathTime's italic Greek capitals
        ("Delta1", "\u0394"),
        ("Omega1", "\u03a9"),
        # a variant's suffix, as Adobe's rules drop it
        ("summationtext.1", "∑"),
        ("a.sc", "a"),
        # Adobe's rules for a name no list holds
        ("uni27E8", "⟨"),
        ("f_i", "fi"),
    ],
)
def test_a_glyph_name_gives_its_character(name: str, character: str) -> None:
    assert glyphs.character(name) == character


@pytest.mark.parametrize("name", ["101", "(", "H9258", "", ".notdef", "uni00000048"])
def test_a_name_no_list_knows_gives_nothing(name: str) -> None:
    """A number or a single character is not guessed at: PDFium's raw
    code, which stays, is that guess already."""
    assert glyphs.character(name) == ""


# --- A font program's own encoding ---

TYPE1_HEADER = b"""%!PS-AdobeFont-1.0: TestSymbols 001.000
/FontName /TestSymbols def
/Encoding 256 array
0 1 255 {1 index exch /.notdef put} for
dup 104 /angbracketleft put
dup 105 /angbracketright put
readonly def
currentdict end
currentfile eexec
"""


def cff_program(encoding: dict[int, str]) -> bytes:
    """A CFF font program whose own encoding is `encoding`, each glyph a
    box."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    names = [".notdef", *sorted(set(encoding.values()))]
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({})
    pen = T2CharStringPen(500, None)
    pen.moveTo((0, 0))
    pen.lineTo((400, 0))
    pen.lineTo((400, 600))
    pen.closePath()
    box = pen.getCharString()
    builder.setupCFF("TestSymbols", {}, dict.fromkeys(names, box), {})
    cff = builder.font["CFF "].cff
    table = [".notdef"] * 256
    for code, name in encoding.items():
        table[code] = name
    cff.topDictIndex[0].Encoding = table
    buffer = io.BytesIO()
    cff.compile(buffer, builder.font)
    return buffer.getvalue()


def test_a_type1_programs_encoding_names_its_codes() -> None:
    assert glyphs.program_names(TYPE1_HEADER) == {
        104: "angbracketleft",
        105: "angbracketright",
    }
    assert glyphs.program_names(b"\x80\x01" + TYPE1_HEADER)[104] == "angbracketleft"


def test_a_cff_programs_encoding_names_its_codes() -> None:
    program = cff_program({104: "angbracketleft", 120: "x"})

    assert glyphs.program_names(program) == {104: "angbracketleft", 120: "x"}


@pytest.mark.parametrize(
    "program", [b"", b"\x00\x01\x00\x00truetype", b"\x01\x00\x04 broken cff"]
)
def test_a_program_of_another_kind_names_nothing(program: bytes) -> None:
    assert glyphs.program_names(program) == {}


# --- A page read by its glyph names ---


def test_the_pdfs_encoding_names_what_pdfium_cannot_map(tmp_path: Path) -> None:
    path = write_named_glyphs_pdf(
        tmp_path / "named.pdf",
        "hxik",
        differences={104: "angbracketleft", 105: "angbracketright", 107: "bardbl"},
    )

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "plain ⟨x⟩‖"


def test_the_embedded_programs_encoding_names_them_too(tmp_path: Path) -> None:
    program = cff_program({104: "angbracketleft", 120: "x"})
    path = write_named_glyphs_pdf(tmp_path / "program.pdf", "hx", program=program)

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "plain ⟨x"


def test_a_name_no_list_knows_leaves_pdfiums_code(tmp_path: Path) -> None:
    path = write_named_glyphs_pdf(
        tmp_path / "unknown.pdf", "hx", differences={104: "H9258"}
    )

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "plain hx"


def test_fonts_that_share_a_name_are_told_apart_by_drawing_order(
    tmp_path: Path,
) -> None:
    """Two subsets of one font, each with its own codes: the name cannot
    tell which drew a character, the order of drawing can."""
    path = write_clashing_fonts_pdf(
        tmp_path / "shared.pdf",
        [
            ("hi", {104: "angbracketleft", 105: "angbracketright"}),
            ("hi", {104: "bardbl", 105: "summationdisplay"}),
        ],
    )

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "⟨⟩‖∑"


@pytest.mark.parametrize("in_form", [False, True])
def test_type3_fonts_with_no_name_are_told_apart_by_drawing_order(
    tmp_path: Path, in_form: bool
) -> None:
    """A plot's fonts have no name and number their glyphs alike, and a
    plot is drawn as a form."""
    path = write_clashing_fonts_pdf(
        tmp_path / "plot.pdf",
        [
            ("\x01\x02", {1: "angbracketleft", 2: "zero"}),
            ("\x01\x02", {1: "bardbl", 2: "one"}),
        ],
        type3=True,
        in_form=in_form,
    )

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "⟨0‖1"


def test_a_name_tells_its_font_where_one_font_has_it() -> None:
    """pypdf writes `ABCDEF+CMSY10`; PDFium may keep the tag without its
    `+`. A name two fonts share, or none, leaves the font to the order of
    drawing."""
    page = pdf._PageEncodings(
        fonts={"CMSY10": {104: "angbracketleft"}}, clashed=frozenset({"DejaVuSans"})
    )

    assert page.table("CMSY10") == {104: "angbracketleft"}
    assert page.table("VyyjynQwxvjlYjbwrcCMSY10") == {104: "angbracketleft"}
    assert page.table("CMMI10") == {}
    assert page.table("QDV+DejaVuSans") is None
    assert page.table("") is None


def test_a_named_character_finds_its_place_past_a_character_of_two_units() -> None:
    """PDFium gives a math italic `𝒂` as its two surrogate halves, the
    bounded text as one character; the characters after it keep their
    places, and a line break the bounded text leaves out is skipped."""
    italic = [0xD835, 0xDC82]
    units = [ord("x"), *italic, ord("\r"), ord("\n"), ord("h"), ord("y")]

    placed = pdf._placed(units, "x𝒂hy", {5: "⟨"})

    assert placed == "x𝒂⟨y"


def test_a_line_up_that_ends_short_places_no_name() -> None:
    """Where the bounded text holds a character the text page lacks, the
    line-up goes astray, and a name could land on a later `h`."""
    units = [ord("h"), ord("a"), ord("h")]

    assert pdf._placed(units, "hXah", {0: "⟨"}) == "hXah"
