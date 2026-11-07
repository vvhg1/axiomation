"""A positioning claim's reading (docs/model.md § 8, "A ruling"): a claim
that the paper's work is new, or that other works lack or did not do
something, is the contribution session's, not the argument's. A
`positioning` ruling hands it over; the contribution session's `checked`
line on it is its reading, filed as a check, read at a kept state, and
`unreviewed` lists each claim ruled `positioning` no live check reads
(§ 11), so the gate it left never passes it unread.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from axiomation import judgments
from axiomation.judgments import Author, JudgmentError, Reader, kept_reader
from axiomation.questions.edge_sheet import norm
from axiomation.records import (
    CONTRIBUTION_CHECK,
    ContributionCheck,
    JudgmentRecords,
    RecordStore,
    Subject,
)
from axiomation.resolver import Filed
from axiomation.versions import State


def check_record(
    state: str, on: Subject, searched: str, found: str
) -> ContributionCheck:
    """A check's record at the state `state`, unnumbered."""
    return ContributionCheck("", None, state, on, searched, found)


def check(
    store: RecordStore,
    kept: State,
    on: Subject,
    searched: str,
    found: str,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A check filed by hand on a kept state, by a person or an agent: the
    claim `on` read against the literature, what was searched and what was
    found, as the contribution session's `checked` line files one."""
    if not searched.strip() or not found.strip():
        raise JudgmentError("a check says what it searched and what it found")
    record = check_record(kept.id, on, searched, found)
    return judgments.file_records(
        store, CONTRIBUTION_CHECK, JudgmentRecords(checks=(record,)), author
    )


@dataclass(frozen=True)
class CheckRead:
    """A check read at a state: its record and the words it quotes there,
    or none where they changed or ended."""

    check: ContributionCheck
    node: str
    words: str | None

    @property
    def live(self) -> bool:
        return self.words is not None


def checks(
    store: RecordStore, kept: State, reader: Reader | None = None
) -> list[CheckRead]:
    """Each check that bears on `kept` (made on it or on a state along its
    parents), walked there: live while the words it quotes read the same."""
    held = store.judgment_records().checks
    if not held:
        return []
    reader = reader or kept_reader(store)
    found = []
    for c in held:
        walked = judgments.walk(
            reader,
            c.state,
            judgments.NO_HOP,
            [c.subject.node],
            (),
            kept,
            c.subject.words,
        )
        if walked is None:
            continue
        node = walked.nodes[0] or c.subject.node
        same = (
            not walked.lapsed
            and walked.words is not None
            and norm(walked.words) == norm(c.subject.words or "")
        )
        found.append(CheckRead(c, node, walked.words if same else None))
    reader.keep()
    return found


def reads(check: CheckRead, claim: Subject) -> bool:
    """Whether a live check reads a claim: on its block, its quoted words
    holding the claim's whole. A fragment reads nothing: a short quote
    would stand for every claim it occurs in."""
    if not check.live or check.node != claim.node:
        return False
    quoted, words = norm(check.words or ""), norm(claim.words or "")
    return bool(words) and words in quoted


def unchecked(claims: Sequence[Subject], held: Sequence[CheckRead]) -> list[Subject]:
    """The claims no live check reads, in the order given."""
    return [c for c in claims if not any(reads(h, c) for h in held)]
