"""A graph edit's classes per layer: two states
of one version, each layer's records compared by what they say."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.changes import Change, GraphChange, GraphState, Layer, graph_changes
from axiomation.cutter.model import GroupKind, Role, Segment
from axiomation.reader import Span
from axiomation.records import (
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Entry,
    Frame,
    Standing,
    Status,
    TargetKind,
)
from questions.review_paper import make_paper

SETTING, DEFINITION, THEOREM = "main.tex:4:1", "main.tex:7:1", "main.tex:12:1"
PROOF, SHARP, CLOSING = "main.tex:15:1", "main.tex:19:1", "main.tex:22:1"


@pytest.fixture
def cut(tmp_path: Path):
    return make_paper(tmp_path / "paper").cut


def state(cut, records: EdgeRecords | None = None) -> GraphState:
    return GraphState(cut, records or EdgeRecords())


def halves(cut, block_id: str, at: int):
    """`block_id` split after `at` characters of its first owned run."""
    block = cut.block(block_id)
    run = block.owned[0]
    first = replace(
        block, owned=(Span(run.file, run.start, run.start + at),), hash="h1"
    )
    second = replace(
        block,
        id=f"{run.file}:x:{at}",
        owned=(Span(run.file, run.start + at, run.end), *block.owned[1:]),
        hash="h2",
    )
    return first, second


def without(cut, *ids: str):
    return tuple(b for b in cut.blocks if b.id not in ids)


def test_no_edit_is_no_change(cut):
    assert graph_changes(state(cut), state(cut)) == []


def test_a_block_split_in_two_and_merged_back(cut):
    first, second = halves(cut, SETTING, 10)
    blocks = list(cut.blocks)
    at = blocks.index(cut.block(SETTING))
    split = replace(cut, blocks=(*blocks[:at], first, second, *blocks[at + 1 :]))
    assert graph_changes(state(cut), state(split)) == [
        GraphChange(Layer.CUT, Change.SPLIT, (SETTING,), (SETTING, second.id))
    ]
    assert graph_changes(state(split), state(cut)) == [
        GraphChange(Layer.CUT, Change.MERGED, (SETTING, second.id), (SETTING,))
    ]


def test_a_boundary_moved_between_two_blocks(cut):
    one, other = cut.block(DEFINITION), cut.block(SETTING)
    moved_one = replace(
        other,
        owned=(
            Span(other.owned[0].file, other.owned[0].start, other.owned[0].end - 5),
        ),
        hash="h1",
    )
    moved_other = replace(
        one,
        owned=(
            Span(other.owned[0].file, other.owned[0].end - 5, other.owned[0].end),
            *one.owned,
        ),
        hash="h2",
    )
    blocks = tuple(
        moved_one if b.id == SETTING else moved_other if b.id == DEFINITION else b
        for b in cut.blocks
    )
    (change,) = graph_changes(state(cut), state(replace(cut, blocks=blocks)))
    assert change.layer is Layer.CUT and change.change is Change.MOVED_BOUNDARY
    assert change.before == (SETTING, DEFINITION)


def test_a_block_s_roles_changed(cut):
    blocks = tuple(
        replace(b, roles=(Role.CLAIM,), segments=(Segment("", Role.CLAIM),))
        if b.id == SHARP
        else b
        for b in cut.blocks
    )
    assert graph_changes(state(cut), state(replace(cut, blocks=blocks))) == [
        GraphChange(Layer.ROLES, Change.CHANGED, (SHARP,), (SHARP,))
    ]


def test_a_group_s_membership_and_a_group_added(cut):
    (proof,) = cut.groups
    shorter = replace(proof, id="proof@shorter", blocks=proof.blocks[:2])
    run = replace(
        proof, id="run@1", kind=GroupKind.STEP_RUN, blocks=(SETTING, DEFINITION)
    )
    found = graph_changes(state(cut), state(replace(cut, groups=(shorter, run))))
    assert found == [
        GraphChange(
            Layer.GROUPS, Change.MEMBERSHIP_CHANGED, (proof.id,), ("proof@shorter",)
        ),
        GraphChange(Layer.GROUPS, Change.ADDED, (), ("run@1",)),
    ]


def edge(n: int, source: str, target: str, parts=(), answer="a") -> Edge:
    return Edge(
        f"{answer}{n}",
        answer,
        source,
        EdgeKind.USES,
        EdgeTarget(TargetKind.BLOCK, target),
        ("words",),
        parts,
    )


def test_edges_by_what_they_say_not_their_ids(cut):
    before = EdgeRecords(
        edges=(
            edge(1, THEOREM, DEFINITION),
            edge(2, PROOF, DEFINITION, ("x",)),
            edge(3, CLOSING, THEOREM),
        )
    )
    after = EdgeRecords(
        edges=(
            edge(1, THEOREM, DEFINITION, answer="b"),  # the same, another id
            edge(2, PROOF, DEFINITION, ("y",), answer="b"),
            edge(4, SHARP, THEOREM, answer="b"),
        )
    )
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.EDGES, Change.PARTS_CHANGED, ("a2",), ("b2",)),
        GraphChange(Layer.EDGES, Change.RETRACTED, ("a3",), ()),
        GraphChange(Layer.EDGES, Change.ADDED, (), ("b4",)),
    ]


def test_standings_by_their_block_and_claim_words(cut):
    def standing(n, block, claim, status, answer="a"):
        return Standing(f"{answer}{n}", answer, block, claim, status)

    before = EdgeRecords(
        standings=(
            standing(1, THEOREM, "The output is bounded.", Status.JUSTIFIED),
            standing(2, SHARP, "the bound is sharp", Status.UNSUPPORTED),
        )
    )
    after = EdgeRecords(
        standings=(
            standing(1, THEOREM, "The output  is bounded.", Status.SELF, "b"),
            standing(3, CLOSING, "Closing words", Status.RESTATED, "b"),
        )
    )
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.STANDINGS, Change.STATUS_CHANGED, ("a1",), ("b1",)),
        GraphChange(Layer.STANDINGS, Change.RETRACTED, ("a2",), ()),
        GraphChange(Layer.STANDINGS, Change.ADDED, (), ("b3",)),
    ]


def test_the_frame_as_a_block_set(cut):
    before = EdgeRecords(frame=(Frame("a1", "a", SETTING),))
    after = EdgeRecords(frame=(Frame("b1", "b", DEFINITION),))
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.FRAME, Change.RETRACTED, (SETTING,), ()),
        GraphChange(Layer.FRAME, Change.ADDED, (), (DEFINITION,)),
    ]


def test_a_block_removed_and_one_added(cut):
    assert graph_changes(
        state(cut), state(replace(cut, blocks=without(cut, CLOSING)))
    ) == [GraphChange(Layer.CUT, Change.REMOVED, (CLOSING,), ())]
    assert graph_changes(
        state(replace(cut, blocks=without(cut, CLOSING))), state(cut)
    ) == [GraphChange(Layer.CUT, Change.ADDED, (), (CLOSING,))]


def test_a_group_removed_and_one_of_another_kind(cut):
    (proof,) = cut.groups
    assert graph_changes(state(cut), state(replace(cut, groups=()))) == [
        GraphChange(Layer.GROUPS, Change.REMOVED, (proof.id,), ())
    ]
    derivation = replace(proof, id="derivation@1", kind=GroupKind.DERIVATION)
    assert graph_changes(state(cut), state(replace(cut, groups=(derivation,)))) == [
        GraphChange(Layer.GROUPS, Change.REMOVED, (proof.id,), ()),
        GraphChange(Layer.GROUPS, Change.ADDED, (), ("derivation@1",)),
    ]


def entry(label: str, words: str, role=Role.DEFINITION, answer="a") -> Entry:
    return Entry(f"{answer}-{label}", answer, DEFINITION, label, words, role)


def test_parts_by_their_entries_words_and_roles_not_their_labels(cut):
    before = EdgeRecords(
        entries=(entry("sigma", "the  gain"), entry("in", "input")),
        edges=(
            edge(1, THEOREM, DEFINITION, ("sigma",)),
            edge(2, PROOF, DEFINITION, ("in",)),
        ),
    )
    relabelled = EdgeRecords(
        entries=(entry("g", "the gain", answer="b"), entry("in", "input", answer="b")),
        edges=(
            edge(1, THEOREM, DEFINITION, ("g",), answer="b"),
            edge(2, PROOF, DEFINITION, ("in",), answer="b"),
        ),
    )
    assert graph_changes(state(cut, before), state(cut, relabelled)) == []
    no_role = replace(
        relabelled,
        entries=(relabelled.entries[0], entry("in", "input", None, answer="b")),
    )
    assert graph_changes(state(cut, before), state(cut, no_role)) == [
        GraphChange(Layer.EDGES, Change.PARTS_CHANGED, ("a2",), ("b2",))
    ]


def test_a_where_used_flag_toggled_is_a_flags_change(cut):
    before = EdgeRecords(edges=(edge(1, THEOREM, DEFINITION),))
    after = EdgeRecords(
        edges=(replace(edge(1, THEOREM, DEFINITION, answer="b"), where_used=True),)
    )
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.EDGES, Change.FLAGS_CHANGED, ("a1",), ("b1",))
    ]


def test_a_generalizes_flag_toggled_is_a_flags_change(cut):
    before = EdgeRecords(edges=(edge(1, THEOREM, DEFINITION),))
    after = EdgeRecords(
        edges=(replace(edge(1, THEOREM, DEFINITION, answer="b"), generalizes=True),)
    )
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.EDGES, Change.FLAGS_CHANGED, ("a1",), ("b1",))
    ]


def test_each_record_counts_and_parts_pair_per_edge(cut):
    before = EdgeRecords(
        entries=(entry("x", "the gain"), entry("y", "the ratio")),
        edges=(
            edge(1, THEOREM, DEFINITION),
            edge(2, THEOREM, DEFINITION),  # a duplicate
            edge(3, PROOF, DEFINITION, ("x",)),
            edge(4, PROOF, DEFINITION, ("y",)),
        ),
    )
    after = EdgeRecords(
        entries=(
            entry("x", "the gain", answer="b"),
            entry("y", "the ratio", answer="b"),
        ),
        edges=(
            edge(1, THEOREM, DEFINITION, answer="b"),
            edge(4, PROOF, DEFINITION, ("y",), answer="b"),
            edge(3, PROOF, DEFINITION, ("x",), answer="b"),
        ),
    )
    assert graph_changes(state(cut, before), state(cut, after)) == [
        GraphChange(Layer.EDGES, Change.RETRACTED, ("a2",), ())
    ]


def test_the_parts_a_line_leaves_from_by_their_words_not_their_labels(cut):
    """The definition's line onto the theorem leaves from its part [g]:
    relabelled, nothing changed; leaving from the block whole, or from
    another part, its parts changed."""
    leaving = edge(1, DEFINITION, THEOREM)
    before = EdgeRecords(
        entries=(entry("g", "the gain"), entry("h", "the loss")),
        edges=(replace(leaving, source_parts=("g",)),),
    )
    relabelled = EdgeRecords(
        entries=(
            entry("k", "the  gain", answer="b"),
            entry("h", "the loss", answer="b"),
        ),
        edges=(replace(leaving, source_parts=("k",)),),
    )
    assert graph_changes(state(cut, before), state(cut, relabelled)) == []
    for moved in ((), ("h",)):
        after = replace(before, edges=(replace(leaving, source_parts=moved),))
        assert graph_changes(state(cut, before), state(cut, after)) == [
            GraphChange(Layer.EDGES, Change.PARTS_CHANGED, ("a1",), ("a1",))
        ], moved
