"""The providers behind the evidence contract: the library corpus (the
full tier), the PDFs on this machine (the plain tier) and the transient
fetch, each returning a page's text and nothing else, for
`evidence.Sources` to stamp, and holding back a work whose file is not
its entry's (`sources.match`) unless a person confirmed it.

`sources_for` is what the core's `evidence.sources_for` reaches: a paper's
providers as its settings name them, the version of record first (the
library where the `[library]` extra is installed, else the PDFs), so it
wins where both hold a work and a fetched copy stands in where only it
does. The two are never read together: a page would read as its text
layer today and as its OCR after the next ingest. A provider that cannot
be reached fails on its own; `Sources` passes over it and reads the next.

The plain tier reads a page's text layer when asked and keeps nothing on
disk: no OCR. A page with no layer, or under the text floor, reads
`content`, to be read as an image. A file the match cannot decide on
from its layer is served unchecked, for the reader to check from its
title page's image (`UNCHECKED`): a scan with no layer, or a scan whose
layer, an old OCR's, misses its title.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from axiomation.evidence import (
    Origin,
    Page,
    RawPage,
    ReadStatus,
    Results,
    Sources,
    work_of,
)
from axiomation.sources import confirm
from axiomation.sources.bib import BibRecord, records
from axiomation.sources.config import (
    ResolvedSources,
    SourcesError,
    contact,
    core_api_key,
    library_on,
    load_settings,
    ocr_api_key,
    optional_api_key,
    resolve,
)
from axiomation.sources.files import Choice, choose, file_key, file_sha256
from axiomation.sources.match import (
    FIRST_PAGES,
    Opening,
    check,
    expected,
    readable,
    scanned_window,
)
from axiomation.sources.read.locate import cited_pages
from axiomation.sources.read.transient import load_pages, pdf_path, unchecked_path


def _raw(page: Page) -> RawPage:
    return RawPage(
        key=page.bib_key,
        page_index=page.page_index,
        text=page.text,
        title=page.title,
        file_path=page.file_path,
        text_source=page.text_source,
        score=page.score,
        disputed=page.disputed,
    )


def _pages_of(read: Results) -> list[RawPage]:
    if read.read_status is ReadStatus.INFRASTRUCTURE and not read.pages:
        raise SourcesError(read.detail)
    return [_raw(p) for p in read.pages]


class Entries:
    """A bibliography's entries and confirmations, read once at the first
    check, and each work's check kept: what decides whether a file is held
    back, and which of an entry's several files is read."""

    def __init__(self, bib: Path | None) -> None:
        self.bib = bib
        self._records: dict[str, BibRecord] | None = None
        self._kept: confirm.Kept = {}
        self._held: dict[tuple[str, str], str] = {}
        self._choices: dict[str, Choice | None] = {}

    def record(self, key: str) -> BibRecord | None:
        """The bibliography's entry of `key`; None where it names none."""
        return self._read().get(key)

    def keys(self) -> tuple[str, ...]:
        """Every entry's key, in the bibliography's order; none where it
        is not on this machine."""
        return tuple(self._read())

    def gone(self) -> str:
        """Why the bibliography the paper names cannot be read here, where
        it names one that is not on this machine: an outage (a drive not
        mounted), not a work absent; else empty."""
        if self.bib is not None and not self.bib.is_file():
            return f"its bibliography {self.bib} is not on this machine"
        return ""

    def _read(self) -> dict[str, BibRecord]:
        if self._records is None:
            # no bibliography on this machine: nothing to check a file against
            bib = self.bib if self.bib is not None and self.bib.is_file() else None
            entries = records(bib) if bib is not None else {}
            kept = confirm.load(bib)  # both read before either is kept
            self._records, self._kept = entries, kept
        return self._records

    def held(
        self,
        key: str,
        pages: Callable[[], dict[int, str]],
        file_sha256: Callable[[], str],
    ) -> str:
        """Why `key`'s file is held back, or empty: a key the bibliography
        does not name (a found work) is never held. Kept for each file
        (its SHA-256): a file replaced under its key is checked again."""
        record = self._read().get(key)
        if record is None:
            return ""
        sha = file_sha256()
        if (key, sha) not in self._held:
            found = check(expected(record), pages())
            self._held[key, sha] = confirm.hold(
                key, found, self._kept, sha if not found.matches else ""
            )
        return self._held[key, sha]

    def choice(self, key: str) -> Choice | None:
        """The files of `key`'s entry (or, for another file's key, of its
        work's) and its cited edition, where the entry holds several
        distinct ones on this machine; else None."""
        work = work_of(key)
        if work not in self._choices:
            record = self._read().get(work)
            chosen = (
                choose(record, self._kept)
                if record is not None and record.files
                else None
            )
            self._choices[work] = chosen if chosen and chosen.several else None
        return self._choices[work]

    def confirmed(self, key: str, file_sha256: str) -> str:
        """Why a person confirmed this file as `key`'s work; empty where
        none did."""
        self._read()
        held = confirm.of_file(self._kept, key, file_sha256)
        return "" if held is None else held.why


