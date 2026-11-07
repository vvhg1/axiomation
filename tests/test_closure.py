"""The closure in the package: the product's
graph read from its records, and the rule's cases on it.

The paper is the cut review's synthetic one (`questions/review_paper.py`):
an assumption, a definition, a theorem, its proof (a footnote defining
"small"), the bound's sharpness, closing words.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.closure import (
    SCOPE_PAPER,
    from_export,
    from_records,
    impact_reach,
    reach,
    rule,
)
from axiomation.cutter.model import Role, Segment
from axiomation.records import (
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Entry,
    Frame,
    TargetKind,
)
from questions.review_paper import make_paper

SETTING, DEFINITION, THEOREM = "main.tex:4:1", "main.tex:7:1", "main.tex:12:1"
PROOF, FOOTNOTE, SHARP = "main.tex:15:1", "main.tex:16:48", "main.tex:19:1"
CLOSING, OTHER = "main.tex:22:1", "extra.tex:2:1"
GROUP = f"proof@{PROOF}"

ROLES = {
    SETTING: ((Role.ASSUMPTION,), ()),
    DEFINITION: ((Role.DEFINITION,), ()),
    THEOREM: ((Role.CLAIM,), ()),
    PROOF: (
        (Role.CLAIM, Role.JUSTIFICATION),
        (
            Segment("", Role.JUSTIFICATION),
            Segment("Hence the output is bounded.", Role.CLAIM),
        ),
    ),
    FOOTNOTE: ((Role.DEFINITION,), ()),
    SHARP: ((Role.CLAIM,), ()),
    CLOSING: ((Role.CLAIM,), ()),
}


def edge(n: int, source: str, kind: str, target: str, **more) -> Edge:
    kind_of = TargetKind.BLOCK
    if target.startswith("proof@"):
        kind_of = TargetKind.GROUP
    elif target == "paper":
        kind_of = TargetKind.SCOPE
    elif target.endswith(".pdf"):
        kind_of = TargetKind.FILE
    return Edge(
        f"e{n}",
        "a",
        source,
        EdgeKind(kind),
        EdgeTarget(kind_of, target),
        ("words",),
        **more,
    )


@pytest.fixture
def paper(tmp_path: Path):
    found = make_paper(tmp_path / "paper")
    blocks = []
    for block in found.cut.blocks:
        roles, segments = ROLES.get(block.id, ((), ()))
        blocks.append(
            replace(
                block,
                roles=roles,
                segments=segments or ((Segment("", roles[0]),) if roles else ()),
            )
        )
    return replace(found.cut, blocks=tuple(blocks))


RECORDS = EdgeRecords(
    entries=(Entry("n1", "a", SETTING, "x", "a field of scalars", Role.ASSUMPTION),),
    edges=(
        edge(1, DEFINITION, "uses", SETTING, parts=("x",)),  # assumption content
        edge(2, THEOREM, "uses", DEFINITION),  # a whole definition block
        edge(3, PROOF, "uses", FOOTNOTE),
        edge(4, GROUP, "justifies", THEOREM),
        edge(5, SHARP, "about", THEOREM),
        edge(6, CLOSING, "about", GROUP),
        edge(7, CLOSING, "uses", PROOF),  # a claim and a justification
        edge(8, OTHER, "about", "paper"),
        edge(9, SHARP, "about", DEFINITION, where_used=True),
        edge(10, OTHER, "uses", "figure.pdf"),
    ),
)


def test_from_records_reads_each_target_s_content(paper):
    graph = from_records(paper, RECORDS)
    content = {link.source + ">" + link.target: link.content for link in graph.links}
    assert content[f"{DEFINITION}>{SETTING}"] == "assumption"  # its entry's role
    assert content[f"{THEOREM}>{DEFINITION}"] == "definition"  # the block's
    assert content[f"{GROUP}>{THEOREM}"] == "claim"
    assert content[f"{CLOSING}>{GROUP}"] == "justification"
    # a block of two segments counts as its first meaning content, else
    # its first (recall first)
    assert content[f"{CLOSING}>{PROOF}"] == "justification"
    assert content[f"{OTHER}>{SCOPE_PAPER}"] == ""
    assert content[f"{OTHER}>file:figure.pdf"] == ""
    assert graph.groups == {GROUP: (PROOF, FOOTNOTE, SHARP)}
    assert graph.blocks == tuple(b.id for b in paper.blocks)
    (where,) = [link for link in graph.links if link.where_used]
    assert where.flag == "where-used"


def test_an_assumption_moves_the_definition_and_the_chain_stops_at_a_claim(paper):
    found = reach(from_records(paper, RECORDS), SETTING)
    by = {r.handle: r for r in found.reached}
    assert by[DEFINITION].changed and by[DEFINITION].content == "assumption"
    assert by[THEOREM].changed and by[THEOREM].content == "definition"
    assert not by[SHARP].changed  # about the theorem: asked
    assert not by[OTHER].changed  # scope:paper is asked only on the edit itself


def test_an_edit_inside_the_proof_asks_about_the_group_and_its_support(paper):
    found = reach(from_records(paper, RECORDS), FOOTNOTE)
    by = {r.handle: r for r in found.reached}
    assert by[PROOF].changed  # uses the footnote's definition
    assert not by[CLOSING].changed and by[CLOSING].via.target == GROUP
    assert (THEOREM, GROUP) in found.support


def test_the_edited_block_asks_where_used_and_scope_paper_edges(paper):
    step = rule(from_records(paper, RECORDS), THEOREM, edited=True)
    asked = [(block, why) for block, _, why in step.asked]
    assert (OTHER, "the paper, on any edit") in asked
    assert (SHARP, "claim") in asked  # its about edge onto the theorem
    # it uses the definition, which SHARP is about, where used
    assert (SHARP, f"where-used: {THEOREM}, a user of {DEFINITION}, changed") in asked
    step = rule(from_records(paper, RECORDS), THEOREM, edited=False)
    assert OTHER not in {block for block, _, _ in step.asked}


def test_the_ambient_frame_moves_every_block_after_it(paper):
    records = replace(RECORDS, frame=(Frame("f1", "a", SETTING),))
    found = reach(from_records(paper, records), SETTING)
    changed = {r.handle for r in found.reached if r.changed}
    after = [b.id for b in paper.blocks]
    assert changed == set(after[after.index(SETTING) + 1 :])


# --- the rule gated by Δ classes -------------------------------


def reached(paper, deltas, roots, records=RECORDS):
    found = impact_reach(from_records(paper, records), deltas, roots)
    return {(i.block, i.meaning_moved) for i in found}, found


def test_reworded_or_unchanged_closes_a_change_where_it_stands(paper):
    for cls in ("reworded", "unchanged"):
        found, _ = reached(paper, {SETTING: {"x": cls}}, [SETTING])
        assert found == set(), cls


def test_a_change_to_a_part_goes_on_through_the_edges_naming_it(paper):
    found, impacts = reached(paper, {SETTING: {"x": "narrowed"}}, [SETTING])
    # the definition uses x (assumption content): it moves, and wholly (no Δ
    # of its own), on to the theorem and from there to what is asked
    assert (DEFINITION, True) in found and (THEOREM, True) in found
    assert (SHARP, False) in found
    first = next(i for i in impacts if i.block == DEFINITION)
    assert (first.handle, first.parts, first.origin, first.depth) == (
        SETTING,
        ("x",),
        SETTING,
        1,
    )
    # the sharpness is about the definition, where used: asked from it
    sharp = next(i for i in impacts if i.block == SHARP)
    assert (sharp.handle, sharp.origin, sharp.depth) == (DEFINITION, SETTING, 2)


def test_a_part_the_edge_does_not_name_does_not_carry(paper):
    """A Δ on another part of the setting leaves the edge naming x closed;
    a part without a Δ takes the block's whole one, else counts changed."""
    assert (
        reached(paper, {SETTING: {"y": "removed", "x": "reworded"}}, [SETTING])[0]
        == set()
    )
    assert reached(paper, {SETTING: {None: "reworded"}}, [SETTING])[0] == set()
    found, _ = reached(paper, {SETTING: {"y": "removed"}}, [SETTING])
    assert (DEFINITION, True) in found


