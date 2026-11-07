"""Bibliography metadata shared by source ingestion and resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from axiomation.sources.config import SourcesError


@dataclass(frozen=True, slots=True)
class BibRecord:
    """One bibliography record, whether or not this machine has its PDF."""

    key: str
    title: str = ""
    #: the authors, else the editors: whose work it is
    author: str = ""
    doi: str = ""
    url: str = ""
    year: str = ""
    #: the entry's type, lowercased: `article`, `book`, …
    kind: str = ""
    #: the pages the entry gives, as it writes them: `30--35`
    pages: str = ""
    #: every PDF its `file` field names that is on this machine, in order
    files: tuple[Path, ...] = ()
    #: how many PDFs its `file` field names that are not on this machine
    unseen: int = 0
    #: its arXiv identifier, where it names one: `2105.21050`
    arxiv: str = ""


def clean(value: str) -> str:
    """Strip BibTeX's protective braces from a field value."""
    return value.replace("{", "").replace("}", "")


def load(bib_path: Path) -> list[dict[str, str]]:
    import bibtexparser

    try:
        with open(bib_path, encoding="utf-8") as handle:
            return bibtexparser.load(handle).entries
    except OSError as error:
        raise SourcesError(f"{bib_path}: cannot read: {error.strerror}") from error


def listed_pdfs(raw: str) -> tuple[Path, ...]:
    """Every `.pdf` a `file` field names, here or not, in the field's
    order, each path once.

    Three shapes in the wild: a plain path, semicolon-separated paths, and
    `description:path:type` triples whose path may itself contain colons.
    """
    whole = Path(raw.strip())
    if whole.suffix.lower() == ".pdf" and whole.exists():
        return (whole,)
    found: list[Path] = []
    for part in raw.split(";"):
        part = part.strip()
        segments = part.split(":")
        inner = ":".join(segments[1:-1]).strip() if len(segments) >= 3 else ""
        for candidate in (part, inner):
            path = Path(candidate)
            if candidate and path.suffix.lower() == ".pdf":
                if path not in found:
                    found.append(path)
                break
    return tuple(found)


def pdf_paths(raw: str) -> tuple[Path, ...]:
    """The `.pdf` files a `file` field names that exist here, in order."""
    return tuple(path for path in listed_pdfs(raw) if path.exists())


def _unseen(raw: str) -> int:
    return sum(not path.exists() for path in listed_pdfs(raw))


def people(entry: dict[str, str]) -> str:
    """Whose work an entry is: its authors, else its editors."""
    return clean(entry.get("author", "") or entry.get("editor", ""))


def year(entry: dict[str, str]) -> str:
    """The entry's year, or the year of its `date`."""
    return clean(entry.get("year", "") or entry.get("date", "")[:4]).strip()


#: An arXiv identifier: the new form (`2105.21050`, a version allowed) or
#: the old (`math/0601001`).
_ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
_ARXIV_IN = (
    re.compile(r"^10\.48550/arxiv\." + _ARXIV_ID + "$", re.IGNORECASE),
    re.compile(r"arxiv\.org/(?:abs|pdf)/" + _ARXIV_ID, re.IGNORECASE),
)


def arxiv_id(entry: dict[str, str]) -> str:
    """The arXiv identifier an entry names: its `eprint` where its
    `archiveprefix` (or `eprinttype`) is arXiv, its arXiv DOI, or an
    arXiv URL; else empty. An identifier, never the entry's type."""
    prefix = (entry.get("archiveprefix") or entry.get("eprinttype") or "").lower()
    eprint = clean(entry.get("eprint", "")).strip()
    if prefix == "arxiv" and re.fullmatch(_ARXIV_ID, eprint):
        return re.sub(r"v\d+$", "", eprint)
    for pattern, field in zip(_ARXIV_IN, ("doi", "url"), strict=True):
        found = pattern.search(clean(entry.get(field, "")).strip())
        if found:
            return found.group(1)
    return ""


def records(bib_path: Path) -> dict[str, BibRecord]:
    """Every entry by key, with the fields a resolver can use. A key holding
    `evidence.FILE_MARK` is refused: the mark names another file of a
    work, and a key holding it would take another work's pages."""
    from axiomation.evidence import FILE_MARK

    entries = load(bib_path)
    marked = [e["ID"] for e in entries if FILE_MARK in e["ID"]]
    if marked:
        raise SourcesError(
            f"{bib_path}: {', '.join(marked)} holds {FILE_MARK!r}, which names "
            "another file of a work: rename the key"
        )
    return {
        entry["ID"]: BibRecord(
            key=entry["ID"],
            title=clean(entry.get("title", "")),
            author=people(entry),
            doi=clean(entry.get("doi", "")).strip(),
            url=entry.get("url", "").strip(),
            year=year(entry),
            kind=entry.get("ENTRYTYPE", "").lower(),
            pages=clean(entry.get("pages", "")).strip(),
            files=pdf_paths(entry.get("file", "")),
            unseen=_unseen(entry.get("file", "")),
            arxiv=arxiv_id(entry),
        )
        for entry in entries
    }
