"""A quotation checked against the page it names.

A quotation attributes exact words to a cited work, and nothing else reads
the page for the words themselves: a reader asks whether the work supports
the sentence, which a quotation that paraphrases, drops a qualifier or
changes one word supports fine. This module runs the tiers a script can
settle.

- **Exact.** The folded quotation and the folded page (`_fold`): a word
  the page breaks over a line joined again (a compound broken at its own
  hyphen read either way; the paper's source breaks no word, so a hyphen
  at its line's end is its own), `evidence.normalize`, and each
  typographic quotation mark and apostrophe written plain, so an untouched
  quotation matches whatever marks the page or the paper prints.
- **Aligned.** A
  semi-global word alignment that consumes the whole quotation and may start
  and end anywhere on the page, charging a cost for every step. Two words are
  equal when they are once the marks a quotation may change at their ends
  are stripped (`stability.` is `stability`; `-1` is not `1`); equal words are
  free; so is a repair the table below allows, a mathematics span absorbing a
  bounded run of formula tokens, a page token with no letter in it, and an
  ellipsis the quotation itself writes. Every other step costs one. Cost zero
  is `verified`; one up to a fifth of the quotation's words is `not-shown`,
  every edit named; above that the script cannot tell a misquotation from a
  paraphrase or a wrong page, which is a reader's distinction, so it files
  nothing.

A difference is free, a repair, only when a machine read the page (its
`text_source` in `evidence.OCR_SOURCES`) and the page could not have meant
it: one letter of the paper's word read as a digit or a mark between two
letters, `b0unded` for `bounded`. Every other change OCR makes can turn one
real word into another (`he`/`be`, `modern`/`modem`, `not`/`no.`), so the
script never decides it: on a page a machine read, or one with no recorded
source, a difference with OCR's shape from the fixed table leaves the site
to a reader; on a text-layer page a different word is a different
word, and `Holder` for `Hölder` is a misquotation. The table is a policy,
not a measurement, and it grows only with a recorded case. A page whose
OCR the ingest's witness left disputed (`Page.disputed`) may lack words
the page shows, so any difference on it is left to a reader.

The pages are read through `evidence.Sources`, stamped there; what to file
is `verify.run`'s.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from axiomation.evidence import (
    NO_PAGE,
    OCR_SOURCES,
    Page,
    ReadStatus,
    Results,
    Sources,
    TextSource,
    normalize,
)
from axiomation.records import Verdict
from axiomation.sites import Site
from axiomation.sources.read.locate import LabelKind, locate
from axiomation.sources.read.text import join_broken_words
from axiomation.sources.verify import locators

#: How many pages a search may offer the tiers. The citation resolver offers
#: the same order of magnitude; a quotation needs the page that carries it,
#: and a query over the quoted words is far more specific than a sentence.
PAGES = 5

#: Display before inline, so an inline `$$ ... $$` does not pair its close with
#: the prose after it. Mirrors `reader/formats/md_latex._MATH_RE`.
_MATH_RE = re.compile(r"\$\$[^$]+\$\$|\$[^$]+\$")

_WORD_RE = re.compile(r"\s+")

#: The typographic quotation marks and apostrophes, and LaTeX's quotes,
#: each written plain: a paper typed on a keyboard and a page a publisher
#: set print the same quotation with different marks.
_PLAIN_MARKS = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201a": "'",  # ‚
        "\u201b": "'",  # ‛
        "\u02bc": "'",  # ʼ
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u201e": '"',  # „
        "\u201f": '"',  # ‟
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
    }
)
_LATEX_QUOTES_RE = re.compile(r"``|''")

#: What a quotation may change at a word's ends and still quote it: the
#: marks that close a clause or a sentence where the quoting sentence cuts
#: it (`stability.` quoted as `stability`), brackets, and quotation marks,
#: which turn from double to single when nested (folded plain first). A
#: sign, `%`, `§`, `?` and `!` carry meaning and are no such mark. An
#: apostrophe at a word's end reads as a quotation mark: a prime written
#: as one in prose (`f'`) is mathematics the paper leaves unmarked.
_QUOTING_MARKS = ".,;:()[]{}\"'"

#: Where a word broken over a line was joined: a page's `self-` then
#: `adjoint` may be one word or a compound broken at its own hyphen, and
#: the page cannot tell which (`_joined`).
_JOINT = "\x02"

#: What a dropped mathematics span leaves behind. It absorbs a bounded run of
#: the page's formula tokens, which is how a quotation carrying `$\mu$` is
#: checked against a text layer that renders the symbol rather than the
#: source's LaTeX. A NUL byte cannot occur in normalized page text.
_MATH_MARK = "\x00"

#: What an ellipsis in the quotation leaves behind. It absorbs any run of page
#: words, because an elided quotation is checked segment by segment.
_ELLIPSIS_MARK = "\x01"

#: How many page tokens one mathematics span may absorb. A formula is typeset
#: one way in the paper and shredded another in the source's text layer, so a
#: single span can stand for a handful of tokens; a run longer than this is
#: not a formula, and absorbing it would hide an omission.
ABSORB_BOUND = 3

#: The names a formula renders as, which the sentinel may absorb even though
#: they carry three letters.
_OPERATORS = frozenset(
    {
        "log",
        "exp",
        "sup",
        "inf",
        "lim",
        "max",
        "min",
        "det",
        "tr",
        "dim",
        "sin",
        "cos",
    }
)

#: Three letters in a row: a word, not a formula token.
_LETTERS_RUN = re.compile(r"[^\W\d_]{3}")

#: The ellipsis forms a quotation writes: `...`, `…`, `[...]`, `[…]`. NFKC
#: folds `…` to `...`, so the two spell the same thing by the time this runs.
_ELLIPSIS_RE = re.compile(r"\[?\s*(?:\.\.\.|…)\s*\]?")

#: A bracket that is not an ellipsis. `[the rate]` is the paper's word and not
#: the source's, so it is the reader's unless the exact tier matches.
_BRACKET_RE = re.compile(r"[\[\]]")
#: A word's line-end hyphen as a reader copies it, the break a space.
_COPIED_BREAK_RE = re.compile(r"(?<=\w)- +(?=\w)")

#: Two characters OCR reads for one another, or one it reads as a mark. The
#: letter/non-letter pairs (`l`/`1`, `O`/`0`, `S`/`5`, `B`/`8`) also fall out
#: of the general non-letter-between-letters rule and are listed here for the
#: cases where both are letters. These mark a difference as OCR-shaped, which
#: sends it to a reader; none of them is free, because each can make a real
#: word of another (`_is_repair`).
_OCR_GROUPS: tuple[frozenset[str], ...] = (
    frozenset("l1i|"),
    frozenset("ec"),
    frozenset("hb"),
    frozenset("un"),
)

#: Two glyphs OCR reads as one, or one it reads as two.
_OCR_PAIRS: tuple[tuple[str, str], ...] = (
    ("rn", "m"),
    ("cl", "d"),
    ("vv", "w"),
)

#: The values that mean a machine read the page, for the repair policy.
_OCR_VALUES = frozenset(member.value for member in OCR_SOURCES)

#: Why an OCR-shaped difference that is not a free repair is left to a reader.
_UNSETTLED = {
    "ocr": "a difference with OCR's shape that could also be a changed word",
    "unknown": "a difference with OCR's shape on a page of unknown origin",
}


@dataclass(frozen=True, slots=True)
class QuotationCheck:
    """What the script concluded about one quotation site: `verdict` None
    where it cannot settle it, the site left to a reader; `how` how the
    pages were found (`locator`, `locate` or `search`), so a wrong method is
    visible; `page_index` the page a verdict rests on; `evidence` the
    page's passage, as the judgment records it."""

    site: Site
    verdict: Verdict | None
    note: str
    how: str = ""
    page_index: int | None = None
    evidence: str = ""
    dropped: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.site.kind.value,
            "block": self.site.block,
            "key": self.site.key,
            "locator": self.site.locator,
            "file": self.site.file,
            "line": self.site.line,
            "text": self.site.text,
            "how": self.how,
            "verdict": self.verdict.value if self.verdict else None,
            "note": self.note,
            "page_index": self.page_index,
            "dropped": self.dropped,
        }


