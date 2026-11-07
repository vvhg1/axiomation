"""The cited works as the store knows them: each file of each cited work
stamped through a paper's sources (its holder, the file check, its
SHA-256 and its content hash), filed where it differs from the store
(docs/model.md § 8).

A review observes at its start, so what any command or edit changed in
the corpus (an ingest, a confirmation, a bibliography's entry, the file
check's code) reaches the store before the review decides what to ask: a
reading that did not verify is asked again where its evidence grew
(`judgments.grown`), and a file that prints other content, or is absent
or held back, lapses what read it. A page's text is never read here: it
is our reading of the file, and another reading lapses nothing.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace

from axiomation import judgments
from axiomation.evidence import FileStamp, ReadStatus, Sources, work_of
from axiomation.judgments import Author
from axiomation.records import SEEN, FileSeen, JudgmentRecords, RecordStore
from axiomation.resolver import Filed
from axiomation.versions import State

#: Code's author for what a review observes.
OBSERVER = Author("axiomation", "review")


@dataclass(frozen=True)
class Observed:
    """The corpus as read now: every file stamped, those that differ from
    the store's latest, and each key whose file could not be told (an
    outage), with why."""

    files: tuple[FileStamp, ...] = ()
    changed: tuple[FileStamp, ...] = ()
    undecided: dict[str, str] = field(default_factory=dict)
    #: the keys among `changed` the store never saw before
    first_seen: frozenset[str] = frozenset()
    #: the keys among `changed` whose only change is their content hash,
    #: the store's first of a file it saw before the hash
    first_hashed: frozenset[str] = frozenset()

    @property
    def records(self) -> JudgmentRecords:
        """What filing it adds to the store."""
        return JudgmentRecords(files=tuple(FileSeen("", None, s) for s in self.changed))

    @property
    def news(self) -> bool:
        """Whether the store does not know it yet."""
        return bool(self.changed)

    def lines(self) -> list[str]:
        """What a command says of it: how many files the store sees or
        hashes first, each file that changed, the files that could not be
        told."""
        found = (
            [f"{len(self.first_seen)} files seen, the store's first sight of them"]
            if self.first_seen
            else []
        )
        found += (
            [f"{len(self.first_hashed)} files' content hashed, the store's first"]
            if self.first_hashed
            else []
        )
        found += [
            f"file {s.key}: {s.read_status.value}"
            + (f" {s.file[:12]}" if s.file else "")
            for s in self.changed
            if s.key not in self.first_seen | self.first_hashed
        ]
        found += [
            f"could not tell whether {k} reads: {why}"
            for k, why in self.undecided.items()
        ]
        return found


def observe(
    store: RecordStore,
    kept: State,
    sources: Sources,
    restamp: Collection[str] | None = (),
) -> Observed:
    """Each work `kept` cites, each named in `restamp` and, for None, each
    a judgment, a finding or a ruling read a page of, by its files (its key, its
    other files, any key of it the store saw before), stamped: a file's
    content hashed once, the store's where it knows it. Nothing is filed
    here (`file`)."""
    if isinstance(restamp, str):
        raise TypeError("observe takes a collection of keys, never one key bare")
    held = store.judgment_records()
    known = judgments.files_seen(held.files)
    contents = judgments.file_contents(held.files)
    at = judgments.seen(store, kept)
    named = {work_of(k) for k in restamp or ()}
    if restamp is None:
        named |= {work_of(k) for k in _read_keys(held)}
    works = sorted({k for ks in at.works_of.values() for k in ks} | named)
    files: list[FileStamp] = []
    undecided: dict[str, str] = {}
    for work in works:
        listed, unlisted = sources.work_listing(work)
        for key in dict.fromkeys([*listed, *(k for k in known if work_of(k) == work)]):
            # a key seen before that the work no longer lists is no file a
            # reading is given: absent, whatever the corpus still holds under
            # it; unless a source failed, which may list it
            if key in listed:
                stamp, why = sources.file_stamp(key, contents)
            else:
                stamp, why = FileStamp(key, ReadStatus.ABSENT, ""), unlisted
            if why:
                undecided[key] = why
                continue
            files.append(stamp)
    changed = tuple(s for s in files if known.get(s.key) != s)
    first_seen = frozenset(s.key for s in changed if s.key not in known)
    first_hashed = frozenset(
        s.key
        for s in changed
        if s.key in known
        and not known[s.key].content
        and replace(s, content="") == known[s.key]
    )
    return Observed(tuple(files), changed, undecided, first_seen, first_hashed)


def rested_keys(held: JudgmentRecords) -> set[str]:
    """The keys a judgment or a ruling read a page of: what they rest on,
    a work the paper cites or another entry of its bibliography, which a
    review observes beside the works the paper cites so a changed file
    lapses them (docs/model.md § 8)."""
    return {p.key for record in (*held.judgments, *held.rulings) for p in record.pages}


def _read_keys(held: JudgmentRecords) -> set[str]:
    """The keys a judgment, a finding or a ruling read a page of."""
    return {
        p.key
        for record in (*held.judgments, *held.findings, *held.rulings)
        for p in record.pages
    }


def file(store: RecordStore, observed: Observed, author: Author) -> Filed | None:
    """What `observed` found that the store does not know, filed as one
    record by `author`; nothing where it found nothing new."""
    if not observed.news:
        return None
    return judgments.file_records(store, SEEN, observed.records, author)
