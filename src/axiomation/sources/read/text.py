"""A stored page as lines a string can be matched against.

A locator names a string the source prints, and an exact match answers it,
over text normalized the way the measurement found it has to be. Three
things stand between a stored page and a match, and each one turns a hit
into a confident miss, which is the failure this exists to prevent:

- ligatures: `Deﬁnition` is not `Definition` byte for byte, and text layers
  carry them often. NFKC folds them.
- a word broken over a line, `Theo-` then `rem`, common in a page's text.
  The hyphen and the break are folded before the match.
- whitespace: OCR pads a line with runs of spaces and non-breaking spaces.
  A run collapses to one space.

Line structure survives on purpose. A statement starts a line and a
cross-reference sits inside a sentence, and that difference is how `locate`
ranks its candidates. What a publisher prints around a label, and the full
stop after it, are the matcher's business (`locate.py`), not this module's.

`TextQuality` is the other half of a negative. A match that did not happen
over OCR text, or over pages under the text floor, is a weaker statement
than one over a text layer, and every verb reports which one it had.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from axiomation.evidence import OCR_SOURCES, ReadStatus, TextSource, normalize

if TYPE_CHECKING:
    # `Page` is an annotation-only import. `client` pulls in the Qdrant
    # client, and this module's other consumer is `fence`, which is pure and
    # which the verdict command imports: a runtime import here
    # would charge every ledger command for a corpus it never touches.
    from axiomation.evidence import Page

#: A word broken over a line: a letter, a hyphen, the break, a lowercase
#: letter. The hyphen and the break go; the two halves join.
_HYPHEN_BREAK_RE = re.compile(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[a-z])")


def join_broken_words(text: str, joint: str = "") -> str:
    """The text with each word broken over a line joined again, `joint`
    where the hyphen and the break were."""
    return _HYPHEN_BREAK_RE.sub(joint, text)


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a page: as it is shown back, and as it is matched."""

    shown: str
    text: str


def lines_of(text: str) -> list[Line]:
    """A page as lines: hyphen breaks folded, blank lines dropped.

    `shown` is the source's own line, ligatures and all, because that is what
    a reader quotes back as evidence; `text` is what a pattern runs over.
    """
    joined = join_broken_words(text)
    lines: list[Line] = []
    for raw in joined.split("\n"):
        normalized = normalize(raw)
        if normalized:
            lines.append(Line(shown=raw.strip(), text=normalized))
    return lines


#: What OCR prints before a heading: Markdown emphasis and list marks, and
#: the bullets a scan turns into. A match after nothing but these still
#: starts its line.
_LEAD_MARKUP = " \t*#_>-•·"


@dataclass(frozen=True, slots=True)
class Hit:
    """One line a pattern matched, and whether it starts the line."""

    page_index: int
    line: Line
    line_initial: bool


def scan_page(page: Page, pattern: re.Pattern[str]) -> Iterator[Hit]:
    """Every line of one page the pattern matches, in page order.

    `line_initial` is what `locate` ranks by: a statement starts a line and
    a cross-reference sits inside a sentence.
    """
    for line in lines_of(page.text):
        match = pattern.search(line.text)
        if match is None:
            continue
        lead = line.text[: match.start()].strip(_LEAD_MARKUP)
        yield Hit(page_index=page.page_index, line=line, line_initial=not lead)


@dataclass(frozen=True, slots=True)
class TextQuality:
    """What kind of text a source's pages are, so a negative says what it
    was established over.

    `under_floor` lists the page indices whose stored text is unusable
    (`read_status` `content`): a label or a claim could sit on one of them and
    no match would see it, so a miss over such a source is `content` and not
    an absence. `text_layer`, `ocr` and `unknown` count pages by where their
    text came from; `unknown` is a point that names no `ocr_backend` (one
    another tool wrote).
    """

    pages: int
    text_layer: int
    ocr: int
    failed: int
    unknown: int
    under_floor: tuple[int, ...]

    @property
    def degraded(self) -> bool:
        """Whether a miss over this text is `content` rather than `ok`."""
        return bool(self.under_floor)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "text_layer": self.text_layer,
            "ocr": self.ocr,
            "failed": self.failed,
            "unknown": self.unknown,
            "under_floor": list(self.under_floor),
        }

    def summary(self) -> str:
        """One line: `565 pages: 0 from the text layer, 565 OCR, ...`."""
        parts = [f"{self.text_layer} from the text layer", f"{self.ocr} OCR"]
        if self.failed:
            parts.append(f"{self.failed} where OCR gave up")
        if self.unknown:
            parts.append(f"{self.unknown} of unknown origin")
        floor = f"{len(self.under_floor)} under the text floor"
        if self.under_floor:
            shown = ", ".join(str(index) for index in self.under_floor[:10])
            more = "" if len(self.under_floor) <= 10 else ", ..."
            floor += f" (page index {shown}{more})"
        parts.append(floor)
        return f"{self.pages} pages: " + ", ".join(parts)


def text_quality(pages: Iterable[Page]) -> TextQuality:
    """Count a source's pages by where their text came from and how much of
    it there is."""
    text_layer = ocr = failed = unknown = total = 0
    under_floor: list[int] = []
    for page in pages:
        total += 1
        if page.text_source == TextSource.NONE:
            text_layer += 1
        elif page.text_source in OCR_SOURCES:
            ocr += 1
        elif page.text_source == TextSource.FAILED:
            failed += 1
        else:
            unknown += 1
        if page.read_status is ReadStatus.CONTENT:
            under_floor.append(page.page_index)
    return TextQuality(
        pages=total,
        text_layer=text_layer,
        ocr=ocr,
        failed=failed,
        unknown=unknown,
        under_floor=tuple(under_floor),
    )
