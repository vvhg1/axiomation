"""An `analogy` line's `valid`: what it carries over is a uses edge onto
the argument (`docs/model.md` § 3), so the argument sits in the reading's
one hop and an edit to it lapses the reading; a uses edge onto the result
alone leaves the proof outside it, the miss the edges gate refuses. A
line's `sketch` or `analogy` is no part of its edge (§ 8).

Synthetic kept states of a one-line paper (`test_judgments`' helpers): a
definition `d`, a theorem `t` and its proof `p`, a second theorem `u`
whose justification `q` carries `p` over. No model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from axiomation.judgments import CauseKind, StatusKind, judge, seen, subject
from axiomation.records import Aspect, Depth, RecordStore, Verdict
from axiomation.versions import Command
from test_judgments import AGENT, edge, graph, same, status, updated
from test_versions import V1, keep

TEXT = (
    "Let f be a gain. Then f is bounded. By the gain, f is bounded. "
    "Then g is bounded. As for f, so for g."
)
BLOCKS = [
    ("d", "Let f be a gain."),
    ("t", "Then f is bounded."),
    ("p", "By the gain, f is bounded."),
    ("u", "Then g is bounded."),
    ("q", "As for f, so for g."),
]
STANDINGS = [("t", "Then f is bounded."), ("u", "Then g is bounded.")]


def flagged(e: dict[str, Any], partial: str) -> dict[str, Any]:
    return {**e, "partial": partial}


ANALOGY = flagged(edge("q", "justifies", "u"), "analogy")
BASE = [
    edge("t", "uses", "d"),
    edge("p", "uses", "d"),
    edge("p", "justifies", "t"),
    edge("q", "uses", "t"),
    ANALOGY,
]
CARRIED = [*BASE, edge("q", "uses", "p")]


def paper(text: str = TEXT, blocks=BLOCKS, edges=CARRIED, groups=()) -> dict[str, Any]:
    payload = graph(text, blocks, edges, STANDINGS)
    payload["groups"] = [{"id": g, "blocks": list(m)} for g, m in groups]
    return payload


def built(tmp_path: Path, payload: dict[str, Any]):
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, payload)


def filed_valid(store, kept, on: str = "q") -> None:
    at = seen(store, kept)
    judge(
        store,
        kept,
        Aspect.VALID,
        subject(at, Aspect.VALID, on),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "carried over, it holds",
        AGENT,
    )


def proof_edited(store, first, payload_edges, groups=()):
    """`p`'s words changed, everything else the same."""
    text = TEXT.replace("By the gain", "By the gain alone")
    blocks = [
        *BLOCKS[:2],
        ("p", "By the gain alone, f is bounded."),
        *BLOCKS[3:],
    ]
    return updated(
        store,
        first,
        text,
        paper(text, blocks, payload_edges, groups),
        same("d", "t", "p", "u", "q", but={"p": "changed"}),
    )


def test_an_edit_to_the_proof_carried_over_lapses_the_analogy_s_valid(
    tmp_path,
) -> None:
    store, first = built(tmp_path, paper())
    filed_valid(store, first)
    second = proof_edited(store, first, CARRIED)

    found = status(store, second, Aspect.VALID, "q")
    assert found.status is StatusKind.LAPSED
    assert found.cause.kind is CauseKind.TEXT
    assert found.cause.element.name == second.node_of("p")


def test_a_uses_edge_onto_the_result_alone_leaves_the_proof_outside_the_hop(
    tmp_path,
) -> None:
    """The miss the edges gate refuses: `q` names the theorem, not its
    proof, so an edit to the proof lapses nothing."""
    store, first = built(tmp_path, paper(edges=BASE))
    filed_valid(store, first)
    second = proof_edited(store, first, BASE)

    assert status(store, second, Aspect.VALID, "q").status is StatusKind.HELD


def test_a_closing_analogy_using_its_group_lapses_on_any_member(tmp_path) -> None:
    """`q` closes the proof group `g` it sits in and carries it over: the
    group's node is its members' text, so an edit to `p` lapses `q`'s
    `valid`."""
    groups = [("g", ["p", "q"])]
    edges = [
        edge("t", "uses", "d"),
        edge("p", "uses", "d"),
        edge("g", "justifies", "t"),
        ANALOGY,
        edge("q", "uses", "g"),
    ]
    store, first = built(tmp_path, paper(edges=edges, groups=groups))
    filed_valid(store, first)
    second = proof_edited(store, first, edges, groups)

    found = status(store, second, Aspect.VALID, "q")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.TEXT


def test_a_closing_analogy_without_its_group_edge_holds(tmp_path) -> None:
    """The lapse above is the uses edge's: without it, the analogy sitting
    in the group keeps the members out of its one hop."""
    groups = [("g", ["p", "q"])]
    edges = [
        edge("t", "uses", "d"),
        edge("p", "uses", "d"),
        edge("g", "justifies", "t"),
        ANALOGY,
    ]
    store, first = built(tmp_path, paper(edges=edges, groups=groups))
    filed_valid(store, first)
    second = proof_edited(store, first, edges, groups)

    assert status(store, second, Aspect.VALID, "q").status is StatusKind.HELD


def test_the_carried_argument_added_by_an_edit_lapses_the_reading(tmp_path) -> None:
    """The reading was never shown the proof: the uses edge onto it, added,
    lapses it by `edge`."""
    store, first = built(tmp_path, paper(edges=BASE))
    filed_valid(store, first)
    added = keep(store, V1, TEXT, paper(edges=CARRIED), Command.EDIT, first)

    found = status(store, added, Aspect.VALID, "q")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.EDGE


@pytest.mark.parametrize(
    ("on", "before", "after"),
    [
        ("q", "analogy", None),
        ("p", None, "sketch"),
        ("p", "sketch", None),
    ],
    ids=["analogy-to-given", "given-to-sketch", "sketch-to-given"],
)
def test_a_flag_flipped_lapses_nothing(tmp_path, on, before, after) -> None:
    """The flag reads J's words, the `valid` J's argument; the same words
    read, a flip changes neither."""

    def edges(partial: str | None) -> list[dict[str, Any]]:
        line = edge(on, "justifies", "u" if on == "q" else "t")
        line = flagged(line, partial) if partial else line
        return [e for e in CARRIED if e["id"] != line["id"]] + [line]

    store, first = built(tmp_path, paper(edges=edges(before)))
    filed_valid(store, first, on)
    flipped = keep(store, V1, TEXT, paper(edges=edges(after)), Command.EDIT, first)

    assert status(store, flipped, Aspect.VALID, on).status is StatusKind.HELD