@dataclass(frozen=True, slots=True)
class _Edit:
    """One charged step, for the note.

    `after` is an omission: a page word the quotation leaves out, named by the
    quotation word it follows. A changed word names both; a quotation word the
    page lacks names only the paper's word.
    """

    after: bool
    position: int
    paper: str | None
    page: str | None


@dataclass(frozen=True, slots=True)
class _Align:
    """The cheapest alignment of a quotation against one page's words."""

    cost: int
    repairs: int
    edits: tuple[_Edit, ...]
    span: str
    start: int | None
    end: int | None
    #: Why the script cannot decide, or empty. A difference with OCR's shape
    #: that is not a free repair, on a page a machine read or one of unknown
    #: origin, may be OCR and may be a changed word; that is a reader's call.
    unsettled: str


def _cheaper(candidate: _Align, best: _Align) -> bool:
    """Ties go to the lower cost, then the earlier position."""
    return (candidate.cost, _position(candidate)) < (best.cost, _position(best))


def _position(align: _Align) -> int:
    return align.start if align.start is not None else -1


def _drop_math(value: str, marker: str = " ") -> tuple[str, int]:
    """The text with inline and display mathematics removed, and how many spans.

    A formula is typeset one way in the paper and shredded another way in the
    source's text layer, so the alignment compares the words around it and says
    how much it dropped. The tolerant tier marks a span with `_MATH_MARK` so
    the sentinel can absorb the page's formula tokens; every other caller takes
    the default space, because a query is embedded and not aligned.
    """
    spans = _MATH_RE.findall(value)
    return _MATH_RE.sub(f" {marker} ", value), len(spans)


