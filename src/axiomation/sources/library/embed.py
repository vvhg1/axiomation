"""Document embedding: packed batches and what they cost.

The read half embeds one query at a time and needs none of this. Batching is
an ingest problem: a thousand pages in one run, two caps to respect, and a
bill worth printing as it grows.

Two document paths, chosen by the model. A plain model (`voyage-4`) takes texts
through `embed`, packed by token count. A contextualized model
(`voyage-context-4`) takes a source's pages in sequence order, cut into
windows, each window one document to `contextualized_embed`, and gives one
vector per page computed with the rest of the window as context. The window
constants below are measured."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from axiomation.sources.contract import is_contextualized
from axiomation.sources.library import vectors

# Per-request limits of the embeddings endpoint: at most this many texts, and
# a total token cap that depends on the model tier. An unknown model falls
# back to the strictest tier. Batches are packed by the local tokenizer,
# whose count can differ slightly from the server's, hence the margin.
BATCH_SIZE = 128
BATCH_TOKEN_LIMITS = {
    "voyage-context-4": 120_000,
    "voyage-4-large": 120_000,
    "voyage-4": 320_000,
    "voyage-4-lite": 1_000_000,
    "voyage-context-3": 120_000,
    "voyage-code-3": 120_000,
    "voyage-3-large": 120_000,
    "voyage-3.5": 320_000,
    "voyage-3.5-lite": 1_000_000,
    "voyage-3": 320_000,
    "voyage-3-lite": 1_000_000,
}
STRICTEST_TOKEN_LIMIT = 120_000
BATCH_TOKEN_MARGIN = 0.8

#: How many consecutive pages a contextualized model sees as one document.
#: Measured: 8, 32 and 128 pages, and overlapping windows, on the
#: verifier's own queries: 8 loses thirteen points, 128 and the overlap are
#: level with 32 at more cost. Recorded on the collection's marker as
#: `context_window`, because the name carries the vector space and not this.
CONTEXT_WINDOW = 32
#: A contextualized document cannot exceed the model's 32K context and the
#: API refuses to truncate one, so a window whose pages tokenize past this
#: is split in two, recursively, down to a single page. The margin under 32K
#: is for the local tokenizer's count differing from the server's.
CONTEXT_DOCUMENT_TOKENS = 28_000
#: At most this many documents per contextualized request.
CONTEXT_BATCH_DOCUMENTS = 1_000

# Dollars per million tokens, for the running cost an ingest prints. Read off
# docs.voyageai.com/docs/pricing; the current models also carry
# 200M free tokens per account, which the estimate ignores.
PRICING = {
    "voyage-context-4": 0.12,
    "voyage-4-large": 0.12,
    "voyage-4": 0.06,
    "voyage-4-lite": 0.02,
    "voyage-context-3": 0.18,
    "voyage-code-3": 0.18,
    "voyage-3-large": 0.12,
    "voyage-3.5": 0.06,
    "voyage-3.5-lite": 0.02,
    "voyage-3": 0.06,
    "voyage-3-lite": 0.02,
}

#: A window of pages, as `(start, stop)` indices into the page sequence.
Window = tuple[int, int]


def token_budget(voyage_model: str) -> int:
    limit = BATCH_TOKEN_LIMITS.get(voyage_model, STRICTEST_TOKEN_LIMIT)
    return int(limit * BATCH_TOKEN_MARGIN)


def cost(voyage_model: str, tokens: int) -> float | None:
    """What those tokens cost, or None for a model with no price on file."""
    price = PRICING.get(voyage_model)
    return None if price is None else tokens / 1_000_000 * price


def embed_documents(
    texts: Sequence[str], *, voyage_model: str, voyage_dims: int, api_key: str
) -> tuple[list[list[float]], list[dict[str, list]], int]:
    """Dense and sparse vectors for every text, and the tokens they cost.

    Under a contextualized model `texts` must be one source's pages in
    sequence order, because the window each page is embedded in is its
    neighbours in that order. `chunk_pages` yields them so.
    """
    embed = _embed_contextualized if is_contextualized(voyage_model) else _embed_dense
    dense, tokens = embed(
        texts, voyage_model=voyage_model, voyage_dims=voyage_dims, api_key=api_key
    )
    return dense, vectors.sparse_vectors(texts), tokens


def _embed_dense(
    texts: Sequence[str], *, voyage_model: str, voyage_dims: int, api_key: str
) -> tuple[list[list[float]], int]:
    client = vectors.voyage(api_key)
    embeddings: list[list[float]] = []
    tokens = 0
    budget = token_budget(voyage_model)
    for batch in token_batches(texts, client, voyage_model, budget, BATCH_SIZE):
        result = client.embed(
            batch,
            model=voyage_model,
            input_type="document",
            output_dimension=voyage_dims,
        )
        embeddings.extend(result.embeddings)
        tokens += result.total_tokens
    return embeddings, tokens


def _embed_contextualized(
    texts: Sequence[str], *, voyage_model: str, voyage_dims: int, api_key: str
) -> tuple[list[list[float]], int]:
    """One vector per page, each computed over its window of neighbours."""
    client = vectors.voyage(api_key)
    lengths = token_lengths(texts, client, voyage_model)
    windows = list(context_windows(lengths))
    weights = [sum(lengths[start:stop]) for start, stop in windows]
    embeddings: list[list[float]] = []
    tokens = 0
    budget = token_budget(voyage_model)
    for request in pack(windows, weights, budget, CONTEXT_BATCH_DOCUMENTS):
        result = client.contextualized_embed(
            inputs=[list(texts[start:stop]) for start, stop in request],
            model=voyage_model,
            input_type="document",
            output_dimension=voyage_dims,
        )
        for document in result.results:
            embeddings.extend(document.embeddings)
        tokens += result.total_tokens
    return embeddings, tokens


def context_windows(
    lengths: Sequence[int],
    window: int = CONTEXT_WINDOW,
    cap: int = CONTEXT_DOCUMENT_TOKENS,
) -> Iterator[Window]:
    """Cut a page sequence into consecutive windows that fit a document.

    Every `window` pages is one document; a document whose pages tokenize
    past `cap` is split in two, recursively. A single page over the cap
    ships alone, and the API's own limit is then the one that applies.
    """
    for start in range(0, len(lengths), window):
        yield from _fitted(lengths, start, min(start + window, len(lengths)), cap)


def _fitted(
    lengths: Sequence[int], start: int, stop: int, cap: int
) -> Iterator[Window]:
    if stop - start <= 1 or sum(lengths[start:stop]) <= cap:
        yield (start, stop)
        return
    middle = (start + stop) // 2
    yield from _fitted(lengths, start, middle, cap)
    yield from _fitted(lengths, middle, stop, cap)


def token_lengths(texts: Sequence[str], client: Any, voyage_model: str) -> list[int]:
    """How many tokens each text is, by the model's own tokenizer."""
    return [len(item) for item in client.tokenize(list(texts), model=voyage_model)]


def token_batches(
    texts: Sequence[str],
    client: Any,
    voyage_model: str,
    budget: int,
    size: int = BATCH_SIZE,
) -> Iterator[list[str]]:
    """Pack texts into consecutive batches inside both caps.

    A single text over budget still ships alone: the API truncates it to the
    model's context length, which is far below any batch limit.
    """
    yield from pack(texts, token_lengths(texts, client, voyage_model), budget, size)


def pack[T](
    items: Sequence[T], weights: Sequence[int], budget: int, size: int
) -> Iterator[list[T]]:
    """Consecutive groups of `items`, each under `budget` weight and `size` long.

    Order is kept and every item is shipped; one item over budget is a group
    of its own.
    """
    batch: list[T] = []
    batch_weight = 0
    for item, weight in zip(items, weights, strict=True):
        if batch and (len(batch) >= size or batch_weight + weight > budget):
            yield batch
            batch, batch_weight = [], 0
        batch.append(item)
        batch_weight += weight
    if batch:
        yield batch