def _unlisted(file_sha256: str, unseen: int) -> str:
    """A stored file the entry's files on this machine do not hold."""
    if unseen:
        return f"a file not on this machine, or no longer listed ({file_sha256[:12]})"
    return f"a file no longer listed ({file_sha256[:12]})"


class Library:
    """The paper's corpus in Qdrant: pages by a scroll,
    no embedding; a search embeds its query with Voyage."""

    origin = Origin.LIBRARY
    searches = True

    def __init__(
        self,
        settings: ResolvedSources,
        api_key: str = "",
        entries: Entries | None = None,
    ) -> None:
        from axiomation.sources.library.client import SourceReader

        self.settings = settings
        self.reader = SourceReader(settings, api_key=api_key)
        self.entries = entries or Entries(settings.bib)
        #: each key's first pages and file, read once: the file check and
        #: the file's stamp read the same scroll
        self._openings: dict[str, Opening | None] = {}

    def holds(self, key: str) -> bool:
        return self.reader.has_key(key)

    def _opening(self, key: str) -> Opening | None:
        if key not in self._openings:
            self._openings[key] = self.reader.openings(FIRST_PAGES, key).get(key)
        return self._openings[key]

    def held(self, key: str) -> str:
        def read() -> dict[int, str]:
            opening = self._opening(key)
            return opening.pages if opening else {}

        def sha() -> str:
            opening = self._opening(key)
            return opening.file_sha256 if opening else ""

        return self.entries.held(key, read, sha)

    def file(self, key: str) -> str:
        return self._stored(key)

    def content(self, key: str) -> str:
        """The content hash of the file the corpus holds under `key`, read
        from where the ingest read it (any stored page names it: a work
        stored from a later page has no page 1), while that file is still
        the one held; empty where it is not, or cannot be read here. The
        corpus failing to say where raises, an outage."""
        stored = self._stored(key)
        if not stored:
            return ""
        read = self.reader.pages(key, 1, FIRST_PAGES)
        if not read.pages:
            # stored from a later page still: all of them, once a file
            read = self.reader.source_pages(key)
        found = [p for p in read.pages if p.file_path]
        if not found and read.read_status is ReadStatus.INFRASTRUCTURE:
            raise SourcesError(read.detail)
        if not found:
            return ""
        from axiomation.sources.content import content_hash

        path = Path(found[0].file_path)
        try:
            if not path.is_file() or file_sha256(path) != stored:
                return ""
            return content_hash(path)
        except OSError:
            # a file this machine cannot read: its SHA-256 compares
            return ""

    def copy(self, key: str) -> str:
        """What the corpus holds of `key`'s work, said for every read of a
        key of it where its entry holds several files: each key and what
        its file is (the cited edition, kept until a person names it, or
        another file and its label), and the files not yet held; or a
        person's word that a work's one file is its work. Empty where there
        is nothing to say. What a person does about it is `sources
        status`'s."""
        work = work_of(key)
        choice = self.entries.choice(key)
        if choice is None:
            if key != work:
                return (
                    f"a file of {work} that its entry, as this machine reads it, "
                    "does not list"
                )
            why = self.entries.confirmed(key, self._stored(key))
            return (
                f"a person confirmed its file as its entry's work: {why}" if why else ""
            )
        stored = self._stored(work)
        held: list[str] = []
        cited = choice.cited
        if stored:
            found = choice.held(stored)
            name = found.where(False) if found else _unlisted(stored, choice.unseen)
            if found is not None and found is cited:
                what = f"the cited edition, {choice.how}"
            elif cited is None:
                what = "kept here until a person names the cited edition"
            else:
                what = (
                    f"not the cited edition, which is {cited.where(False)}, "
                    f"{choice.how}, not yet read under `{work}`"
                )
            held.append(f"`{work}`: {name}, {what}")
        elif cited is None:
            held.append(f"`{work}`: nothing, the cited edition not decided")
        else:
            held.append(
                f"`{work}`: nothing yet, its cited edition {cited.where(False)} "
                f"({choice.how}) not yet read under it"
            )
        unheld: list[str] = []
        for f in choice.files:
            if f.file_sha256 == stored:
                continue
            if f is cited:
                # the cited edition still under the key it had as another file
                before = file_key(work, f.file_sha256)
                if self.holds(before):
                    held.append(
                        f"`{before}`: {f.where(False)}, the cited edition, not yet "
                        f"moved under `{work}`"
                    )
                continue
            (held if self.holds(f.key) else unheld).append(
                f"`{f.key}`: {f.where(False)}, {f.label}"
            )
        said = f"{work}'s files in the corpus: " + "; ".join(held)
        if unheld:
            said += "; not yet in it: " + "; ".join(unheld)
        return said

    def _stored(self, key: str) -> str:
        """The hash of the file the corpus holds under `key`; empty where
        it holds none."""
        opening = self._opening(key)
        return opening.file_sha256 if opening else ""

    def other_files(self, key: str) -> tuple[str, ...]:
        """The keys under which the corpus holds the entry's other files."""
        choice = self.entries.choice(key) if key == work_of(key) else None
        if choice is None:
            return ()
        return tuple(f.key for f in choice.others if self.holds(f.key))

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        if end is None:
            found = _pages_of(self.reader.source_pages(key))
            return [p for p in found if p.page_index >= start]
        return _pages_of(self.reader.pages(key, start, end))

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        return _pages_of(self.reader.search(query, bib_key=key, top_k=top_k))