def _fold(text: str, joint: str) -> str:
    """The text both tiers compare: each word broken over a line joined
    again (as `read.text` joins it for a reader) with `joint` where the
    hyphen and the break were, normalized, and its quotation marks and
    apostrophes written plain."""
    plain = normalize(join_broken_words(text, joint)).translate(_PLAIN_MARKS)
    return _LATEX_QUOTES_RE.sub('"', plain)


def _fold_quotation(text: str) -> str:
    """The paper's words folded: its source wraps a line where its author
    likes, never inside a word, so a hyphen at a line's end is the
    author's own and stays."""
    return _fold(text, "-")


def _fold_page(text: str) -> str:
    """A page's words folded: a hyphen at a line's end may be the
    typesetter's or the word's own, so `_JOINT` keeps both readings."""
    return _fold(text, _JOINT)


def _joined(text: str) -> tuple[str, ...]:
    """A folded text's readings: each broken word joined, and each broken
    at its own hyphen (`self-` then `adjoint`) joined with it. The
    first is what a reader is shown."""
    if _JOINT not in text:
        return (text,)
    return (text.replace(_JOINT, ""), text.replace(_JOINT, "-"))


def _same(paper: str, page: str) -> bool:
    """Whether the aligned tier holds two words equal (`_bare`), the
    page's word in either reading of a break it carries."""
    return any(_bare(paper) == _bare(form) for form in _joined(page))


def _bare(word: str) -> str:
    """The word without the marks a quotation may change at its ends
    (`_QUOTING_MARKS`), casefolded: what the aligned tier compares. A word
    of those marks alone stays as it is."""
    return (word.strip(_QUOTING_MARKS) or word).casefold()


def _words(value: str) -> list[str]:
    return [word for word in _WORD_RE.split(value.strip()) if word]


def _segments(words: Sequence[str]) -> Iterator[list[str]]:
    """The quotation's word runs, split on the ellipses it writes."""
    segment: list[str] = []
    for word in words:
        if word == _ELLIPSIS_MARK:
            yield segment
            segment = []
        else:
            segment.append(word)
    yield segment


