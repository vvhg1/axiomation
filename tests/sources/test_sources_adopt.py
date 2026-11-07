"""Adopting a collection somebody else built.

axiomation reads another tool's corpus and will never write to it, which leaves a
paper unable to prune a stale key or add a source of its own. Adopting copies
the points into a collection axiomation owns — no re-embedding, because the
vectors already exist — and stamps it.

The tests that matter are the refusals and the one about the marker going on
last, because those are what keep a shared corpus safe from a command whose
whole job is to read it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

from axiomation.sources.config import ResolvedSources, SourcesError  # noqa: E402
from axiomation.sources.contract import MARKER_FIELD, vector_space_hash  # noqa: E402
from axiomation.sources.library.adopt import adopt, plan_adopt  # noqa: E402
from axiomation.sources.library.qdrant import read_marker  # noqa: E402
from axiomation.sources.library.upsert import CREATOR, write_marker  # noqa: E402
from sources.fakes import FakeQdrant, sparse  # noqa: E402

SOURCE = "somebody_elses_corpus"


@pytest.fixture
def settings(settings: ResolvedSources) -> ResolvedSources:
    """The fixture paper, asking for a collection of its own."""
    from dataclasses import replace

    return replace(settings, collection="ours_voyage-3-lite_8")


@pytest.fixture
def corpus(fake_qdrant: FakeQdrant, settings: ResolvedSources) -> FakeQdrant:
    """A source collection with three papers and no axiomation marker.

    The target does not exist yet, which is the state a paper is in before it
    adopts anything; the shared `fake_qdrant` makes it by default.
    """
    fake_qdrant.collections.pop(settings.collection, None)
    fake_qdrant.make(SOURCE, size=settings.voyage_dims)
    for index, key in enumerate(("hardy1949", "knuth1997", "erdos1950")):
        point = fake_qdrant.add_chunk(SOURCE, bib_key=key, chunk_index=index)
        point.vector = {"": [0.5] * settings.voyage_dims, "sparse": sparse(1, 2)}
    return fake_qdrant


# --- Refusals ---


class TestRefusals:
    def test_a_source_that_is_not_there(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        plan = plan_adopt(corpus, settings, "no_such_collection")
        assert plan.blocked
        assert "no collection 'no_such_collection'" in plan.refusal

    def test_adopting_our_own_collection(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        """The mistake that would mean "write to the shared corpus": pointing
        adopt at the collection the paper already reads."""
        plan = plan_adopt(corpus, settings, settings.collection)
        assert plan.blocked
        assert "already this paper's collection" in plan.refusal

    def test_a_source_in_another_vector_space(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        """The vectors are the thing being copied, so a mismatch would build a
        collection no query of this paper's could read."""
        corpus.make("wrong_space", size=settings.voyage_dims * 2)
        plan = plan_adopt(corpus, settings, "wrong_space")
        assert plan.blocked
        assert "dimensions" in plan.refusal
        assert str(settings.voyage_dims) in plan.refusal

    def test_a_target_somebody_else_owns(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        corpus.make(settings.collection, size=settings.voyage_dims)
        corpus.add_chunk(settings.collection, bib_key="theirs")
        plan = plan_adopt(corpus, settings, SOURCE)
        assert plan.blocked
        assert "carries no axiomation marker" in plan.refusal

    def test_a_blocked_plan_cannot_be_carried_out(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        plan = plan_adopt(corpus, settings, "no_such_collection")
        with pytest.raises(SourcesError, match="no collection"):
            adopt(corpus, settings, plan)


# --- The copy ---


class TestAdopting:
    def test_the_plan_counts_what_would_move(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        plan = plan_adopt(corpus, settings, SOURCE)
        assert not plan.blocked
        assert plan.points == 3
        assert plan.create is True
        assert plan.vector_size == settings.voyage_dims

    def test_every_point_arrives(self, corpus: FakeQdrant, settings: ResolvedSources):
        plan = plan_adopt(corpus, settings, SOURCE)
        report = adopt(corpus, settings, plan)
        assert report.copied == 3
        assert report.created is True
        landed = {
            point.payload["bib_key"]
            for point in corpus.points(settings.collection)
            if MARKER_FIELD not in point.payload
        }
        assert landed == {"hardy1949", "knuth1997", "erdos1950"}

    def test_the_vectors_are_copied_not_recomputed(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        """The whole reason this is a command: the expensive half of a corpus
        is the embedding, and it already happened."""
        adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
        copied = next(
            point
            for point in corpus.points(settings.collection)
            if MARKER_FIELD not in point.payload
        )
        assert copied.vector[""] == [0.5] * settings.voyage_dims
        assert copied.vector["sparse"].indices == [1, 2]

    def test_the_payloads_arrive_whole(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        original = {
            point.payload["bib_key"]: point.payload for point in corpus.points(SOURCE)
        }
        adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
        for point in corpus.points(settings.collection):
            if MARKER_FIELD in point.payload:
                continue
            assert point.payload == original[point.payload["bib_key"]]

    def test_the_source_is_untouched(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        before = {point.id: dict(point.payload) for point in corpus.points(SOURCE)}
        adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
        after = {point.id: dict(point.payload) for point in corpus.points(SOURCE)}
        assert after == before
        assert read_marker(corpus, SOURCE) is None, (
            "adopting must not stamp the collection it read from"
        )

    def test_the_target_is_stamped_as_ours(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        report = adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
        marker = read_marker(corpus, settings.collection)
        assert marker is not None
        assert marker.creator == CREATOR
        assert marker.vector_space == vector_space_hash(
            settings.voyage_model, settings.voyage_dims
        )
        assert report.marker["creator"] == CREATOR

    def test_the_source_s_own_marker_does_not_come_along(
        self, corpus: FakeQdrant, settings: ResolvedSources
    ):
        """A creator stamp is a fact about the collection that carries it."""
        from dataclasses import replace

        write_marker(corpus, replace(settings, collection=SOURCE))
        plan = plan_adopt(corpus, settings, SOURCE)
        report = adopt(corpus, settings, plan)
        assert report.copied == 3
        stamps = [
            point
            for point in corpus.points(settings.collection)
            if MARKER_FIELD in point.payload
        ]
        assert len(stamps) == 1

    def test_progress_is_reported(self, corpus: FakeQdrant, settings: ResolvedSources):
        seen: list[str] = []
        adopt(
            corpus,
            settings,
            plan_adopt(corpus, settings, SOURCE),
            progress=seen.append,
        )
        assert any("copied" in line for line in seen)
        assert any("stamped" in line for line in seen)


# --- After adopting ---


def test_an_adopted_collection_can_then_be_written_to(
    corpus: FakeQdrant, settings: ResolvedSources
):
    """The point of the whole exercise: `ingest` refuses the source and
    accepts the adopted copy."""
    from dataclasses import replace

    from axiomation.sources.library.upsert import guard

    assert guard(corpus, replace(settings, collection=SOURCE))
    adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
    assert guard(corpus, settings) == ""


def test_adopting_again_tops_the_collection_up(
    corpus: FakeQdrant, settings: ResolvedSources
):
    """A second adoption is not a refusal: the target is ours by then, and
    copying the same points again writes them at the same ids."""
    adopt(corpus, settings, plan_adopt(corpus, settings, SOURCE))
    added = corpus.add_chunk(SOURCE, bib_key="renyi1958", chunk_index=9)
    added.vector = {"": [0.25] * settings.voyage_dims, "sparse": sparse(3)}

    second = plan_adopt(corpus, settings, SOURCE)
    assert not second.blocked
    assert second.create is False
    report = adopt(corpus, settings, second)

    assert report.copied == 4
    landed = {
        point.payload["bib_key"]
        for point in corpus.points(settings.collection)
        if MARKER_FIELD not in point.payload
    }
    assert "renyi1958" in landed
