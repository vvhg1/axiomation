"""Planning and running an ingest.

Split the way `graph.sync` is split: `plan_ingest` reads and decides,
`apply_ingest` writes. `--dry-run` is then the plan printed and nothing
else, and the creator guard is one decision reported in the plan rather than
an exception raised somewhere in the middle of a write.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation.evidence import work_of
from axiomation.sources import confirm, files
from axiomation.sources.bib import records as bib_records
from axiomation.sources.config import (
    OcrBackend,
    ResolvedSources,
    SettingFrom,
    SourcesError,
)
from axiomation.sources.contract import TextSource
from axiomation.sources.files import Choice
from axiomation.sources.ingest import bib as bib_module
from axiomation.sources.ingest import figures
from axiomation.sources.ingest.bib import BibEntry
from axiomation.sources.ingest.chunk import chunk_pages
from axiomation.sources.ingest.extract import PageText, extract_pdf
from axiomation.sources.ingest.ocr import (
    OcrError,
    OcrProvider,
    TesseractProvider,
    Witness,
    provider_for,
    tesseract_provider,
)
from axiomation.sources.ingest.readers import (
    LayerAudit,
    ReaderAudit,
    audit,
    layer_audit,
)
from axiomation.sources.library.embed import cost, embed_documents
from axiomation.sources.library.qdrant import QdrantClient, collection_names, openings
from axiomation.sources.library.store import (
    settle_figures,
    settle_readers,
    stamp_layers,
    stored_readings,
    unsettled_pages,
    unstamped_layers,
    untested_pages,
)
from axiomation.sources.library.upsert import (
    IndexedPaper,
    delete_paper,
    ensure_collection,
    guard,
    indexed_papers,
    move_papers,
    stale_backends,
    update_payload,
    upsert_chunks,
)
from axiomation.sources.match import FIRST_PAGES, check, expected
from axiomation.sources.tesseract import missing as tesseract_missing

# Prune refuses to delete more than this share of the index without --force:
# a fifth of the corpus gone usually means the wrong .bib, not a real edit.
PRUNE_SHARE = 5
PRUNE_FLOOR = 3

Progress = Callable[[str], None]


class Reason(StrEnum):
    """Why a paper is being ingested: `READER` a stored work whose pages
    only OCR serves were stored as their layer (`readers.audit`); `WITNESS`
    a stored work whose pages the declared service read before its witness
    existed (`witness.py`); `FIGURES` a stored work whose layer pages carry
    images never tested for text (`figures.py`); `LAYER` a stored work
    whose layer pages an older layer reader wrote and today's reads
    otherwise (`readers.layer_audit`)."""

    NEW = "new"
    FORCED = "forced"
    CHANGED = "changed"
    OCR_BACKEND = "ocr-backend"
    READER = "reader"
    WITNESS = "witness"
    FIGURES = "figures"
    LAYER = "layer"


#: The reasons a stored work is read again with its file unchanged: the
#: declared service's stored readings are each page's first, so only the
#: pages they do not serve, and those the witness finds a fault in, are read.
_REREAD = frozenset({Reason.READER, Reason.WITNESS, Reason.FIGURES, Reason.LAYER})


@dataclass(frozen=True, slots=True)
class Pending:
    """One paper the plan will ingest, and why: for a work read again, the
    pages its reason names (a scan's pages stored as their layer, the
    figures' pages, the layer pages read otherwise), or how many where they
    are not listed (the pages the witness holds); and, read again for
    another reason, the layer pages today's layer reader reads otherwise,
    which the read refreshes too."""

    entry: BibEntry
    file_sha256: str
    reason: Reason
    pages: tuple[int, ...] = ()
    count: int = 0
    refreshed: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkWitness:
    """What Tesseract, the witness, did on one work: the OCR'd pages it
    held (a stored page a witness already judged is not held again), those
    it had read again, those it dropped a block of as having no
    room in its box (a figure's reading on a layer page), those a block
    read alone settled, and those it left disputed; and the layer pages
    whose figures it found text in, read by the OCR."""

    bib_key: str
    witnessed: int
    reread: tuple[int, ...] = ()
    disputed: tuple[int, ...] = ()
    figures: tuple[int, ...] = ()
    dropped: tuple[int, ...] = ()
    alone: tuple[int, ...] = ()

    @classmethod
    def of(cls, bib_key: str, pages: list[PageText]) -> WorkWitness | None:
        held = [page for page in pages if page.disputed is not None]
        figured = tuple(page.page_index for page in pages if page.figures)
        dropped = tuple(page.page_index for page in pages if page.dropped)
        if not held and not figured and not dropped:
            return None
        return cls(
            bib_key,
            sum(page.kept is None for page in held),
            tuple(page.page_index for page in held if page.readings > 1),
            tuple(page.page_index for page in held if page.disputed),
            figured,
            dropped,
            tuple(page.page_index for page in held if page.alone),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "witnessed": self.witnessed,
            "reread": list(self.reread),
            "disputed": list(self.disputed),
            "figures": list(self.figures),
            "dropped": list(self.dropped),
            "alone": list(self.alone),
        }


@dataclass(frozen=True, slots=True)
class Skipped:
    """A work the ingest was asked about and leaves as it is: why, and why
    its file is held back where it is (empty when it reads)."""

    key: str
    why: str
    held: str = ""


@dataclass(frozen=True, slots=True)
class Move:
    """A file the corpus holds under `source` that the ingest holds under
    `entry.key` now: its pages, vectors and records move."""

    source: str
    entry: BibEntry
    file_sha256: str


@dataclass(frozen=True, slots=True)
class IngestPlan:
    """What an ingest would do, decided without writing anything."""

    collection: str
    exists: bool
    refusal: str
    pending: tuple[Pending, ...] = ()
    metadata_only: tuple[BibEntry, ...] = ()
    stale: tuple[str, ...] = ()
    prune_refusal: str = ""
    indexed: int = 0
    #: each key asked for by name that is not read again, and why
    skipped: tuple[Skipped, ...] = ()
    #: each stored work whose pages' records the run settles, its text
    #: untouched, and the pages a declared backend would read again
    readers: tuple[ReaderAudit, ...] = ()
    #: each stored work with pages the declared service read and no witness
    #: held, and how many, left for `--reread`
    unwitnessed: tuple[tuple[str, int], ...] = ()
    #: each stored work with layer pages never tested for figures: those
    #: without an image the run records, those with one left for `--reread`
    untested: tuple[figures.FigureAudit, ...] = ()
    #: each stored work with layer pages an older layer reader wrote: those
    #: today's reads alike the run records, those it reads otherwise left
    #: for `--reread`
    layers: tuple[LayerAudit, ...] = ()
    #: each entry holding several files none of which is told apart as the
    #: edition it cites: each file held under its own key, the one stored
    #: under the entry's key kept there, until a person names the cited one
    undecided: tuple[Choice, ...] = ()
    #: each file the corpus holds under another key of its work than the one
    #: it takes now: its pages move, nothing read or embedded
    moves: tuple[Move, ...] = ()

    @property
    def empty(self) -> bool:
        settles = (
            any(found.records for found in self.readers)
            or any(found.plain for found in self.untested)
            or any(found.alike for found in self.layers)
        )
        return not (
            self.pending or self.moves or self.metadata_only or self.stale or settles
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "exists": self.exists,
            "refusal": self.refusal,
            "indexed": self.indexed,
            "pending": [
                {
                    "bib_key": item.entry.key,
                    "title": item.entry.title,
                    "reason": item.reason.value,
                    "pages": list(item.pages),
                    "count": item.count,
                    "refreshed": list(item.refreshed),
                }
                for item in self.pending
            ],
            "metadata_only": [
                {"bib_key": entry.key, "title": entry.title}
                for entry in self.metadata_only
            ],
            "stale": list(self.stale),
            "prune_refusal": self.prune_refusal,
            "skipped": [
                {"bib_key": item.key, "why": item.why, "held": item.held}
                for item in self.skipped
            ],
            "readers": [found.as_dict() for found in self.readers],
            "unwitnessed": [
                {"bib_key": key, "pages": pages} for key, pages in self.unwitnessed
            ],
            "untested": [found.as_dict() for found in self.untested],
            "layers": [found.as_dict() for found in self.layers],
            "undecided": [
                {"bib_key": choice.key, "why": choice.said()}
                for choice in self.undecided
            ],
            "moves": [
                {"from": m.source, "to": m.entry.key, "file_sha256": m.file_sha256}
                for m in self.moves
            ],
        }


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What an ingest did."""

    plan: IngestPlan
    created: bool = False
    pruned: tuple[str, ...] = ()
    ingested: int = 0
    chunks: int = 0
    tokens: int = 0
    metadata_updated: int = 0
    failures: tuple[tuple[str, str], ...] = ()
    #: each work ingested whose file is not its entry's work, and why: held
    #: back, unread, until a person confirms it or replaces the file
    held: tuple[tuple[str, str], ...] = ()
    #: the pages whose records were settled, and of those recorded `none`
    settled: int = 0
    relabelled: int = 0
    #: the stored layer pages recorded as carrying no figure
    figureless: int = 0
    #: the stored layer pages today's layer reader reads alike, its version
    #: recorded
    stamped: int = 0
    #: why the run stopped before its last work, empty when it did not: the
    #: OCR engine failed, and every work after would fail the same way
    stopped: str = ""
    #: the works read in, in order
    read: tuple[str, ...] = ()
    #: how many points moved to another key of their work, nothing embedded
    moved: int = 0
    #: what the OCR's witness did, per work it ran on
    witnessed: tuple[WorkWitness, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.plan.collection,
            "created": self.created,
            "pruned": list(self.pruned),
            "ingested": self.ingested,
            "chunks": self.chunks,
            "tokens": self.tokens,
            "metadata_updated": self.metadata_updated,
            "failures": [
                {"bib_key": key, "error": message} for key, message in self.failures
            ],
            "held": [{"bib_key": key, "why": why} for key, why in self.held],
            "settled": self.settled,
            "relabelled": self.relabelled,
            "figureless": self.figureless,
            "stamped": self.stamped,
            "stopped": self.stopped,
            "read": list(self.read),
            "moved": self.moved,
            "witnessed": [work.as_dict() for work in self.witnessed],
        }


def plan_ingest(
    client: QdrantClient,
    settings: ResolvedSources,
    *,
    bib_path: Path,
    key: str | None = None,
    force: bool = False,
    prune: bool = False,
    reread: bool = False,
) -> IngestPlan:
    """Decide what to ingest. Reads the bib, the PDFs and the collection.

    A stored work whose pages only OCR serves were stored as their layer is
    read again only when `reread` asks and a backend is declared: it takes
    hours and lapses what read those pages, so a run never starts it
    unasked. Otherwise its pages are listed (`readers`). So is a stored
    work whose pages the declared service read before its witness, and one
    whose layer pages carry images never tested for figures; layer pages
    without an image are recorded as tested by the run, their text
    untouched (`untested`). So is a stored work whose layer pages an older
    layer reader wrote and today's reads otherwise; those it reads alike
    take its version, their text untouched (`layers`).
    """
    entries, undecided = bib_module.read(bib_path)
    if key is not None:
        # an entry's key asks for each of its files
        entries = [entry for entry in entries if work_of(entry.key) == key]
        undecided = [choice for choice in undecided if choice.key == key]
        if not entries and not undecided:
            raise SourcesError(f"{bib_path}: no entry with key {key!r}")

    refusal = guard(client, settings)
    exists = settings.collection in collection_names(client)
    stored = indexed_papers(client, settings.collection) if exists else {}

    stale: tuple[str, ...] = ()
    prune_refusal = ""
    if prune:
        found, kept = bib_records(bib_path), confirm.load(bib_path)
        hashes = {k: paper.file_sha256 or "" for k, paper in stored.items()}
        stale = tuple(sorted(k for k in stored if files.stale(k, found, kept, hashes)))
        ceiling = max(PRUNE_FLOOR, len(stored) // PRUNE_SHARE)
        if len(stale) > ceiling and not force:
            prune_refusal = (
                f"{len(stale)} of {len(stored)} indexed keys name no file of an "
                f"entry in {bib_path}. Refusing to prune more than {ceiling} without "
                f"--force; check that [sources].bib names the right file."
            )

    shas = {entry.key: files.file_sha256(entry.file_path) for entry in entries}
    # an undecided entry keeps the file stored under its key where it is
    kept_under = {
        c.key: stored[c.key].file_sha256
        for c in undecided
        if c.key in stored
        and (c.unseen or c.held(stored[c.key].file_sha256 or "") is not None)
    }
    entries = [
        e
        for e in entries
        if not (
            e.key != work_of(e.key) and kept_under.get(work_of(e.key)) == shas[e.key]
        )
    ]
    # each key's file once the ingest is done, to tell a file stored under a
    # key it leaves, which moves rather than being read again
    held_after = {k: v for k, v in kept_under.items() if v} | {
        e.key: shas[e.key] for e in entries
    }
    pending: list[Pending] = []
    moves: list[Move] = []
    metadata_only: list[BibEntry] = []
    skipped: list[Skipped] = []
    readers: list[ReaderAudit] = []
    unwitnessed: list[tuple[str, int]] = []
    untested: list[figures.FigureAudit] = []
    layers: list[LayerAudit] = []
    confirmer = None if tesseract_missing() else TesseractProvider()
    for entry in entries:
        file_sha256 = shas[entry.key]
        reason = _reason(entry, file_sha256, stored.get(entry.key), settings, force)
        if reason in (Reason.NEW, Reason.CHANGED) and not force:
            source = _moving(entry.key, file_sha256, stored, held_after)
            if source:
                moves.append(Move(source, entry, file_sha256))
                continue
        pages: tuple[int, ...] = ()
        count = 0
        if reason is None and stored[entry.key].unmarked and entry.file_path.is_file():
            found = audit(
                entry.key,
                entry.file_path,
                unsettled_pages(client, settings.collection, entry.key),
                tesseract=confirmer,
            )
            if found.reread and reread and _declared(settings):
                reason, pages = Reason.READER, found.reread
            elif found.reread or found.records:
                readers.append(found)
        if reason is None and _witnessed(settings) and stored[entry.key].unwitnessed:
            if reread and entry.file_path.is_file():
                reason, count = Reason.WITNESS, stored[entry.key].unwitnessed
            else:
                unwitnessed.append((entry.key, stored[entry.key].unwitnessed))
        if (
            reason is None
            and _witnessed(settings)
            and stored[entry.key].untested
            and entry.file_path.is_file()
        ):
            tested = figures.audit(
                entry.key,
                entry.file_path,
                untested_pages(client, settings.collection, entry.key),
            )
            if tested.images and reread:
                reason, pages = Reason.FIGURES, tested.images
            elif tested.images or tested.plain:
                untested.append(tested)
        if reason is None and stored[entry.key].unstamped and entry.file_path.is_file():
            drawn = layer_audit(
                entry.key,
                entry.file_path,
                unstamped_layers(client, settings.collection, entry.key),
            )
            alone = _declared(settings) or _layers_alone(stored[entry.key])
            if drawn.outdated and reread and alone:
                reason, pages = Reason.LAYER, drawn.outdated
            elif drawn.outdated or drawn.alike:
                outdated_only = bool(drawn.outdated) and not alone
                layers.append(replace(drawn, needs_backend=outdated_only))
        if reason is not None:
            refreshed: tuple[int, ...] = ()
            if (
                reason is not Reason.LAYER
                and entry.key in stored
                and stored[entry.key].unstamped
                and entry.file_path.is_file()
            ):
                refreshed = layer_audit(
                    entry.key,
                    entry.file_path,
                    unstamped_layers(client, settings.collection, entry.key),
                ).outdated
            pending.append(
                Pending(
                    entry, file_sha256, reason, pages, count or len(pages), refreshed
                )
            )
            continue
        if _metadata_moved(entry, stored[entry.key]):
            metadata_only.append(entry)
        if key is not None:
            skipped.append(
                _skipped(client, settings, bib_path, entry, stored[entry.key])
            )

    return IngestPlan(
        collection=settings.collection,
        exists=exists,
        refusal=refusal,
        pending=tuple(pending),
        metadata_only=tuple(metadata_only),
        stale=stale,
        prune_refusal=prune_refusal,
        indexed=len(stored),
        skipped=tuple(skipped),
        readers=tuple(readers),
        unwitnessed=tuple(unwitnessed),
        untested=tuple(untested),
        layers=tuple(layers),
        undecided=tuple(undecided),
        moves=tuple(moves),
    )


def _emptied(plan: IngestPlan, key: str) -> str:
    """Where a move took `key`'s earlier file to another key and its new
    one was not read: the key holds nothing, said as a sentence."""
    gone = [m.entry.key for m in plan.moves if m.source == key]
    if not gone:
        return ""
    return (
        f" {key} holds nothing now: its earlier file moved to {gone[0]}; the "
        "ingest reads it again"
    )


def _moving(
    key: str, file_sha256: str, stored: dict[str, IndexedPaper], after: dict[str, str]
) -> str:
    """The key of the same work under which the corpus holds this file and
    which the ingest takes it from (another file now the cited edition, or
    the cited edition become another file): its pages move, not read again;
    empty where none does."""
    return next(
        (
            k
            for k, paper in stored.items()
            if k != key
            and work_of(k) == work_of(key)
            and paper.file_sha256 == file_sha256
            and after.get(k) != file_sha256
        ),
        "",
    )


def _reason(
    entry: BibEntry,
    file_sha256: str,
    stored: IndexedPaper | None,
    settings: ResolvedSources,
    force: bool,
) -> Reason | None:
    if stored is None:
        return Reason.NEW
    if force:
        return Reason.FORCED
    if stored.file_sha256 not in (None, file_sha256):
        return Reason.CHANGED
    if _declared(settings) and stale_backends(stored, settings.ocr_backend.value):
        return Reason.OCR_BACKEND
    return None


def _declared(settings: ResolvedSources) -> bool:
    """Whether a backend was asked for. A default is no request to read a
    stored page again, which costs an OCR read of every scanned page: only
    a declared backend re-reads."""
    return settings.origin("ocr_backend") is not SettingFrom.DEFAULT


def _layers_alone(paper: IndexedPaper) -> bool:
    """Whether every stored page of a work is a digital page kept as its
    layer, so reading it again under the default backend reads no page by
    OCR: only a declared backend reads again a page OCR read, a scan, or
    a page whose kind is not yet known."""
    return paper.text_sources <= {TextSource.NONE} and not (
        paper.scans or paper.unmarked
    )


def _witnessed(settings: ResolvedSources) -> bool:
    """Whether the pages OCR reads are witnessed: under a declared backend
    other than Tesseract, the witness itself."""
    return _declared(settings) and settings.ocr_backend is not OcrBackend.TESSERACT


def _skipped(
    client: QdrantClient,
    settings: ResolvedSources,
    bib_path: Path,
    entry: BibEntry,
    stored: IndexedPaper,
) -> Skipped:
    """Why a work asked for by name is not read again, and whether the
    corpus holds it back: a file replaced by the same bytes, as a reference
    manager may, changes nothing, and a held work stays held."""
    if stored.file_sha256 is None:
        why = "the corpus recorded no hash of its file, so no change shows"
    else:
        why = (
            f"its file is the one ingested (SHA-256 {stored.file_sha256[:12]}): "
            "the same bytes, wherever they lie"
        )
    why += "; `--force` reads it again"
    if entry.label:  # another file of an entry: never held to its title page
        return Skipped(entry.key, why)
    opening = openings(client, settings.collection, FIRST_PAGES, entry.key)
    pages = opening[entry.key].pages if entry.key in opening else {}
    found = check(expected(entry.record), pages)
    held = confirm.hold(
        entry.key, found, confirm.load(bib_path), stored.file_sha256 or ""
    )
    return Skipped(entry.key, why, held)


def _metadata_moved(entry: BibEntry, stored: IndexedPaper) -> bool:
    return stored.bib_sha256 not in (None, entry.payload_hash())


def apply_ingest(
    client: QdrantClient,
    settings: ResolvedSources,
    plan: IngestPlan,
    *,
    api_key: str,
    ocr_key: str = "",
    progress: Progress = lambda _: None,
) -> IngestReport:
    """Carry out a plan. Refuses a collection axiomation did not create.

    `api_key` opens the embedding API and `ocr_key` the OCR service, when
    that service asks for one. The OCR provider and Tesseract are built here,
    before the first page, and Tesseract's binary looked for: it is needed
    under any backend, to confirm a layer that may spell no words.

    A failure of the OCR engine (a service that is down, met at the first
    page that needs it) stops the run, reported in `stopped`: every work
    after it would fail the same way, and the work in hand keeps its stored
    pages. A failure of one work's own (a PDF that does not open, a file
    with no text) is that work's alone.
    """
    if plan.refusal:
        raise SourcesError(plan.refusal)
    if plan.prune_refusal:
        raise SourcesError(plan.prune_refusal)
    if plan.pending:
        ocr = provider_for(settings, api_key=ocr_key)
        tesseract = ocr if isinstance(ocr, TesseractProvider) else tesseract_provider()

    created = ensure_collection(client, settings)
    for bib_key in plan.stale:
        delete_paper(client, settings.collection, bib_key)
        progress(f"[prune] {bib_key}")

    for entry in plan.metadata_only:
        update_payload(client, settings.collection, entry)
        progress(f"[meta] {entry.key}: payload updated, nothing re-embedded")

    moved = 0
    failures: list[tuple[str, str]] = []
    if plan.moves:
        try:
            moved = move_papers(
                client,
                settings.collection,
                [(m.source, m.entry, m.file_sha256) for m in plan.moves],
            )
        except Exception as error:  # noqa: BLE001 - a failed move must not stop the run
            for m in plan.moves:
                why = f"moving {m.source} to {m.entry.key} failed: {error}"
                failures.append((m.entry.key, why))
                progress(f"  ERROR {why}")
        else:
            for m in plan.moves:
                progress(f"[move] {m.source} -> {m.entry.key}: nothing re-embedded")

    settled = relabelled = 0
    for found in plan.readers:
        settle_readers(client, settings.collection, found)
        settled += len(found.records)
        relabelled += len(found.relabelled)
        if found.relabelled:
            progress(
                f"[reader] {found.bib_key}: {len(found.relabelled)} pages "
                "recorded as their layer (`none`), nothing re-embedded"
            )
    figureless = 0
    for tested in plan.untested:
        settle_figures(client, settings.collection, tested)
        figureless += len(tested.plain)
    stamped = 0
    for drawn in plan.layers:
        stamp_layers(client, settings.collection, drawn)
        stamped += len(drawn.alike)

    chunks = 0
    tokens = 0
    read: list[str] = []
    held: list[tuple[str, str]] = []
    stopped = ""
    witnessed: list[WorkWitness] = []
    kept = confirm.load(settings.bib)
    total = len(plan.pending)
    for position, item in enumerate(plan.pending, start=1):
        progress(
            f"[{position}/{total}] ({item.reason.value}) {item.entry.key}: "
            f"{item.entry.title[:60]}"
        )
        try:
            written, used, why, witness = _ingest_entry(
                client,
                settings,
                item,
                api_key=api_key,
                ocr=ocr,
                tesseract=tesseract,
                kept=kept,
            )
        except OcrError as error:
            if _engines_read(ocr, tesseract):
                why = (
                    f"{error} The engine reads a blank page, so the fault is "
                    "this work's; its stored pages are kept."
                    + _emptied(plan, item.entry.key)
                )
                failures.append((item.entry.key, why))
                progress(f"  ERROR {why}")
                continue
            stopped = (
                f"{error} Stopped at {item.entry.key}, {len(read)} of {total} "
                "works read; the rest keep their stored pages. Run the ingest "
                "again once the engine reads."
                + "".join(
                    _emptied(plan, rest.entry.key)
                    for rest in plan.pending[position - 1 :]
                )
            )
            progress(f"  STOPPED {stopped}")
            break
        except Exception as error:  # noqa: BLE001 - one bad PDF must not stop the run
            why = f"{error}{_emptied(plan, item.entry.key)}"
            failures.append((item.entry.key, why))
            progress(f"  ERROR {why}")
            continue
        if why:
            held.append((item.entry.key, why))
            progress(f"  HELD {why}")
        chunks += written
        tokens += used
        read.append(item.entry.key)
        progress(f"  -> {written} chunks, {_tokens_line(settings, used, tokens)}")
        if witness is not None:
            witnessed.append(witness)
            progress(f"  {_witness_line(witness)}")

    return IngestReport(
        plan=plan,
        created=created,
        pruned=plan.stale,
        ingested=len(read),
        chunks=chunks,
        tokens=tokens,
        metadata_updated=len(plan.metadata_only),
        failures=tuple(failures),
        held=tuple(held),
        settled=settled,
        relabelled=relabelled,
        figureless=figureless,
        stamped=stamped,
        stopped=stopped,
        read=tuple(read),
        moved=moved,
        witnessed=tuple(witnessed),
    )


def _engines_read(*engines: OcrProvider) -> bool:
    """Whether every engine still reads, asked with a blank page: a page
    one work's reading fails on while the engines read is that work's
    fault, and the run goes on; an engine that fails the blank page fails
    every work after."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (200, 100), 255).save(buffer, "PNG")
    blank = buffer.getvalue()
    asked: list[OcrProvider] = []
    for engine in engines:
        if any(engine is other for other in asked):  # one engine, asked once
            continue
        asked.append(engine)
        try:
            engine.read(blank)
        except OcrError:
            return False
    return True


def _ingest_entry(
    client: QdrantClient,
    settings: ResolvedSources,
    item: Pending,
    *,
    api_key: str,
    ocr: OcrProvider,
    tesseract: Witness,
    kept: confirm.Kept,
) -> tuple[int, int, str, WorkWitness | None]:
    """One work in: its chunks written, the tokens embedded, why its file
    is held back (empty when it matches its entry or is confirmed), and
    what the OCR's witness did on it.

    A work read again is replaced only once its new pages are read and
    embedded, so a failure on the way leaves its stored pages as they were.
    """
    stored = (
        stored_readings(client, settings.collection, item.entry.key, ocr.name)
        if item.reason in _REREAD
        else None
    )
    pages = extract_pdf(
        item.entry.file_path, ocr=ocr, tesseract=tesseract, stored=stored
    )
    found = check(
        expected(item.entry.record),
        {p.page_index: p.text for p in pages if p.page_index <= FIRST_PAGES},
    )
    # another file of the entry is held as what a person or the choice
    # labelled it, never against the entry's title page
    why = (
        ""
        if item.entry.label
        else confirm.hold(item.entry.key, found, kept, item.file_sha256)
    )
    chunks = chunk_pages(pages, item.entry.key, item.entry.title)
    if not chunks:
        raise SourcesError(f"no text extracted from {item.entry.file_path}")
    texts = [chunk.text for chunk in chunks]
    dense, sparse, tokens = embed_documents(
        texts,
        voyage_model=settings.voyage_model,
        voyage_dims=settings.voyage_dims,
        api_key=api_key,
    )
    if item.reason is not Reason.NEW:
        delete_paper(client, settings.collection, item.entry.key)
    written = upsert_chunks(
        client,
        settings.collection,
        item.entry,
        chunks,
        dense,
        sparse,
        item.file_sha256,
    )
    return written, tokens, why, WorkWitness.of(item.entry.key, pages)


def _witness_line(witness: WorkWitness) -> str:
    parts = []
    if witness.witnessed:
        parts.append(f"{witness.witnessed} pages, {len(witness.reread)} read again")
    if witness.dropped:
        pages = ", ".join(str(page) for page in witness.dropped)
        parts.append(f"a block with no room in its box dropped on pp. {pages}")
    if witness.alone:
        pages = ", ".join(str(page) for page in witness.alone)
        parts.append(f"a block read alone settled pp. {pages}")
    if witness.disputed:
        pages = ", ".join(str(page) for page in witness.disputed)
        parts.append(f"{len(witness.disputed)} disputed (pp. {pages})")
    if witness.figures:
        pages = ", ".join(str(page) for page in witness.figures)
        parts.append(f"figures' text added on pp. {pages}")
    return "witness: " + ", ".join(parts)


def _tokens_line(settings: ResolvedSources, used: int, running: int) -> str:
    line = f"{used / 1000:.1f}k tokens"
    spent = cost(settings.voyage_model, used)
    if spent is None:
        return line
    total = cost(settings.voyage_model, running) or 0.0
    return f"{line} (${spent:.4f}), running total ${total:.4f}"
