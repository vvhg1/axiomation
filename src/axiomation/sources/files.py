"""An entry's files: which is the edition it cites, and what the others are.

A bibliography entry may hold several PDFs: a translation beside the
original, a supplement beside the paper, the book beside a scan of its
front matter, one file twice. Files of the same bytes are one file. The
corpus holds each distinct file. One is the cited edition, the edition
the entry names, and keeps the entry's key (`smith1998`, its pages
`smith1998:2`); each other file is held under a key of its own,
`KEY@` and the first eight characters of its SHA-256 (`smith1998@8e3d55d8`),
labelled by what a person said it is (a translation, a supplement), else
"another file of the entry". A content-derived key stays the same however
the bibliography orders the files, and a replaced file is a new key.

The cited edition is decided by what the files print, never by the order
a reference manager lists them in:

1. the one a person named (`sources confirm --file PATH --cited`);
2. else the one that prints the entry's pages (`pages = {30--35}`) as
   its own page numbers;
3. else the one whose title page the file check finds (`match`), where
   every other file's text was read and fails it, every file the entry
   lists is on this machine, and none prints other page numbers than the
   entry's (a translation, a preprint);
4. else none: undecided, for a person to name.

A file a person labelled as another file is never the cited edition but
by a person's word. An entry with one file listed has it as its cited
edition; one with a listed file that is not on this machine is decided
by the rules that need no file absent, else undecided: the absent file
may be the cited edition.

Only each file's own text layer is read, its first pages, so a choice
costs nothing: a scan without a layer neither prints the pages nor passes
the check, and leaves the choice to a rule that does not need it, or to a
person.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from axiomation.evidence import FILE_MARK, work_of
from axiomation.sources import confirm
from axiomation.sources.bib import BibRecord
from axiomation.sources.match import (
    FIRST_PAGES,
    check,
    expected,
    page_range,
    prints_other_pages,
    prints_pages,
    readable,
)

#: How many characters of a file's SHA-256 its key carries.
FILE_TAG = 8
#: The label of a file no person said what it is.
UNLABELLED = "another file of the entry"
#: The label of a file of an entry whose cited edition is not decided.
UNDECIDED_LABEL = "a file of the entry, the cited edition not decided"
#: The label of the cited edition.
CITED = "the cited edition"


class Chosen(StrEnum):
    """How the cited edition was told: `one` the entry's one file;
    `person` named by a person; `pages` the one printing the entry's
    pages; `title` the one whose title page the file check finds;
    `undecided` none."""

    ONE = "one"
    PERSON = "person"
    PAGES = "pages"
    TITLE = "title"
    UNDECIDED = "undecided"


def file_key(key: str, file_sha256: str) -> str:
    """The key of a file of `key`'s entry that is not its cited edition."""
    return f"{key}{FILE_MARK}{file_sha256[:FILE_TAG]}"


@dataclass(frozen=True, slots=True)
class EntryFile:
    """One of an entry's files: where it is, its content's hash (empty
    where it was not needed), its pages (0 where it was not opened), the
    key the corpus holds it under, and what it is."""

    path: Path
    file_sha256: str = ""
    pages: int = 0
    key: str = ""
    label: str = ""

    def where(self, paths: bool = True) -> str:
        """Where a person finds the file: its path (its name alone without
        `paths`), its hash and its pages."""
        counted = f", {self.pages} pages" if self.pages else ""
        hashed = f" ({self.file_sha256[:12]}{counted})" if self.file_sha256 else ""
        return f"{self.path if paths else self.path.name}{hashed}"

    def shown(self, paths: bool = True) -> str:
        """The file as a person finds it: its key and label, then where."""
        where = self.where(paths)
        return f"`{self.key}`, {self.label}: {where}" if self.key else where


@dataclass(frozen=True, slots=True)
class Choice:
    """An entry's distinct files on this machine, its cited edition (None
    where it has no file, or none is told apart), how it was told, and the
    person's reason where a person named it."""

    key: str
    files: tuple[EntryFile, ...]
    cited: EntryFile | None
    chosen: Chosen
    why: str = ""
    #: the entry's pages, as it writes them
    pages: str = ""
    #: how many files the entry lists that are not on this machine
    unseen: int = 0

    @property
    def several(self) -> bool:
        """Whether the entry lists more than one distinct file, here or
        not: which is the cited edition is then a choice."""
        return len(self.files) + self.unseen > 1

    @property
    def others(self) -> tuple[EntryFile, ...]:
        """Its files that are not the cited edition, each under its key."""
        return tuple(f for f in self.files if f is not self.cited)

    def held(self, file_sha256: str) -> EntryFile | None:
        """The entry's file of these bytes; None where it holds none."""
        return next((f for f in self.files if f.file_sha256 == file_sha256), None)

    @property
    def how(self) -> str:
        """How the cited edition was told; empty where it was not."""
        return {
            Chosen.ONE: "its one file",
            Chosen.PERSON: f"named by a person: {self.why}",
            Chosen.PAGES: f"the one printing the entry's pages, {self.pages}",
            Chosen.TITLE: "the one whose title page prints the entry's title "
            "and authors",
            Chosen.UNDECIDED: "",
        }[self.chosen]

    def said(self, paths: bool = True) -> str:
        """The choice as a sentence: the cited edition and why, then the
        other files; or that the cited edition is not decided and a person
        names it. Each file by its path, or its name without `paths`."""
        absent = f", and lists {self.unseen} not on this machine" if self.unseen else ""
        if self.cited is None:
            listed = "; ".join(f.shown(paths) for f in self.files)
            return (
                f"{self.key}'s cited edition is not decided: it holds "
                f"{len(self.files)} files here{absent}, and nothing they print "
                f"tells which is the edition its entry cites ({listed}). A person "
                "names it "
                f"(`axiomation sources confirm DIR {self.key} --file PATH "
                f"--cited --why …`)"
            )
        said = f"{self.key}'s cited edition is {self.cited.where(paths)}, {self.how}"
        if self.others:
            said += "; its other files: " + "; ".join(
                f.shown(paths) for f in self.others
            )
        return said + (f"; it lists {self.unseen} not here" if self.unseen else "")


