"""Writing a collection: the creator guard, the points, the prune.

axiomation never writes to a collection it did not create. A library corpus
another tool built may serve many repositories, and a stray
`--force` here would cost a day of re-embedding. So every collection
axiomation builds carries a marker point naming the creator and the vector
space, and `guard` refuses anything else before a single point is written."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qdrant_client import QdrantClient, models

from axiomation.sources import pdf
from axiomation.sources.config import ResolvedSources
from axiomation.sources.contract import (
    CHUNK_SCHEME_FIELD,
    LAYER_READER_FIELD,
    MARKER_FIELD,
    MARKER_ID,
    MARKER_VALUE,
    OCR_DISPUTED_FIELD,
    OCR_FIGURES_FIELD,
    OCR_READING_FIELD,
    PAGE_INDEX_FIELD,
    PAGE_KIND_FIELD,
    ChunkScheme,
    TextSource,
    is_contextualized,
    point_id,
    vector_space_hash,
)
from axiomation.sources.ingest import witness
from axiomation.sources.ingest.bib import BibEntry
from axiomation.sources.ingest.chunk import Chunk
from axiomation.sources.ingest.readers import outworn
from axiomation.sources.library.embed import CONTEXT_WINDOW
from axiomation.sources.library.qdrant import (
    Marker,
    chunk_filter,
    collection_names,
    read_marker,
    scroll_payloads,
)

CREATOR = "axiomation"
#: The readers no witness holds: a layer, a page that read nothing, and
#: Tesseract, the witness itself.
_UNWITNESSED_SOURCES = frozenset(
    {None, "", TextSource.NONE, TextSource.FAILED, TextSource.TESSERACT}
)
UPSERT_BATCH = 100


@dataclass(frozen=True, slots=True)
class IndexedPaper:
    """What the collection already holds about one bib key."""

    bib_key: str
    title: str = ""
    file_sha256: str | None = None
    bib_sha256: str | None = None
    text_sources: frozenset[str] = field(default_factory=frozenset)
    chunks: int = 0
    #: chunks written without a page kind, whose reader is not yet checked
    unmarked: int = 0
    #: chunks an OCR service read that today's witness must hold: never
    #: held, or left disputed by an older reading (`witness.OCR_READING`)
    unwitnessed: int = 0
    #: chunks kept as their page's layer never tested for figures
    untested: int = 0
    #: chunks of digital pages kept as their layer by an older layer reader,
    #: or by one that recorded none (`contract.LAYER_READER_FIELD`)
    unstamped: int = 0
    #: chunks whose page is a scan
    scans: int = 0


def guard(client: QdrantClient, settings: ResolvedSources) -> str:
    """Why this collection may not be written to, or an empty string.

    A collection that does not exist yet is ours to create. One that carries
    our marker is ours to update, unless the vector space has changed under
    it — a different model or dimension count invalidates every vector
    already stored, so that is a new collection, not an update.
    """
    if settings.collection not in collection_names(client):
        return ""
    marker = read_marker(client, settings.collection)
    if marker is None:
        return (
            f"{settings.collection} carries no axiomation marker, so axiomation "
            f"did not create it and will not write to it. Name a collection of "
            f"this paper's own in [sources].collection."
        )
    if marker.creator != CREATOR:
        return (
            f"{settings.collection} was created by {marker.creator!r}, "
            f"not by axiomation, and will not be written to."
        )
    if not marker.matches(settings):
        return (
            f"{settings.collection} holds {marker.voyage_model} vectors of "
            f"{marker.voyage_dims} dimensions; this paper asks for "
            f"{settings.voyage_model} of {settings.voyage_dims}. Re-embedding "
            f"under a new model is a new collection, not an update."
        )
    return ""


def ensure_collection(client: QdrantClient, settings: ResolvedSources) -> bool:
    """Create the collection and stamp it, if it is not there yet."""
    if settings.collection in collection_names(client):
        write_marker(client, settings)
        return False
    client.create_collection(
        collection_name=settings.collection,
        vectors_config=models.VectorParams(
            size=settings.voyage_dims, distance=models.Distance.COSINE
        ),
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    write_marker(client, settings)
    return True


def write_marker(client: QdrantClient, settings: ResolvedSources) -> Marker:
    """Stamp the collection with the creator and the vector space.

    A contextualized model's marker carries the window its vectors were
    computed over; a plain model's carries no such field.
    """
    marker = Marker(
        creator=CREATOR,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        vector_space=vector_space_hash(settings.voyage_model, settings.voyage_dims),
        voyage_model=settings.voyage_model,
        voyage_dims=settings.voyage_dims,
        context_window=(
            CONTEXT_WINDOW if is_contextualized(settings.voyage_model) else None
        ),
    )
    stamp = {key: value for key, value in marker.as_dict().items() if value is not None}
    client.upsert(
        collection_name=settings.collection,
        points=[
            models.PointStruct(
                id=MARKER_ID,
                vector={"": [0.0] * settings.voyage_dims},
                payload={MARKER_FIELD: MARKER_VALUE, **stamp},
            )
        ],
    )
    return marker


def indexed_papers(client: QdrantClient, collection: str) -> dict[str, IndexedPaper]:
    """One scroll answering every question the ingest planner asks.

    A point another tool wrote may carry no `file_sha256`; its value is
    None, meaning provenance unknown, which the planner treats as
    unchanged. `--force` is how those get re-ingested.
    """
    papers: dict[str, IndexedPaper] = {}
    fields = [
        "bib_key",
        "title",
        "file_sha256",
        "bib_sha256",
        "ocr_backend",
        PAGE_KIND_FIELD,
        OCR_DISPUTED_FIELD,
        OCR_READING_FIELD,
        OCR_FIGURES_FIELD,
        LAYER_READER_FIELD,
    ]
    for payload in scroll_payloads(client, collection, fields):
        key = payload.get("bib_key")
        if not key:
            continue
        known = papers.get(key)
        source = payload.get("ocr_backend")
        unmarked = int(PAGE_KIND_FIELD not in payload)
        unwitnessed = int(
            source not in _UNWITNESSED_SOURCES
            and (OCR_DISPUTED_FIELD not in payload or outworn(payload))
        )
        untested = int(source == TextSource.NONE and OCR_FIGURES_FIELD not in payload)
        unstamped = int(
            source == TextSource.NONE
            and payload.get(PAGE_KIND_FIELD) == pdf.PageKind.DIGITAL
            and payload.get(LAYER_READER_FIELD) != pdf.LAYER_READER
        )
        scans = int(payload.get(PAGE_KIND_FIELD) == pdf.PageKind.SCAN)
        if known is None:
            papers[key] = IndexedPaper(
                bib_key=key,
                title=payload.get("title", ""),
                file_sha256=payload.get("file_sha256"),
                bib_sha256=payload.get("bib_sha256"),
                text_sources=frozenset({source} if source else set()),
                chunks=1,
                unmarked=unmarked,
                unwitnessed=unwitnessed,
                untested=untested,
                unstamped=unstamped,
                scans=scans,
            )
            continue
        papers[key] = IndexedPaper(
            bib_key=key,
            title=known.title,
            file_sha256=known.file_sha256,
            bib_sha256=known.bib_sha256,
            text_sources=known.text_sources | ({source} if source else frozenset()),
            chunks=known.chunks + 1,
            unmarked=known.unmarked + unmarked,
            unwitnessed=known.unwitnessed + unwitnessed,
            untested=known.untested + untested,
            unstamped=known.unstamped + unstamped,
            scans=known.scans + scans,
        )
    return papers


def stale_backends(paper: IndexedPaper, backend: str) -> frozenset[str]:
    """Stored text sources that a re-ingest under `backend` would replace.

    `none` is a text layer, which no backend switch affects. `failed` is a
    page this backend already gave up on, so re-running it would only fail
    again; `--force` is the way to retry one of those.
    """
    return paper.text_sources - {TextSource.NONE, TextSource.FAILED, backend}


def upsert_chunks(
    client: QdrantClient,
    collection: str,
    entry: BibEntry,
    chunks: Sequence[Chunk],
    dense_vectors: Sequence[Sequence[float]],
    sparse_vectors: Sequence[dict[str, list]],
    file_sha256: str,
) -> int:
    """Write one paper's chunks. Returns how many points were written."""
    bib_sha256 = entry.payload_hash()
    points = [
        models.PointStruct(
            id=point_id(chunk.bib_key, chunk.chunk_index),
            vector={
                "": list(dense),
                "sparse": models.SparseVector(
                    indices=sparse["indices"], values=sparse["values"]
                ),
            },
            payload={
                "bib_key": chunk.bib_key,
                "title": chunk.title,
                "author": entry.author,
                "doi": entry.doi,
                PAGE_INDEX_FIELD: chunk.page_index,
                CHUNK_SCHEME_FIELD: ChunkScheme.PAGE.value,
                "chunk_index": chunk.chunk_index,
                "file_path": str(entry.file_path),
                "text": chunk.text,
                "file_sha256": file_sha256,
                "bib_sha256": bib_sha256,
                "ocr_backend": chunk.source.value,
                PAGE_KIND_FIELD: chunk.kind.value,
                **(
                    {}
                    if chunk.disputed is None
                    else {
                        OCR_DISPUTED_FIELD: chunk.disputed,
                        OCR_READING_FIELD: witness.OCR_READING
                        if chunk.kept is None
                        else chunk.kept,
                    }
                ),
                **({} if chunk.figures is None else {OCR_FIGURES_FIELD: chunk.figures}),
                **(
                    {LAYER_READER_FIELD: pdf.LAYER_READER}
                    if chunk.source is TextSource.NONE
                    else {}
                ),
            },
        )
        for chunk, dense, sparse in zip(
            chunks, dense_vectors, sparse_vectors, strict=True
        )
    ]
    for start in range(0, len(points), UPSERT_BATCH):
        client.upsert(
            collection_name=collection, points=points[start : start + UPSERT_BATCH]
        )
    return len(points)


