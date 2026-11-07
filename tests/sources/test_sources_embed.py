"""Batch packing and the two document paths.

The budget is a parameter, not a module constant. The contextualized
path: pages in windows, one document per window, one vector per page.
"""

from __future__ import annotations

import pytest

from axiomation.sources.library import embed as embed_module
from axiomation.sources.library import vectors
from axiomation.sources.library.embed import (
    BATCH_TOKEN_LIMITS,
    CONTEXT_WINDOW,
    PRICING,
    STRICTEST_TOKEN_LIMIT,
    context_windows,
    cost,
    embed_documents,
    pack,
    token_batches,
    token_budget,
)
from sources.fakes import FakeVoyage


def batches(texts, budget, size=128):
    return list(token_batches(texts, FakeVoyage(), "voyage-4", budget, size))


def test_order_is_preserved_and_every_text_is_covered() -> None:
    texts = [f"w{index} " * (index + 1) for index in range(20)]

    assert [text for batch in batches(texts, 30) for text in batch] == texts


def test_a_batch_splits_when_the_token_budget_is_exceeded() -> None:
    texts = ["a b c", "d e f", "g h i"]

    assert batches(texts, 6) == [["a b c", "d e f"], ["g h i"]]


def test_a_batch_splits_when_the_text_count_is_exceeded() -> None:
    assert batches(["x"] * 5, 1000, size=2) == [["x", "x"], ["x", "x"], ["x"]]


def test_an_oversized_text_ships_alone() -> None:
    assert batches(["a b c d e", "f", "g"], 2) == [["a b c d e"], ["f", "g"]]


def test_no_texts_yield_no_batches() -> None:
    assert batches([], 10) == []


def test_pack_groups_any_items_by_weight() -> None:
    assert list(pack(["a", "b", "c"], [5, 5, 5], budget=10, size=10)) == [
        ["a", "b"],
        ["c"],
    ]


def test_the_budget_keeps_a_margin_under_the_published_limit() -> None:
    assert token_budget("voyage-4") < BATCH_TOKEN_LIMITS["voyage-4"]


def test_an_unknown_model_gets_the_strictest_tier() -> None:
    assert token_budget("voyage-99") == int(STRICTEST_TOKEN_LIMIT * 0.8)


def test_cost_is_dollars_per_million_tokens() -> None:
    assert cost("voyage-4", 1_000_000) == pytest.approx(PRICING["voyage-4"])


def test_a_model_with_no_price_on_file_reports_no_cost() -> None:
    assert cost("voyage-99", 1_000_000) is None


def test_every_priced_model_has_a_token_limit() -> None:
    assert set(PRICING) == set(BATCH_TOKEN_LIMITS)


# --- The contextualized path ---


@pytest.fixture
def voyage(monkeypatch: pytest.MonkeyPatch) -> FakeVoyage:
    client = FakeVoyage()
    monkeypatch.setattr(vectors, "voyage", lambda key: client)
    monkeypatch.setattr(vectors, "sparse_vectors", lambda texts: [{}] * len(texts))
    return client


def pages(count: int, words: int = 3) -> list[str]:
    return [
        " ".join(f"p{index}w{word}" for word in range(words)) for index in range(count)
    ]


def test_a_contextualized_model_sends_pages_in_windows(voyage: FakeVoyage) -> None:
    texts = pages(70)

    dense, sparse, tokens = embed_documents(
        texts, voyage_model="voyage-context-4", voyage_dims=4, api_key="k"
    )

    assert voyage.calls == ["contextualized_embed"]
    assert [len(document) for document in voyage.documents[0]] == [32, 32, 6]
    assert [page for doc in voyage.documents[0] for page in doc] == texts
    assert [len(vector) for vector in dense] == [4] * 70
    assert len(sparse) == 70
    assert tokens == 70 * 3


def test_a_page_over_the_cap_is_split_recursively() -> None:
    """A 32-page window of 10-token pages is 320 tokens; a cap of 100 cuts
    it in halves until each half fits."""
    assert list(context_windows([10] * 32, window=32, cap=100)) == [
        (0, 8),
        (8, 16),
        (16, 24),
        (24, 32),
    ]


def test_a_single_page_over_the_cap_ships_alone() -> None:
    assert list(context_windows([500, 1, 1], window=32, cap=100)) == [
        (0, 1),
        (1, 3),
    ]


def test_windows_run_in_sequence_with_the_default_width() -> None:
    assert list(context_windows([1] * 70)) == [(0, 32), (32, 64), (64, 70)]
    assert CONTEXT_WINDOW == 32


def test_windows_pack_into_requests_under_the_document_cap(
    voyage: FakeVoyage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embed_module, "CONTEXT_BATCH_DOCUMENTS", 2)

    embed_documents(
        pages(70), voyage_model="voyage-context-4", voyage_dims=2, api_key="k"
    )

    assert [len(request) for request in voyage.documents] == [2, 1]


def test_windows_pack_into_requests_under_the_token_budget(
    voyage: FakeVoyage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three windows of 32 pages at 100 tokens each are 3,200 tokens apiece;
    a budget of 6,500 takes two per request."""
    monkeypatch.setattr(embed_module, "BATCH_TOKEN_LIMITS", {"voyage-context-4": 6_500})
    monkeypatch.setattr(embed_module, "BATCH_TOKEN_MARGIN", 1.0)

    embed_documents(
        pages(96, words=100),
        voyage_model="voyage-context-4",
        voyage_dims=2,
        api_key="k",
    )

    assert [len(request) for request in voyage.documents] == [2, 1]


def test_the_plain_path_is_untouched_for_voyage_4(voyage: FakeVoyage) -> None:
    dense, _, tokens = embed_documents(
        ["one two", "three"], voyage_model="voyage-4", voyage_dims=4, api_key="k"
    )

    assert voyage.calls == ["embed"]
    assert voyage.documents == []
    assert [len(vector) for vector in dense] == [4, 4]
    assert tokens == 3
