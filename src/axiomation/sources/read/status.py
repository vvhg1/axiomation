"""What the cited works are read from, and whether they can be read.

The first question every source failure raises is what the tool reads
and where that came from. This answers it: the resolved settings with
the origin of each, where the key was found, and whether the optional
adapter is there; then, with the library, the contract check and what
the collection holds; without it (the plain tier), each entry of the
bibliography and where its PDF is read from, or why it is not.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from axiomation.evidence import Origin, ReadStatus, fenced
from axiomation.settings import ignored_env_files
from axiomation.sources import confirm, tesseract
from axiomation.sources.bib import records
from axiomation.sources.config import (
    Contact,
    ResolvedSources,
    Settings,
    api_key_source,
    contact,
    library_off,
    no_contact,
    resolve,
)
from axiomation.sources.contract import TextSource
from axiomation.sources.files import Choice, choose
from axiomation.sources.match import FIRST_PAGES, Check, check, expected
from axiomation.sources.read import zotero

if TYPE_CHECKING:
    from axiomation.sources.library.client import ContractCheck, Paper, SourceReader


@dataclass(frozen=True, slots=True)
class WorkFile:
    """An entry of the bibliography as the plain tier reads it: where its
    file is read from (`origin`, None where none is), its read status
    (`ok`, `transient`; or why it gives no page: `mismatch`, `absent`,
    `infrastructure`), whether the match could not check it, whether its
    cited edition is not decided (no file is read under its key), and
    what a read says of it."""

    key: str
    read_status: ReadStatus
    origin: Origin | None = None
    unchecked: bool = False
    detail: str = ""
    undecided: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.key,
            "read_status": self.read_status.value,
            "origin": self.origin.value if self.origin else None,
            "unchecked": self.unchecked,
            "undecided": self.undecided,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Several:
    """An entry holding several distinct files: its choice, the hash of
    the file the corpus holds under its key (empty where none), and the
    keys of its other files the corpus holds."""

    choice: Choice
    stored: str
    others: tuple[str, ...]

    @property
    def lines(self) -> tuple[str, ...]:
        """What the corpus holds against the choice, a line each."""
        choice = self.choice
        lines = []
        if not self.stored:
            lines.append(f"the corpus holds nothing under {choice.key}")
        elif choice.cited is None or self.stored != choice.cited.file_sha256:
            found = choice.held(self.stored)
            unlisted = (
                "a file not on this machine, or no longer listed"
                if choice.unseen
                else "a file no longer listed"
            )
            held = found.path if found else f"{unlisted} ({self.stored[:12]})"
            lines.append(
                f"the corpus holds {held} under {choice.key}, "
                + (
                    f"not the cited edition: `axiomation sources ingest DIR --key "
                    f"{choice.key}` reads the cited edition under it"
                    if choice.cited is not None
                    else "its pages kept until a person names the cited edition"
                )
            )
        unheld = [f.key for f in choice.others if f.key not in self.others]
        if unheld and choice.cited is not None:
            lines.append(
                f"the corpus does not hold {', '.join(unheld)}: `axiomation sources "
                f"ingest DIR --key {choice.key}` reads them"
            )
        return tuple(lines)

    def as_dict(self) -> dict[str, Any]:
        choice = self.choice
        return {
            "bib_key": choice.key,
            "chosen": choice.chosen.value,
            "cited": str(choice.cited.path) if choice.cited else None,
            "files": [
                {
                    "key": f.key,
                    "label": f.label,
                    "path": str(f.path),
                    "file_sha256": f.file_sha256,
                    "pages": f.pages,
                    "held": f.key in self.others
                    or (f is choice.cited and self.stored == f.file_sha256),
                }
                for f in choice.files
            ],
            "stored": self.stored,
            "said": choice.said(),
            "lines": list(self.lines),
        }


@dataclass(frozen=True, slots=True)
class SourcesStatus:
    """Everything `axiomation sources status` has to say."""

    settings: ResolvedSources
    #: the library's contract check; None in the plain tier
    contract: ContractCheck | None = None
    #: why the library is not read (the plain tier), empty where it is
    library_off: str = ""
    #: each entry of the bibliography, as the plain tier reads it
    works: tuple[WorkFile, ...] = ()
    #: in the plain tier, why no entry can be read (a bibliography named
    #: and not on this machine): an outage
    outage: str = ""
    papers: tuple[Paper, ...] = ()
    chunks: int = 0
    unreadable: tuple[str, ...] = ()
    zotero: bool = False
    api_key: bool = False
    #: where the Voyage key was read (the shell, the user file), or empty
    #: when there is none
    key_source: str = ""
    #: the person's user file, and whether it is there
    user_file: str = ""
    user_file_found: bool = False
    #: the `.env` files beside the paper or at the workspace's root, which
    #: are not read
    ignored: tuple[str, ...] = ()
    #: the lookups' contact address and where it was read
    contact: Contact = Contact()
    #: what a lookup loses without it, and its fix; empty where it is set
    no_contact: str = ""
    #: each work whose file is not its entry's work, and its check: held,
    #: unread
    mismatched: tuple[tuple[str, Check], ...] = ()
    #: each held work a person confirmed another file of, said as a sentence
    confirmed_other: tuple[tuple[str, str], ...] = ()
    #: each work whose first pages print other years than its entry's
    year_notes: tuple[tuple[str, str], ...] = ()
    #: why no file was checked against its entry, where none was
    unchecked: str = ""
    #: why Tesseract cannot run here and how to install it, empty when it
    #: can: every ingest needs it
    tesseract_missing: str = ""
    #: each entry holding several distinct files: its cited edition and
    #: the others, and what the corpus holds of them
    several: tuple[Several, ...] = ()

    @property
    def ok(self) -> bool:
        """The library's contract holds, or its collection is not built
        yet (a new paper's); in the plain tier, no work's place is out of
        reach (an outage)."""
        if self.contract is not None:
            return self.contract.ok or not self.contract.built
        return not self.outage and not any(
            w.read_status is ReadStatus.INFRASTRUCTURE for w in self.works
        )

    @property
    def read_by(self) -> dict[str, int]:
        """How many chunks each text source read, the most first: `none` a
        page's own layer, an OCR backend's name the pages it read."""
        total: Counter[str] = Counter()
        for paper in self.papers:
            total.update(dict(paper.read_by))
        return dict(total.most_common())

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "settings": self.settings.as_dict(),
            "library": not self.library_off,
            "library_off": self.library_off,
            "contract": self.contract.as_dict() if self.contract else None,
            "works": [w.as_dict() for w in self.works],
            "outage": self.outage,
            "papers": len(self.papers),
            "chunks": self.chunks,
            "read_by": self.read_by,
            "unreadable": list(self.unreadable),
            "zotero": self.zotero,
            "api_key": self.api_key,
            "key_source": self.key_source,
            "user_file": self.user_file,
            "user_file_found": self.user_file_found,
            "ignored": list(self.ignored),
            "contact_email": self.contact.address or None,
            "contact_source": self.contact.source or None,
            "mismatched": [
                {
                    "bib_key": key,
                    "why": found.why,
                    "title_page": found.title_page,
                    "says": said(key, found),
                    "confirmed_other": dict(self.confirmed_other).get(key, ""),
                }
                for key, found in self.mismatched
            ],
            "year_notes": [{"bib_key": k, "note": n} for k, n in self.year_notes],
            "unchecked": self.unchecked,
            "tesseract_missing": self.tesseract_missing,
            "several": [entry.as_dict() for entry in self.several],
            "titles": [
                {
                    "bib_key": paper.bib_key,
                    "title": paper.title,
                    "chunks": paper.chunks,
                }
                for paper in self.papers
            ],
        }


def collect(
    config: Settings,
    *,
    zotero_url: str = "",
    reader: SourceReader | None = None,
) -> SourcesStatus:
    """Ask every question at once. Never raises for a corpus that is down.

    A caller that already holds a reader passes it and keeps it; otherwise
    one is built here and closed here. `zotero_url` overrides the resolved
    address, which is what a test that must not touch a real Zotero uses.
    """
    settings = resolve(config)
    zotero_url = zotero_url or settings.zotero_url
    key_source = api_key_source(config)
    asks = contact(config)
    off = "" if reader is not None else library_off(settings)
    told = SourcesStatus(
        settings=settings,
        library_off=off,
        zotero=zotero.available(zotero_url),
        api_key=bool(key_source),
        key_source=key_source,
        user_file=str(config.user.file),
        user_file_found=config.user.file.is_file(),
        ignored=tuple(
            str(f) for f in ignored_env_files(config.root, config.workspace_root)
        ),
        contact=asks,
        no_contact="" if asks.address else no_contact(config),
    )
    if off:
        works, unchecked, outage = _plain_works(settings)
        return replace(told, works=works, unchecked=unchecked, outage=outage)
    from axiomation.sources.library.client import SourceReader

    borrowed = reader is not None
    view = reader or SourceReader(settings)
    try:
        contract = view.check_contract()
        papers: tuple[Paper, ...] = ()
        mismatched: list[tuple[str, Check]] = []
        other: list[tuple[str, str]] = []
        notes: list[tuple[str, str]] = []
        several: list[Several] = []
        unchecked = ""
        if contract.ok and contract.points:
            papers = view.papers()
            if settings.bib is not None and settings.bib.is_file():
                mismatched, other, notes, several = _file_checks(view, settings)
            else:
                unchecked = (
                    f"no bibliography at {settings.bib}"
                    if settings.bib is not None
                    else "no bibliography"
                ) + ": no file is checked against its entry, and none is held"
    finally:
        if not borrowed:
            view.close()
    return replace(
        told,
        contract=contract,
        papers=papers,
        chunks=sum(paper.chunks for paper in papers),
        unreadable=tuple(
            paper.bib_key for paper in papers if TextSource.FAILED in paper.text_sources
        ),
        mismatched=tuple(mismatched),
        confirmed_other=tuple(other),
        year_notes=tuple(notes),
        unchecked=unchecked,
        tesseract_missing=tesseract.missing(),
        several=tuple(several),
    )


def _plain_works(
    settings: ResolvedSources,
) -> tuple[tuple[WorkFile, ...], str, str]:
    """Each entry of the bibliography as a read in the plain tier reads it
    (`providers.plain_providers`, through `Sources`), its file checked
    against its entry; why none is listed where there is no bibliography;
    and the outage where the one named is not on this machine."""
    from axiomation.evidence import Sources
    from axiomation.sources.providers import UNCHECKED, Entries, plain_providers

    bib = settings.bib
    if bib is None:
        return (), "no bibliography: no work's PDF is found by its entry", ""
    entries = Entries(bib)
    gone = entries.gone()
    if gone:
        return (), "", gone
    sources = Sources(list(plain_providers(settings, entries)))
    works = tuple(
        _work_file(sources, entries, key, UNCHECKED) for key in sorted(records(bib))
    )
    return works, "", ""


def _work_file(sources: Any, entries: Any, key: str, unchecked: str) -> WorkFile:
    """One entry as a read of it finds it: the provider holding it, why
    its file is held back, which of its files is read; or why none is."""
    provider, failures = sources.holding(key)
    if failures:
        return WorkFile(key, ReadStatus.INFRASTRUCTURE, detail="; ".join(failures))
    try:
        if provider is None:
            choice = entries.choice(key)
            if choice is not None and choice.cited is None:
                said = choice.said(paths=False)
                return WorkFile(key, ReadStatus.ABSENT, detail=said, undecided=True)
            return WorkFile(key, ReadStatus.ABSENT)
        why = provider.held(key)
        if why:
            return WorkFile(key, ReadStatus.MISMATCH, provider.origin, detail=why)
        copy = sources.copy(key)
    except Exception as error:  # noqa: BLE001 - a provider's failures are its own
        return WorkFile(key, ReadStatus.INFRASTRUCTURE, detail=str(error))
    status = (
        ReadStatus.TRANSIENT if provider.origin is Origin.TRANSIENT else ReadStatus.OK
    )
    return WorkFile(key, status, provider.origin, unchecked in copy, copy)


def said(key: str, found: Check) -> str:
    """What a held file's title page says, fenced: it is page text."""
    if not found.says or found.says_page is None:
        return ""
    return fenced(
        found.says,
        key=key,
        page_index=found.says_page,
        read_status=ReadStatus.MISMATCH.value,
    )


def _file_checks(
    view: SourceReader, settings: ResolvedSources
) -> tuple[
    list[tuple[str, Check]],
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[Several],
]:
    """Every indexed work the bibliography names, its file against its
    entry: the ones held back, those of them a person confirmed another
    file of, and the year notes; and every entry holding several files,
    which is read, beside the hash of the one the corpus holds."""
    assert settings.bib is not None
    entries = records(settings.bib)
    kept = confirm.load(settings.bib)
    held, other, notes = [], [], []
    openings = view.openings(FIRST_PAGES)
    for key, opening in sorted(openings.items()):
        if key not in entries:
            continue
        found = check(expected(entries[key]), opening.pages)
        if confirm.hold(key, found, kept, opening.file_sha256):
            held.append((key, found))
            earlier = confirm.earlier(kept, key, opening.file_sha256)
            if earlier:
                other.append((key, earlier))
        if found.year_note:
            notes.append((key, found.year_note))
    several = []
    for key, record in sorted(entries.items()):
        if not record.files:
            continue
        choice = choose(record, kept)
        if choice.several:
            stored = openings[key].file_sha256 if key in openings else ""
            others = tuple(f.key for f in choice.others if f.key in openings)
            several.append(Several(choice, stored, others))
    return held, other, notes, several