def _origin(page: Page) -> str:
    """Where a page's text came from: `ocr`, `text` or `unknown`."""
    source = page.text_source
    if source in _OCR_VALUES:
        return "ocr"
    if source == TextSource.NONE.value:
        return "text"
    return "unknown"


def _absorbable(token: str) -> bool:
    """Whether a mathematics span may stand for this page token."""
    if token == _MATH_MARK:
        return True
    if token.casefold() in _OPERATORS:
        return True
    return not _LETTERS_RUN.search(token)


def _free_page(token: str) -> bool:
    """A page token that costs nothing to skip: a mark, a number, punctuation."""
    return not any(character.isalpha() for character in token)


def _is_repair(paper: str, page: str, origin: str) -> bool:
    """Whether a difference is free: one the page cannot have meant, on an OCR page.

    Only a digit or a mark read between two letters of a word — `b0unded` for
    `bounded`, `f1ow` for `flow`. The page's token cannot be a word, and
    restoring one letter gives the paper's, so nothing about the meaning is
    decided by calling it a repair. Every other shape OCR makes can turn one
    word into another — `he`/`be`, `cat`/`eat`, `flat`/`fiat`,
    `modern`/`modem`, `schon`/`schön`, `not`/`no.` — and whether it did is a
    reader's question, not the script's.
    """
    if origin != "ocr":
        return False
    return _unreadable_letter(paper, page)


def _unreadable_letter(paper: str, page: str) -> bool:
    """One interior letter of the paper's word read as a digit or a mark."""
    left = paper.casefold()
    right = page.casefold()
    if len(left) != len(right) or len(left) < 3:
        return False
    differing = [i for i in range(len(left)) if left[i] != right[i]]
    if len(differing) != 1:
        return False
    at = differing[0]
    if at in (0, len(left) - 1):
        return False
    read = right[at]
    return (
        left[at].isalpha()
        and not read.isalpha()
        and not read.isspace()
        and right[at - 1].isalpha()
        and right[at + 1].isalpha()
    )


def _ocr_shape(paper: str, page: str) -> bool:
    """Whether a change is one OCR plausibly makes, from the fixed table.

    Recognising the shape is all the table does. On a page a machine read, or
    one whose origin is unknown, a difference with this shape that is not a
    free repair leaves the site to a reader rather than filing a misquotation:
    it may be OCR, and it may be a changed word. On a text-layer page it is a
    changed word — `Holder` is not `Hölder`. The table is a policy and not a
    measurement; it grows only with a recorded case. `not`/`now`,
    `bounded`/`rounded` and `increasing`/`decreasing` are outside it on
    purpose: distance alone cannot tell those from a change.
    """
    left = paper.casefold()
    right = page.casefold()
    if left == right:
        return False
    if _strip_marks(left) == _strip_marks(right):
        return True
    if len(left) == len(right):
        differing = [i for i in range(len(left)) if left[i] != right[i]]
        if len(differing) == 1:
            i = differing[0]
            x, y = left[i], right[i]
            if any(x in group and y in group for group in _OCR_GROUPS):
                return True
            if x.isalpha() != y.isalpha() and not x.isspace() and not y.isspace():
                return True
    for glyph, read_as in _OCR_PAIRS:
        if _replace_one(left, glyph, read_as) == right:
            return True
        if _replace_one(right, glyph, read_as) == left:
            return True
    return False


def _replace_one(value: str, old: str, new: str) -> str | None:
    """`value` with one occurrence of `old` replaced, or `None` if absent."""
    at = value.find(old)
    if at < 0:
        return None
    return f"{value[:at]}{new}{value[at + len(old) :]}"


