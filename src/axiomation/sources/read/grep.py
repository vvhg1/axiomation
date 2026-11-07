"""A pattern over one source's text, or over every file of a work, with
the page and the line.

The free-text question, "does this book mention X at all", and the only
honest way to establish a negative. A vector search that returns nothing has
not proved absence, and the sparse half is no better: its tokenizer
splits `2.2.3` into three digit tokens and reads a ligature as a word of its
own. A regular expression over normalized text is exact, and over one source
it costs a scroll, not a model. The pattern is normalized the same way as
the text, so a ligature in either matches, and it is case-insensitive,
because the question is whether a word occurs and not how it is capitalized.

A negative carries the quality of the text it was established over. Much of a
corpus can be OCR text, and "no match" over a scan is a weaker statement than
"no match" over a text layer: the result says which one it had. A source with a
page under the text floor comes back `content` on a miss, because the pattern
could sit on that page unseen.

Bounded on both sides: 40 matching lines, so a pattern that matches a
thousand lines cannot fill an agent's context; and a pattern length and a
wall clock, because a regular expression from a prompt may run over a
whole corpus. A search the clock stopped is `infrastructure` when it found
nothing, since a stopped search is not a negative.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from axiomation.evidence import ReadStatus, Sources, normalize
from axiomation.sources.config import SourcesError
from axiomation.sources.read.text import TextQuality, scan_page, text_quality

LINE_CAP = 40
PATTERN_LIMIT = 200
TIME_BUDGET = 5.0


@dataclass(frozen=True, slots=True)
class Match:
    """One matching line and the page it is on."""

    page_index: int
    line: str

    def as_dict(self) -> dict[str, Any]:
        return {"page_index": self.page_index, "line": self.line}


@dataclass(frozen=True, slots=True)
class Grep:
    """What a pattern found in one source, and what text it looked through."""

    bib_key: str
    pattern: str
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    matches: tuple[Match, ...] = ()
    dropped: int = 0
    pages_matched: tuple[int, ...] = ()
    pages_searched: int = 0
    timed_out: bool = False
    quality: TextQuality | None = None

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "pattern": self.pattern,
            "read_status": self.read_status.value,
            "detail": self.detail,
            "matches": [match.as_dict() for match in self.matches],
            "dropped": self.dropped,
            "pages_matched": list(self.pages_matched),
            "pages_searched": self.pages_searched,
            "timed_out": self.timed_out,
            "quality": self.quality.as_dict() if self.quality else None,
        }


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """The pattern as it will run: normalized like the text, case-folded.

    Raises `SourcesError` for a pattern that is too long or is not a regular
    expression; that is the caller's mistake and not a read failure.
    """
    if len(pattern) > PATTERN_LIMIT:
        raise SourcesError(
            f"the pattern is {len(pattern)} characters long; the limit is "
            f"{PATTERN_LIMIT}. A negative needs a short pattern that a reader "
            f"can check."
        )
    try:
        return re.compile(normalize(pattern), re.IGNORECASE)
    except re.error as error:
        raise SourcesError(
            f"cannot compile {pattern!r} as a regular expression: {error}"
        ) from error


def grep(
    reader: Sources,
    bib_key: str,
    pattern: str,
    *,
    cap: int = LINE_CAP,
    budget: float = TIME_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> Grep:
    """Run one pattern over every stored page of one source."""
    compiled = compile_pattern(pattern)
    results = reader.source_pages(bib_key)
    if not results.pages:
        return Grep(bib_key, pattern, results.read_status, results.detail)
    pages = results.pages
    quality = text_quality(pages)
    matches: list[Match] = []
    matched_pages: list[int] = []
    total = searched = 0
    timed_out = False
    started = clock()
    for page in pages:
        if clock() - started > budget:
            timed_out = True
            break
        searched += 1
        for hit in scan_page(page, compiled):
            total += 1
            if not matched_pages or matched_pages[-1] != page.page_index:
                matched_pages.append(page.page_index)
            if len(matches) < cap:
                matches.append(Match(page.page_index, hit.line.shown))
    status, detail = _verdict(
        bib_key, pattern, matches, timed_out, searched, len(pages), budget, quality
    )
    if matches and results.read_status is ReadStatus.TRANSIENT:
        status, detail = results.read_status, results.detail
    return Grep(
        bib_key=bib_key,
        pattern=pattern,
        read_status=status,
        detail=detail,
        matches=tuple(matches),
        dropped=total - len(matches),
        pages_matched=tuple(matched_pages),
        pages_searched=searched,
        timed_out=timed_out,
        quality=quality,
    )


def grep_work(
    reader: Sources,
    bib_key: str,
    pattern: str,
    *,
    cap: int = LINE_CAP,
    budget: float = TIME_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Grep, ...]:
    """Run one pattern over every file of a work: `bib_key`'s pages first
    (the cited edition, for a work's key), then each other file the corpus
    holds of it (`Sources.other_files`), each a result of its own under its
    own key. A key of another file (`KEY@…`) is that file alone."""
    keys = [bib_key, *reader.other_files(bib_key)]
    return tuple(
        grep(reader, key, pattern, cap=cap, budget=budget, clock=clock) for key in keys
    )


def _verdict(
    bib_key: str,
    pattern: str,
    matches: list[Match],
    timed_out: bool,
    searched: int,
    total: int,
    budget: float,
    quality: TextQuality,
) -> tuple[ReadStatus, str]:
    stopped = f"stopped after {budget:g} s with {searched} of {total} pages searched"
    if matches:
        return ReadStatus.OK, (
            f"{stopped}; the matches are from those pages" if timed_out else ""
        )
    if timed_out:
        return ReadStatus.INFRASTRUCTURE, (
            f"{stopped} and no match. A stopped search is not a negative; "
            f"narrow the pattern."
        )
    detail = f"no line of {bib_key} matches {pattern!r} over {total} pages"
    if quality.degraded:
        shown = ", ".join(str(index) for index in quality.under_floor[:10])
        return ReadStatus.CONTENT, (
            f"{detail}; {len(quality.under_floor)} page(s) are under the text "
            f"floor and could carry it unseen: page index {shown}. Render them "
            f"before calling this an absence."
        )
    return ReadStatus.OK, detail
