"""The evidence contract: a cited page, read and stamped, and a
run of a paper's script, stamped as code ran it (`RunStamp`).

A citation judgment (`cited`) rests on pages of a work nobody
here wrote. What makes it trustworthy is the same whoever fetched the page,
so it lives here, in the core, and how a page is found sits behind it: a
`Provider` returns a page's text and nothing else, and `Sources` stamps it.
No provider can hand an agent an unstamped page, and none can say a page
read well that did not.

A page's stamp is three facts: the work's key, the page's index (its
position in the PDF, from 1, never the number it prints) and the read
status (a failed read is never evidence; a page of the version of record
with no usable text is evidence as its image). A file's stamp holds its
SHA-256 and its content hash: what a judgment rests on (`FileStamp`). A
judgment read off a `transient` page is provisional, derived from its
stamps and never stored.

The fence goes on where text leaves for an agent: a forged
fence is inert, because the closing line names the hash of the body that
would have to contain it (`text_hash`). The body is never rewritten.

The providers are `axiomation.sources`', which this module imports in
`sources_for` alone.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class EvidenceError(Exception):
    """A cited page that cannot be read as asked, or no way to read one."""


# --- the text, normalised -----------------------------------------------------

_SPACE_RE = re.compile(r"\s+")
#: Format characters a match must not see: the soft hyphen and the zero-width
#: marks OCR leaves behind. NFKC keeps them, so they are dropped by hand.
_INVISIBLE_RE = re.compile("[­​‌‍﻿]")


def normalize(text: str) -> str:
    """NFKC, invisibles dropped, whitespace collapsed, the ends trimmed."""
    folded = unicodedata.normalize("NFKC", _INVISIBLE_RE.sub("", text))
    return _SPACE_RE.sub(" ", folded).strip()


# --- the read status ---------------------------------------------------

#: A page whose stored text is shorter than this was not usefully extracted:
#: a failed OCR pass, a figure plate, a blank verso. It is a content failure,
#: and the reader's next move is the page image.
TEXT_FLOOR = 50


class TextSource(StrEnum):
    """Where a page's text came from, as a corpus records it per chunk
    (`ocr_backend` on the wire): `NONE` the PDF's own text layer, `FAILED` a
    page that needed OCR and got back nothing."""

    NONE = "none"
    TESSERACT = "tesseract"
    PADDLE = "paddle"
    MINERU = "mineru"
    HTTP = "http"
    CHAT = "chat"
    FAILED = "failed"


#: The members that mean a machine read the page rather than the PDF telling.
OCR_SOURCES: frozenset[TextSource] = frozenset(
    {
        TextSource.TESSERACT,
        TextSource.PADDLE,
        TextSource.MINERU,
        TextSource.HTTP,
        TextSource.CHAT,
    }
)


class ReadStatus(StrEnum):
    """How much a read is worth: `image` a page of the version of record
    whose text is unreadable, evidence as its image (a stamp's alone: a
    read serves it as `content`); `transient` a copy that is not the
    version of record; `content` the page is there and unreadable (the
    source's fault: try the image); `mismatch` the file held under the key does not
    match its entry (a wrong file, which a person confirms or replaces);
    `absent` no source holds the work, and none failed to say (a miss,
    which a person fixes by adding the work); `infrastructure` the tool's
    fault, which a person can fix."""

    OK = "ok"
    IMAGE = "image"
    TRANSIENT = "transient"
    CONTENT = "content"
    MISMATCH = "mismatch"
    ABSENT = "absent"
    INFRASTRUCTURE = "infrastructure"


#: The statuses whose page is evidence: a failed read never is.
READ = (ReadStatus.OK, ReadStatus.IMAGE, ReadStatus.TRANSIENT)
#: The statuses of a read that gave no page: a wrong file held back, a
#: miss or an outage, told apart.
NO_PAGE = (ReadStatus.MISMATCH, ReadStatus.ABSENT, ReadStatus.INFRASTRUCTURE)

_SEVERITY = {
    ReadStatus.OK: 0,
    ReadStatus.IMAGE: 1,
    ReadStatus.TRANSIENT: 2,
    ReadStatus.CONTENT: 3,
    ReadStatus.MISMATCH: 4,
    ReadStatus.ABSENT: 5,
    ReadStatus.INFRASTRUCTURE: 6,
}


def classify_text(text: str, text_source: str | None = None) -> ReadStatus:
    """Whether a page's text is worth reading: below the floor, or an OCR
    pass that gave up, it is not."""
    if text_source == TextSource.FAILED:
        return ReadStatus.CONTENT
    if len(text.strip()) < TEXT_FLOOR:
        return ReadStatus.CONTENT
    return ReadStatus.OK


def as_evidence(page: ReadStatus, file: ReadStatus) -> ReadStatus:
    """A page's read status as evidence, given its file's: a page with no
    usable text (`content`) of the version of record (`ok`) is evidence as
    its image (`image`), which the reader reads; any other stands as read."""
    if page is ReadStatus.CONTENT and file is ReadStatus.OK:
        return ReadStatus.IMAGE
    return page


def detail_for(status: ReadStatus, key: str, page_index: int) -> str:
    """The one line that says what to do about a content failure."""
    if status is not ReadStatus.CONTENT:
        return ""
    return (
        f"{key} page {page_index} has no usable text; render the page and read "
        "the image."
    )


# --- the stamp and the fence -------------------------------------------

TAG = "source-text"
#: How much of the digest the stamp shows: enough that no body carries the
#: digest of itself, short enough to quote back.
HASH_DIGITS = 8

#: The printed opening and closing lines, beside `fenced` so the shape a
#: reader quotes and the shape a parser accepts cannot drift.
OPENING_RE = re.compile(
    rf'<{TAG} key="(?P<key>[^"]*)" page-index="(?P<page_index>[0-9]+)"'
    rf' status="(?P<read_status>[^"]*)"'
    rf' hash="(?P<hash>[0-9a-f]{{{HASH_DIGITS}}})">'
)
CLOSING_RE = re.compile(rf'</{TAG} hash="(?P<hash>[0-9a-f]{{{HASH_DIGITS}}})">')


def text_hash(text: str) -> str:
    """SHA-256 over the normalised text, its first eight digits: two OCR
    runs of one page that differ only in whitespace hash equal. What a
    fence shows of the text it wraps; never what a judgment rests on, which
    is the file (`FileStamp`)."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:HASH_DIGITS]


@dataclass(frozen=True, slots=True)
class PageStamp:
    """One page read: its work's key, its index in the PDF (from 1) and the
    read's status. What the page said is its file's (`FileStamp`): a text is
    our reading of the file, and another reading of it changes no
    evidence."""

    key: str
    page_index: int
    read_status: ReadStatus

    @property
    def read(self) -> bool:
        """Whether the page is evidence."""
        return self.read_status in READ

    @property
    def name(self) -> str:
        return f"{self.key}:{self.page_index}"


@dataclass(frozen=True, slots=True)
class FileStamp:
    """One file of a work as its sources hold it: its key (the work's for
    its cited edition, `KEY@…` for another file), the read's status (`ok`,
    `transient`; or why it gives no page: `mismatch`, `absent`), its file's
    SHA-256 (empty where none is held), which file it is, and its content
    hash (`axiomation.sources.content`), what it prints: empty where the
    file is not on this machine to hash. What a
    reading was given, and the corpus as a review last saw it
    (docs/model.md § 8)."""

    key: str
    read_status: ReadStatus
    file: str
    content: str = ""

    @property
    def read(self) -> bool:
        """Whether its pages are evidence."""
        return self.read_status in READ


def same_content(
    a: FileStamp, b: FileStamp, contents: Mapping[str, str] | None = None
) -> bool:
    """Whether two stamps are of files that print the same: one file (one
    SHA-256) always; else by their content hashes where both have one of one
    form (a stamp's own, else the one `contents`, by SHA-256, knows for its
    file); else not. A file annotated, or downloaded again with other
    metadata, prints the same."""
    if a.file == b.file:
        return True
    known = contents or {}
    left = a.content or known.get(a.file, "")
    right = b.content or known.get(b.file, "")
    if left and right and _form(left) == _form(right):
        return left == right
    return False


def _form(content: str) -> str:
    """A content hash's form, the tag before its colon: two forms of the
    hash never compare."""
    return content.partition(":")[0] if ":" in content else ""


class RunStatus(StrEnum):
    """How a run of a script ended: it ran (exit 0), it
    failed (another exit, or its runner could not start), or it ran past
    its timeout. A run that did not run is never evidence, as a failed
    read is not."""

    RAN = "ran"
    FAILED = "failed"
    TIMED_OUT = "timed-out"


@dataclass(frozen=True, slots=True)
class RunStamp:
    """One run of a paper's script, as code ran it: the code
    file's key and its blob, the runner's words as the settings declare
    them or the paper's environment gives them (`computations.runner_found`),
    the arguments a judgment named, the working directory, how it
    ended, and the hash of its output (stdout, normalised)."""

    key: str
    blob: str
    runner: str
    arguments: tuple[str, ...]
    directory: str
    status: RunStatus
    output: str

    @property
    def ran(self) -> bool:
        """Whether the run is evidence."""
        return self.status is RunStatus.RAN

    @property
    def call(self) -> tuple[str, str, str, tuple[str, ...]]:
        """What a later run is the same run of: the script's key and blob,
        the runner and the arguments."""
        return (self.key, self.blob, self.runner, self.arguments)

    @property
    def name(self) -> str:
        return " ".join([self.key, *self.arguments])


def output_hash(stdout: str) -> str:
    """A run's output hash: SHA-256 over its stdout, normalised, so a run
    that differs only in whitespace hashes equal."""
    return hashlib.sha256(normalize(stdout).encode("utf-8")).hexdigest()


def provisional(stamps: Iterable[PageStamp]) -> bool:
    """Whether a reading rests on a copy that is not the version of record
    derived from its stamps, never stored."""
    return any(s.read_status is ReadStatus.TRANSIENT for s in stamps)


def fenced(text: str, *, key: str, page_index: int, read_status: str) -> str:
    """One page, or one excerpt of one, between an opening and a closing
    line that say where it came from. The hash covers what is wrapped: a
    page's own for a page, an excerpt's for a grep line, never a page's
    identity then."""
    identifier = text_hash(text)
    return (
        f'<{TAG} key="{key}" page-index="{page_index}" '
        f'status="{read_status}" hash="{identifier}">\n'
        f"{text}\n"
        f'</{TAG} hash="{identifier}">'
    )


def fenced_pages(pages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Serialised pages, each with its `text` fenced from its own stamp."""
    return [
        {
            **page,
            "text": fenced(
                page["text"],
                key=page["bib_key"],
                page_index=page["page_index"],
                read_status=page["read_status"],
            ),
        }
        for page in pages
    ]


def _fenced_lines(
    rows: Iterable[Mapping[str, Any]], *, key: str, read_status: str
) -> list[dict[str, Any]]:
    """A grep match or a locate candidate: its `line` fenced as the page it
    was read from; the key and status are the whole read's."""
    return [
        {
            **row,
            "line": fenced(
                row["line"],
                key=key,
                page_index=row["page_index"],
                read_status=read_status,
            ),
        }
        for row in rows
    ]


#: Where a serialised read keeps a source's text: `results` a search or a
#: page read, `pages` the contents pages; `matches` and `candidates` lines
#: off one page, from grep and locate.
PAGE_FIELDS = ("results", "pages")
LINE_FIELDS = ("matches", "candidates")


def fenced_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """One serialised read with every page and every quoted line fenced; a
    payload with no source text passes through unchanged. Every read an
    agent is given goes through here."""
    fenced_fields: dict[str, Any] = {
        name: fenced_pages(payload[name]) for name in PAGE_FIELDS if payload.get(name)
    }
    fenced_fields.update(
        {
            name: _fenced_lines(
                payload[name],
                key=payload["bib_key"],
                read_status=payload["read_status"],
            )
            for name in LINE_FIELDS
            if payload.get(name)
        }
    )
    return {**payload, **fenced_fields}


# --- a work's files ----------------------------------------------------------------

#: What joins a work's key and a file's hash in the key of another file of
#: the work than its cited edition (`smith1998@8e3d55d8`: a translation, a
#: supplement): the cited edition keeps the work's key. No bibliography key
#: holds it.
FILE_MARK = "@"


def work_of(key: str) -> str:
    """The work a key's pages belong to: the key itself for a work's cited
    edition, the part before `FILE_MARK` for another file of it."""
    return key.partition(FILE_MARK)[0]


# --- pages, as read --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Page:
    """One page of one work, stamped: `page_index` its position in the PDF,
    from 1; `text_source` where its text came from (a `TextSource` value, or
    empty where the corpus never recorded it); `disputed` how many findings
    its OCR's witness could not settle, so a reader reads the page as an
    image."""

    bib_key: str
    title: str
    page_index: int
    file_path: str
    text: str
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    score: float | None = None
    text_source: str = ""
    disputed: int = 0

    @property
    def stamp(self) -> PageStamp:
        return PageStamp(self.bib_key, self.page_index, self.read_status)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bib_key": self.bib_key,
            "title": self.title,
            "page_index": self.page_index,
            "file_path": self.file_path,
            "text": self.text,
            "read_status": self.read_status.value,
            "detail": self.detail,
            "text_source": self.text_source,
        }
        if self.score is not None:
            payload["score"] = self.score
        if self.disputed:
            payload["disputed"] = self.disputed
        return payload


@dataclass(frozen=True, slots=True)
class Results:
    """What a read returned, under the status of the read itself: the worst
    page's, or the failure's where nothing was read."""

    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    pages: tuple[Page, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "read_status": self.read_status.value,
            "detail": self.detail,
            "results": [page.as_dict() for page in self.pages],
        }

    @classmethod
    def failed(cls, status: ReadStatus, detail: str) -> Results:
        return cls(read_status=status, detail=detail)


def collected(pages: Iterable[Page]) -> Results:
    """Pages in page order, under the worst status any of them carries."""
    found = sorted(pages, key=lambda item: item.page_index)
    worst = max(
        (item.read_status for item in found),
        key=_SEVERITY.__getitem__,
        default=ReadStatus.OK,
    )
    return Results(
        read_status=worst,
        detail=next((item.detail for item in found if item.detail), ""),
        pages=tuple(found),
    )


# --- providers ----------------------------------------------------------------------


class Origin(StrEnum):
    """How a provider obtains its pages: `library` the version of record,
    indexed from the bibliography's PDFs (the full tier); `file` the
    version of record too, the work's PDF on this machine read when asked
    (the plain tier); `transient` an open copy fetched for the occasion."""

    LIBRARY = "library"
    FILE = "file"
    TRANSIENT = "transient"

    @property
    def said(self) -> str:
        """What a person calls it: `the library`, `its PDF on this machine`."""
        return {
            Origin.LIBRARY: "the library",
            Origin.FILE: "its PDF on this machine",
            Origin.TRANSIENT: "a fetched copy",
        }[self]


@dataclass(frozen=True, slots=True)
class RawPage:
    """A page as a provider returns it: its text and where it came from,
    with no status, which `Sources` gives it."""

    key: str
    page_index: int
    text: str
    title: str = ""
    file_path: str = ""
    text_source: str = ""
    score: float | None = None
    disputed: int = 0


class Provider(Protocol):
    """A way to a work's pages: the library corpus, the PDFs on this
    machine and the transient fetch (`axiomation.sources.providers`). A
    failure of the provider's own raises; a page it does not hold is left
    out."""

    origin: Origin
    #: whether it searches its pages by meaning (`search`)
    searches: bool

    def holds(self, key: str) -> bool: ...

    def held(self, key: str) -> str:
        """Why a work it holds is held back, unread: its file is not its
        entry's work (`mismatch`); empty when it reads."""
        ...

    def copy(self, key: str) -> str:
        """Which copy of the work it reads, where there is more to say than
        its status: which of its entry's files, and why; empty else."""
        ...

    def other_files(self, key: str) -> tuple[str, ...]:
        """The keys of the work's other files than its cited edition that
        it holds (`FILE_MARK`)."""
        ...

    def file(self, key: str) -> str:
        """The SHA-256 of the file it holds under `key`; empty where it
        holds none, or keeps no file beside its pages."""
        ...

    def content(self, key: str) -> str:
        """The content hash of the file it holds under `key`
        (`axiomation.sources.content`), read from that file on this
        machine; empty where it holds none, or the file is not here."""
        ...

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        """Pages `start` to `end` inclusive (None: to the last), in order."""
        ...

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        """The pages that best match `query`, in one work or all, best first;
        none where the provider cannot search."""
        ...


def _holder_first(
    holder: Provider | None, providers: Sequence[Provider]
) -> tuple[Provider, ...]:
    """The providers to ask about a work's files: the one holding its key
    first, then the rest in their order."""
    rest = tuple(p for p in providers if p is not holder)
    return rest if holder is None else (holder, *rest)


#: Each origin's status for a page that read well.
_READ_AS = {
    Origin.LIBRARY: ReadStatus.OK,
    Origin.FILE: ReadStatus.OK,
    Origin.TRANSIENT: ReadStatus.TRANSIENT,
}

#: Why a search found nothing where no source searches: the plain tier.
NO_SEARCH = (
    "no search here: searching the cited works by meaning needs the library "
    "(`pip install 'axiomation[library]'`, and its Qdrant named by "
    "`qdrant_url`); find a passage by its words (`grep`) or its label "
    "(`locate`)"
)


class Sources:
    """The pages a paper's providers give, each stamped here: the reader every
    command, verifier and filing reads through. A key is read from the first
    provider holding it: the library first, where it is listed first, so
    the version of record wins and a transient copy stands in."""

    def __init__(
        self,
        providers: Sequence[Provider],
        placer: Callable[[Sources, str, str | None], tuple[int, ...]] | None = None,
        fetcher: Callable[[str], str] | None = None,
        listed: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self.providers = tuple(providers)
        self.placer = placer
        self.fetcher = fetcher
        self.listed = listed
        #: each file's content hash, by its SHA-256, as hashed here
        self._contents: dict[str, str] = {}

    def entries(self) -> tuple[str, ...]:
        """The keys of the paper's bibliography, in its order: the works a
        review that reads the bibliography may read beside those the paper
        cites (none where no bibliography is named, or it cannot be read
        here)."""
        return () if self.listed is None else tuple(self.listed())

    def places(self, bib_key: str, locator: str | None) -> tuple[int, ...]:
        """The pages a citation's locator names in the work, found at no
        cost (`axiomation.sources`' placer; none without one)."""
        return () if self.placer is None else self.placer(self, bib_key, locator)

    def fetch(self, key: str) -> Results:
        """A work found outside the bibliography (`arxiv:ID`, `doi:DOI`),
        its pages read: from a provider that holds it already,
        else fetched as an open copy by `axiomation.sources`' fetcher (none
        where a read fetches nothing), which says why where it could not."""
        provider, failures = self.holding(key)
        if provider is None and failures:
            # the library may hold it: an open copy is no stand-in for it
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"cannot tell whether a source holds {key}: " + "; ".join(failures),
            )
        if provider is None:
            if self.fetcher is None:
                return Results.failed(
                    ReadStatus.INFRASTRUCTURE,
                    "no work outside the bibliography is fetched by this read",
                )
            why = self.fetcher(key)
            if why:
                return Results.failed(ReadStatus.INFRASTRUCTURE, why)
        return self.source_pages(key)

    def _stamped(self, raw: RawPage, origin: Origin) -> Page:
        status = classify_text(raw.text, raw.text_source)
        if status is ReadStatus.OK:
            status = _READ_AS[origin]
        detail = detail_for(status, raw.key, raw.page_index)
        if status is ReadStatus.TRANSIENT:
            detail = "open copy, not the version of record: it may differ from it"
        return Page(
            bib_key=raw.key,
            title=raw.title,
            page_index=raw.page_index,
            file_path=raw.file_path,
            text=raw.text,
            read_status=status,
            detail=detail,
            score=raw.score,
            text_source=raw.text_source,
            disputed=raw.disputed,
        )

    def held(self, key: str) -> str:
        """Why a work is held back, unread, by the provider that holds it
        (`ReadStatus.MISMATCH`); empty when it reads, or nothing holds it.
        Raises where a provider that failed may hold it."""
        provider, failures = self.holding(key)
        if provider is None and failures:
            raise EvidenceError(
                f"cannot tell whether a source holds {key}: " + "; ".join(failures)
            )
        return "" if provider is None else provider.held(key)

    def copy(self, key: str) -> str:
        """Which copy of a work its pages come from, as the provider that
        holds it says (`Provider.copy`), else the first other provider that
        says anything of it (a work whose other files it holds); empty where
        none says anything; a failure said as one."""
        provider, _ = self.holding(key)
        # the holder first; then any other, which may hold the work's other
        # files where the holder is a fetched copy or nothing holds the key
        for asked in _holder_first(provider, self.providers):
            try:
                said = asked.copy(key)
            except Exception as error:  # noqa: BLE001 - a provider's failures are its own
                return (
                    f"which file of its work these pages are could not be told: the "
                    f"{asked.origin.value} failed: {error}"
                )
            if said:
                return said
        return ""

    def other_files(self, key: str) -> tuple[str, ...]:
        """The keys of a work's other files than its cited edition, as the
        provider holding the work holds them, else the first other provider
        holding any; none where none does, or the provider failed to say
        (its `copy` says so)."""
        provider, _ = self.holding(key)
        for asked in _holder_first(provider, self.providers):
            try:
                found = asked.other_files(key)
            except Exception:  # noqa: BLE001 - a provider's failures are its own
                continue
            if found:
                return found
        return ()

    def work_listing(self, work: str) -> tuple[tuple[str, ...], str]:
        """`work_files`, and why the list may be short: a source failed
        before one held the work, or one failed to list its other files
        before any listed them; empty where the list is whole."""
        provider, failures = self.holding(work)
        if failures:
            return (work,), "; ".join(failures)
        for asked in _holder_first(provider, self.providers):
            try:
                found = asked.other_files(work)
            except Exception as error:  # noqa: BLE001 - a provider's failures are its own
                return (work,), f"the {asked.origin.value} failed: {error}"
            if found:
                return (work, *found), ""
        return (work,), ""

    def work_files(self, work: str) -> tuple[str, ...]:
        """The keys of a work's files a reading is given: its key, then its
        other files (`other_files`). A review lays in these, and the
        corpus as the store knows it is these (`axiomation.corpus`)."""
        return (work, *self.other_files(work))

    def readable(self, key: str) -> tuple[bool, str]:
        """Whether a work reads now: a provider holds it and holds it back
        for nothing; and, where that cannot be told, why (an outage)."""
        provider, failures = self.holding(key)
        if provider is None:
            return False, "; ".join(failures)
        try:
            return not provider.held(key), ""
        except Exception as error:  # noqa: BLE001 - a provider's failures are its own
            return False, f"the {provider.origin.value} failed: {error}"

    def file_stamp(
        self, key: str, contents: Mapping[str, str] | None = None
    ) -> tuple[FileStamp, str]:
        """A file as its sources hold it now (`FileStamp`), and, where that
        cannot be told (an outage), why: its stamp then says
        `infrastructure`. Its content hash is the one `contents` knows for
        its SHA-256 (the store's), else the one this reader hashed for it,
        else hashed now: once for each file."""
        provider, failures = self.holding(key)
        if failures:
            # a source that failed may hold it: a later one's copy is no
            # stand-in for the file it holds
            return FileStamp(key, ReadStatus.INFRASTRUCTURE, ""), "; ".join(failures)
        if provider is None:
            return FileStamp(key, ReadStatus.ABSENT, ""), ""
        try:
            why = provider.held(key)
            file = provider.file(key)
            content = (contents or {}).get(file) or self._contents.get(file)
            if file and not content:
                content = provider.content(key)
                if content:
                    self._contents[file] = content
        except Exception as error:  # noqa: BLE001 - a provider's failures are its own
            return (
                FileStamp(key, ReadStatus.INFRASTRUCTURE, ""),
                f"the {provider.origin.value} failed: {error}",
            )
        status = ReadStatus.MISMATCH if why else _READ_AS[provider.origin]
        return FileStamp(key, status, file, content or ""), ""

    def holding(self, key: str) -> tuple[Provider | None, list[str]]:
        """The first provider that holds `key`, and the failures of those
        passed over before it: with no holder, a failure is an outage, not a
        miss."""
        failures: list[str] = []
        for provider in self.providers:
            try:
                if provider.holds(key):
                    return provider, failures
            except Exception as error:  # noqa: BLE001 - a provider's own failure
                failures.append(f"the {provider.origin.value} failed: {error}")
        return None, failures

    def pages(self, bib_key: str, start: int, end: int | None) -> Results:
        """Pages `start` to `end` inclusive (None: to the last), in order."""
        if start < 1:
            return Results.failed(ReadStatus.INFRASTRUCTURE, "page numbers start at 1")
        if end is not None and end < start:
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"page range {start}-{end} ends before it begins",
            )
        provider, failures = self.holding(bib_key)
        if provider is None and failures:
            # a provider that failed to say may hold it: an outage, not a miss
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"cannot tell whether a source holds {bib_key}: " + "; ".join(failures),
            )
        if provider is None:
            looked = " nor ".join(p.origin.said for p in self.providers)
            return Results.failed(
                ReadStatus.ABSENT,
                f"no source holds {bib_key}: neither {looked}; "
                "see `axiomation sources resolve`",
            )
        try:
            why = provider.held(bib_key)
            raw = [] if why else provider.pages(bib_key, start, end)
        except Exception as error:  # noqa: BLE001 - a provider's failures are its own
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"reading {bib_key} from the {provider.origin.value} failed: {error}",
            )
        if why:
            return Results.failed(ReadStatus.MISMATCH, why)
        if not raw:
            span = (
                f"page {start}"
                if start == end
                else f"pages {start}-{'' if end is None else end}"
            )
            return Results.failed(ReadStatus.INFRASTRUCTURE, f"{bib_key} has no {span}")
        return collected(self._stamped(p, provider.origin) for p in raw)

    def page(self, bib_key: str, page_index: int) -> Results:
        return self.pages(bib_key, page_index, page_index)

    def source_pages(self, bib_key: str) -> Results:
        """Every page of one work, in page order: what a string match runs
        over."""
        return self.pages(bib_key, 1, None)

    def search(
        self, query: str, *, bib_key: str | None = None, top_k: int = 5
    ) -> Results:
        """The best-matching pages over every provider that searches, best
        first, ties by key and page; a work's own provider alone where
        `bib_key` names one. A work held back is never searched: named, its
        search fails as its read does; over every work, its pages are left
        out. Where no source searches (the plain tier), it fails, saying
        so (`NO_SEARCH`)."""
        if not any(p.searches for p in self.providers):
            return Results.failed(ReadStatus.INFRASTRUCTURE, NO_SEARCH)
        providers = self.providers
        if bib_key is not None:
            holder, failed = self.holding(bib_key)
            if holder is None and failed:
                return Results.failed(
                    ReadStatus.INFRASTRUCTURE,
                    f"cannot tell whether a source holds {bib_key}: "
                    + "; ".join(failed),
                )
            providers = () if holder is None else (holder,)
            try:
                why = "" if holder is None else holder.held(bib_key)
            except Exception as error:  # noqa: BLE001 - a provider's failures are its own
                return Results.failed(
                    ReadStatus.INFRASTRUCTURE,
                    f"reading {bib_key} from the {holder.origin.value} failed: {error}",
                )
            if why:
                return Results.failed(ReadStatus.MISMATCH, why)
        found: list[Page] = []
        failures: list[str] = []
        for provider in providers:
            try:
                raw = provider.search(query, bib_key, top_k)
                kept = {k: not provider.held(k) for k in {p.key for p in raw}}
            except Exception as error:  # noqa: BLE001 - a provider's failures are its own
                failures.append(f"the {provider.origin.value}: {error}")
                continue
            found += [self._stamped(p, provider.origin) for p in raw if kept[p.key]]
        if not found and failures:
            return Results.failed(
                ReadStatus.INFRASTRUCTURE, "search failed: " + "; ".join(failures)
            )
        ranked = sorted(
            found, key=lambda p: (-(p.score or 0.0), p.bib_key, p.page_index)
        )[:top_k]
        worst = max(
            (p.read_status for p in ranked),
            key=_SEVERITY.__getitem__,
            default=ReadStatus.OK,
        )
        return Results(worst, "; ".join(failures), tuple(ranked))

    def stamp(self, bib_key: str, page_index: int) -> PageStamp:
        """One page's stamp as evidence now, a failed read's included. A
        page of the version of record whose text is unreadable is evidence
        as its image (`image`): what it says is its file's, which the
        judgment rests on, and a session that names it read the image
        (`axiomation.sources.cited.QueueReads.unread`). A copy's is not."""
        read = self.page(bib_key, page_index)
        if not read.pages:
            return PageStamp(bib_key, page_index, read.read_status)
        status = self.as_evidence(bib_key, read.pages[0].read_status)
        return PageStamp(bib_key, page_index, status)

    def as_evidence(self, bib_key: str, status: ReadStatus) -> ReadStatus:
        """A page's read status as evidence (`as_evidence`), its file's
        told by the provider holding the work: the one rule the runner's
        stamp and a brief's pages share."""
        if status is not ReadStatus.CONTENT:
            return status
        provider, _ = self.holding(bib_key)
        if provider is None:
            return status
        return as_evidence(status, _READ_AS[provider.origin])