def _strip_marks(value: str) -> str:
    """The word without combining marks, so a diacritic reads as a difference."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _align(quote: Sequence[str], page: Sequence[str], origin: str) -> _Align:
    """The cheapest alignment consuming every quotation word, anywhere on the page.

    A semi-global word alignment. The quotation must be consumed
    whole; the page may be entered and left anywhere, and a page word the
    quotation omits costs one unless it carries no letter. A mathematics span
    absorbs a bounded run of formula tokens, and an ellipsis absorbs any run of
    page words, both free. Ties go to the lower cost, then the earlier
    position, so the same quotation always cites the same passage.
    """
    n = len(quote)
    m = len(page)
    inf = 1 << 30
    # Each cell holds (cost, start, missing, changed): the cheapest way to
    # consume `quote[:i]` ending at `page[:j]`, the page index it begins at,
    # how many quotation words it left missing, and how many it charged as a
    # change. A changed word, a missing word and an omitted page word all cost
    # one, so the last two are what prefer the weaker claim — the page carries
    # the word, changed — and the exact match that is already there.
    best: list[list[tuple[int, int, int, int]]] = [
        [(inf, 0, 0, 0)] * (m + 1) for _ in range(n + 1)
    ]
    move: list[list[tuple[str, int, int, int] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    for j in range(m + 1):
        best[0][j] = (0, j, 0, 0)
    for i in range(1, n + 1):
        token = quote[i - 1]
        for j in range(m + 1):
            if j >= 1 and token == _MATH_MARK:
                for k in range(0, min(ABSORB_BOUND, j) + 1):
                    if k and not all(_absorbable(page[t]) for t in range(j - k, j)):
                        break
                    _offer(
                        best,
                        move,
                        i,
                        j,
                        best[i - 1][j - k],
                        ("absorb", i - 1, j - k, k),
                    )
            elif j >= 1 and token == _ELLIPSIS_MARK:
                for k in range(0, j + 1):
                    _offer(
                        best, move, i, j, best[i - 1][j - k], ("elide", i - 1, j - k, k)
                    )
            elif j >= 1:
                cost, start, missing, changed = best[i - 1][j - 1]
                step = _sub_cost(token, page[j - 1], origin)
                _offer(
                    best,
                    move,
                    i,
                    j,
                    (cost + step, start, missing, changed + step),
                    ("match", i - 1, j - 1, 0),
                )
            # A quotation word the page lacks.
            cost, start, missing, changed = best[i - 1][j]
            _offer(
                best,
                move,
                i,
                j,
                (cost + 1, start, missing + 1, changed),
                ("quote", i - 1, j, 0),
            )
            # A page word the quotation leaves out.
            if j >= 1:
                cost, start, missing, changed = best[i][j - 1]
                _offer(
                    best,
                    move,
                    i,
                    j,
                    (
                        cost + (0 if _free_page(page[j - 1]) else 1),
                        start,
                        missing,
                        changed,
                    ),
                    ("page", i, j - 1, 0),
                )

    best_j = 0
    for j in range(1, m + 1):
        if best[n][j] < best[n][best_j]:
            best_j = j
    cost, start, _missing, _changed = best[n][best_j]
    return _reconstruct(quote, page, cost, start, move, n, best_j, origin)


def _offer(
    best: list[list[tuple[int, int, int, int]]],
    move: list[list[tuple[str, int, int, int] | None]],
    i: int,
    j: int,
    candidate: tuple[int, int, int, int],
    transition: tuple[str, int, int, int],
) -> None:
    """Keep the cheaper way into one cell, the earlier position on a tie."""
    if candidate < best[i][j]:
        best[i][j] = candidate
        move[i][j] = transition


def _reconstruct(
    quote: Sequence[str],
    page: Sequence[str],
    cost: int,
    start: int,
    move: Sequence[Sequence[tuple[str, int, int, int] | None]],
    i: int,
    j: int,
    origin: str,
) -> _Align:
    """Walk the chosen path back, collecting the edits and the page run."""
    repairs = 0
    unsettled = ""
    edits: list[_Edit] = []
    consumed: list[int] = []
    while i > 0:
        transition = move[i][j]
        assert transition is not None
        kind, pi, pj, k = transition
        if kind == "match":
            paper, on_page = quote[i - 1], _joined(page[j - 1])[0]
            if not _same(paper, page[j - 1]):
                if _is_repair(_bare(paper), _bare(on_page), origin):
                    repairs += 1
                else:
                    # OCR's shape is in the glyphs as printed: `no.` may be
                    # `not` read with its last letter as a mark
                    if origin != "text" and (
                        _ocr_shape(paper, on_page)
                        or _ocr_shape(_bare(paper), _bare(on_page))
                    ):
                        unsettled = _UNSETTLED[origin]
                    edits.append(_Edit(False, i, paper, on_page))
            consumed.append(j - 1)
        elif kind == "page":
            if not _free_page(page[j - 1]):
                edits.append(_Edit(True, i, None, _joined(page[j - 1])[0]))
            consumed.append(j - 1)
        elif kind == "quote":
            edits.append(_Edit(False, i, quote[i - 1], None))
        elif kind in ("absorb", "elide"):
            consumed.extend(range(pj, j))
        i, j = pi, pj
    end = max(consumed) if consumed else None
    span = _joined(" ".join(page[start : end + 1]))[0] if end is not None else ""
    return _Align(
        cost=cost,
        repairs=repairs,
        edits=tuple(edits),
        span=span,
        start=start if consumed else None,
        end=end,
        unsettled=unsettled,
    )


def _sub_cost(paper: str, page: str, origin: str) -> int:
    if _same(paper, page):
        return 0
    return 0 if _is_repair(_bare(paper), _bare(_joined(page)[0]), origin) else 1


def _tolerant_note(align: _Align, dropped: int, elisions: int) -> str:
    return (
        f"tolerant, {align.repairs} words repaired, {dropped} spans dropped, "
        f"{elisions} elisions"
    )


def _diff_note(align: _Align, dropped: int) -> str:
    parts = []
    for edit in align.edits:
        if edit.after:
            parts.append(
                f'after word {edit.position}: "{edit.page}" on the page, '
                "nothing in the paper"
            )
        elif edit.page is None:
            parts.append(
                f'word {edit.position}: "{edit.paper}" in the paper, '
                "nothing on the page"
            )
        else:
            parts.append(
                f'word {edit.position}: "{edit.paper}" in the paper, '
                f'"{edit.page}" on the page'
            )
    note = "; ".join(parts)
    if dropped:
        suffix = f"{dropped} spans dropped"
        note = f"{note}; {suffix}" if note else suffix
    return note


def check(sources: Sources, site: Site) -> QuotationCheck:
    """One quotation site against its work's pages, filing nothing."""
    try:
        results, how = _resolve(sources, site)
    except Exception:  # noqa: BLE001 - an unreachable corpus is not a verdict
        return QuotationCheck(site, None, "the corpus could not be read")
    if results.read_status in NO_PAGE:
        return QuotationCheck(
            site, None, results.detail or "no source holds the work", how
        )
    readable = [
        page
        for page in results.pages
        if page.read_status is not ReadStatus.CONTENT and page.text.strip()
    ]
    if not readable:
        # the pages are there and their text is not: an unreadable page is
        # not "the work does not say it"
        return QuotationCheck(
            site,
            None,
            "no page above the text floor; the words could not be read"
            if results.pages
            else "no page the work holds",
            how,
        )
    return _tiers(site, readable, how)