def update_payload(client: QdrantClient, collection: str, entry: BibEntry) -> None:
    """The metadata-only channel: bib fields moved, the PDF did not.

    Title, author and doi live in the payload alone. The chunk text and its
    vectors depend on the PDF, so nothing here needs re-embedding.
    """
    client.set_payload(
        collection_name=collection,
        payload={
            "title": entry.title,
            "author": entry.author,
            "doi": entry.doi,
            "bib_sha256": entry.payload_hash(),
        },
        points=models.FilterSelector(filter=chunk_filter(entry.key)),
    )


def delete_paper(client: QdrantClient, collection: str, bib_key: str) -> None:
    """Remove every chunk of one paper."""
    client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(filter=chunk_filter(bib_key)),
    )


def move_papers(
    client: QdrantClient,
    collection: str,
    moves: Sequence[tuple[str, BibEntry, str]],
) -> int:
    """Each file's points moved from the key the corpus holds it under to
    its entry's key (`entry.key`), by the file's hash: read with their
    vectors and written under the new key, its entry's metadata with them.
    Nothing is embedded: a page's text and vectors are its file's, whatever
    key holds it.

    A move to a key no move takes from is written first, and only then are
    the target's other file and the source's copy cleared, so a failure
    leaves the file where it was. Where two files trade keys, both are read
    before either is cleared: those points are held in memory between the
    clearing and the writing. Returns how many points were written."""
    held = [
        (source, entry, sha, _points(client, collection, source))
        for source, entry, sha in moves
    ]
    sources = {source for source, *_ in held}
    written = 0
    for source, entry, sha, points in held:
        if entry.key in sources:
            continue
        written += _write(client, collection, entry, points)
        _clear(client, collection, entry.key, keep=sha)
        _clear(client, collection, source, only=sha)
    traded = [m for m in held if m[1].key in sources]
    for key in {k for source, entry, *_ in traded for k in (source, entry.key)}:
        delete_paper(client, collection, key)
    for _, entry, _, points in traded:
        written += _write(client, collection, entry, points)
    return written


