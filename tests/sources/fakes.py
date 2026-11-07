"""Stand-ins for the services the sources module talks to.

Importable without the `library` extra: only `sparse` needs Qdrant's
models. A fake Qdrant that speaks the payload contract, and a real HTTP server
answering Better BibTeX's JSON-RPC. Both are small enough to read, which is
the point: a test that fails should say something about axiomation, not
about the mock.

The Zotero fake is a socket rather than a patch, because the adapter's whole
job is to decide whether a port answers.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from axiomation.sources.contract import (
    MARKER_FIELD,
    MARKER_VALUE,
    TextSource,
    point_id,
)
from axiomation.sources.ingest.ocr import Word

if TYPE_CHECKING:
    from qdrant_client import models

# --- Qdrant ---


@dataclass
class FakePoint:
    id: int
    payload: dict[str, Any]
    vector: Any = None
    score: float = 0.0


@dataclass
class _Vectors:
    size: int


@dataclass
class _Params:
    vectors: _Vectors
    sparse_vectors: dict[str, Any] = field(default_factory=lambda: {"sparse": None})


@dataclass
class _CollectionConfig:
    params: _Params


@dataclass
class _CollectionInfo:
    config: _CollectionConfig
    points_count: int


@dataclass
class _Named:
    name: str


@dataclass
class _Collections:
    collections: list[_Named]


@dataclass
class _QueryResponse:
    points: list[FakePoint]


@dataclass
class _Count:
    count: int


@dataclass
class FakeQdrant:
    """A Qdrant that stores points in a dict and filters them honestly.

    Search is not vector search: it returns every point the prefetch filters
    admit, scored from `scores` when a test wants control and by descending
    rank otherwise. What it does check is that the query was built the way
    the contract's collections are searched, because that is the part the
    contract covers.
    """

    collections: dict[str, dict[int, FakePoint]] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    scores: list[float] | None = None
    queries: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    unreachable: bool = False

    # -- construction helpers, not part of the client's own surface --

    def make(self, name: str, size: int = 8) -> None:
        self.collections.setdefault(name, {})
        self.sizes[name] = size

    def add_chunk(self, collection: str, **payload: Any) -> FakePoint:
        """One point carrying the whole payload contract, defaults filled."""
        full = {
            "bib_key": "hardy1949",
            "title": "Divergent Series",
            "author": "Hardy, G. H.",
            "doi": "",
            "page_number": 1,
            "chunk_scheme": "page",
            "chunk_index": 0,
            "file_path": "/tmp/hardy1949.pdf",
            "text": "x" * 400,
            "file_sha256": "f" * 64,
            "bib_sha256": "b" * 64,
            "ocr_backend": "none",
            **payload,
        }
        self.make(collection)
        point = FakePoint(
            id=point_id(full["bib_key"], full["chunk_index"]), payload=full
        )
        self.collections[collection][point.id] = point
        return point

    def points(self, collection: str) -> list[FakePoint]:
        return list(self.collections.get(collection, {}).values())

    # -- the QdrantClient surface the sources module uses --

    def _store(self, collection: str) -> dict[int, FakePoint]:
        self._reachable()
        if collection not in self.collections:
            raise ValueError(f"collection {collection!r} does not exist")
        return self.collections[collection]

    def _reachable(self) -> None:
        if self.unreachable:
            raise ConnectionError("connection refused")

    def get_collections(self) -> _Collections:
        self._reachable()
        return _Collections([_Named(name) for name in sorted(self.collections)])

    def get_collection(self, collection_name: str) -> _CollectionInfo:
        store = self._store(collection_name)
        return _CollectionInfo(
            config=_CollectionConfig(
                _Params(_Vectors(self.sizes.get(collection_name, 8)))
            ),
            points_count=len(store),
        )

    def create_collection(
        self, collection_name: str, vectors_config: Any, sparse_vectors_config: Any
    ) -> None:
        self._reachable()
        self.collections[collection_name] = {}
        self.sizes[collection_name] = vectors_config.size

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        store = self._store(collection_name)
        for point in points:
            store[point.id] = FakePoint(
                id=point.id, payload=dict(point.payload), vector=point.vector
            )

    def retrieve(
        self,
        collection_name: str,
        ids: list[int],
        with_payload: Any = True,
        with_vectors: Any = False,
    ) -> list[FakePoint]:
        store = self._store(collection_name)
        return [store[item] for item in ids if item in store]

    def scroll(
        self,
        collection_name: str,
        scroll_filter: Any = None,
        limit: int = 10,
        offset: Any = None,
        with_payload: Any = True,
        with_vectors: Any = False,
    ) -> tuple[list[FakePoint], Any]:
        store = self._store(collection_name)
        matching = [
            point for point in store.values() if matches(point.payload, scroll_filter)
        ]
        start = offset or 0
        window = matching[start : start + limit]
        following = start + limit
        return window, (following if following < len(matching) else None)

    def count(
        self, collection_name: str, count_filter: Any = None, exact: bool = True
    ) -> _Count:
        store = self._store(collection_name)
        return _Count(
            sum(1 for point in store.values() if matches(point.payload, count_filter))
        )

    def query_points(
        self, collection_name: str, prefetch: list[Any], query: Any, limit: int
    ) -> _QueryResponse:
        store = self._store(collection_name)
        self.queries.append({"prefetch": prefetch, "query": query, "limit": limit})
        matching = [
            point
            for point in store.values()
            if matches(point.payload, prefetch[0].filter)
        ]
        scored = []
        for rank, point in enumerate(matching):
            score = (
                self.scores[rank]
                if self.scores and rank < len(self.scores)
                else 1.0 - rank / 100
            )
            scored.append(FakePoint(point.id, point.payload, point.vector, score))
        return _QueryResponse(scored[:limit])

    def set_payload(
        self, collection_name: str, payload: dict[str, Any], points: Any
    ) -> None:
        store = self._store(collection_name)
        ids = getattr(points, "points", None)
        for key, point in store.items():
            chosen = (
                key in ids if ids is not None else matches(point.payload, points.filter)
            )
            if chosen:
                point.payload.update(payload)

    def delete(self, collection_name: str, points_selector: Any) -> None:
        store = self._store(collection_name)
        doomed = [
            key
            for key, point in store.items()
            if matches(point.payload, points_selector.filter)
        ]
        for key in doomed:
            del store[key]

    def close(self) -> None:
        self.closed = True


def matches(payload: dict[str, Any], query: Any) -> bool:
    """Whether a payload satisfies a `models.Filter`. `None` admits all."""
    if query is None:
        return True
    if not all(_condition(payload, item) for item in query.must or []):
        return False
    return not any(_condition(payload, item) for item in query.must_not or [])


def _condition(payload: dict[str, Any], condition: Any) -> bool:
    value = payload.get(condition.key)
    if condition.match is not None:
        return value == condition.match.value
    window = condition.range
    if window is None:
        raise AssertionError(f"the fake does not understand {condition!r}")
    if value is None:
        return False
    return window.gte <= value <= window.lte


def marker_payload(**overrides: Any) -> dict[str, Any]:
    """The payload of a collection marker, as `ingest.upsert` writes it."""
    return {
        MARKER_FIELD: MARKER_VALUE,
        "creator": "axiomation",
        "created_at": "2026-09-13T00:00:00+00:00",
        "vector_space": "",
        "voyage_model": "voyage-3-lite",
        "voyage_dims": 8,
        "ocr_backend": "tesseract",
        **overrides,
    }


def sparse(*indices: int) -> models.SparseVector:
    from qdrant_client import models

    return models.SparseVector(indices=list(indices), values=[1.0] * len(indices))


# --- Voyage ---


@dataclass
class _Embedded:
    embeddings: list[list[float]]
    total_tokens: int


@dataclass
class _Document:
    index: int
    embeddings: list[list[float]]


@dataclass
class _Contextualized:
    results: list[_Document]
    total_tokens: int


class FakeVoyage:
    """Both embedding endpoints and the tokenizer, recording what was sent.

    Tokenizes by whitespace, one token per word, so a test can size a page
    by counting words. `embed` records each batch in `batches`;
    `contextualized_embed` records each request's documents in `documents`.
    `calls` is the order the endpoints were hit in. Every vector is constant,
    so a test asserts on counts and order, never on values.
    """

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.documents: list[list[list[str]]] = []
        self.calls: list[str] = []

    def tokenize(self, texts: list[str], model: str | None = None) -> list[list[str]]:
        return [text.split() for text in texts]

    def embed(
        self, texts: list[str], model: str, input_type: str, output_dimension: int
    ) -> _Embedded:
        self.calls.append("embed")
        self.batches.append(list(texts))
        return _Embedded(
            embeddings=[[0.5] * output_dimension for _ in texts],
            total_tokens=sum(len(text.split()) for text in texts),
        )

    def contextualized_embed(
        self,
        inputs: list[list[str]],
        model: str,
        input_type: str,
        output_dimension: int,
    ) -> _Contextualized:
        self.calls.append("contextualized_embed")
        self.documents.append([list(document) for document in inputs])
        return _Contextualized(
            results=[
                _Document(index, [[0.25] * output_dimension for _ in document])
                for index, document in enumerate(inputs)
            ],
            total_tokens=sum(
                len(chunk.split()) for document in inputs for chunk in document
            ),
        )


# --- Zotero ---


class FakeZotero:
    """Better BibTeX's two read calls, over a real socket on a real port.

    The adapter's first question is whether anything answers at all, so a
    patched function would test nothing. `items` maps a bib key to its
    Zotero item key and its attachment paths.
    """

    def __init__(self, items: dict[str, dict[str, Any]] | None = None) -> None:
        self.items = items or {}
        self.calls: list[tuple[str, list[Any]]] = []
        handler = _handler_for(self)
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        # The default poll interval makes every shutdown wait half a second.
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeZotero:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def search(self, bib_key: str) -> list[dict[str, Any]]:
        item = self.items.get(bib_key)
        if item is None:
            return []
        return [
            {
                "id": f"http://zotero.org/users/local/AAAA/items/{item['item_key']}",
                "citekey": bib_key,
                "title": item.get("title", ""),
            }
        ]

    def attachments(self, bib_key: str) -> list[dict[str, Any]]:
        item = self.items.get(bib_key)
        if item is None:
            return []
        return [
            {"path": str(path), "open": f"zotero://open-pdf/{item['item_key']}"}
            for path in item.get("paths", [])
        ]


def _handler_for(fake: FakeZotero) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def do_GET(self) -> None:  # noqa: N802 - http.server's own spelling
            if self.path != "/connector/ping":
                self.send_error(404)
                return
            self._reply(b"<html><body>Zotero is running</body></html>", "text/html")

        def do_POST(self) -> None:  # noqa: N802 - http.server's own spelling
            if self.path != "/better-bibtex/json-rpc":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length))
            method, params = request["method"], request["params"]
            fake.calls.append((method, params))
            if method == "item.search":
                result = fake.search(params[0])
            elif method == "item.attachments":
                result = fake.attachments(params[0])
            else:
                self._reply(
                    json.dumps(
                        {"jsonrpc": "2.0", "error": {"message": "no such method"}}
                    ).encode(),
                    "application/json",
                )
                return
            self._reply(
                json.dumps({"jsonrpc": "2.0", "result": result, "id": 1}).encode(),
                "application/json",
            )

        def _reply(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


# --- OCR ---


@dataclass
class OcrCall:
    """One POST the fake OCR service received."""

    path: str
    body: bytes
    content_type: str
    authorization: str


class FakeOcr:
    """A service that answers the axiomation OCR contract, or breaks it.

    `text` is what a well-behaved service returns. `status` and `body` are
    the two ways one breaks the contract: a refusal, and an answer that is
    not the JSON `docs/ocr.md` names.
    """

    def __init__(
        self,
        text: str = "  read by the service  ",
        *,
        status: int = 200,
        body: bytes | None = None,
    ) -> None:
        self.text = text
        self.status = status
        self.body = body
        self.calls: list[OcrCall] = []
        self._server = HTTPServer(("127.0.0.1", 0), _ocr_handler_for(self))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/parse"

    def __enter__(self) -> FakeOcr:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _ocr_handler_for(fake: FakeOcr) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802 - http.server's own spelling
            length = int(self.headers.get("Content-Length", 0))
            fake.calls.append(
                OcrCall(
                    path=self.path,
                    body=self.rfile.read(length),
                    content_type=self.headers.get("Content-Type", ""),
                    authorization=self.headers.get("Authorization", ""),
                )
            )
            if fake.status != 200:
                self.send_error(fake.status)
                return
            body = (
                fake.body
                if fake.body is not None
                else json.dumps({"text": fake.text}).encode()
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@dataclass
class Exchange:
    """One request a fake server received."""

    method: str
    path: str
    body: bytes
    headers: dict[str, str]


#: How a fake server answers one request: a status and a body (for a
#: redirect, the address it points to).
Answer = tuple[int, bytes]


class FakeServer:
    """A server that answers each request as `route` says, and keeps every
    request: an engine's own server, as a test needs it to behave."""

    def __init__(self, route: Callable[[Exchange], Answer]) -> None:
        self.route = route
        self.exchanges: list[Exchange] = []
        self._server = HTTPServer(("127.0.0.1", 0), _routed_handler_for(self))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def base(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _routed_handler_for(fake: FakeServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            return None

        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            exchange = Exchange(
                self.command, self.path, self.rfile.read(length), dict(self.headers)
            )
            fake.exchanges.append(exchange)
            status, body = fake.route(exchange)
            self.send_response(status)
            if 300 <= status < 400:  # a redirect's body is where it points
                self.send_header("Location", body.decode())
                body = b""
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = _answer  # noqa: N815 - http.server's spelling

    return Handler


@dataclass
class StubOcr:
    """A provider that reads whatever a test says, and keeps every image."""

    text: str = "o" * 200
    name: TextSource = TextSource.TESSERACT
    seen: list[bytes] = field(default_factory=list)
    #: the words it reads on any page, with their places
    page_words: list[Word] = field(default_factory=list)

    def read(self, png: bytes) -> str:
        self.seen.append(png)
        return self.text

    def words(self, png: bytes) -> list[Word]:
        self.seen.append(png)
        return self.page_words
