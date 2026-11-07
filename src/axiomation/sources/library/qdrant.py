"""Qdrant plumbing both halves share: connect, filter, scroll, marker.

The read and the ingest halves never import each other (architecture section
3.6), but they talk to the same server about the same collection, so the
connection, the filters and the marker point live here, below both. Nothing
in this file writes: `read_marker` reads it, `ingest.upsert` writes it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from axiomation.sources.config import ResolvedSources, SourcesError
from axiomation.sources.contract import (
    MARKER_FIELD,
    MARKER_ID,
    MARKER_VALUE,
    PAGE_INDEX_FIELD,
    vector_space_hash,
)
from axiomation.sources.match import Opening

SCROLL_BATCH = 1000


@dataclass(frozen=True, slots=True)
class Marker:
    """The creator stamp on a collection axiomation built.

    `context_window` is how many neighbouring pages each vector was computed
    over, for a collection embedded with a contextualized model. It
    is a property of how the vectors were made that the collection's name
    does not carry — the name is the vector space, which is model and
    dimensions — so somebody rebuilding the collection reads it here. `None`
    for a plain model.
    """

    creator: str
    created_at: str
    vector_space: str
    voyage_model: str
    voyage_dims: int
    context_window: int | None = None

    def matches(self, settings: ResolvedSources) -> bool:
        return self.vector_space == vector_space_hash(
            settings.voyage_model, settings.voyage_dims
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "creator": self.creator,
            "created_at": self.created_at,
            "vector_space": self.vector_space,
            "voyage_model": self.voyage_model,
            "voyage_dims": self.voyage_dims,
            "context_window": self.context_window,
        }


def connect(settings: ResolvedSources) -> QdrantClient:
    """Open a client. Reaching the server is a later call's problem."""
    try:
        # The client's own version check warns on stderr whenever the minor
        # versions differ, which an agent reading our output does not need to
        # see. The payload contract is what actually has to hold, and
        # `check_contract` is what checks it.
        return QdrantClient(url=settings.qdrant_url, check_compatibility=False)
    except Exception as error:  # noqa: BLE001 - the client's failures are its own
        raise SourcesError(
            f"cannot open a Qdrant client for {settings.qdrant_url}: {error}"
        ) from error


def collection_names(client: QdrantClient) -> set[str]:
    return {item.name for item in client.get_collections().collections}


def vector_size(info: Any) -> int | None:
    """The dense vector size a collection was created with.

    Takes the collection info rather than fetching it: the contract check
    already has it in hand, and asking twice is one round trip too many.
    """
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        unnamed = vectors.get("")
        return unnamed.size if unnamed is not None else None
    return vectors.size if vectors is not None else None


def not_the_marker() -> models.Condition:
    """Exclude the creator stamp; it is not a chunk of anything."""
    return models.FieldCondition(
        key=MARKER_FIELD, match=models.MatchValue(value=MARKER_VALUE)
    )


def chunk_filter(
    bib_key: str | None = None, page_range: tuple[int, int] | None = None
) -> models.Filter:
    """A filter over real chunks: never the marker, optionally one paper."""
    must: list[models.Condition] = []
    if bib_key:
        must.append(
            models.FieldCondition(key="bib_key", match=models.MatchValue(value=bib_key))
        )
    if page_range is not None:
        start, end = page_range
        if start == end:
            must.append(
                models.FieldCondition(
                    key=PAGE_INDEX_FIELD, match=models.MatchValue(value=start)
                )
            )
        else:
            must.append(
                models.FieldCondition(
                    key=PAGE_INDEX_FIELD, range=models.Range(gte=start, lte=end)
                )
            )
    return models.Filter(must=must, must_not=[not_the_marker()])


def scroll_payloads(
    client: QdrantClient,
    collection: str,
    fields: Sequence[str] | bool,
    *,
    scroll_filter: models.Filter | None = None,
    limit: int = SCROLL_BATCH,
) -> Iterator[dict[str, Any]]:
    """Walk every matching point's payload, a page of results at a time."""
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter or chunk_filter(),
            limit=limit,
            offset=offset,
            with_payload=list(fields) if isinstance(fields, Sequence) else fields,
            with_vectors=False,
        )
        for point in points:
            yield point.payload or {}
        if offset is None:
            return


def openings(
    client: QdrantClient, collection: str, last: int, bib_key: str | None = None
) -> dict[str, Opening]:
    """Each work's pages 1 to `last` and its file's hash (of one work alone,
    where named): what the file check reads. One scroll, nothing embedded."""
    found: dict[str, Opening] = {}
    for payload in scroll_payloads(
        client,
        collection,
        ["bib_key", PAGE_INDEX_FIELD, "text", "file_sha256"],
        scroll_filter=chunk_filter(bib_key, page_range=(1, last)),
    ):
        key = payload.get("bib_key")
        if not key:
            continue
        opening = found.setdefault(key, Opening(payload.get("file_sha256") or ""))
        opening.pages[int(payload[PAGE_INDEX_FIELD])] = payload.get("text", "")
    return found


def read_marker(client: QdrantClient, collection: str) -> Marker | None:
    """The creator stamp, or None for a collection axiomation did not build."""
    points = client.retrieve(
        collection_name=collection,
        ids=[MARKER_ID],
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None
    payload = points[0].payload or {}
    if payload.get(MARKER_FIELD) != MARKER_VALUE:
        return None
    return Marker(
        creator=payload.get("creator", ""),
        created_at=payload.get("created_at", ""),
        vector_space=payload.get("vector_space", ""),
        voyage_model=payload.get("voyage_model", ""),
        voyage_dims=payload.get("voyage_dims", 0),
        context_window=payload.get("context_window"),
    )