def stands_on(text: str, page: Page) -> bool:
    """Whether `text`, quoted from `page`, stands on it as a quotation site
    is verified there: exact, or aligned at no cost (a repair the table
    allows on a page a machine read, a formula span absorbed). A reader
    copying a page may keep a word's line-end hyphen and write the break
    as a space (`meta- analysis`), so that reading is tried too. False is
    no finding: the page's stored text may be what is wrong."""
    rejoined = _COPIED_BREAK_RE.sub("-\n", text)
    return any(_stands(t, page) for t in dict.fromkeys((text, rejoined)))


def _stands(text: str, page: Page) -> bool:
    quote_norm, clean_quote, quote_words, _ = _prepared(text)
    if quote_norm and any(quote_norm in t for t in _joined(_fold_page(page.text))):
        return True
    if not quote_words or _BRACKET_RE.search(clean_quote):
        return False
    if _ELLIPSIS_MARK in quote_words and any(
        len(segment) < 3 for segment in _segments(quote_words) if segment
    ):
        return False
    return _align(quote_words, _words(_fold_page(page.text)), _origin(page)).cost == 0


def _prepared(text: str) -> tuple[str, str, list[str], int]:
    """A quotation folded; then with its formula spans and elisions marked,
    its words, and how many formula spans were dropped."""
    quote_norm = _fold_quotation(text)
    clean_quote, dropped = _drop_math(quote_norm, _MATH_MARK)
    clean_quote = _ELLIPSIS_RE.sub(f" {_ELLIPSIS_MARK} ", clean_quote)
    return quote_norm, clean_quote, _words(clean_quote), dropped