def test_a_moved_block_goes_on_by_its_own_delta(paper):
    found, _ = reached(
        paper,
        {SETTING: {"x": "narrowed"}, DEFINITION: {None: "reworded"}},
        [SETTING],
    )
    assert found == {(DEFINITION, True)}


def test_one_impact_per_reaching_edge(paper):
    """The sharpness is asked through its about edge onto the definition
    and through the one onto the theorem, whose meaning moved; the walk
    lists each edge once."""
    found, impacts = reached(paper, {DEFINITION: {None: "widened"}}, [DEFINITION])
    onto = [i for i in impacts if i.block == SHARP]
    assert {i.via.target for i in onto} == {DEFINITION, THEOREM}
    assert len({i.via for i in impacts}) == len(impacts)
    # the edited block's own reasons are not the walk's
    assert not any(i.via.where_used and i.via.target != DEFINITION for i in impacts)


def test_an_edge_naming_a_standing_asks_its_source(paper):
    """An edge onto a block of a definition and a
    claim reads its block's first content, `justification` here, and goes
    on; naming the block's standing, it is `claim`, and asked, whatever
    part beside it says. The export reads the same."""
    named = EdgeRecords(
        entries=(Entry("n1", "a", PROOF, "y", "the bound", Role.DEFINITION),),
        edges=(
            edge(1, CLOSING, "uses", PROOF),
            edge(2, SHARP, "uses", PROOF, claims=("Hence the output is bounded.",)),
            edge(
                3,
                OTHER,
                "uses",
                PROOF,
                parts=("y",),
                claims=("Hence the output is bounded.",),
            ),
        ),
    )
    content = {link.source: link.content for link in from_records(paper, named).links}
    assert content == {CLOSING: "justification", SHARP: "claim", OTHER: "claim"}
    export = {
        "blocks": [{"id": PROOF, "segments": [{"role": "justification"}]}],
        "groups": [],
        "entries": [{"id": "p [y]", "role": "definition"}],
        "edges": [
            {"source": SHARP, "kind": "uses", "target": {"block": PROOF}},
            {
                "source": OTHER,
                "kind": "uses",
                "target": {
                    "block": PROOF,
                    "entries": ["p [y]"],
                    "claims": ["Hence the output is bounded."],
                },
            },
        ],
    }
    read = {link.source: link.content for link in from_export(export).links}
    assert read == {SHARP: "justification", OTHER: "claim"}
