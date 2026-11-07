"""What a page's text layer is worth: whether a stored text is the layer,
and whether a layer spells words.

The stored reader of a page (`ocr_backend`) is not trusted to say what the
stored text is: a copied collection carries `paddle` on pages whose text
is the PDF's layer. The text itself is compared with the layer instead,
letter for letter in order. And a layer whose font lost its encoding spells no
words ("ACBD GFBH FBKJLBH"), which a page class cannot see: it draws
visible text like any digital page.
"""

from __future__ import annotations

import difflib
import functools
import re
from pathlib import Path

from axiomation.evidence import normalize

#: The system's word list; absent on many Linux systems, where a layer is
#: not screened for lost encoding.
WORD_LIST = Path("/usr/share/dict/words")
#: A layer of fewer words than this is too short to judge by them.
SCREEN_WORDS = 20
#: A layer of this many characters, its spaces aside, with fewer than
#: `SCREEN_WORDS` words may spell nothing too: a font that lost its encoding
#: whole spells almost no word (one paper's first page: 1,027 characters,
#: 18 words). Tesseract has the last word as on any doubted page: of the 30
#: such stored layers of the corpus it kept 27 (contents, indexes,
#: formulas) and sent 3 to OCR, each a lost encoding or a matrix drawn in
#: bracket glyphs.
SCREEN_CHARACTERS = 200
#: The share of a layer's words in the word list under which it may spell
#: none. A sound English layer holds 0.75 to 0.89 (the works' medians); a
#: lost encoding holds 0.00 to 0.02 (a front-matter line).
#: German, indexes and symbol lists also fall under it, so Tesseract has
#: the last word (`extract.py`).
SPELLING_FLOOR = 0.3
#: The similarity, letter for letter, at which Tesseract's reading of a page
#: holds its layer better than an unrelated page would: unrelated pages
#: score 0.03 to 0.19 (124 pairs of the corpus's digital pages), a lost
#: encoding scores among them (0.03 to 0.15), and the lowest sound layer the
#: word list doubted, a page of formulas, 0.20 (61 doubted pages).
TESSERACT_AGREES = 0.2
#: A word for the screen: three letters or more.
_SCREEN_WORD_RE = re.compile(r"[^\W\d_]{3,}")


def letters(text: str) -> str:
    """A text's letters and digits in order, case folded: what two
    extractors of one layer agree on, whatever spacing and symbols each
    gives a formula ("∥β− ̃β∥∞" against "k β − β ̃k∞")."""
    return "".join(char for char in normalize(text).casefold() if char.isalnum())


def similarity(text: str, layer: str) -> float:
    """How far `text` is `layer`, letter for letter in order: 1.0 the same."""
    a, b = letters(text), letters(layer)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


@functools.cache
def word_list() -> frozenset[str]:
    """The system's word list, casefolded; empty where it is absent."""
    try:
        lines = WORD_LIST.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return frozenset()
    return frozenset(line.strip().casefold() for line in lines if line.strip())


def may_spell_nothing(layer: str) -> bool:
    """Whether a layer may have lost its font's encoding: enough words to
    judge and too few of them in the word list, or too few words to judge
    in `SCREEN_CHARACTERS` characters or more. False where there is no
    word list."""
    known = word_list()
    found = _SCREEN_WORD_RE.findall(layer)
    if not known:
        return False
    if len(found) < SCREEN_WORDS:
        return len("".join(layer.split())) >= SCREEN_CHARACTERS
    share = sum(word.casefold() in known for word in found) / len(found)
    return share < SPELLING_FLOOR