def _tiers(site: Site, pages: Sequence[Page], how: str) -> QuotationCheck:
    quote_norm, clean_quote, quote_words, dropped = _prepared(site.text)
    threshold = len(quote_words) // 5

    def settled(page_index: int, verdict: Verdict, note: str, span: str):
        return QuotationCheck(
            site,
            verdict,
            note,
            how,
            page_index,
            f'{site.key} page index {page_index}: "{span}"',
            dropped,
        )

    def unsettled(why: str) -> QuotationCheck:
        return QuotationCheck(site, None, why, how, dropped=dropped)

    for page in pages:
        if quote_norm and any(quote_norm in t for t in _joined(_fold_page(page.text))):
            return settled(page.page_index, Verdict.VERIFIED, "exact", quote_norm)

    if not quote_words:
        return unsettled("the quotation holds no words to check")
    if _BRACKET_RE.search(clean_quote):
        return unsettled("an editorial bracket is the paper's word and not the work's")
    segments = [segment for segment in _segments(quote_words) if segment]
    if _ELLIPSIS_MARK in quote_words and any(len(segment) < 3 for segment in segments):
        return unsettled("an elision leaves fewer than three words to check")
    elisions = quote_words.count(_ELLIPSIS_MARK)

    best: tuple[_Align, int] | None = None
    for page in pages:
        align = _align(quote_words, _words(_fold_page(page.text)), _origin(page))
        if align.cost == 0:
            return settled(
                page.page_index,
                Verdict.VERIFIED,
                _tolerant_note(align, dropped, elisions),
                align.span,
            )
        if best is None or _cheaper(align, best[0]):
            best = (align, page.page_index)

    if best is not None and best[0].cost <= threshold:
        align, page_index = best
        if align.unsettled:
            return unsettled(align.unsettled)
        disputed = next(p.disputed for p in pages if p.page_index == page_index)
        if disputed:
            return unsettled(
                f"page index {page_index}'s OCR left {disputed} findings its "
                "witness could not settle; read the page as an image"
            )
        return settled(
            page_index, Verdict.NOT_SHOWN, _diff_note(align, dropped), align.span
        )
    return unsettled("no page carries the words")


def _resolve(sources: Sources, site: Site) -> tuple[Results, str]:
    """The pages a quotation site is checked against, and how they were
    found. A page locator is read directly. A label locator, `Theorem
    2.2.3`, runs `locate` over the work first, because the work's own
    numbering is not the index's; where it names nothing the quotation's
    words are searched instead, inline mathematics dropped before
    embedding."""
    locator = locators.parse(site.locator)
    if locator.is_page:
        return sources.pages(site.key, locator.pages[0], locator.pages[-1]), "locator"
    if locator.is_label:
        found = locate(
            sources,
            site.key,
            locator.word,
            locator.number,
            kind=LabelKind(locator.kind.value),
        )
        if found.candidates:
            first = found.candidates[0].page_index
            return sources.page(site.key, first), "locate"
    query = locators.query(locator, _drop_math(site.text)[0])
    return sources.search(query, bib_key=site.key, top_k=PAGES), "search"
