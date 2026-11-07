"""Where a source prints a label.

A locator names a string the source prints: `Theorem 2.2.3`, `\\S4.6`,
`Chapter 7`. A search for its meaning cannot separate `2.2.3` from `2.2.30`,
and neither can the sparse vectors, whose tokenizer splits the number into
digits. A string match with a digit guard does, exactly. This is that match,
over `grep`'s line scanner, with the pattern written for the agent, because
the pattern is where the silent misses live:

- publishers print a label three ways: `Theorem 2.2.3` (Dembo), `6.4.11.
  Corollary.` (Bogachev), `3.2.12` alone (Federer). The three forms are
  tried in order and the one that answered is reported. A fourth exists and
  is not built: an equation prints as `(2.2.6)`, parenthesized and wordless,
  at the end of its line; the bare number form catches it, and the ranking
  does not know it.
- the number must end where it ends. `2.2.3` is not `2.2.30` and not
  `2.2.3.1`, and `Theorem 1.1.` (Macci) is the statement with its full stop.
  The guard rejects a following digit and a following dotted digit, not a
  following dot: `(?![0-9.])` would miss every label printed with its full
  stop.
- a statement starts a line and a cross-reference sits inside a sentence, so
  a line-initial match ranks first, then by page. `Theorem 2.2.3` is printed
  on seven pages of one book, and the ranking puts the page that states
  it first.

A section or a chapter goes to the PDF's outline first (`structure.py`),
because the outline names it once and the bare number is printed on every
page that refers to it; the string is the fallback.

Nothing is embedded, so `locate` needs no Voyage key.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from axiomation.evidence import Page, ReadStatus, Sources
from axiomation.sources.read import zotero
from axiomation.sources.read.render import pdf_path_for
from axiomation.sources.read.structure import outline_of
from axiomation.sources.read.text import TextQuality, scan_page, text_quality

#: How many candidates come back. A label on thirty pages is a
#: cross-reference storm, and the ranking has already decided which page is
#: the statement.
CANDIDATE_CAP = 10


class LabelKind(StrEnum):
    """What kind of label is being looked up. The values are
    `agents.locators.LocatorKind`'s, so a locator converts by value."""

    RESULT = "result"
    SECTION = "section"
    CHAPTER = "chapter"
    EQUATION = "equation"


#: The kinds the outline answers before the string does.
STRUCTURAL = frozenset({LabelKind.SECTION, LabelKind.CHAPTER})

#: What a publisher may print before a number in an outline entry.
_OUTLINE_WORDS = r"chapter|section|part|appendix|§"


