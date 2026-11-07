"""A cited work's publication, as `check` and a claim's status read it
(docs/model.md § 8, § 11): which publications warn, and a person's word
that the paper rests on such a work as it is.

The publication itself is a work seen (`judgments.see_work`), filed by the
cited works' lookup (`sources/publication.py`) or by a person; who speaks for it is
`judgments.speaking_works`. An acceptance is a person's alone: an agent's
is refused, and the latest for a work speaks, a withdrawal included.
"""

from __future__ import annotations

from collections.abc import Mapping

from axiomation.judgments import Author, JudgmentError, author_of, file_records
from axiomation.records import (
    SEEN,
    JudgmentRecords,
    Publication,
    RecordStore,
    WorkAccepted,
)
from axiomation.resolver import Filed

#: The publications `cited-publications` warns of: ground a referee would
#: question, though the citation may be right.
WARNED = (Publication.PREPRINT, Publication.THESIS_OR_REPORT, Publication.ERRATUM)

#: How a publication reads on a line: "cites a preprint", "on a preprint".
WORDS = {
    Publication.REFEREED: "a refereed work",
    Publication.BOOK: "a book",
    Publication.PREPRINT: "a preprint",
    Publication.PREPRINT_PUBLISHED: "a preprint published since",
    Publication.THESIS_OR_REPORT: "a thesis or a report",
    Publication.ERRATUM: "a work with an erratum",
    Publication.RETRACTED: "a retracted work",
    Publication.UNKNOWN: "a work of unknown publication",
}


def accept(
    store: RecordStore, key: str, reason: str, author: Author, withdrawn: bool = False
) -> Filed:
    """A person's word that the paper rests on the work `key` as it is,
    with why; `withdrawn` takes it back. Refused for an agent, and with no
    reason."""
    if not author.person:
        raise JudgmentError(
            "only a person accepts resting on a work: its weakness is the "
            "author's to accept"
        )
    if not reason.strip():
        raise JudgmentError("say why: --why")
    accepted = WorkAccepted("", None, key, reason, withdrawn)
    return file_records(store, SEEN, JudgmentRecords(accepted=(accepted,)), author)


def accepted(store: RecordStore) -> dict[str, tuple[WorkAccepted, Author]]:
    """Each work a person's latest word accepts, with that word and its
    author, by key; a work whose latest word withdrew it is not here."""
    authors = store.authoritative()
    latest: dict[str, tuple[WorkAccepted, Author]] = {}
    for a in store.judgment_records().accepted:
        author = author_of(authors, a.answer)
        if author.person:
            latest[a.key] = (a, author)
    return {key: held for key, held in latest.items() if not held[0].withdrawn}


def warned(publications: Mapping[str, Publication]) -> dict[str, Publication]:
    """The works whose publication warns, of `publications` by key."""
    return {key: p for key, p in publications.items() if p in WARNED}
