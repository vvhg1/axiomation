"""Suggestions: a work the paper may want to cite at a place
(docs/model.md § 8, "A suggestion"); filed by a person, an agent or a
review, read at a kept state, and answered by the author's decision.

A suggestion is a pointer offered, never a problem: no status, gate or
count reads one. It rests on its anchors' text, as a finding on how the
paper reads does, and is shown lapsed where that text changed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from axiomation import judgments
from axiomation.judgments import (
    Author,
    Cause,
    JudgmentError,
    Reader,
    Seen,
    Walked,
    kept_reader,
)
from axiomation.records import (
    NOT_FILED,
    SUGGESTION,
    SUGGESTION_ANSWER,
    Anchor,
    AnchorKind,
    Aspect,
    FiledAt,
    JudgmentRecords,
    PageStamp,
    RecordStore,
    Suggestion,
    SuggestionAnswer,
    SuggestionStatus,
)
from axiomation.resolver import Filed
from axiomation.versions import State

#: The aspect a suggestion's anchors are read under: its basis is their
#: text alone, as a `literature` finding's.
READ_AS = Aspect.LITERATURE


def works_of(suggestion: Suggestion) -> tuple[str, ...]:
    """The works a suggestion offers, by key."""
    return tuple(a.work for a in suggestion.anchors if a.work is not None)


def suggestion_record(
    at: Seen,
    anchors: Sequence[Anchor],
    why: str,
    pages: Sequence[PageStamp] = (),
    shown: Seen | None = None,
) -> Suggestion:
    """A suggestion's record at the state `at` reads, unnumbered: on a
    block or group first, naming a work; `shown` as a finding's."""
    if not anchors or anchors[0].kind not in (
        AnchorKind.BLOCK,
        AnchorKind.GROUP,
        AnchorKind.SPAN,
        AnchorKind.PART,
    ):
        raise JudgmentError(
            "a suggestion sits on a block or group first: where the paper would cite it"
        )
    if not any(a.kind is AnchorKind.WORK for a in anchors):
        raise JudgmentError("a suggestion names the work it suggests (`work:KEY`)")
    if not why.strip():
        raise JudgmentError("a suggestion says why a reader might expect it there")
    basis = tuple(judgments.anchors_basis(shown or at, READ_AS, anchors))
    return Suggestion("", None, at.state.id, tuple(anchors), why, basis, tuple(pages))


def suggest(
    store: RecordStore,
    kept: State,
    anchors: Sequence[Anchor],
    why: str,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A suggestion filed on a kept state."""
    record = suggestion_record(judgments.seen(store, kept), anchors, why)
    return judgments.file_records(
        store, SUGGESTION, JudgmentRecords(suggestions=(record,)), author
    )


def suggestion_named(store: RecordStore, ref: str) -> Suggestion:
    """The suggestion an id or a unique prefix of it names; refused
    otherwise."""
    found = [s for s in store.judgment_records().suggestions if s.id.startswith(ref)]
    if len(found) != 1:
        raise JudgmentError(
            f"{ref!r} names {len(found)} suggestions: give a suggestion's id, or a "
            "unique prefix of it"
        )
    return found[0]


def answer(
    store: RecordStore,
    ref: str,
    status: SuggestionStatus,
    note: str | None = None,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """The author's decision on a suggestion; the latest answer speaks."""
    record = SuggestionAnswer("", None, suggestion_named(store, ref).id, status, note)
    return judgments.file_records(
        store, SUGGESTION_ANSWER, JudgmentRecords(suggestion_answers=(record,)), author
    )


# --- read at a state ----------------------------------------------------------------


@dataclass(frozen=True)
class SuggestionRead:
    """A suggestion read at a state: its author, its place in the store,
    its walk there, and its latest answer."""

    suggestion: Suggestion
    author: Author
    order: FiledAt
    walked: Walked
    answer: SuggestionAnswer | None

    @property
    def live(self) -> bool:
        return not self.walked.lapsed

    @property
    def open(self) -> bool:
        """Not yet decided on."""
        return self.answer is None

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(n for n in self.walked.nodes if n is not None)

    @property
    def works(self) -> tuple[str, ...]:
        return works_of(self.suggestion)

    @property
    def cause(self) -> Cause | None:
        return judgments.first(self.walked.causes)


def suggestions(
    store: RecordStore, kept: State, reader: Reader | None = None
) -> tuple[SuggestionRead, ...]:
    """The suggestions that bear on `kept` (made on it or on a state along
    its parents), each walked to it, in filing order."""
    reader = reader or kept_reader(store)
    held = store.judgment_records()
    if not held.suggestions:
        return ()
    answers = store.authoritative()
    filed = store.filed_order()
    latest = {a.suggestion: a for a in held.suggestion_answers}
    found = []
    for s in held.suggestions:
        walked = judgments.walk(
            reader,
            s.state,
            judgments.anchored_hop(READ_AS),
            judgments.anchor_nodes(s.anchors),
            s.basis,
            kept,
        )
        if walked is not None:
            found.append(
                SuggestionRead(
                    s,
                    judgments.author_of(answers, s.answer),
                    filed.get(s.answer or "", NOT_FILED),
                    walked,
                    latest.get(s.id),
                )
            )
    reader.keep()
    return tuple(sorted(found, key=lambda r: r.order))