def parse_page(ref: str) -> tuple[str, int]:
    """`KEY:INDEX`, a page by its work's key and its index in the PDF."""
    key, sep, index = ref.rpartition(":")
    if not sep or not key or not index.isdigit() or int(index) < 1:
        raise EvidenceError(
            f"--page {ref!r}: expected KEY:INDEX, the page's position in the PDF from 1"
        )
    return key, int(index)


#: The word a search of the cited works is tried with: one embedding.
SEARCH_PROBE = "theorem"


def search_failure(sources: Sources) -> str | None:
    """Why the cited works cannot be searched by meaning, else None: one
    search of one word through each provider that searches (the library:
    one embedding, a fraction of a cent). None in the plain tier, where
    none searches, and where each answers."""
    failed = []
    for provider in sources.providers:
        if not provider.searches:
            continue
        try:
            provider.search(SEARCH_PROBE, None, 1)
        except Exception as error:  # noqa: BLE001 - a provider's failures are its own
            failed.append(f"the {provider.origin.value}: {error}")
    return "; ".join(failed) or None


def sources_for(paper: Path, store: Path | None = None) -> Sources:
    """A paper's sources, as its settings name them, their caches under
    `store` (default: the paper's own store) (`axiomation.sources`): the
    core's one way into that package. The library where the `[library]`
    extra is installed, else each work's PDF on this machine. Settings
    that cannot be read (a person's value refused) raise as this
    contract's error, which every caller says."""
    from axiomation.sources.config import SourcesError
    from axiomation.sources.providers import sources_for as configured

    try:
        return configured(paper, store)
    except SourcesError as error:
        raise EvidenceError(str(error)) from None
