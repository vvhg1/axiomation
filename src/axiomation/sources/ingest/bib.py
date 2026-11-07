"""BibTeX: the entries that have a local PDF.

The bibliography file is the contract; Zotero is an optional adapter.
Any `.bib` with `file` fields works, which is what a second author on JabRef
or Overleaf needs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from axiomation.evidence import FILE_MARK
from axiomation.sources import confirm
from axiomation.sources.bib import BibRecord, listed_pdfs, pdf_paths
from axiomation.sources.bib import clean as _clean
from axiomation.sources.bib import load as _load
from axiomation.sources.bib import year as _year
from axiomation.sources.config import SourcesError
from axiomation.sources.files import Choice, choose


@dataclass(frozen=True, slots=True)
class BibEntry:
    """One bibliography entry with a PDF on this machine."""

    key: str
    title: str
    author: str
    file_path: Path
    doi: str = ""
    year: str = ""
    kind: str = ""
    editor: str = ""
    #: the pages the entry gives, as it writes them
    pages: str = ""
    #: what the file is where it is not the entry's cited edition (a
    #: translation, "another file of the entry"); empty for the cited one
    label: str = ""
    #: the entry's files and its cited edition, where it holds several
    choice: Choice | None = None

    @property
    def record(self) -> BibRecord:
        """The entry as a resolver and the file check read it: its authors,
        else its editors."""
        return BibRecord(
            self.key,
            self.title,
            self.author or self.editor,
            self.doi,
            "",
            self.year,
            self.kind,
            pages=self.pages,
        )

    def payload_hash(self) -> str:
        """Hash of the payload-borne fields, stored as `bib_sha256`.

        A change here is a metadata-only update: the title, author and doi
        live in the payload, while the chunk text and its vectors depend on
        the PDF alone, so nothing needs re-embedding.
        """
        return hashlib.sha256(
            "\x1f".join([self.title, self.author, self.doi]).encode()
        ).hexdigest()


def parse(bib_path: Path) -> list[BibEntry]:
    """The entries that have a local PDF to read, in file order."""
    return read(bib_path)[0]


def read(bib_path: Path) -> tuple[list[BibEntry], list[Choice]]:
    """Each file to hold, in file order: an entry's cited edition under
    its key, and each other distinct file under its own key and label
    (`sources.files`); and the entries holding several files none of which
    is told apart as the cited edition. Each file of those is held too,
    under its own key, until a person names the cited edition; the ingest
    keeps the one stored under the entry's key where it stays."""
    kept = confirm.load(bib_path)
    entries: list[BibEntry] = []
    undecided: list[Choice] = []
    loaded = _load(bib_path)
    marked = [entry["ID"] for entry in loaded if FILE_MARK in entry["ID"]]
    if marked:
        raise SourcesError(
            f"{bib_path}: {', '.join(marked)} holds {FILE_MARK!r}, which names "
            "another file of a work: rename the key"
        )
    for entry in loaded:
        record = BibRecord(
            entry["ID"],
            _clean(entry.get("title", "")),
            _clean(entry.get("author", "") or entry.get("editor", "")),
            entry.get("doi", ""),
            "",
            _year(entry),
            entry.get("ENTRYTYPE", "").lower(),
            pages=_clean(entry.get("pages", "")).strip(),
            files=pdf_paths(entry.get("file", "")),
            unseen=sum(not p.exists() for p in listed_pdfs(entry.get("file", ""))),
        )
        if not record.files:
            continue
        choice = choose(record, kept)
        if choice.cited is None:
            undecided.append(choice)
        for held in choice.files:
            entries.append(
                BibEntry(
                    key=held.key,
                    title=_clean(entry.get("title", "")),
                    author=_clean(entry.get("author", "")),
                    file_path=held.path,
                    doi=entry.get("doi", ""),
                    year=_year(entry),
                    kind=entry.get("ENTRYTYPE", "").lower(),
                    editor=_clean(entry.get("editor", "")),
                    pages=record.pages,
                    label="" if held is choice.cited else held.label,
                    choice=choice if choice.several else None,
                )
            )
    return entries, undecided
