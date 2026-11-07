"""Does the result a citation's locator names stand in the work?

A locator says where in a work a claim lives: `Theorem~2.2.3`, `\\S4.6`,
`p.~130`. A locator that names a theorem the work does not print is worse
than none, because it looks checked: the reader who trusts the number never
opens the page. This module runs `locate` over the citations that carry a
label locator.

- **Found:** the work prints the label. Reported; nothing filed: a label printed
  somewhere is not the work saying the claim, and as a `cited` judgment it would
  establish the claim, false support.
- **Not printed:** a clean miss over a readable library work of at least
  `NEGATIVE_PAGE_FLOOR` pages, no page of it without text. `verify.run`
  files a finding. It is no proof that the complete publication lacks the
  result, nor a judgment of the citing sentence.
- **Inconclusive:** a work below the floor, a page with no text, a copy
  that is not the version of record, an outage. Reported; nothing filed.
- **Not checked:** a page, an equation, an unparsed locator, a range.

A page under the text floor is read with care, because almost every book
has a short page, a half-title or a blank back page, and `locate` calls the
whole work `content` for any one of them. This verifier does not: a page
whose text was read is evidence about that text, however short; only a page
with no text at all blocks a miss, as it could carry the label unseen.

It reads the work's stored pages and embeds nothing: a run charges nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from axiomation.evidence import NO_PAGE, Page, ReadStatus, Results, Sources, normalize
from axiomation.sites import Site
from axiomation.sources.read.locate import LabelKind, Located, locate
from axiomation.sources.verify import locators
from axiomation.sources.verify.locators import Locator, LocatorKind

#: A negative is trusted only over a work long enough that a missing label
#: is more likely absent than outside a short excerpt. Ten is a
#: conservative initial policy, not a measured completeness boundary; it
#: moves only with recorded evidence.
NEGATIVE_PAGE_FLOOR = 10


class Outcome(StrEnum):
    FOUND = "found"
    NOT_PRINTED = "not-printed"
    INCONCLUSIVE = "inconclusive"
    NOT_CHECKED = "not-checked"


@dataclass(frozen=True, slots=True)
class LocatorCheck:
    """What the script concluded about one locator site: `page_index` and
    `line` where it found the label; `pages` how many it searched."""

    site: Site
    outcome: Outcome
    note: str
    page_index: int | None = None
    line: str = ""
    pages: int = 0

    @property
    def label(self) -> str:
        parsed = locators.parse(self.site.locator)
        return f"{parsed.word} {parsed.number}".strip()

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.site.kind.value,
            "block": self.site.block,
            "key": self.site.key,
            "locator": self.site.locator,
            "file": self.site.file,
            "line": self.site.line,
            "outcome": self.outcome.value,
            "note": self.note,
            "page_index": self.page_index,
            "pages": self.pages,
        }


class PagesOnce:
    """`Sources` that read each work's pages once, however often asked: a
    run meets one book at many sites (a paper may cite a 400-page book
    from several), and the corpus does not change within one run."""

    def __init__(self, sources: Sources) -> None:
        self.sources = sources
        self._held: dict[str, Results] = {}

    def source_pages(self, bib_key: str) -> Results:
        if bib_key not in self._held:
            self._held[bib_key] = self.sources.source_pages(bib_key)
        return self._held[bib_key]


def check(reader: PagesOnce, site: Site) -> LocatorCheck:
    """One locator site against its work's pages, filing nothing."""
    locator = locators.parse(site.locator)
    unsupported = _unsupported(locator)
    if unsupported is not None:
        return LocatorCheck(site, Outcome.NOT_CHECKED, unsupported)

    def inconclusive(why: str) -> LocatorCheck:
        return LocatorCheck(site, Outcome.INCONCLUSIVE, why)

    try:
        results = reader.source_pages(site.key)
    except Exception:  # noqa: BLE001 - an unreachable corpus is an outage, not an absence
        return inconclusive("the corpus could not be read")
    if results.read_status in NO_PAGE:
        return inconclusive(results.detail or "no source holds the work")
    if results.read_status is ReadStatus.TRANSIENT:
        # a copy that is not the version of record cannot establish an
        # indexed work's outcome, whatever the pattern does over it
        return inconclusive("a copy that is not the version of record")

    located = locate(
        reader,  # type: ignore[arg-type] - `locate` reads `source_pages` alone
        site.key,
        locator.word,
        locator.number,
        kind=LabelKind(locator.kind.value),
    )
    if located.candidates:
        top = located.candidates[0]
        return LocatorCheck(
            site,
            Outcome.FOUND,
            f"{site.key} prints {located.label}",
            top.page_index,
            top.line,
            len(results.pages),
        )
    caveat = ""
    if located.read_status is ReadStatus.CONTENT:
        short = _short_pages(located, results)
        textless = [page for page in short if not normalize(page.text)]
        if textless:
            indexes = ", ".join(str(page.page_index) for page in textless)
            return inconclusive(
                f"page index {indexes} carries no text to read, so a miss "
                "cannot rule it out"
            )
        if not short:
            return inconclusive(located.detail or "no page above the text floor")
        caveat = _short_pages_note(short)
    pages = located.quality.pages if located.quality else len(results.pages)
    if pages < NEGATIVE_PAGE_FLOOR:
        return LocatorCheck(
            site,
            Outcome.INCONCLUSIVE,
            f"{site.key} holds {pages} indexed pages, below the "
            f"{NEGATIVE_PAGE_FLOOR}-page floor for a negative search",
            pages=pages,
        )
    note = (
        f"no page of {site.key} prints {locator.word} {locator.number} in any "
        f"of the spellings tried, over {pages} pages"
    )
    return LocatorCheck(
        site,
        Outcome.NOT_PRINTED,
        f"{note}; {caveat}" if caveat else note,
        pages=pages,
    )


def _short_pages(located: Located, results: Results) -> list[Page]:
    """The pages `locate` counts under the text floor, with their text."""
    indexes = set(located.quality.under_floor) if located.quality else set()
    return [page for page in results.pages if page.page_index in indexes]


def _short_pages_note(pages: Sequence[Page]) -> str:
    """The short pages whose text was read, so a partial read is honest."""
    indexes = ", ".join(str(page.page_index) for page in pages)
    count = len(pages)
    noun, verb = ("page", "was") if count == 1 else ("pages", "were")
    return (
        f"{count} {noun} under the text floor {verb} searched as text: "
        f"page index {indexes}"
    )


def _unsupported(locator: Locator) -> str | None:
    """Why `locate` must not be relied on for this locator, else None. A
    range is refused outright: testing only its first label and calling the
    whole range found is the failure this exists to avoid."""
    kind = locator.kind
    if kind is LocatorKind.PAGE:
        return "a page locator: a page is not a label to look for"
    if kind is LocatorKind.EQUATION:
        return "an equation locator is not checked"
    if kind is LocatorKind.UNPARSED:
        return "an unparsed locator is not checked"
    if kind is LocatorKind.NONE:
        return "no locator"
    if locator.last:
        return "a label range is not checked; only its first label would be tested"
    return None
