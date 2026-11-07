"""The ingest's checks read and settled in a Qdrant collection: each
work's stored pages as the reader, figures and layer checks read them
(`ingest/readers.py`, `ingest/figures.py`), and the records they settle
written back as payload updates, the text and the vectors untouched."""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from axiomation.sources import pdf
from axiomation.sources.contract import (
    LAYER_READER_FIELD,
    OCR_DISPUTED_FIELD,
    OCR_FIGURES_FIELD,
    OCR_READING_FIELD,
    PAGE_INDEX_FIELD,
    PAGE_KIND_FIELD,
    TextSource,
    point_id,
)
from axiomation.sources.ingest.extract import StoredReading
from axiomation.sources.ingest.figures import FigureAudit
from axiomation.sources.ingest.readers import (
    LayerAudit,
    ReaderAudit,
    StoredPage,
    outworn,
)
from axiomation.sources.library.qdrant import chunk_filter, scroll_payloads


def unsettled_pages(
    client: QdrantClient, collection: str, bib_key: str
) -> list[StoredPage]:
    """A work's stored pages that carry no kind."""
    fields = [PAGE_INDEX_FIELD, "chunk_index", "ocr_backend", "text", PAGE_KIND_FIELD]
    return [
        StoredPage(
            page_index=int(payload[PAGE_INDEX_FIELD]),
            chunk_index=int(payload["chunk_index"]),
            reader=str(payload.get("ocr_backend") or ""),
            text=str(payload.get("text") or ""),
        )
        for payload in scroll_payloads(
            client, collection, fields, scroll_filter=chunk_filter(bib_key)
        )
        if PAGE_KIND_FIELD not in payload
    ]


def stored_readings(
    client: QdrantClient, collection: str, bib_key: str, reader: str
) -> dict[int, StoredReading]:
    """A work's stored pages one reader gave, by page index, each with the
    witness's judgment where one judged it (a page recorded disputed with
    no version, by the first witness): a re-read keeps a judged page as it
    stands and takes an unjudged one as its first reading. A page an older
    reading left disputed is none: it is read afresh (`upsert.outworn`)."""
    fields = [
        PAGE_INDEX_FIELD,
        "ocr_backend",
        "text",
        OCR_DISPUTED_FIELD,
        OCR_READING_FIELD,
    ]
    return {
        int(payload[PAGE_INDEX_FIELD]): StoredReading(
            str(payload.get("text") or ""),
            int(payload.get(OCR_READING_FIELD, 1))
            if OCR_DISPUTED_FIELD in payload
            else None,
            int(payload.get(OCR_DISPUTED_FIELD) or 0),
        )
        for payload in scroll_payloads(
            client, collection, fields, scroll_filter=chunk_filter(bib_key)
        )
        if payload.get("ocr_backend") == reader
        and payload.get("text")
        and not outworn(payload)
    }


def settle_readers(client: QdrantClient, collection: str, found: ReaderAudit) -> None:
    """Write each settled page's kind, and its reader where it changes; the
    text and the vectors are not touched."""
    groups: dict[tuple[str, str | None], list[int]] = {}
    for record in found.records:
        reader = record.reader.value if record.reader else None
        groups.setdefault((record.kind.value, reader), []).append(
            point_id(found.bib_key, record.chunk_index)
        )
    for (kind, reader), ids in groups.items():
        payload: dict[str, str] = {PAGE_KIND_FIELD: kind}
        if reader is not None:
            payload["ocr_backend"] = reader
        client.set_payload(
            collection_name=collection,
            payload=payload,
            points=models.PointIdsList(points=ids),
        )


def unstamped_layers(
    client: QdrantClient, collection: str, bib_key: str
) -> list[StoredPage]:
    """A work's stored layer pages of digital pages an older layer reader
    wrote, or one that recorded no version. A scan's page stored as its
    layer is old OCR, read again by OCR (`audit`), never as a layer."""
    fields = [
        PAGE_INDEX_FIELD,
        "chunk_index",
        "ocr_backend",
        "text",
        PAGE_KIND_FIELD,
        LAYER_READER_FIELD,
    ]
    return [
        StoredPage(
            page_index=int(payload[PAGE_INDEX_FIELD]),
            chunk_index=int(payload["chunk_index"]),
            reader=TextSource.NONE.value,
            text=str(payload.get("text") or ""),
        )
        for payload in scroll_payloads(
            client, collection, fields, scroll_filter=chunk_filter(bib_key)
        )
        if payload.get("ocr_backend") == TextSource.NONE
        and payload.get(PAGE_KIND_FIELD) == pdf.PageKind.DIGITAL
        and payload.get(LAYER_READER_FIELD) != pdf.LAYER_READER
    ]


def stamp_layers(client: QdrantClient, collection: str, found: LayerAudit) -> None:
    """Record today's layer reader on the pages it reads alike; the text and
    the vectors are not touched."""
    if not found.alike:
        return
    client.set_payload(
        collection_name=collection,
        payload={LAYER_READER_FIELD: pdf.LAYER_READER},
        points=models.PointIdsList(
            points=[point_id(found.bib_key, chunk) for chunk in found.alike]
        ),
    )


def untested_pages(
    client: QdrantClient, collection: str, bib_key: str
) -> list[tuple[int, int]]:
    """A work's stored layer pages without the figures' record, as page and
    chunk indices."""
    fields = [PAGE_INDEX_FIELD, "chunk_index", "ocr_backend", OCR_FIGURES_FIELD]
    return [
        (int(payload[PAGE_INDEX_FIELD]), int(payload["chunk_index"]))
        for payload in scroll_payloads(
            client, collection, fields, scroll_filter=chunk_filter(bib_key)
        )
        if payload.get("ocr_backend") == TextSource.NONE
        and OCR_FIGURES_FIELD not in payload
    ]


def settle_figures(client: QdrantClient, collection: str, found: FigureAudit) -> None:
    """Record the pages without an image as tested, finding no figure; the
    text and the vectors are not touched."""
    if not found.plain:
        return
    client.set_payload(
        collection_name=collection,
        payload={OCR_FIGURES_FIELD: 0},
        points=models.PointIdsList(
            points=[point_id(found.bib_key, chunk) for chunk in found.plain]
        ),
    )
