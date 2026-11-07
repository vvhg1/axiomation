"""Where a cited source can be read from.

The chain's links: the indexed collection (the library) or the work's PDF
on this machine (the plain tier), the Zotero adapter when the stored path
has gone stale, and the resolvers' open-access candidates for a work no
source holds, fetched as a `transient` source.

The answer says where it stopped, so a verifier records `inconclusive` with
a class rather than guessing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from axiomation.evidence import Origin, ReadStatus, Sources
from axiomation.sources.bib import records
from axiomation.sources.config import Settings, contact, core_api_key
from axiomation.sources.read import zotero
from axiomation.sources.resolvers import Candidate, ResolutionStatus, Resolver


@dataclass(frozen=True, slots=True)
class Resolution:
    """What is known about one bib key, and what to do about it."""

    bib_key: str
    #: whether the library's corpus holds it (never in the plain tier)
    indexed: bool = False
    chunks: int = 0
    title: str = ""
    file_path: str = ""
    file_exists: bool = False
    zotero_pdf: str = ""
    zotero_link: str = ""
    doi: str = ""
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    item_type: str = ""
    creators: tuple[str, ...] = ()
    issued: str = ""
    venue: str = ""
    publisher: str = ""
    canonical_url: str = ""
    resolvers: tuple[str, ...] = ()
    retrieved_at: str = ""
    fingerprint: str = ""
    citation_count: int | None = None
    retracted: bool | None = None
    version: str = ""
    candidates: tuple[Candidate, ...] = ()
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""
    #: why its file is held back, unread: not its entry's work
    held: str = ""
    #: whether its file was checked against its entry: held, or read with
    #: its entry in the bibliography on this machine
    checked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "indexed": self.indexed,
            "chunks": self.chunks,
            "title": self.title,
            "file_path": self.file_path,
            "file_exists": self.file_exists,
            "zotero_pdf": self.zotero_pdf,
            "zotero_link": self.zotero_link,
            "doi": self.doi,
            "resolution_status": self.resolution_status.value,
            "item_type": self.item_type,
            "creators": list(self.creators),
            "issued": self.issued,
            "venue": self.venue,
            "publisher": self.publisher,
            "canonical_url": self.canonical_url,
            "resolvers": list(self.resolvers),
            "retrieved_at": self.retrieved_at,
            "fingerprint": self.fingerprint,
            "citation_count": self.citation_count,
            "retracted": self.retracted,
            "version": self.version,
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "read_status": self.read_status.value,
            "detail": self.detail,
            "held": self.held,
            "checked": self.checked,
        }


def resolve(
    reader: Sources,
    bib_key: str,
    *,
    config: Settings | None = None,
    fresh: bool = False,
    zotero_url: str = zotero.DEFAULT_ZOTERO_URL,
) -> Resolution:
    """Find a source. Never raises: an unreachable corpus is a status."""
    results = reader.page(bib_key, 1)
    held = results.detail if results.read_status is ReadStatus.MISMATCH else ""
    holder = reader.holding(bib_key)[0] if results.pages or held else None
    indexed = holder is not None and holder.origin is Origin.LIBRARY
    available = zotero.available(zotero_url)
    link = zotero.select_link(bib_key, base_url=zotero_url) if available else None
    pdf = zotero.pdf_path(bib_key, base_url=zotero_url) if available else None

    if results.pages:
        page = results.pages[0]
        stored = Path(page.file_path) if page.file_path else None
        local = Resolution(
            bib_key=bib_key,
            indexed=indexed,
            chunks=len(reader.source_pages(bib_key).pages),
            title=page.title,
            file_path=page.file_path,
            file_exists=bool(stored and stored.is_file()),
            zotero_pdf=str(pdf) if pdf else "",
            zotero_link=link or "",
            read_status=results.read_status,
            detail=results.detail,
        )
    else:
        local = Resolution(
            bib_key=bib_key,
            indexed=indexed,
            zotero_pdf=str(pdf) if pdf else "",
            zotero_link=link or "",
            read_status=results.read_status,
            detail=results.detail,
            held=held,
            checked=bool(held),
        )
    if config is None or config.bib is None or config.cache_dir is None:
        return local
    entries = records(config.bib)
    record = entries.get(bib_key)
    if record is None:
        return local
    cache_path = config.cache_dir / "sources.db"
    with Resolver(
        cache_path,
        email=contact(config).address,
        core_key=core_api_key(config),
    ) as resolver:
        work = resolver.resolve(record.doi, bib_key=bib_key, fresh=fresh)
    status = local.read_status
    detail = local.detail
    # a miss, never an outage: a source that failed to say may hold it
    if status is ReadStatus.ABSENT and work.candidates:
        status = ReadStatus.OK
        detail = (
            f"no source holds it; {len(work.candidates)} open-copy candidate(s). "
            f"Run `axiomation sources fetch {bib_key}`."
        )
    return Resolution(
        bib_key=local.bib_key,
        indexed=local.indexed,
        chunks=local.chunks,
        title=work.title or local.title or record.title,
        file_path=local.file_path,
        file_exists=local.file_exists,
        zotero_pdf=local.zotero_pdf,
        zotero_link=local.zotero_link,
        doi=work.doi,
        resolution_status=work.status,
        item_type=work.item_type,
        creators=work.creators,
        issued=work.issued,
        venue=work.venue,
        publisher=work.publisher,
        canonical_url=work.canonical_url,
        resolvers=work.resolvers,
        retrieved_at=work.retrieved_at,
        fingerprint=work.fingerprint,
        citation_count=work.citation_count,
        retracted=work.retracted,
        version=work.version,
        candidates=work.candidates,
        read_status=status,
        detail=detail or work.detail,
        held=local.held,
        checked=local.checked or bool(local.file_path and config.bib.is_file()),
    )
