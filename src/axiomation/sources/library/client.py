"""Reading a source collection: search, pages, status.

Hybrid search, as the contract's collections are built for: dense and
sparse prefetches over three times the requested depth, fused server-side
by RRF. What is new is that nothing here raises for a source that cannot
be read. Qdrant being down, a bib key that is not in the collection and a
page whose text is unusable are all answers with a `read_status`, because
an agent that gets an exception back has nothing to record, and an agent
with nothing to record falls back on what it remembers.

The contract check runs once per reader: one point, nine payload fields and
the vector size against the model. A collection that does not match is a
loud error naming the collection, not a wrong answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from axiomation.evidence import (
    Page,
    ReadStatus,
    Results,
    classify_text,
    detail_for,
)
from axiomation.evidence import collected as _collected
from axiomation.sources.config import ResolvedSources, SourcesError
from axiomation.sources.contract import (
    CHUNK_SCHEME_FIELD,
    OCR_DISPUTED_FIELD,
    PAGE_INDEX_FIELD,
    READ_FIELDS,
    ChunkScheme,
    is_contextualized,
    missing_payload_fields,
    missing_provenance_fields,
)
from axiomation.sources.library import vectors
from axiomation.sources.library.qdrant import (
    Marker,
    chunk_filter,
    collection_names,
    connect,
    openings,
    read_marker,
    scroll_payloads,
    vector_size,
)
from axiomation.sources.match import Opening

PREFETCH_FACTOR = 3


@dataclass(frozen=True, slots=True)
class Paper:
    """One indexed source, as `status` lists it: its chunks, and how many
    of them each text source read."""

    bib_key: str
    title: str
    chunks: int
    read_by: tuple[tuple[str, int], ...] = ()

    @property
    def text_sources(self) -> tuple[str, ...]:
        return tuple(source for source, _ in self.read_by)


@dataclass(frozen=True, slots=True)
class ContractCheck:
    """What the collection turned out to be."""

    collection: str
    ok: bool
    points: int = 0
    #: False where the collection does not exist yet: a new paper's state
    #: until its first ingest, which builds it; not a broken contract.
    built: bool = True
    vector_size: int | None = None
    missing_fields: tuple[str, ...] = ()
    legacy_fields: tuple[str, ...] = ()
    marker: Marker | None = None
    problem: str = ""
    #: What the sampled point says a chunk is; None for a collection whose
    #: chunks declare no scheme and carry their neighbours' edges.
    chunk_scheme: ChunkScheme | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "ok": self.ok,
            "built": self.built,
            "points": self.points,
            "vector_size": self.vector_size,
            "missing_fields": list(self.missing_fields),
            "legacy_fields": list(self.legacy_fields),
            "marker": self.marker.as_dict() if self.marker else None,
            "problem": self.problem,
            "chunk_scheme": self.chunk_scheme.value if self.chunk_scheme else None,
        }


@dataclass
class SourceReader:
    """A read-only view of one collection.

    Built lazily: constructing it costs nothing, and the first call that
    needs Qdrant is the one that pays for the connection and the contract
    check. `axiomation sources status` is therefore the cheap command it
    ought to be, and an agent that never asks a source question never loads
    a vector model.
    """

    settings: ResolvedSources
    # Never in the repr: a reader shows up in pytest headers, in tracebacks
    # and in log lines, and a secret that reaches any of those is spent.
    api_key: str = field(default="", repr=False)
    _client: QdrantClient | None = field(default=None, repr=False)
    _checked: bool = field(default=False, repr=False)
    _scheme: ChunkScheme | None = field(default=None, repr=False)
    _has_points: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SourceReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = connect(self.settings)
        return self._client

    # --- The contract ---

    def check_contract(self) -> ContractCheck:
        """One point, nine fields and the vector size. Never raises."""
        collection = self.settings.collection
        try:
            if collection not in collection_names(self.client):
                return ContractCheck(
                    collection,
                    ok=False,
                    built=False,
                    problem=f"no collection {collection!r} at "
                    f"{self.settings.qdrant_url}",
                )
            info = self.client.get_collection(collection)
            size = vector_size(info)
            marker = read_marker(self.client, collection)
            points = getattr(info, "points_count", 0) or 0
        except SourcesError as error:
            return ContractCheck(collection, ok=False, problem=str(error))
        except Exception as error:  # noqa: BLE001 - the client's failures are its own
            return ContractCheck(
                collection,
                ok=False,
                problem=f"cannot reach Qdrant at {self.settings.qdrant_url}: {error}",
            )

        if size is not None and size != self.settings.voyage_dims:
            return ContractCheck(
                collection,
                ok=False,
                points=points,
                vector_size=size,
                marker=marker,
                problem=f"{collection} holds vectors of {size} dimensions; "
                f"{self.settings.voyage_model} produces "
                f"{self.settings.voyage_dims}. Queries against it would be "
                f"meaningless.",
            )

        sample = next(iter(self._sample()), None)
        if sample is None:
            # Empty, or axiomation's marker alone: a first ingest stopped
            # before its first work, which the next one fills.
            empty = points == 0 or (marker is not None and points == 1)
            return ContractCheck(
                collection,
                ok=empty,
                # no chunk to read: the marker is no page
                points=0 if empty else points,
                vector_size=size,
                marker=marker,
                problem=""
                if empty
                else f"{collection} returned no point to check the payload against",
            )
        missing = missing_payload_fields(sample)
        return ContractCheck(
            collection,
            ok=not missing,
            points=points,
            vector_size=size,
            marker=marker,
            missing_fields=missing,
            legacy_fields=missing_provenance_fields(sample),
            chunk_scheme=_scheme(sample),
            problem=""
            if not missing
            else f"{collection} points are missing {', '.join(missing)}; "
            f"a readable point carries {', '.join(READ_FIELDS)}",
        )

    def _sample(self) -> Sequence[dict[str, Any]]:
        points, _ = self.client.scroll(
            collection_name=self.settings.collection,
            scroll_filter=chunk_filter(),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        return [point.payload or {} for point in points]

    def _ensure_contract(self) -> None:
        if self._checked:
            return
        check = self.check_contract()
        if not check.ok:
            raise SourcesError(check.problem)
        self._checked = True
        self._scheme = check.chunk_scheme
        self._has_points = bool(check.points)

    def _ensure_readable(self) -> None:
        """A text read needs the contract and a collection that holds pages.

        A collection whose chunks declare no scheme holds the neighbours'
        scheme: each chunk is a page plus 500 characters of each neighbour,
        and its vectors were built over all of it. Serving that as a page cites the
        wrong page; trimming it serves text the vectors never matched. So it
        is refused, naming the fix, the way `ingest` refuses a collection
        axiomation did not create. `status` still describes it. An overlapped
        collection is re-ingested: `ingest` writes pages.
        """
        self._ensure_contract()
        if self._has_points and self._scheme is None:
            raise SourcesError(
                f"{self.settings.collection} stores each page with its "
                "neighbours' text joined on, and a read "
                "from it would serve three pages as one. Rebuild it as pages "
                "with `axiomation sources ingest --force`, or point "
                "[sources].collection at a collection whose chunks declare "
                "`chunk_scheme = page`."
            )

    # --- Reading ---

    def search(
        self, query: str, *, bib_key: str | None = None, top_k: int = 5
    ) -> Results:
        """Hybrid search, RRF-fused. Failures come back as a status."""
        try:
            self._ensure_readable()
            dense, sparse = self.embed_query(query)
            points = self.client.query_points(
                collection_name=self.settings.collection,
                prefetch=[
                    models.Prefetch(
                        query=dense,
                        limit=top_k * PREFETCH_FACTOR,
                        filter=chunk_filter(bib_key),
                    ),
                    models.Prefetch(
                        query=models.SparseVector(
                            indices=sparse["indices"], values=sparse["values"]
                        ),
                        using="sparse",
                        limit=top_k * PREFETCH_FACTOR,
                        filter=chunk_filter(bib_key),
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=top_k * PREFETCH_FACTOR,
            ).points
        except SourcesError as error:
            return Results.failed(ReadStatus.INFRASTRUCTURE, str(error))
        except Exception as error:  # noqa: BLE001 - the client's failures are its own
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"search against {self.settings.collection} failed: {error}",
            )
        pages = _ranked(
            _page(point.payload or {}, score=point.score) for point in points
        )[:top_k]
        if bib_key and not pages:
            return Results.failed(ReadStatus.INFRASTRUCTURE, self._absent(bib_key))
        return Results(pages=pages)

    def embed_query(self, text: str) -> tuple[list[float], dict[str, list]]:
        """One query, in the collection's own vector space.

        A contextualized model (`voyage-context-*`) has no plain `embed`: a
        query is one document of one chunk through `contextualized_embed`,
        and the vector comes back nested one level deeper. Both paths give
        one dense vector in the collection's dimensions.
        """
        client = vectors.voyage(self.api_key or _require_key())
        if is_contextualized(self.settings.voyage_model):
            result = client.contextualized_embed(
                inputs=[[text]],
                model=self.settings.voyage_model,
                input_type="query",
                output_dimension=self.settings.voyage_dims,
            )
            dense = result.results[0].embeddings[0]
        else:
            result = client.embed(
                [text],
                model=self.settings.voyage_model,
                input_type="query",
                output_dimension=self.settings.voyage_dims,
            )
            dense = result.embeddings[0]
        return dense, vectors.sparse_vectors([text])[0]

    def page(self, bib_key: str, page_index: int) -> Results:
        """One page's stored text."""
        return self.pages(bib_key, page_index, page_index)

    def pages(self, bib_key: str, start: int, end: int) -> Results:
        """A page range, inclusive, in page order."""
        if start < 1:
            return Results.failed(ReadStatus.INFRASTRUCTURE, "page numbers start at 1")
        if end < start:
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"page range {start}-{end} ends before it begins",
            )
        try:
            self._ensure_readable()
            points, _ = self.client.scroll(
                collection_name=self.settings.collection,
                scroll_filter=chunk_filter(bib_key, page_range=(start, end)),
                limit=end - start + 1,
                with_payload=True,
                with_vectors=False,
            )
        except SourcesError as error:
            return Results.failed(ReadStatus.INFRASTRUCTURE, str(error))
        except Exception as error:  # noqa: BLE001 - the client's failures are its own
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"reading {bib_key} from {self.settings.collection} failed: {error}",
            )
        if not points:
            return Results.failed(
                ReadStatus.INFRASTRUCTURE, self._missing(bib_key, start, end)
            )
        return _collected(_page(point.payload or {}) for point in points)

    def source_pages(self, bib_key: str) -> Results:
        """Every stored page of one source, in page order.

        What `locate` and `grep` run over: a string match needs the
        whole text and a scroll gives it in a fraction of a second, nothing
        embedded and nothing charged. The result's `read_status` is the worst
        page's, so a caller knows a page under the text floor is in there;
        `text.text_quality` says which ones.
        """
        try:
            self._ensure_readable()
            payloads = list(
                scroll_payloads(
                    self.client,
                    self.settings.collection,
                    SOURCE_FIELDS,
                    scroll_filter=chunk_filter(bib_key),
                )
            )
        except SourcesError as error:
            return Results.failed(ReadStatus.INFRASTRUCTURE, str(error))
        except Exception as error:  # noqa: BLE001 - the client's failures are its own
            return Results.failed(
                ReadStatus.INFRASTRUCTURE,
                f"reading {bib_key} from {self.settings.collection} failed: {error}",
            )
        if not payloads:
            return Results.failed(ReadStatus.INFRASTRUCTURE, self._absent(bib_key))
        return _collected(_page(payload) for payload in payloads)

    def papers(self) -> tuple[Paper, ...]:
        """Every indexed source, with its chunk count. Raises on a bad read."""
        self._ensure_contract()
        counts: dict[str, Paper] = {}
        for payload in scroll_payloads(
            self.client,
            self.settings.collection,
            ["bib_key", "title", "ocr_backend"],
        ):
            key = payload.get("bib_key")
            if not key:
                continue
            known = counts.get(key)
            source = payload.get("ocr_backend") or ""
            read_by = Counter(dict(known.read_by) if known else {})
            if source:
                read_by[source] += 1
            counts[key] = Paper(
                bib_key=key,
                title=known.title if known else payload.get("title", ""),
                chunks=(known.chunks if known else 0) + 1,
                read_by=tuple(sorted(read_by.items())),
            )
        return tuple(counts[key] for key in sorted(counts))

    def chunk_count(self, bib_key: str) -> int:
        """How many chunks one paper has, counted server-side."""
        return self.client.count(
            collection_name=self.settings.collection,
            count_filter=chunk_filter(bib_key),
            exact=True,
        ).count

    def openings(self, last: int, bib_key: str | None = None) -> dict[str, Opening]:
        """Each work's pages 1 to `last` and its file's hash (of one work
        alone, where named): what the file check reads. Raises on a bad
        read."""
        self._ensure_readable()
        return openings(self.client, self.settings.collection, last, bib_key)

    def has_key(self, bib_key: str) -> bool:
        points, _ = self.client.scroll(
            collection_name=self.settings.collection,
            scroll_filter=chunk_filter(bib_key),
            limit=1,
            with_payload=["bib_key"],
            with_vectors=False,
        )
        return bool(points)

    def _absent(self, bib_key: str) -> str:
        return (
            f"{bib_key!r} is not in {self.settings.collection}. Add it to the "
            f"bibliography and run `axiomation sources ingest`, or resolve it "
            f"with `axiomation sources resolve {bib_key}`."
        )

    def _missing(self, bib_key: str, start: int, end: int) -> str:
        if not self.has_key(bib_key):
            return self._absent(bib_key)
        span = f"page {start}" if start == end else f"pages {start}-{end}"
        return f"{bib_key} has no {span} in {self.settings.collection}"