def _write(
    client: QdrantClient, collection: str, entry: BibEntry, points: Sequence[Any]
) -> int:
    """Points written under `entry`'s key, its metadata theirs."""
    moved = [
        models.PointStruct(
            id=point_id(entry.key, point.payload["chunk_index"]),
            vector=point.vector,
            payload={
                **point.payload,
                "bib_key": entry.key,
                "file_path": str(entry.file_path),
                "title": entry.title,
                "author": entry.author,
                "doi": entry.doi,
                "bib_sha256": entry.payload_hash(),
            },
        )
        for point in points
    ]
    for start in range(0, len(moved), UPSERT_BATCH):
        client.upsert(
            collection_name=collection, points=moved[start : start + UPSERT_BATCH]
        )
    return len(moved)


def _clear(
    client: QdrantClient,
    collection: str,
    bib_key: str,
    *,
    keep: str = "",
    only: str = "",
) -> None:
    """Remove one key's points of every file but `keep`, or of `only`."""
    by_file = models.FieldCondition(
        key="file_sha256", match=models.MatchValue(value=keep or only)
    )
    query = chunk_filter(bib_key)
    if keep:
        query = models.Filter(must=query.must, must_not=[by_file])
    else:
        query = models.Filter(must=[*(query.must or []), by_file])
    client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(filter=query),
    )


def _points(client: QdrantClient, collection: str, bib_key: str) -> list[Any]:
    """Every point of one paper, with its payload and its vectors."""
    found: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            scroll_filter=chunk_filter(bib_key),
            limit=UPSERT_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        found += batch
        if offset is None:
            return found
