"""The two embedding models, built once and lazily.

Both halves embed text: the ingest half embeds documents in packed batches,
the read half embeds one query at a time. What they share is only the models
themselves, so only the models live here — below both halves, imported by
each, importing neither.

The imports are inside the accessors. `fastembed` alone costs well over a
second to import, and `axiomation sources status` has no business paying it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

BM25_MODEL = "Qdrant/bm25"

_voyage: Any = None
_voyage_key: str | None = None
_bm25: Any = None


def voyage(api_key: str) -> Any:
    """The Voyage client for this key, built on first use."""
    global _voyage, _voyage_key
    if _voyage is None or _voyage_key != api_key:
        import voyageai

        _voyage = voyageai.Client(api_key=api_key)
        _voyage_key = api_key
    return _voyage


def bm25() -> Any:
    """The local sparse model. Downloads its weights on first use."""
    global _bm25
    if _bm25 is None:
        from fastembed import SparseTextEmbedding

        _bm25 = SparseTextEmbedding(model_name=BM25_MODEL)
    return _bm25


def sparse_vectors(texts: Sequence[str]) -> list[dict[str, list]]:
    """BM25 vectors as Qdrant's `{indices, values}` pairs."""
    return [
        {"indices": vector.indices.tolist(), "values": vector.values.tolist()}
        for vector in bm25().embed(list(texts))
    ]