#: What a whole-source read asks for: the read fields plus where each page's
#: text came from and what its OCR's witness left disputed. Never the vectors.
SOURCE_FIELDS: tuple[str, ...] = (
    "bib_key",
    "title",
    PAGE_INDEX_FIELD,
    "file_path",
    "text",
    "ocr_backend",
    OCR_DISPUTED_FIELD,
)


def _require_key() -> str:
    raise SourcesError(
        "no Voyage API key, so a query cannot be embedded. See "
        "`axiomation sources status`."
    )


def _ranked(pages: Iterable[Page]) -> tuple[Page, ...]:
    """Fused results in a deterministic order.

    RRF gives tied scores freely — two prefetches that rank the same two
    chunks first and second produce one score between them — and Qdrant
    breaks those ties by whatever its segments happen to return. A verifier
    records the passage it read as evidence, so the same query has to come
    back the same way tomorrow. Sorting the tie by key and page costs
    nothing and makes it so.

    The server still cuts its own list before we see it, so a tie that
    straddles that cut is beyond reach; `search` over-fetches by the
    prefetch factor to push the cut well past what it returns.
    """
    return tuple(
        sorted(
            pages,
            key=lambda page: (-(page.score or 0.0), page.bib_key, page.page_index),
        )
    )


def _scheme(payload: dict[str, Any]) -> ChunkScheme | None:
    value = payload.get(CHUNK_SCHEME_FIELD)
    try:
        return ChunkScheme(value) if value else None
    except ValueError:
        return None


def _page(payload: dict[str, Any], *, score: float | None = None) -> Page:
    text = payload.get("text", "")
    bib_key = payload.get("bib_key", "")
    # The payload field is `page_number` on the wire and means the index; see
    # `contract.READ_FIELDS`.
    page_index = payload.get(PAGE_INDEX_FIELD, 0)
    status = classify_text(text, payload.get("ocr_backend"))
    return Page(
        bib_key=bib_key,
        title=payload.get("title", ""),
        page_index=page_index,
        file_path=payload.get("file_path", ""),
        text=text,
        read_status=status,
        detail=detail_for(status, bib_key, page_index),
        score=score,
        text_source=payload.get("ocr_backend") or "",
        disputed=int(payload.get(OCR_DISPUTED_FIELD) or 0),
    )
