"""Coverage: every rendered character owned by exactly one block.

Whitespace is not counted: it separates blocks and belongs to none.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiomation.cutter.model import Cut
from axiomation.cutter.text import views
from axiomation.reader import Document, Span


@dataclass(frozen=True, slots=True)
class Coverage:
    """The rendered characters no block owns, and those two or more own,
    each as runs that may hold whitespace between them."""

    unowned: tuple[Span, ...]
    owned_twice: tuple[Span, ...]

    @property
    def complete(self) -> bool:
        return not self.unowned and not self.owned_twice


def coverage(document: Document, cut: Cut) -> Coverage:
    counts: dict[str, bytearray] = {
        source.path: bytearray(len(source.text)) for source in document.files
    }
    for block in cut.blocks:
        for span in block.owned:
            count = counts[span.file]
            for index in range(span.start, span.end):
                count[index] = min(count[index] + 1, 2)
    unowned: list[Span] = []
    twice: list[Span] = []
    for path, view in views(document).items():
        count = counts[path]
        for wanted, found in ((0, unowned), (2, twice)):
            start: int | None = None
            last = 0
            for index, char in enumerate(view.rendered):
                if char.isspace():
                    continue
                if count[index] == wanted:
                    if start is None:
                        start = index
                    last = index + 1
                elif start is not None:
                    found.append(Span(path, start, last))
                    start = None
            if start is not None:
                found.append(Span(path, start, last))
    return Coverage(tuple(unowned), tuple(twice))
