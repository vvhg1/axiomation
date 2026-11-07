"""What a citation's locator points at.

`\\autocite[Theorem~4.2.1]{brown1998}` names a result, `[p.~130]` names a
page and `[\\S3]` names a section, and the three want different reads. A page
is a direct lookup with no embedding cost; the other two are a search inside
the key, because a book's own numbering is not the index's.

Parsing is deliberately shallow. The locator is a hint about where to look,
never evidence about what the source says, so an unrecognised one costs a
search rather than a failure: `UNPARSED` still carries the text, and the
citing sentence is still the query.

The full resolver chain — DOI, Zotero, the open web — is `resolvers`. This is the
half that reads the corpus the paper already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Result words as they are abbreviated in practice, mapped to the word a
#: search query should carry. `thm`, `Thm.`, `Theorem` are one locator.
RESULT_WORDS: dict[str, str] = {
    "thm": "theorem",
    "theorem": "theorem",
    "lem": "lemma",
    "lemma": "lemma",
    "prop": "proposition",
    "proposition": "proposition",
    "cor": "corollary",
    "corollary": "corollary",
    "def": "definition",
    "defn": "definition",
    "definition": "definition",
    "rem": "remark",
    "remark": "remark",
    "ex": "example",
    "example": "example",
    "exercise": "exercise",
    "fact": "fact",
    "claim": "claim",
    "assumption": "assumption",
    "condition": "condition",
}

SECTION_WORDS = frozenset({"sec", "section", "s", "subsection"})
CHAPTER_WORDS = frozenset({"ch", "chap", "chapter"})
PAGE_WORDS = frozenset({"p", "pp", "page", "pages"})
EQUATION_WORDS = frozenset({"eq", "eqn", "equation", "formula"})

#: A number as a locator writes it: `4`, `4.2.1`, `A.3`, `IV`.
_NUMBER = r"[0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*|[IVXLC]+"

_WORD_RE = re.compile(
    r"^\s*(?:\\S\s*|\u00a7\s*)?"  # a leading section sign counts as the word
    r"(?P<word>[A-Za-z]+)?\.?\s*~?\s*"
    r"(?P<number>" + _NUMBER + r")"
    r"(?:\s*[-\u2013]+\s*(?P<last>" + _NUMBER + r"))?\s*$"
)

_SECTION_SIGN_RE = re.compile(r"^\s*(?:\\S|\u00a7)")

#: LaTeX that carries no meaning for a search query.
_MARKUP_RE = re.compile(
    r"\\(?:autocite|textcite|parencite|cite)[a-zA-Z]*(?:\[[^\]]*\])*\{[^}]*\}"
    r"|\\(?:ref|autoref|label|eqref)\{[^}]*\}"
    r"|\\[a-zA-Z]+\s*"
    r"|[{}$~^_\\]"
)
_SPACE_RE = re.compile(r"\s+")

#: How much of a citing sentence is worth embedding. A query longer than this
#: is a paragraph, and a paragraph retrieves the paper rather than the page.
QUERY_LIMIT = 400


class LocatorKind(StrEnum):
    """What the bracket argument of a citation names."""

    PAGE = "page"
    RESULT = "result"
    SECTION = "section"
    CHAPTER = "chapter"
    EQUATION = "equation"
    #: No bracket argument at all. A book cited this way is the
    #: `book-cite-without-locator` rule's complaint, not this module's.
    NONE = "none"
    #: There is one and it is not a place: "see also", "cf.", a page range in
    #: a form nobody else writes. The sentence is still the query.
    UNPARSED = "unparsed"


#: The kinds `locate` can look up: each names a word and a number the source
#: prints. Their values are `sources.read.locate.LabelKind`'s, so a locator's
#: kind converts by value and neither module imports the other.
LABEL_KINDS = frozenset(
    {
        LocatorKind.RESULT,
        LocatorKind.SECTION,
        LocatorKind.CHAPTER,
        LocatorKind.EQUATION,
    }
)


@dataclass(frozen=True, slots=True)
class Locator:
    """One parsed bracket argument, and what a reader should do with it."""

    kind: LocatorKind
    text: str = ""
    word: str = ""
    number: str = ""
    last: str = ""
    pages: tuple[int, ...] = ()

    @property
    def is_page(self) -> bool:
        """True when the locator names pages the reader can open directly."""
        return self.kind is LocatorKind.PAGE and bool(self.pages)

    @property
    def is_label(self) -> bool:
        """True when the locator names a string the source prints.

        A result, a section, a chapter or an equation is a label, and
        `locate` matches it inside the source's own text. A page is an
        index and is read directly; nothing else is a place at all.
        """
        return self.kind in LABEL_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "word": self.word,
            "number": self.number,
            "last": self.last,
            "pages": list(self.pages),
        }


def parse(text: str | None) -> Locator:
    """Read a citation's bracket argument.

    biblatex's own default — a bare number is a page — is honoured, because
    `\\autocite[130]{brown1998}` prints "p. 130" and means it.
    """
    raw = (text or "").strip()
    if not raw:
        return Locator(kind=LocatorKind.NONE)
    match = _WORD_RE.match(raw)
    if match is None:
        return Locator(kind=LocatorKind.UNPARSED, text=raw)

    number = match.group("number")
    last = match.group("last") or ""
    word = (match.group("word") or "").lower().rstrip(".")
    if not word and _SECTION_SIGN_RE.match(raw):
        word = "section"

    if not word or word in PAGE_WORDS:
        return Locator(
            kind=LocatorKind.PAGE,
            text=raw,
            word=word or "page",
            number=number,
            last=last,
            pages=_page_range(number, last),
        )
    if word in SECTION_WORDS:
        return Locator(
            kind=LocatorKind.SECTION, text=raw, word="section", number=number, last=last
        )
    if word in CHAPTER_WORDS:
        return Locator(
            kind=LocatorKind.CHAPTER, text=raw, word="chapter", number=number, last=last
        )
    if word in EQUATION_WORDS:
        return Locator(
            kind=LocatorKind.EQUATION,
            text=raw,
            word="equation",
            number=number,
            last=last,
        )
    if word in RESULT_WORDS:
        return Locator(
            kind=LocatorKind.RESULT,
            text=raw,
            word=RESULT_WORDS[word],
            number=number,
            last=last,
        )
    return Locator(kind=LocatorKind.UNPARSED, text=raw)


def _page_range(number: str, last: str) -> tuple[int, ...]:
    """The pages a page locator names, or nothing when they are not numbers.

    A page printed as `xii` or `A.3` is a real page and not one this can turn
    into an index lookup, so it falls back to a search like any other locator.
    """
    if not number.isdigit():
        return ()
    first = int(number)
    if not last:
        return (first,)
    if not last.isdigit() or int(last) < first:
        return (first,)
    # A range nobody meant — a scan of a whole chapter — is not a read.
    return tuple(range(first, min(int(last), first + MAX_PAGES - 1) + 1))


#: How many pages a range locator may open. Beyond this the citation is
#: pointing at a chapter, and the search is the better read.
MAX_PAGES = 8


def query(locator: Locator, sentence: str) -> str:
    """What to search the source for: the claimed content, positive-first.

    The citing sentence is the query, because searching for the content finds
    the page whether or not the paper's theorem number is right — and when it
    lands somewhere other than the locator, that discrepancy is the finding.
    Searching for "Theorem 4.2.1" instead can only ever confirm the number.

    The locator joins the query rather than replacing it: a source that
    numbers its results usually prints the number on the page that carries
    them, so it is a useful term and a useless whole query.
    """
    content = plain(sentence)
    named = f"{locator.word} {locator.number}".strip()
    if locator.kind in (LocatorKind.RESULT, LocatorKind.EQUATION) and named:
        return f"{named} {content}".strip()[:QUERY_LIMIT] if content else named
    return content[:QUERY_LIMIT]


def plain(text: str) -> str:
    """A sentence with its LaTeX taken out, for embedding."""
    return _SPACE_RE.sub(" ", _MARKUP_RE.sub(" ", text or "")).strip()


def why_not_a_label(
    locator: Locator,
    page_read: str = "`sources page {key} {index}`",
    key: str = "<key>",
) -> str:
    """The sentence `sources locate`, or a session's `cited_locate`, refuses
    a non-label locator with; `page_read`, how its reader reads a page."""
    if locator.kind is LocatorKind.PAGE:
        read = page_read.format(key=key, index=locator.number)
        return f"{locator.text!r} names a page index, not a label; {read} reads it."
    if locator.kind is LocatorKind.NONE:
        return "no locator given; a label is a word and a number, `Theorem 2.2.3`."
    return (
        f"cannot read {locator.text!r} as a label: a word and a number, "
        f"`Theorem 2.2.3`, `\\S4.6` or `Chapter 7`."
    )