def choose(record: BibRecord, kept: confirm.Kept) -> Choice:
    """`record`'s files, its cited edition by the rules above, and each
    other file's key and label. Opens a file only where the entry lists
    several."""
    if len(record.files) + record.unseen < 2:
        files = tuple(EntryFile(path, key=record.key) for path in record.files)
        cited = files[0] if files else None
        return Choice(record.key, files, cited, Chosen.ONE, pages=record.pages)
    distinct: dict[str, Path] = {}
    for path in record.files:
        distinct.setdefault(file_sha256(path), path)
    opened = {sha: _opening(path) for sha, path in distinct.items()}
    pick, chosen, why = _cited(record, kept, opened)

    def labelled(sha: str, path: Path) -> EntryFile:
        pages = opened[sha][0]
        if sha == pick:
            return EntryFile(path, sha, pages, record.key, CITED)
        held = confirm.of_file(kept, record.key, sha)
        unlabelled = UNLABELLED if pick else UNDECIDED_LABEL
        label = held.why if held is not None and not held.cited else unlabelled
        return EntryFile(path, sha, pages, file_key(record.key, sha), label)

    files = tuple(labelled(sha, path) for sha, path in distinct.items())
    cited = next((f for f in files if f.file_sha256 == pick), None)
    return Choice(record.key, files, cited, chosen, why, record.pages, record.unseen)


def file_sha256(path: Path) -> str:
    """The PDF's SHA-256, stored as `file_sha256`: which file it is, not
    what it prints (`sources.content`)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cited(
    record: BibRecord,
    kept: confirm.Kept,
    opened: Mapping[str, tuple[int, dict[int, str]]],
) -> tuple[str, Chosen, str]:
    """The hash of the cited edition among an entry's distinct files, how
    it was told, and a person's reason; an empty hash where none is."""
    shas = list(opened)
    if len(shas) == 1 and not record.unseen:
        return shas[0], Chosen.ONE, ""
    named = confirm.cited(kept, record.key)
    if named is not None and named.file_sha256 in shas:
        return named.file_sha256, Chosen.PERSON, named.why
    # a file a person said is another file is never the cited edition by a rule
    shas = [sha for sha in shas if confirm.of_file(kept, record.key, sha) is None]
    span = page_range(record.pages)
    if span is not None:
        printing = [sha for sha in shas if prints_pages(opened[sha][1], *span)]
        if len(printing) == 1:
            return printing[0], Chosen.PAGES, ""
        # a translation or a preprint, its own page numbers printed
        shas = [sha for sha in shas if not prints_other_pages(opened[sha][1], *span)]
    if record.unseen:
        return "", Chosen.UNDECIDED, ""
    want = expected(record)
    passing = [sha for sha in shas if check(want, opened[sha][1]).matches]
    read_through = all(
        any(readable(text) for text in pages.values()) for _, pages in opened.values()
    )
    if len(passing) == 1 and read_through:
        return passing[0], Chosen.TITLE, ""
    return "", Chosen.UNDECIDED, ""


def stale(
    key: str,
    records: Mapping[str, BibRecord],
    kept: confirm.Kept,
    stored: Mapping[str, str] | None = None,
) -> bool:
    """Whether a stored key no longer names a file of its entry: its entry
    left the bibliography, or the key is another file's and that file left
    the entry (every file the entry lists is here, and none is held under
    this key), or became its cited edition and is held under the entry's
    key now (`stored`, each stored key's file hash); or the key is an
    undecided entry's and holds a file the entry no longer lists. A file
    the entry lists that is not on this machine may be it: kept."""
    record = records.get(work_of(key))
    if record is None:
        return True
    if record.unseen:
        return False
    if key == record.key:
        # an undecided entry's key keeps the file stored under it while the
        # entry lists it; one it no longer lists is stale
        if stored is None or not stored.get(key) or len(record.files) < 2:
            return False
        choice = choose(record, kept)
        return choice.cited is None and choice.held(stored.get(key, "")) is None
    choice = choose(record, kept)
    if any(f.key == key for f in choice.files):
        return False
    cited = choice.cited
    moved = cited is not None and file_key(record.key, cited.file_sha256) == key
    # its pages stay until the cited edition is read under the entry's key
    return not moved or (stored or {}).get(record.key) == cited.file_sha256


def _opening(path: Path) -> tuple[int, dict[int, str]]:
    """A file's page count and its first pages' text layer; none where it
    cannot be opened."""
    from axiomation.sources.pdf import open_pdf

    try:
        with open_pdf(path) as document:
            last = min(document.page_count, FIRST_PAGES)
            return document.page_count, {
                i: document.text(i) for i in range(1, last + 1)
            }
    except Exception:  # noqa: BLE001 - a broken PDF tells nothing, and is no choice
        return 0, {}