class LabelForm(StrEnum):
    """Which spelling answered."""

    WORD_NUMBER = "word-number"
    NUMBER_WORD = "number-word"
    NUMBER = "number"
    OUTLINE = "outline"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One page that prints the label, and the line it prints it on."""

    page_index: int
    line: str
    line_initial: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "line": self.line,
            "line_initial": self.line_initial,
        }


@dataclass(frozen=True, slots=True)
class Located:
    """Where one source prints one label, ranked, or why nothing was found."""

    bib_key: str
    kind: LabelKind
    word: str
    number: str
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    form: LabelForm | None = None
    candidates: tuple[Candidate, ...] = ()
    dropped: int = 0
    #: How many entries the PDF's outline has, when one was read; None when
    #: the label is not structural or the PDF was not there.
    outline_entries: int | None = None
    quality: TextQuality | None = None

    @property
    def label(self) -> str:
        return f"{self.word} {self.number}"

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "kind": self.kind.value,
            "label": self.label,
            "word": self.word,
            "number": self.number,
            "read_status": self.read_status.value,
            "detail": self.detail,
            "form": self.form.value if self.form else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "dropped": self.dropped,
            "outline_entries": self.outline_entries,
            "quality": self.quality.as_dict() if self.quality else None,
        }


def locate(
    reader: Sources,
    bib_key: str,
    word: str,
    number: str,
    *,
    kind: LabelKind,
    cap: int = CANDIDATE_CAP,
    zotero_url: str = zotero.DEFAULT_ZOTERO_URL,
) -> Located:
    """Find where one source prints `word number`. Never raises for a source
    that cannot be read: a key no source holds is `absent`, an outage
    `infrastructure`, and a miss over a source with a page under the text
    floor is `content`, because the label could sit on that page unseen."""
    results = reader.source_pages(bib_key)
    if not results.pages:
        return Located(
            bib_key,
            kind,
            word,
            number,
            read_status=results.read_status,
            detail=results.detail,
        )
    pages = results.pages
    quality = text_quality(pages)
    entries: int | None = None
    form: LabelForm | None = None
    found: list[Candidate] = []
    if kind in STRUCTURAL:
        entries, found = _from_outline(pages[0], word, number, zotero_url)
        if found:
            form = LabelForm.OUTLINE
    if not found:
        form, found = _from_text(pages, forms(word, number))
    ranked = sorted(found, key=lambda item: (not item.line_initial, item.page_index))
    kept = tuple(ranked[:cap])
    status, detail = (
        (
            (results.read_status, results.detail)
            if results.read_status is ReadStatus.TRANSIENT
            else (ReadStatus.OK, "")
        )
        if kept
        else _miss(bib_key, word, number, len(pages), quality, entries, kind)
    )
    return Located(
        bib_key=bib_key,
        kind=kind,
        word=word,
        number=number,
        read_status=status,
        detail=detail,
        form=form if kept else None,
        candidates=kept,
        dropped=len(ranked) - len(kept),
        outline_entries=entries,
        quality=quality,
    )


#: The places a label locator's citation names at most.
PLACES = 3


def cited_pages(reader: Sources, bib_key: str, locator: str | None) -> tuple[int, ...]:
    """The pages a citation's locator names in the work, at no cost: a
    page locator's own, a label's first places as the work prints it (the
    verifiers' route); none for a citation with no locator, since a search
    would embed its query."""
    from axiomation.sources.verify import locators

    found = locators.parse(locator)
    if found.is_page:
        return found.pages
    if not found.is_label:
        return ()
    located = locate(
        reader, bib_key, found.word, found.number, kind=LabelKind(found.kind.value)
    )
    return tuple(c.page_index for c in located.candidates[:PLACES])


def guard(number: str) -> str:
    """What may not follow the number: another digit, or a dot and a digit.
    A dot alone is the full stop after a label and is allowed. A number that
    ends in a letter (`IX`, `A`) may not run on into a word."""
    if number[-1].isdigit():
        return r"(?!\d)(?!\.\d)"
    return r"(?![A-Za-z])"


def forms(word: str, number: str) -> list[tuple[LabelForm, re.Pattern[str]]]:
    """The three spellings, in the order they are tried.

    The bare number is tried only when the number has a dot in it: a bare `3`
    is printed on every page of every book.
    """
    escaped_word, escaped_number, tail = (
        re.escape(word),
        re.escape(number),
        guard(number),
    )
    spellings = [
        (
            LabelForm.WORD_NUMBER,
            re.compile(rf"\b{escaped_word}\.?\s*{escaped_number}{tail}", re.IGNORECASE),
        ),
        (
            LabelForm.NUMBER_WORD,
            re.compile(
                rf"(?<![\w.]){escaped_number}\.?\s+{escaped_word}\b", re.IGNORECASE
            ),
        ),
    ]
    if "." in number:
        spellings.append(
            (LabelForm.NUMBER, re.compile(rf"(?<![\w.]){escaped_number}{tail}"))
        )
    return spellings


def _from_text(
    pages: Sequence[Page], spellings: Sequence[tuple[LabelForm, re.Pattern[str]]]
) -> tuple[LabelForm | None, list[Candidate]]:
    """The first spelling that matches anywhere answers, one candidate per
    page: the line-initial match when the page has one, else its first."""
    for form, pattern in spellings:
        found: list[Candidate] = []
        for page in pages:
            best: Candidate | None = None
            for hit in scan_page(page, pattern):
                candidate = Candidate(page.page_index, hit.line.shown, hit.line_initial)
                if hit.line_initial:
                    best = candidate
                    break
                best = best or candidate
            if best is not None:
                found.append(best)
        if found:
            return form, found
    return None, []


def _from_outline(
    lead: Page, word: str, number: str, zotero_url: str
) -> tuple[int | None, list[Candidate]]:
    """The outline entries that open with the number, alone or after the
    publisher's own word.

    `4.6 Large Deviations for Projective Limits`, `Chapter 7`, `7. Title` all
    name their section; `4.6.1` does not name `4.6`. The word is the
    publisher's and not the paper's: `\\S2` in a paper is `Chapter 2` in the
    outline of a book with no sections above its chapters.
    """
    path = pdf_path_for(lead, zotero_url)
    if path is None:
        return None, []
    try:
        entries = outline_of(path)
    except Exception:  # noqa: BLE001 - a broken PDF: the string answers instead
        return None, []
    pattern = re.compile(
        rf"^(?:(?:{re.escape(word)}|{_OUTLINE_WORDS})\s+)?"
        rf"{re.escape(number)}{guard(number)}",
        re.IGNORECASE,
    )
    return len(entries), [
        Candidate(entry.page_index, entry.title, True)
        for entry in entries
        if pattern.match(entry.title)
    ]


def _miss(
    bib_key: str,
    word: str,
    number: str,
    pages: int,
    quality: TextQuality,
    entries: int | None,
    kind: LabelKind,
) -> tuple[ReadStatus, str]:
    tried = len(forms(word, number))
    detail = (
        f"no page of {bib_key} prints {word} {number!s} in any of the {tried} "
        f"spellings tried, over {pages} pages"
    )
    if kind in STRUCTURAL:
        if entries:
            detail += (
                f"; the PDF's outline has {entries} entries and none opens with it"
            )
        elif entries == 0:
            detail += "; the PDF carries no outline"
        else:
            detail += "; the PDF was not there to read an outline from"
    if quality.degraded:
        shown = ", ".join(str(index) for index in quality.under_floor[:10])
        return ReadStatus.CONTENT, (
            f"{detail}; {len(quality.under_floor)} page(s) are under the text "
            f"floor and could carry it unseen: page index {shown}. Render them "
            f"before calling this an absence."
        )
    return ReadStatus.OK, detail
