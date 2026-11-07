"""The shape of a source: its outline, or the pages that carry its contents
list.

Nobody should read a chapter to find out what is in it. Many PDFs carry an
embedded outline with page targets, and most long sources can be navigated
from the outline or from a `Contents` heading in their first 40 pages. A few
have neither, and for those, and for a paper, the honest answer is that
there is no contents list.

The outline's page target is an index into the PDF, like every other number
the reader gives out. A contents list is returned as the text of
the pages that carry its heading and is never parsed: reading a page number
out of an OCR'd list is a guess, and the reader makes no guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation.evidence import Page, ReadStatus, Sources, normalize
from axiomation.sources import pdf
from axiomation.sources.read import zotero
from axiomation.sources.read.render import pdf_path_for
from axiomation.sources.read.text import lines_of

#: How far into a source a `Contents` heading is looked for: most large
#: sources without an outline print one inside 40 pages.
CONTENTS_SCAN = 40

_CONTENTS_RE = re.compile(r"^\W*(?:table of )?contents\W*$", re.IGNORECASE)


class ContentsKind(StrEnum):
    """What `toc` found to navigate by."""

    OUTLINE = "outline"
    CONTENTS_PAGES = "contents-pages"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Entry:
    """One outline entry: its depth, its title and the page it opens."""

    level: int
    title: str
    page_index: int

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "title": self.title, "page_index": self.page_index}


@dataclass(frozen=True, slots=True)
class Contents:
    """What a source has by way of structure, or why it has none."""

    bib_key: str
    kind: ContentsKind = ContentsKind.NONE
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    file_path: str = ""
    outline: tuple[Entry, ...] = ()
    pages: tuple[Page, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "kind": self.kind.value,
            "read_status": self.read_status.value,
            "detail": self.detail,
            "file_path": self.file_path,
            "outline": [entry.as_dict() for entry in self.outline],
            "pages": [page.as_dict() for page in self.pages],
        }


def outline_of(pdf_path: Path) -> tuple[Entry, ...]:
    """The PDF's embedded outline, titles normalized, targets as indices.

    The reader converts the library's 0-based levels and pages to 1-based and
    drops a bookmark with no target; here a title that normalizes to nothing
    is dropped too.
    """
    entries: list[Entry] = []
    for bookmark in pdf.outline(pdf_path):
        shown = normalize(bookmark.title)
        if shown:
            entries.append(
                Entry(
                    level=bookmark.level,
                    title=shown,
                    page_index=bookmark.page_index,
                )
            )
    return tuple(entries)


def contents(
    reader: Sources,
    bib_key: str,
    *,
    zotero_url: str = zotero.DEFAULT_ZOTERO_URL,
) -> Contents:
    """What shape one source has. Never raises for a source that cannot be
    read: a key no source holds is `absent`, an outage `infrastructure`."""
    first = reader.pages(bib_key, 1, CONTENTS_SCAN)
    if not first.pages:
        return Contents(bib_key, read_status=first.read_status, detail=first.detail)
    lead = first.pages[0]
    notes: list[str] = []
    outline: tuple[Entry, ...] = ()
    path = pdf_path_for(lead, zotero_url)
    if path is None:
        notes.append(
            f"the PDF for {bib_key} is not at {lead.file_path or '(no path stored)'} "
            "and Zotero offers no one other file of it, so its outline could not "
            "be read"
        )
    else:
        try:
            outline = outline_of(path)
        except Exception as error:  # noqa: BLE001 - a broken PDF is not our bug
            notes.append(f"cannot read the outline of {path}: {error}")
    shown_path = str(path) if path is not None else lead.file_path
    if outline:
        return Contents(
            bib_key,
            ContentsKind.OUTLINE,
            read_status=_successful_status(first.read_status),
            file_path=shown_path,
            outline=outline,
            detail="; ".join(notes),
        )
    heading = tuple(
        page
        for page in first.pages
        if any(_CONTENTS_RE.match(line.text) for line in lines_of(page.text))
    )
    if heading:
        indices = ", ".join(str(page.page_index) for page in heading)
        notes.append(
            f"no outline; the Contents heading is on page index {indices}, and "
            f"the list may run on to the pages after. Read them with `sources page`."
        )
        return Contents(
            bib_key,
            ContentsKind.CONTENTS_PAGES,
            read_status=_successful_status(first.read_status),
            file_path=shown_path,
            pages=heading,
            detail="; ".join(notes),
        )
    notes.append(
        f"{bib_key} has no outline and no Contents heading in its first "
        f"{CONTENTS_SCAN} pages; a paper has neither, and some "
        f"books have neither"
    )
    return Contents(
        bib_key,
        ContentsKind.NONE,
        read_status=_successful_status(first.read_status),
        file_path=shown_path,
        detail="; ".join(notes),
    )


def _successful_status(status: ReadStatus) -> ReadStatus:
    """A successful exact read preserves only the transient qualification."""
    return ReadStatus.TRANSIENT if status is ReadStatus.TRANSIENT else ReadStatus.OK