class Transient:
    """Open copies fetched by `sources fetch`, one per work, in the store's
    cache: not the version of record. In the full tier their pages are
    the ones the fetch extracted; in the plain tier (`layers` given) each
    page is its PDF's text layer, read when asked, as the PDFs' are."""

    origin = Origin.TRANSIENT
    searches = False

    def __init__(
        self,
        cache_dir: Path | None,
        entries: Entries | None = None,
        layers: Layers | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.entries = entries or Entries(None)
        self.layers = layers
        #: each fetched PDF's SHA-256, hashed once while it stands
        self._hashed = layers or Layers()

    def _pdf(self, key: str) -> Path | None:
        """The fetched PDF; None where none is cached."""
        if self.cache_dir is None:
            return None
        path = pdf_path(self.cache_dir, key)
        return path if path.is_file() else None

    def holds(self, key: str) -> bool:
        if self.layers is not None:
            return self._pdf(key) is not None
        return bool(load_pages(self.cache_dir, key))

    def held(self, key: str) -> str:
        """A copy fetched before the file check, or past it, checked as a
        library file is; in the plain tier, as a PDF here is, never held
        back where the match cannot decide on it (`UNCHECKED`)."""
        path = self._pdf(key)
        if self.layers is not None:
            if path is None or self._kept_unchecked(key):
                return ""
            return _layer_held(self.entries, key, path, self.layers)[0]

        def read() -> dict[int, str]:
            return {p.page_index: p.text for p in self.pages(key, 1, FIRST_PAGES)}

        return self.entries.held(key, read, lambda: self.file(key))

    def _kept_unchecked(self, key: str) -> bool:
        """Whether the fetch kept this copy though the match could not
        decide on it (`read.transient.unchecked_path`): a found work has no
        entry to check it against again."""
        return (
            self.cache_dir is not None and unchecked_path(self.cache_dir, key).is_file()
        )

    def file(self, key: str) -> str:
        """The fetched PDF's SHA-256; empty where none is cached."""
        path = self._pdf(key)
        return "" if path is None else self._hashed.sha256(path)

    def content(self, key: str) -> str:
        """The fetched PDF's content hash; empty where none is cached."""
        if self.cache_dir is None:
            return ""
        path = pdf_path(self.cache_dir, key)
        if not path.is_file():
            return ""
        from axiomation.sources.content import content_hash

        return content_hash(path)

    def copy(self, key: str) -> str:
        """Nothing beyond its status, but in the plain tier a copy the
        match could not check: a fetched copy is one file, and `transient`
        says it is not the version of record."""
        path = self._pdf(key)
        if self.layers is None or path is None:
            return ""
        if self._kept_unchecked(key):
            return UNCHECKED
        return UNCHECKED if _layer_held(self.entries, key, path, self.layers)[1] else ""

    def other_files(self, key: str) -> tuple[str, ...]:
        """None: a fetched copy is one file."""
        return ()

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        if self.layers is not None:
            path = self._pdf(key)
            if path is None:
                return []
            return _layer_pages(key, path, self.layers, start, end, "")
        found = [
            RawPage(
                key=key,
                page_index=int(str(item.get("page_index", 0))),
                text=str(item.get("text", "")),
                title=str(item.get("title", "")),
                file_path=str(item.get("file_path", "")),
                text_source=str(item.get("text_source", "")),
                disputed=int(str(item.get("disputed") or 0)),
            )
            for item in load_pages(self.cache_dir, key)
        ]
        return sorted(
            (
                p
                for p in found
                if start <= p.page_index and (end is None or p.page_index <= end)
            ),
            key=lambda p: p.page_index,
        )

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        return []


#: A page read from its PDF's text layer (`TextSource.NONE` on the wire).
LAYER = "none"
#: A scanned page's layer: an old OCR's, of no known engine, so the
#: quotation check reads it as of unknown origin, never as typeset text.
SCAN_LAYER = ""

#: What a read says of a file the match cannot decide on from its layer:
#: it is not held back, and a reader checks it.
UNCHECKED = (
    "its file is not checked against what it should be: its first pages' "
    "text layer (none, an old OCR's, or a broken one) cannot show its title "
    "page, with no OCR here to read it again. Read its title page as an "
    "image (`cited_image`, `sources image`) to confirm it is the work; read "
    "a page as an image wherever its text reads wrong"
)


def _unchecked(first: dict[int, str]) -> bool:
    """Whether a file's first pages hold no readable text: the match
    cannot decide on it (`match.check`)."""
    return not any(readable(text) for text in first.values())


def _layer_held(
    entries: Entries, key: str, path: Path, layers: Layers
) -> tuple[str, bool]:
    """Why a file read from its text layer is held back (`Entries.held`),
    and whether the match could not decide on it (`UNCHECKED`): its first
    pages hold no readable text, or they fail the check and the pages it
    read include a scan (`match.scanned_window`)."""
    first = layers.texts(path, 1, FIRST_PAGES)
    if entries.confirmed(key, layers.sha256(path)):
        # a person checked the file: it is its work
        return "", False
    if _unchecked(first):
        return "", True
    why = entries.held(key, lambda: first, lambda: layers.sha256(path))
    record = entries.record(key)
    if why and record is not None:
        scans = layers.scans(path)
        if scanned_window(expected(record), first, scans):
            return "", True
    return why, False


def _layer_pages(
    key: str, path: Path, layers: Layers, start: int, end: int | None, title: str
) -> list[RawPage]:
    """A file's pages `start` to `end`, each its text layer: a scanned
    page's of unknown origin (`SCAN_LAYER`), a digital page's the PDF's
    own (`LAYER`)."""
    texts = layers.texts(path, start, end)
    scans = layers.scans(path) if texts else frozenset()
    return [
        RawPage(key, i, text, title, str(path), SCAN_LAYER if i in scans else LAYER)
        for i, text in texts.items()
    ]


@dataclass
class _Layer:
    """One PDF's pages read so far, its page count, its SHA-256 and its
    scanned pages, while the file stands as it was (`stat`: its size, its
    times and its inode)."""

    stat: tuple[int, int, int, int]
    count: int | None = None
    sha256: str = ""
    texts: dict[int, str] = field(default_factory=dict)
    #: the indices of its scanned pages (`pdf.Pdf.page_kinds`)
    scans: frozenset[int] | None = None


class Layers:
    """PDFs on this machine, each page's text layer read when asked
    (`pdf.Pdf.text`) and kept for the life of the reader, a command or a
    session, while its file stands: a grep over a book reads its PDF once.
    Nothing is written."""

    def __init__(self) -> None:
        self._files: dict[Path, _Layer] = {}

    def _layer(self, path: Path) -> _Layer:
        info = path.stat()
        stat = (info.st_size, info.st_mtime_ns, info.st_ino, info.st_ctime_ns)
        held = self._files.get(path)
        if held is None or held.stat != stat:
            held = self._files[path] = _Layer(stat)
        return held

    def texts(self, path: Path, start: int, end: int | None) -> dict[int, str]:
        """Pages `start` to `end` inclusive (None: to the last) that the
        file has, by index, each its text layer, empty where it has none."""
        held = self._layer(path)

        def last(count: int) -> int:
            return count if end is None else min(end, count)

        if held.count is None or any(
            i not in held.texts for i in range(start, last(held.count) + 1)
        ):
            self._read(path, held, range(start, (end or 0) + 1), whole=end is None)
        assert held.count is not None
        return {i: held.texts[i] for i in range(start, last(held.count) + 1)}

    def scans(self, path: Path) -> frozenset[int]:
        """The indices of the file's scanned pages (`pdf.Pdf.page_kinds`),
        read the first time they are asked: the whole file is read for
        them, so a file check that decides on its first pages never pays
        it."""
        held = self._layer(path)
        if held.scans is None:
            self._read(path, held, range(0), kinds=True)
        assert held.scans is not None
        return held.scans

    @staticmethod
    def _read(
        path: Path,
        held: _Layer,
        wanted: range,
        *,
        whole: bool = False,
        kinds: bool = False,
    ) -> None:
        """One opening of the file: its page count, its scanned pages where
        asked (`kinds`) and not read yet, and the pages `wanted` (all, for
        `whole`) not read yet."""
        from axiomation.sources.pdf import PageKind, open_pdf

        with open_pdf(path) as document:
            held.count = document.page_count
            if kinds and held.scans is None:
                held.scans = frozenset(
                    i
                    for i, kind in enumerate(document.page_kinds(), 1)
                    if kind is PageKind.SCAN
                )
            pages = range(wanted.start, held.count + 1) if whole else wanted
            for i in pages:
                if 1 <= i <= held.count and i not in held.texts:
                    held.texts[i] = document.text(i)

    def sha256(self, path: Path) -> str:
        """The file's SHA-256 (`files.file_sha256`), hashed once while it
        stands."""
        held = self._layer(path)
        if not held.sha256:
            held.sha256 = file_sha256(path)
        return held.sha256


class Pdfs:
    """The plain tier's version of record: each work's PDF on this machine,
    read when asked. A work's PDF is its entry's file (its cited edition,
    `files.choose`, where it lists several; each other file under its own
    key), else `<pdf_dir>/<key>.pdf` where the entry names none here."""

    origin = Origin.FILE
    searches = False

    def __init__(
        self,
        entries: Entries,
        pdf_dir: Path | None = None,
        layers: Layers | None = None,
    ) -> None:
        self.entries = entries
        self.pdf_dir = pdf_dir
        self.layers = layers or Layers()

    def _path(self, key: str) -> Path | None:
        """The PDF read under `key`; None where there is none here. Raises
        where the place it would be is not on this machine (a drive not
        mounted): an outage, which lapses nothing, not a work absent."""
        gone = self.entries.gone()
        if gone:
            raise SourcesError(gone)
        work = work_of(key)
        record = self.entries.record(work)
        if record is None:
            return None
        choice = self.entries.choice(work)
        if choice is not None:
            found = next((f for f in choice.files if f.key == key), None)
            return found.path if found is not None else None
        if key != work:
            return None
        if record.files:
            return record.files[0]
        if self.pdf_dir is not None:
            if not self.pdf_dir.is_dir():
                raise SourcesError(
                    f"the PDF directory {self.pdf_dir} (pdf_dir) is not on this machine"
                )
            named = self.pdf_dir / f"{key}.pdf"
            if named.parent == self.pdf_dir and named.is_file():
                return named
        return None

    def holds(self, key: str) -> bool:
        return self._path(key) is not None

    def held(self, key: str) -> str:
        """Why the file is held back, as a library file is
        (`Entries.held`); never one the match cannot decide on from its
        layer (`UNCHECKED`)."""
        path = self._path(key)
        if path is None:
            return ""
        return _layer_held(self.entries, key, path, self.layers)[0]

    def copy(self, key: str) -> str:
        """Which of its entry's files this is, where it lists several; a
        file the match could not check; a person's word that the file is
        its work."""
        path = self._path(key)
        choice = self.entries.choice(key)
        said = [choice.said(paths=False)] if choice is not None else []
        if path is not None and _layer_held(self.entries, key, path, self.layers)[1]:
            said.append(UNCHECKED)
        elif path is not None and choice is None:
            why = self.entries.confirmed(key, self.layers.sha256(path))
            if why:
                said.append(f"a person confirmed its file as its entry's work: {why}")
        return "; ".join(said)

    def other_files(self, key: str) -> tuple[str, ...]:
        """The keys of the entry's other files on this machine."""
        choice = self.entries.choice(key) if key == work_of(key) else None
        return () if choice is None else tuple(f.key for f in choice.others)

    def file(self, key: str) -> str:
        path = self._path(key)
        return "" if path is None else self.layers.sha256(path)

    def content(self, key: str) -> str:
        from axiomation.sources.content import content_hash

        path = self._path(key)
        return "" if path is None else content_hash(path)

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        path = self._path(key)
        if path is None:
            return []
        record = self.entries.record(work_of(key))
        title = record.title if record is not None else ""
        return _layer_pages(key, path, self.layers, start, end, title)

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        return []


def sources_for(
    paper: Path, store: Path | None = None, *, fetch: bool = True
) -> Sources:
    """A paper's sources: its library (the full tier) or its PDFs on this
    machine (the plain tier, `library_on`), then its fetched copies, as its
    settings resolve (`[sources]`, the person's values and the defaults);
    the caches under `store`, the paper's own store by default. Without
    `fetch`, a work neither holds is not fetched: what a session's tools
    read of its cited works is the corpus and the copies already fetched."""
    settings = load_settings(paper, store)
    resolved = resolve(settings)
    key = optional_api_key(settings)

    def fetcher(found: str) -> str:
        """Why `found` could not be fetched, or empty."""
        from axiomation.sources.resolvers import Resolver
        from axiomation.sources.resolvers.fetch import fetch_found

        if resolved.cache_dir is None:
            return "the paper has no source cache directory"
        resolved.cache_dir.mkdir(parents=True, exist_ok=True)
        with Resolver(
            resolved.cache_dir / "sources.db",
            email=contact(settings).address,
            core_key=core_api_key(settings),
        ) as resolver:
            fetched = fetch_found(
                resolved,
                found,
                lambda doi: resolver.resolve(doi, bib_key=found),
                ocr_key=ocr_api_key(settings),
            )
        return "" if fetched.read_status is ReadStatus.TRANSIENT else fetched.detail

    entries = Entries(resolved.bib)
    if library_on(resolved):
        providers: list[Library | Pdfs | Transient] = [
            Library(resolved, key, entries),
            Transient(resolved.cache_dir, entries),
        ]
    else:
        providers = [*plain_providers(resolved, entries)]
    return Sources(
        providers,
        placer=cited_pages,
        fetcher=fetcher if fetch else None,
        listed=entries.keys,
    )


def plain_providers(
    resolved: ResolvedSources, entries: Entries | None = None
) -> tuple[Pdfs, Transient]:
    """The plain tier's providers: each work's PDF on this machine, then
    its fetched copy, one reading of each file shared between them."""
    entries = entries or Entries(resolved.bib)
    layers = Layers()
    return (
        Pdfs(entries, resolved.pdf_dir, layers),
        Transient(resolved.cache_dir, entries, layers),
    )
