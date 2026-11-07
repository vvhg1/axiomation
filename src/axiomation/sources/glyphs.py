"""A glyph's name, and the character it names.

A font with no Unicode map still names its glyphs: the PDF's `/Encoding`
`/Differences` names a code's glyph, and an embedded font program has its
own encoding. PDFium maps a name only by Adobe's list, so TeX's names
(`angbracketleft`, `bardbl`, `summationdisplay`) come back as the font's
raw code: `h` for `⟨`, `k` for `‖`, `X` for `∑`. `pdf.Pdf.text` asks here
for the character such a name stands for, and for the names a font
program gives its codes; reading the PDF's own encodings is `pdf.py`'s.
"""

from __future__ import annotations

import io
import re

#: Where pypdf's list lacks a TeX name or maps it otherwise than TeX's own
#: glyph list (LCDF Typetools' `texglyphlist.txt`); three names of the
#: txfonts, each its character's Unicode name; and MathTime's italic Greek
#: capitals, which its math italic font (`MTMI`) names with a `1`: `Delta1`
#: is its italic Δ.
TEX_NAMES = {
    "Delta1": "\u0394",  # Δ
    "Gamma1": "\u0393",  # Γ
    "Lambda1": "\u039b",  # Λ
    "Omega1": "\u03a9",  # Ω
    "Phi1": "\u03a6",  # Φ
    "Pi1": "\u03a0",  # Π
    "Psi1": "\u03a8",  # Ψ
    "Sigma1": "\u03a3",  # Σ
    "Theta1": "\u0398",  # Θ
    "Upsilon1": "\u03a5",  # Υ
    "Xi1": "\u039e",  # Ξ
    "colonequal": "\u2254",  # ≔ COLON EQUALS
    "equalcolon": "\u2255",  # ≕ EQUALS COLON
    "followsequal": "\u2ab0",  # ⪰
    "greaterorequalslant": "\u2a7e",  # ⩾
    "greaterorsimilar": "\u2273",  # ≳
    "lessorequalslant": "\u2a7d",  # ⩽
    "lessorsimilar": "\u2272",  # ≲
    "lscript": "\u2113",  # ℓ
    "negationslash": "\u0338",  # the long solidus laid over the next sign
    "nequivalence": "\u2262",  # ≢ NOT IDENTICAL TO
    "precedesequal": "\u2aaf",  # ⪯
}

#: A control character, which no glyph draws.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
#: One entry of a Type 1 program's own encoding: `dup 104 /angbracketleft put`.
_TYPE1_ENTRY = re.compile(rb"dup\s+(\d+)\s*/([^\s/\[\]{}()<>%]+)\s+put")
#: How a Type 1 program begins: PFA text, or a PFB segment's marker.
_TYPE1_STARTS = (b"%!", b"\x80")
#: A CFF program's major version.
_CFF_START = b"\x01"


def character(name: str) -> str:
    """The character a glyph name stands for: TeX's list where pypdf's
    differs, pypdf's list (Adobe's names and TeX's), else the Adobe Glyph
    List's rules (`uni2329`, `u1D400`, `f_i`). A name's suffix after a
    period names a variant of its glyph (`summationtext.1`), as those
    rules say. Empty where none knows it: a glyph named by a number or by
    one character is not guessed at, as PDFium's raw code is that guess
    already. No glyph stands for a control character, so a name read as
    one is no name: a subset that names its glyphs by their number in
    the font (`uni00000048`, glyph 72) reads under Adobe's rules as
    U+0000 and a letter."""
    # pypdf keeps its list private; `tests/sources/test_sources_glyphs.py`
    # holds the names this module needs from it
    from fontTools import agl
    from pypdf._codecs.adobe_glyphs import adobe_glyphs

    base = name.split(".", 1)[0]
    if not base:  # `.notdef` and its kin name no character
        return ""
    found = TEX_NAMES.get(base) or adobe_glyphs.get(f"/{base}")
    if not found:
        try:
            found = agl.toUnicode(name)
        except (ValueError, KeyError):
            return ""
    return "" if _CONTROL_RE.search(found) else found


def program_names(program: bytes) -> dict[int, str]:
    """An embedded font program's own encoding, each code's glyph name:
    a Type 1 program's `dup N /name put` entries, a CFF program's custom
    encoding. Empty for a program with a standard encoding, or one of
    another kind (TrueType, OpenType) or that cannot be read."""
    if program.startswith(_TYPE1_STARTS):
        return {
            int(code): name.decode("latin-1")
            for code, name in _TYPE1_ENTRY.findall(program)
        }
    if program.startswith(_CFF_START):
        return _cff_names(program)
    return {}


def _cff_names(program: bytes) -> dict[int, str]:
    from fontTools.cffLib import CFFFontSet

    try:
        fonts = CFFFontSet()
        fonts.decompile(io.BytesIO(program), None)
        encoding = fonts[fonts.fontNames[0]].Encoding
    except Exception:  # noqa: BLE001 - a program fontTools cannot read names nothing
        return {}
    if not isinstance(encoding, list):  # "StandardEncoding", "ExpertEncoding"
        return {}
    return {code: name for code, name in enumerate(encoding) if name != ".notdef"}
