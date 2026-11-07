"""The kept versions: states recorded by whole runs, node ids
minted once and continued by identity's lineage, block texts kept once,
history computed from the kept exports, `update` from the store alone,
and nothing destroyed. Synthetic states for the identity cases; the
synthetic paper, built and updated in place by a fake host, for the
commands. No model is called.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import record_file
from axiomation import derived, run_state, versions
from axiomation.cli import BUILD_STAGES, KEPT_COMMANDS, main, parser
from axiomation.cutter import CutRules, DisplayRule, continuation, cut
from axiomation.driver import ReviewSettings, review_cut
from axiomation.exchange import cut_payload, dumps
from axiomation.questions.edges_file import EDGES_FILE
from axiomation.reader import (
    EnvEntry,
    EnvKind,
    Vocabulary,
    VocabularyKind,
    document_hash,
    files_hash,
    read_paper,
)
from axiomation.reader.includes import STORE_DIRECTORY
from axiomation.records import RECORD, LineageLink, RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings
from axiomation.update import run as run_update
from axiomation.versions import Command, EdgeStep, VersionError
from questions.review_paper import MAIN, make_paper
from questions.test_edges_file import written
from runner.fakes import FakeHost
from test_driver import build_host
from test_update import EDITED_SETTING, FILES, FLAT, answering, stepping

# --- synthetic states -----------------------------------------------------------------


def _span(text: str, file: str, words: str) -> dict[str, Any]:
    start = text.index(words)
    return {"file": file, "start": [1, start + 1], "end": [1, start + len(words) + 1]}


#: Who set a synthetic export's lines, as every edge half names one.
PROVENANCE = {
    "by": "agent",
    "model": "model-a",
    "question": "q-1",
    "detail": {"stage": "edges", "template": 1},
}


def export(
    text: str,
    blocks: Sequence[tuple[str, str]],
    groups: Sequence[tuple[str, Sequence[str]]] = (),
    edges: Sequence[dict[str, Any]] = (),
    entries: Sequence[dict[str, Any]] = (),
    standings: Sequence[tuple[str, str]] = (),
    update: dict[str, Any] | None = None,
    handles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """An export of a one-line file `p.md`: each block its id and its words
    (found once), its groups, edge half and `review.update`."""
    return {
        "document": {
            "files": [
                {"path": "p.md", "sha256": hashlib.sha256(text.encode()).hexdigest()}
            ]
        },
        "blocks": [{"id": b, "owned": [_span(text, "p.md", w)]} for b, w in blocks],
        "groups": [{"id": g, "blocks": list(m)} for g, m in groups],
        "entries": list(entries),
        "edges": [{**e, "provenance": e.get("provenance", PROVENANCE)} for e in edges],
        "standings": [
            {
                "block": b,
                "claim": {"text": c},
                "status": "justified",
                "provenance": PROVENANCE,
            }
            for b, c in standings
        ],
        "handles": handles or {},
        "review": {} if update is None else {"update": update},
    }


def keep(
    store: RecordStore,
    version: str,
    text: str,
    payload: dict[str, Any],
    command: Command = Command.BUILD,
    started_from: versions.State | None = None,
    identity: str | None = None,
    answers: Sequence[str] = (),
) -> versions.State:
    found, _ = versions.record(
        store,
        {"p.md": text.encode()},
        version,
        "p.md",
        dumps(payload).encode(),
        command,
        started_from,
        identity,
        answers,
    )
    return found


def identity(
    store: RecordStore,
    old: str,
    new: str,
    rebased: dict[str, Any],
    links: Sequence[tuple[Sequence[str], Sequence[str], str, str | None]],
) -> str:
    """An identity answer from `old` to `new`: NEW's rebased cut and the
    links, each `(old, new, class, heir)`."""
    key = hashlib.sha256(f"{old}->{new}".encode()).hexdigest()
    raw = {"old": old, "new": new, "cut": rebased, "carried": {"from": None}}
    Resolver(store).resolve_identity(
        key,
        "test",
        json.dumps(raw),
        tuple(
            LineageLink("", None, tuple(o), tuple(n), cls, "words", heir)
            for o, n, cls, heir in links
        ),
    )
    return key


def node(state: versions.State, block: str) -> str:
    found = state.node_of(block)
    assert found is not None, block
    return found


V1, V2 = "1" * 64, "2" * 64


def two_versions(
    tmp_path: Path,
    old_text: str,
    old: dict[str, Any],
    new_text: str,
    new: dict[str, Any],
    links: Sequence[tuple[Sequence[str], Sequence[str], str, str | None]],
) -> tuple[RecordStore, versions.State, versions.State]:
    """A build of `old`, then an update to `new` by `links`, its rebased cut
    NEW's own."""
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, old_text, old)
    key = identity(store, V1, V2, new, links)
    second = keep(store, V2, new_text, new, Command.UPDATE, first, key)
    return store, first, second


# --- identity across states ------------------------------------


def test_a_merge_continues_its_first_block_not_its_heir(tmp_path) -> None:
    """The merged block opens with the first block's
    words, its heir by words the second; the start decides."""
    old_text, new_text = "Alpha one. Beta two is long.", "Alpha one, beta two is long."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", "Alpha one."), ("b", "Beta two is long.")]),
        new_text,
        export(new_text, [("c", "Alpha one, beta two is long.")]),
        [(("a", "b"), ("c",), "merged", "b")],
    )

    assert node(second, "c") == node(first, "a") != node(first, "b")
    *_, ended = versions.history(store, node(first, "b"))
    assert ended.block is None and ended.change == "merged"
    assert ended.joined == (node(second, "c"),)
    *_, merged = versions.history(store, "c")
    assert merged.change == "merged" and merged.joined == (node(first, "b"),)


def test_a_split_continues_in_its_first_piece_though_its_label_is_in_the_second(
    tmp_path,
) -> None:
    """Identity's heir holds the label; the node stays with the piece that
    holds the start, whatever order the link names its pieces in."""
    old_text = "First half. Second half, labelled."
    new_text = "First half. Second half, labelled!"
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", old_text)]),
        new_text,
        export(new_text, [("a", "First half."), ("a2", "Second half, labelled!")]),
        [(("a",), ("a2", "a"), "split", "a2")],
    )

    assert node(second, "a") == node(first, "a")
    assert node(second, "a2") not in {n.node for n in first.nodes}
    assert [(s.change, s.block) for s in versions.history(store, "a")] == [
        ("built", "a"),
        ("split", "a"),
    ]
    (began,) = versions.history(store, "a2")
    assert began.change == "split" and began.joined == (node(second, "a"),)


def test_a_split_s_other_piece_is_new_and_the_edge_on_its_words_moves_to_it(
    tmp_path,
) -> None:
    """A split where the part's words land in the piece that is
    not the heir, and the edge's target with them."""
    old_text = "Source text. First half. Second half words."
    new_text = "Source text. First half. Second half words!"
    part = {"id": "a [p]", "anchor": "Second half words"}
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("x", "Source text."), ("a", "First half. Second half words.")],
            edges=[
                {
                    "id": "e1",
                    "source": "x",
                    "kind": "uses",
                    "target": {"block": "a", "entries": ["a [p]"]},
                }
            ],
            entries=[part],
        ),
        new_text,
        export(
            new_text,
            [
                ("x", "Source text."),
                ("a1", "First half."),
                ("a2", "Second half words!"),
            ],
            edges=[
                {
                    "id": "e1",
                    "source": "x",
                    "kind": "uses",
                    "target": {"block": "a2", "entries": ["a2 [p]"]},
                }
            ],
            entries=[{"id": "a2 [p]", "anchor": "Second  half words"}],
        ),
        [(("x",), ("x",), "same", None), (("a",), ("a1", "a2"), "split", "a1")],
    )

    assert node(second, "a1") == node(first, "a")
    assert node(second, "a2") not in {n.node for n in first.nodes}
    before = versions.edge_ids(first, json.loads(versions.kept_export(store, first)))
    after = versions.edge_ids(second, json.loads(versions.kept_export(store, second)))
    assert before["e1"] == (
        node(first, "x"),
        "uses",
        node(first, "a"),
        ("Second half words",),
    )
    assert after["e1"] == (
        node(first, "x"),
        "uses",
        node(second, "a2"),
        ("Second half words",),
    )
    (split,) = versions.history(store, "a2")
    assert split.state == second.id and split.change == "split"
    assert split.joined == (node(second, "a1"),)


def test_a_group_whose_first_member_is_merged_away_continues_with_its_edges(
    tmp_path,
) -> None:
    old_text = "See it. One. Two. Three."
    new_text = "See it. One two. Three."
    uses = {"id": "e", "source": "x", "kind": "uses"}
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("x", "See it."), ("a", "One."), ("b", "Two."), ("c", "Three.")],
            groups=[("proof@a", ("a", "b", "c"))],
            edges=[{**uses, "target": {"group": "proof@a"}}],
        ),
        new_text,
        export(
            new_text,
            [("x", "See it."), ("b", "One two."), ("c", "Three.")],
            groups=[("proof@b", ("b", "c"))],
            edges=[{**uses, "target": {"group": "proof@b"}}],
        ),
        [
            (("x",), ("x",), "same", None),
            (("a", "b"), ("b",), "merged", "b"),
            (("c",), ("c",), "same", None),
        ],
    )

    assert second.groups["proof@b"] == first.groups["proof@a"]
    assert versions.edge_ids(
        first, json.loads(versions.kept_export(store, first))
    ) == versions.edge_ids(second, json.loads(versions.kept_export(store, second)))


def test_a_part_relabelled_and_a_claim_quoted_anew_are_the_same(tmp_path) -> None:
    """Labels are display: a part is its words; a standing, its block's."""
    text = "Uses it. We fix a field. The output is bounded."
    blocks = [
        ("x", "Uses it."),
        ("k", "We fix a field."),
        ("t", "The output is bounded."),
    ]
    edge = {"id": "e", "source": "x", "kind": "uses"}
    store = RecordStore(tmp_path / "store")
    first = keep(
        store,
        V1,
        text,
        export(
            text,
            blocks,
            edges=[{**edge, "target": {"block": "k", "entries": ["k [K]"]}}],
            entries=[{"id": "k [K]", "anchor": "We fix"}],
            standings=[("t", "The output is bounded.")],
        ),
    )
    second = keep(
        store,
        V1,
        text,
        export(
            text,
            blocks,
            edges=[{**edge, "target": {"block": "k", "entries": ["k [field]"]}}],
            entries=[{"id": "k [field]", "anchor": "We  fix"}],
            standings=[("t", "the output is bounded")],
        ),
        Command.EDIT,
        started_from=first,
    )

    assert second.parent == first.id and second.nodes == first.nodes
    one, two = (json.loads(versions.kept_export(store, s)) for s in (first, second))
    assert versions.edge_ids(first, one) == versions.edge_ids(second, two)
    assert versions.continued_parts(
        versions.standing_ids(first, one), versions.standing_ids(second, two)
    ) == {0: 0}


def test_within_one_version_a_block_continues_the_one_holding_most_of_it() -> None:
    before = {"a": [("f", 0, 10)], "b": [("f", 10, 20)], "c": [("f", 20, 30)]}
    # a's boundary moved into b; b and c joined
    after = {"a2": [("f", 0, 14)], "bc": [("f", 14, 30)], "z": [("g", 0, 5)]}

    assert continuation.by_overlap(before, after) == {"a2": "a", "bc": "c"}


def test_sharing_counts_every_run_a_block_shares_with_another() -> None:
    """Each block of the later cut and the earlier blocks it shares
    characters with, in the earlier cut's order: across several runs and
    files, and where an earlier run lies within a longer one."""
    before = {
        "long": [("f", 0, 100)],
        "inner": [("f", 40, 50)],
        "g": [("g", 0, 10), ("f", 120, 130)],
        "tail": [("f", 100, 120)],
    }
    after = {
        "x": [("f", 95, 125)],
        "y": [("g", 5, 6), ("f", 45, 46)],
        "z": [("h", 0, 10)],
    }

    assert continuation.sharing(before, after) == {
        "x": ["long", "g", "tail"],
        "y": ["long", "inner", "g"],
        "z": [],
    }
    # x shares 5 with long, 5 with g, 20 with tail: tail is the largest
    assert continuation.by_overlap(before, after) == {"x": "tail", "y": "long"}


def test_within_one_version_a_block_keeping_its_start_keeps_its_node() -> None:
    """A split's first piece keeps the node though the second is
    longer; a merge's result keeps the first block's; a boundary moved keeps
    both; a block whose start moved continues by shared characters."""
    before = {
        "a": [("f", 0, 30)],
        "b": [("f", 30, 40)],
        "c": [("f", 40, 50)],
        "d": [("f", 50, 60)],
        "e": [("f", 60, 70)],
    }
    after = {
        "a": [("f", 0, 10)],
        "a2": [("f", 10, 30)],  # split off a, the longer piece
        "b": [("f", 30, 50)],  # b took in c
        "d": [("f", 50, 62)],  # d's end moved into e
        "e2": [("f", 62, 70)],
    }

    assert continuation.by_overlap(before, after)["a2"] == "a"  # by size alone
    assert versions.within(before, after) == {"a": "a", "b": "b", "d": "d", "e2": "e"}


# --- a block's history (three states) -------------------------------------------------


def test_a_block_s_history_names_each_step_s_class_and_cause(tmp_path) -> None:
    """A build; an update with a changed, a moved, an added and a deleted
    block and a split; then a person's edit of the same version."""
    old_text = "Aa. Bb. Cc. Dd. Ee ff."
    new_text = "Aa. Cc. Bb narrowed. Ee. Ff. New."
    blocks = [
        ("a", "Aa."),
        ("c", "Cc."),
        ("b", "Bb narrowed."),
        ("e1", "Ee."),
        ("e2", "Ff."),
        ("n", "New."),
    ]
    update = {
        "deltas": [
            {
                "block": "b",
                "entry": None,
                "class": "narrowed",
                "note": "only some",
                "by": "writer",
            },
            {
                "block": "deleted:d",
                "entry": None,
                "class": "removed",
                "note": "deleted in this version",
                "by": "rule",
            },
        ],
        "reopened": [{"handle": "cc", "why": "moved", "session": 1, "from": None}],
    }
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("a", "Aa."), ("b", "Bb."), ("c", "Cc."), ("d", "Dd."), ("e", "Ee ff.")],
        ),
        new_text,
        export(new_text, blocks, update=update, handles={"c": "cc"}),
        [
            (("a",), ("a",), "same", None),
            (("b",), ("b",), "changed", None),
            (("c",), ("c",), "moved", None),
            (("d",), (), "deleted", None),
            (("e",), ("e1", "e2"), "split", "e1"),
            ((), ("n",), "added", None),
        ],
    )
    third = keep(
        store,
        V2,
        new_text,
        export(new_text, blocks, update=update, handles={"c": "cc", "a": "aa"}),
        Command.EDIT,
        started_from=second,
        answers=["k1"],
    )

    def steps(name: str) -> list[tuple[str, str | None]]:
        return [(s.change, s.block) for s in versions.history(store, name)]

    assert third.parent == second.id
    assert steps("b") == [("built", "b"), ("changed", "b"), ("same", "b")]
    (_, changed, edited) = versions.history(store, "b")
    assert changed.causes[0]["delta"] == "narrowed"
    assert edited.causes == ({"person": "k1"},)
    assert steps("c") == [("built", "c"), ("moved", "c"), ("same", "c")]
    assert versions.history(store, "c")[1].causes == (
        {"reopened": "moved", "from": None},
    )
    assert steps("n") == [("added", "n"), ("same", "n")]
    assert steps("e1") == [("built", "e"), ("split", "e1"), ("same", "e1")]
    assert steps("e2") == [("split", "e2"), ("same", "e2")]
    assert versions.history(store, "e2")[0].joined == (node(second, "e1"),)
    deleted = versions.history(store, node(first, "d"))
    assert [(s.change, s.block) for s in deleted] == [("built", "d"), ("deleted", None)]
    assert deleted[-1].causes[0]["delta"] == "removed"


def test_a_block_s_history_names_the_edges_each_step_began_ended_or_changed(
    tmp_path,
) -> None:
    """An edit that ends one edge, begins another by a person, names other
    parts on a third and rewords a fourth: each end's step names the three
    changed, the parts' change as one, the reworded one not."""
    text = "Aa. Bb. Cc."
    blocks = [("a", "Aa."), ("b", "Bb."), ("c", "Cc.")]

    def edge(source: str, kind: str, target: str, why: str, by: str = "agent", **at):
        return {
            "id": None,
            "source": source,
            "kind": kind,
            "target": {"block": target, **at},
            "evidence": [{"text": why}],
            "provenance": {"by": by},
        }

    store = RecordStore(tmp_path / "store")
    first = keep(
        store,
        V1,
        text,
        export(
            text,
            blocks,
            edges=[
                edge("a", "uses", "b", "x"),
                edge("c", "uses", "b", "y"),
                edge("b", "uses", "c", "w"),
            ],
        ),
    )
    keep(
        store,
        V1,
        text,
        export(
            text,
            blocks,
            edges=[
                edge("c", "uses", "b", "y, reworded"),
                edge("a", "about", "c", "z", "person"),
                edge("b", "uses", "c", "w", entries=["c [K]"]),
            ],
        ),
        Command.EDIT,
        started_from=first,
        answers=["k1"],
    )
    a, b, c = (node(first, x) for x in "abc")
    (built, edited) = versions.history(store, a)
    assert built.edges == ()
    assert {(e.step, e.kind, e.source, e.target, e.by) for e in edited.edges} == {
        (EdgeStep.ENDED, "uses", a, b, "agent"),
        (EdgeStep.BEGAN, "about", a, c, "person"),
    }
    (changed,) = [e for e in versions.history(store, c)[1].edges if e.kind == "uses"]
    assert (changed.step, changed.source, changed.parts, changed.was) == (
        EdgeStep.CHANGED,
        b,
        ("c [K]",),
        (),
    )
    assert {e.kind for e in versions.history(store, c)[1].edges} == {"uses", "about"}
    assert sorted(e.step for e in versions.history(store, b)[1].edges) == sorted(
        [EdgeStep.CHANGED, EdgeStep.ENDED]
    ), "its own edge's parts changed, and the edge onto it ended"
    assert edited.data()["edges"][0].keys() == {
        "step",
        "kind",
        "source",
        "target",
        "parts",
        "was",
        "by",
        "piece",
    }


def test_edges_of_one_kind_and_ends_pair_in_the_lines_order(tmp_path) -> None:
    """Four `about` lines from a to b end and five begin, each on other
    parts: the first four begun pair with the four ended, in the lines'
    order, as changed, and the fifth began; then five end and two begin:
    two pair, three end. Listed in that order, whatever order a set gives
    them."""
    text = "Aa. Bb."
    blocks = [("a", "Aa."), ("b", "Bb.")]

    def about(n: int) -> dict[str, Any]:
        return {
            "id": None,
            "source": "a",
            "kind": "about",
            "target": {"block": "b", "entries": [f"b [P{n}]"]},
            "evidence": [{"text": f"line {n}"}],
        }

    store = RecordStore(tmp_path / "store")
    first = keep(
        store, V1, text, export(text, blocks, edges=[about(n) for n in range(4)])
    )
    second = keep(
        store,
        V1,
        text,
        export(text, blocks, edges=[about(n) for n in range(10, 15)]),
        Command.EDIT,
        started_from=first,
        answers=["k1"],
    )
    keep(
        store,
        V1,
        text,
        export(text, blocks, edges=[about(n) for n in (20, 21)]),
        Command.EDIT,
        started_from=second,
        answers=["k2"],
    )
    a = node(first, "a")
    (_, edited, fewer) = versions.history(store, a)
    assert [(e.step, e.parts, e.was) for e in edited.edges] == [
        *((EdgeStep.CHANGED, (f"b [P{10 + n}]",), (f"b [P{n}]",)) for n in range(4)),
        (EdgeStep.BEGAN, ("b [P14]",), ()),
    ]
    # five end and two begin: the first two pair, the rest end in order
    assert [(e.step, e.parts, e.was) for e in fewer.edges] == [
        (EdgeStep.CHANGED, ("b [P20]",), ("b [P10]",)),
        (EdgeStep.CHANGED, ("b [P21]",), ("b [P11]",)),
        *((EdgeStep.ENDED, (f"b [P{n}]",), ()) for n in (12, 13, 14)),
    ]


def test_an_edge_a_split_carried_to_the_other_piece_is_carried_not_ended(
    tmp_path,
) -> None:
    """A block split in two, the edge onto its second half now onto the
    piece that holds it: carried there from the block's side, carried from
    it on the piece's; an edge the split truly ended stays ended."""
    old_text = "Aa bb. Cc dd. See it."
    new_text = "Aa bb. Cc dd. See it."

    def uses(source: str, target: str, why: str) -> dict[str, Any]:
        return {
            "id": None,
            "source": source,
            "kind": "uses",
            "target": {"block": target},
            "evidence": [{"text": why}],
            "provenance": {"by": "agent"},
        }

    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("e", "Aa bb. Cc dd."), ("x", "See it.")],
            edges=[uses("x", "e", "it"), uses("e", "x", "see")],
        ),
        new_text,
        export(
            new_text,
            [("e1", "Aa bb."), ("e2", "Cc dd."), ("x", "See it.")],
            edges=[uses("x", "e2", "it")],
        ),
        [(("e",), ("e1", "e2"), "split", "e1"), (("x",), ("x",), "same", None)],
    )
    e, x = node(first, "e"), node(first, "x")
    e2 = node(second, "e2")
    (_, split) = versions.history(store, e)
    assert {(c.step, c.source, c.target, c.piece) for c in split.edges} == {
        (EdgeStep.CARRIED_TO, x, e, e2),
        (EdgeStep.ENDED, e, x, None),
    }
    (begun,) = versions.history(store, e2)
    assert [(c.step, c.source, c.target, c.piece) for c in begun.edges] == [
        (EdgeStep.CARRIED_FROM, x, e2, e)
    ]


# --- block texts, blobs, states -------------------------------------------------------


def test_block_texts_are_kept_once_each_as_the_kept_files_hold_them(tmp_path) -> None:
    text = "Same. Other. Same."
    store = RecordStore(tmp_path / "store")
    payload = export(text, [("a", "Same."), ("b", "Other.")])
    payload["blocks"].append({"id": "c", "owned": [_span(text, "p.md", "Same.")]})
    payload["blocks"][2]["owned"][0]["start"] = [1, 14]
    payload["blocks"][2]["owned"][0]["end"] = [1, 19]
    first = keep(store, V1, text, payload)
    stored = versions.texts(store)

    assert len(stored) == 2  # a and c share one
    assert {n.block: stored[n.text] for n in first.nodes} == {
        "a": ("Same.",),
        "b": ("Other.",),
        "c": ("Same.",),
    }
    (first_entry,) = record_file.entries(store.directory)
    assert len(first_entry["body"]["texts"]) == 2
    changed = text.replace("Other.", "Moved.")
    keep(store, V2, changed, export(changed, [("a", "Same."), ("b", "Moved.")]))
    grown = record_file.entries(store.directory)
    assert grown[0] == first_entry
    (added,) = grown[1]["body"]["texts"].values()
    assert added == ["Moved."]


def test_a_comment_inserted_mid_paragraph_changes_the_text_not_the_hash(
    tmp_path,
) -> None:
    rules = CutRules(DisplayRule.IN_PARAGRAPH)
    store = RecordStore(tmp_path / "store")
    found = {}
    for name, main_text in (
        ("old", MAIN),
        (
            "new",
            MAIN.replace(
                "A second sentence", "A % a note mid-sentence\nsecond sentence"
            ),
        ),
    ):
        paper = make_paper(tmp_path / name, main=main_text)
        files = versions.snapshot(paper.root, paper.document)
        payload = cut_payload(paper.document, cut(paper.document, rules))
        kept, _ = versions.record(
            store,
            files,
            document_hash(paper.document),
            paper.document.root,
            dumps(payload).encode(),
            Command.BUILD,
        )
        block = paper.id_at("We fix a field")
        (entry,) = [b for b in payload["blocks"] if b["id"] == block]
        found[name] = (
            entry["hash"],
            next(n.text for n in kept.nodes if n.block == block),
        )

    assert found["old"][0] == found["new"][0]
    assert found["old"][1] != found["new"][1]
    # the comment is no owned text, but the run it splits is two
    assert versions.texts(store)[found["new"][1]] == (
        "We fix a field of scalars.",
        "A",
        "second sentence of the setting.",
    )


def _blobs_held(store: RecordStore) -> dict[str, bytes]:
    """Every blob the record's state entries hold, as written (compressed
    and encoded); a name held twice fails."""
    held: dict[str, str] = {}
    for entry in record_file.entries(store.directory):
        if entry["kind"] != "state":
            continue
        for name, encoded in entry["body"]["blobs"].items():
            assert name not in held, f"blob {name[:12]} is held twice"
            held[name] = encoded
    return held


def test_a_blob_is_kept_once_per_content_and_never_written_over(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    text = "One."
    first = keep(store, V1, text, export(text, [("a", "One.")]))
    blobs = _blobs_held(store)

    assert keep(store, V1, text, export(text, [("a", "One.")])) == first
    # a second state naming the same blobs adds none, and writes none over
    keep(store, V2, text, export(text, [("a", "One.")]))
    assert _blobs_held(store) == blobs
    name = hashlib.sha256(b"One.").hexdigest()
    assert zlib.decompress(base64.b64decode(blobs[name])) == b"One."
    record_file.rewrite_blob(store.directory, name, b"Two.")
    with pytest.raises(VersionError, match="does not hold its content"):
        versions.blob(store, name)


def test_a_blob_read_is_held_and_one_the_record_does_not_hold_right_is_refused(
    tmp_path,
) -> None:
    """A blob read once is not decompressed again; a blob whose bytes are
    not its name's is refused when read, and one the record lacks is
    refused as missing."""
    store = RecordStore(tmp_path / "store")
    text = "One."
    keep(store, V1, text, export(text, [("a", "One.")]))
    name = hashlib.sha256(b"One.").hexdigest()

    assert versions.blob(store, name) == b"One."
    assert versions.blob(store, name) is versions.blob(store, name)
    # a copy of the store, so no blob this process holds answers for it
    other = tmp_path / "other"
    shutil.copytree(store.directory, other)
    record_file.rewrite_blob(other, name, b"Two, longer.")
    with pytest.raises(VersionError, match="does not hold its content"):
        versions.blob(RecordStore(other), name)
    record_file.rewrite_blob(other, name, None)
    with pytest.raises(VersionError, match="holds no blob"):
        versions.blob(RecordStore(other), name)


def test_a_state_keeps_its_export_as_its_lines_and_renders_it_back(tmp_path) -> None:
    """A state keeps no export blob and no node table in its row: its
    lines give both, the export byte for byte; a later state keeps only
    the sections it changes."""
    store = RecordStore(tmp_path / "store")
    text = "One. Two."
    handles = {"a": "one", "b": "two"}
    payload = export(text, [("a", "One."), ("b", "Two.")], handles=handles)
    first = keep(store, V1, text, payload)
    (entry,) = record_file.entries(store.directory)

    assert first.export not in entry["body"]["blobs"]
    assert "nodes" not in entry["body"]["state"]
    assert versions.kept_export(store, first) == dumps(payload).encode()
    assert [n.block for n in versions.latest(store).nodes] == ["a", "b"]

    edge = {
        "id": "f00d-1-0",
        "source": "a",
        "evidence": [{"text": "One.", "at": _span(text, "p.md", "One.")}],
        "target": {"block": "b"},
        "kind": "uses",
    }
    edited = export(text, [("a", "One."), ("b", "Two.")], edges=[edge], handles=handles)
    second = keep(store, V1, text, edited, Command.EDIT, started_from=first)
    lines = record_file.entries(store.directory)[1]["body"]["lines"]

    assert set(lines) == {"sections", "edge_ids"}
    assert set(lines["sections"]) == {"edges"}
    assert versions.kept_export(store, second) == dumps(edited).encode()
    assert versions.kept_export(store, first) == dumps(payload).encode()


def test_a_file_is_kept_as_its_change_of_the_one_its_parent_kept(tmp_path) -> None:
    """A text file a later state keeps changed is held as the runs of its
    parent's lines it replaces, and read back whole; a first file, and one
    whose change is not the smaller, whole."""
    store = RecordStore(tmp_path / "store")
    text = "".join(
        f"Line {n} of the paper, long enough to compress.\n" for n in range(60)
    )
    first = keep(store, V1, text, export(text, [("a", "Line 0")]))
    changed = text.replace("Line 30 of", "Line thirty of")
    second = keep(
        store, V2, changed, export(changed, [("a", "Line 0")]), started_from=first
    )
    name = second.files["p.md"]
    held = record_file.entries(store.directory)[1]["body"]["blobs"][name]

    assert held == {
        "from": first.files["p.md"],
        "lines": [[30, 31, ["Line thirty of the paper, long enough to compress.\n"]]],
    }
    assert versions.blob(store, name) == changed.encode()
    assert isinstance(
        record_file.entries(store.directory)[0]["body"]["blobs"][first.files["p.md"]],
        str,
    )


def test_an_export_its_lines_do_not_give_back_is_kept_whole(tmp_path) -> None:
    """An export not in the exchange file's form (here, not indented)
    does not render back from its lines: it is kept whole, as written."""
    store = RecordStore(tmp_path / "store")
    text = "One."
    written = json.dumps(export(text, [("a", "One.")])).encode()
    found, _ = versions.record(
        store, {"p.md": text.encode()}, V1, "p.md", written, Command.BUILD
    )
    (entry,) = record_file.entries(store.directory)

    assert found.export in entry["body"]["blobs"]
    assert versions.kept_export(store, found) == written


def test_what_a_process_holds_of_blobs_stays_in_bounds_across_threads() -> None:
    """The viewer reads on several threads: reads and evictions at once
    never fail, the least recently used value goes first, and the weight
    held stays within the bound."""
    from concurrent.futures import ThreadPoolExecutor

    held: versions.Held[int] = versions.Held(limit=10)
    stamp = (1, 1)

    def churn(n: int) -> None:
        for i in range(500):
            path = Path(f"/blob/{(n * 7 + i) % 23}")
            if held.get(path, stamp) is None:
                held.put(path, stamp, i, 3)

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(churn, range(8)))

    assert held._weight <= 10 and len(held._held) <= 3
    held.put(Path("/a"), stamp, 1, 3)
    held.put(Path("/b"), stamp, 2, 3)
    held.put(Path("/c"), stamp, 3, 3)
    assert held.get(Path("/a"), stamp) == 1  # used: /b is now the oldest
    held.put(Path("/d"), stamp, 4, 3)
    assert held.get(Path("/b"), stamp) is None
    assert held.get(Path("/a"), stamp) == 1
    assert held.get(Path("/a"), (2, 2)) is None  # a file that changed


def test_an_export_s_outline_is_what_its_export_says(tmp_path, monkeypatch) -> None:
    """The outline a walk reads holds the export's ids in order, its
    handles, its update's record (each reader its own copy) and each
    block's owned runs; held by its export's content."""
    store = RecordStore(tmp_path / "store")
    text = "One. Two."
    update = {"deltas": [{"block": "a", "class": "widened"}], "stale": []}
    payload = export(
        text,
        [("a", "One."), ("b", "Two.")],
        groups=[("g1", ["a", "b"])],
        update=update,
        handles={"a": "one", "b": "two", "g1": "both"},
    )
    kept = keep(store, V1, text, payload)
    data = json.loads(versions.kept_export(store, kept))
    assert data["review"]["update"] == update

    outline = versions.export_outline(store, kept)

    assert outline.order == ("a", "b", "g1")
    assert dict(outline.handles) == {"a": "one", "b": "two", "g1": "both"}
    first = outline.update_record()
    assert first == update
    first["deltas"].clear()
    assert outline.update_record() == update
    assert outline.owned == tuple(
        (b["id"], tuple((r["file"], *r["start"], *r["end"]) for r in b["owned"]))
        for b in data["blocks"]
    )
    assert versions.export_outline(store, kept) is outline

    # an outline, and an export rendered once, is named by its export's
    # content and so held, whichever store asks; rendered anew, the export
    # is refused where its lines do not give its bytes
    other = tmp_path / "other"
    shutil.copytree(store.directory, other)
    monkeypatch.setenv(derived.CACHE_VARIABLE, str(tmp_path / "fresh-cache"))
    record_file.rewrite_lines(other, kept.id, lambda lines: lines | {"skeleton": {}})
    with pytest.raises(VersionError, match="do not give its export"):
        versions.kept_export(RecordStore(other), kept)


def test_an_export_s_outline_names_the_given_answers_it_found_stale(tmp_path) -> None:
    """What `pending_answers` reads of every state of a version: the
    person's stale answers its export records, none where it records
    none."""
    store = RecordStore(tmp_path / "store")
    text = "One."
    payload = export(text, [("a", "One.")])
    plain = keep(store, V1, text, payload)
    assert versions.export_outline(store, plain).stale == ()
    payload["review"] = {"person": {"stale": ["k1", "k2"]}}
    marked = keep(store, V1, text, payload)
    assert versions.export_outline(store, marked).stale == ("k1", "k2")


def test_the_states_and_texts_are_read_once_while_their_files_stand(tmp_path) -> None:
    """The kept states and texts are parsed once while their files stand,
    and read again once a state is kept; each caller has its own texts."""
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, "One.", export("One.", [("a", "One.")]))
    assert versions.states(store) is versions.states(store)
    stamp = versions.kept_stamp(store)
    texts = versions.texts(store)
    texts.clear()
    assert versions.texts(store)

    second = keep(store, V2, "Two.", export("Two.", [("a", "Two.")]))

    assert versions.kept_stamp(store) != stamp
    assert versions.states(store) == (first, second)
    assert second.nodes[0].text in versions.texts(store)


def test_a_transition_is_worked_out_once_as_a_fresh_one_works_it_out(
    tmp_path,
) -> None:
    """A kept state never changes, so neither does the step between two:
    worked out once a process, the same as worked out afresh."""
    store, first, second = two_versions(
        tmp_path,
        "One. Two.",
        export("One. Two.", [("a", "One."), ("b", "Two.")]),
        "One. Three.",
        export("One. Three.", [("a", "One."), ("b", "Three.")]),
        [(["a"], ["a"], "same", None), (["b"], ["b"], "changed", None)],
    )

    held = versions.transition(store, first, second)

    assert versions.transition(store, first, second) is held
    assert held == versions._transition(store, first, second)
    assert held.across and held.link_of_new["b"].cls == "changed"


def test_a_state_s_document_is_read_once(tmp_path) -> None:
    """Today's reader reads a state's kept files once a process: they never
    change."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    files = versions.snapshot(paper.root, paper.document)
    payload = cut_payload(paper.document, paper.cut)
    kept, _ = versions.record(
        store, files, "f" * 64, "main.tex", dumps(payload).encode(), Command.BUILD
    )

    found = versions.document_at(store, kept)

    assert versions.document_at(store, kept) is found
    assert found.version == "f" * 64


def test_a_state_opens_by_its_kept_version_whatever_today_s_reader_hashes(
    tmp_path,
) -> None:
    """The version a state was kept as names its text: a reader that hashes
    its intact files otherwise reads them, and the document carries the
    kept id, which every key and answer on the state uses."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    files = versions.snapshot(paper.root, paper.document)
    payload = cut_payload(paper.document, paper.cut)
    kept, _ = versions.record(
        store, files, "f" * 64, "main.tex", dumps(payload).encode(), Command.BUILD
    )

    found = versions.materialize(store, kept, tmp_path / "copy")

    assert document_hash(found) == "f" * 64 == found.version
    assert files_hash(found) == document_hash(paper.document)


def test_a_kept_file_that_does_not_hold_its_content_is_refused(tmp_path) -> None:
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    files = versions.snapshot(paper.root, paper.document)
    payload = cut_payload(paper.document, paper.cut)
    kept, _ = versions.record(
        store,
        files,
        document_hash(paper.document),
        "main.tex",
        dumps(payload).encode(),
        Command.BUILD,
    )
    record_file.rewrite_blob(
        store.directory,
        kept.files["extra.tex"],
        b"\\section{More}\nAnother paragraph.\n",
    )

    with pytest.raises(VersionError, match="does not hold its content"):
        versions.materialize(store, kept, tmp_path / "copy")


def test_a_state_whose_files_today_s_reader_reads_otherwise_is_refused(
    tmp_path,
) -> None:
    """What still refuses: a file the export names (one its reader read)
    that today's reader does not read, and text today's reader reads
    beyond the export's files, which the kept cut cannot cover."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    files = versions.snapshot(paper.root, paper.document)
    version = document_hash(paper.document)
    named = cut_payload(paper.document, paper.cut)
    named["document"]["files"].append(
        {"path": "notes.tex", "sha256": hashlib.sha256(b"A note.\n").hexdigest()}
    )
    kept, _ = versions.record(
        store,
        {**files, "notes.tex": b"A note.\n"},
        version,
        "main.tex",
        dumps(named).encode(),
        Command.BUILD,
    )
    with pytest.raises(VersionError, match="as its export names them: notes.tex"):
        versions.materialize(store, kept, tmp_path / "copy")

    unnamed = cut_payload(paper.document, paper.cut)
    unnamed["document"]["files"] = [
        f for f in unnamed["document"]["files"] if f["path"] != "extra.tex"
    ]
    beyond, _ = versions.record(
        store, files, version, "main.tex", dumps(unnamed).encode(), Command.BUILD
    )
    with pytest.raises(VersionError, match="as its export names them: extra.tex"):
        versions.materialize(store, beyond, tmp_path / "beyond")


def test_a_state_is_read_again_with_the_vocabulary_map_its_export_was_read_with(
    tmp_path,
) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "paper.md").write_text(
        "\\begin{lawbox}\nOne.\n\nTwo.\n\\end{lawbox}\n", encoding="utf-8"
    )
    mapped = Vocabulary(environments=(EnvEntry("lawbox", VocabularyKind.CONTAINER),))
    document = read_paper(paper, vocabulary=mapped)
    store = RecordStore(tmp_path / "store")
    payload = cut_payload(document, cut(document, CutRules()))
    kept, _ = versions.record(
        store,
        versions.snapshot(paper, document),
        document_hash(document),
        "paper.md",
        dumps(payload).encode(),
        Command.BUILD,
    )

    again = versions.materialize(store, kept, tmp_path / "copy")

    assert again.vocabulary == mapped
    assert [env.kind for env in again.environments] == [EnvKind.CONTAINER]


# --- the commands, on the synthetic paper's own store ---------------------------------


def _grown(directory: Path, before: dict[Path, bytes]) -> dict[Path, bytes]:
    """Every file under `directory` now but a run's working state (run
    data, written over and dropped as the run goes); each one before still
    there, its old bytes a prefix of its new (nothing destroyed)."""
    now = {
        p: p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file() and run_state.RUNS not in p.relative_to(directory).parts
    }
    for path, data in before.items():
        assert path in now, f"{path} is gone"
        assert now[path].startswith(data), f"{path} was written over"
    return now


def one_edge_host() -> FakeHost:
    """The build's host, its edges one line: an update from the export
    finds its edges answer by an edge's provenance."""
    host = build_host()
    host.edits[EDGES_FILE] = lambda text: written(
        text, {"closing-words-of-the": ['→ about scope:paper · "Closing words"']}, []
    )
    return host


def built(tmp_path: Path, capsys) -> tuple[Path, Path, dict[Path, bytes]]:
    """The synthetic paper built into its own store by a fake host, the
    build replayed by the command, which keeps its state; OLD's files and
    export copied aside for comparison."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(paper.root / STORE_DIRECTORY)
    review = review_cut(
        paper.document,
        paper.cut,
        store,
        ReviewSettings(stages=BUILD_STAGES, files=FILES),
        Runner(one_edge_host(), Resolver(store), 10.0, FLAT),
    )
    assert review.complete, review.unanswered
    seen = _grown(store.directory, {})
    capsys.readouterr()
    assert (
        main(
            [
                "build",
                "--json",
                str(paper.root),
                "--host",
                "none",
                "--json",
                "-o",
                str(tmp_path / "old.json"),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["state"]["parent"] is None
    seen = _grown(store.directory, seen)
    shutil.copytree(
        paper.root, tmp_path / "old", ignore=shutil.ignore_patterns(STORE_DIRECTORY)
    )
    return paper.root, store.directory, seen


def _main(
    args: Sequence[str], capsys, seen: dict[Path, bytes], store: Path
) -> tuple[int, str, dict[Path, bytes]]:
    capsys.readouterr()
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out + out.err, _grown(store, seen)


@pytest.mark.usefixtures("develop_mode")
def test_build_then_update_in_place_from_the_store_alone(tmp_path, capsys) -> None:
    root, directory, seen = built(tmp_path, capsys)
    store = RecordStore(directory)
    (first,) = versions.states(store)
    assert set(first.files) == {"extra.tex", "main.tex"}
    assert (directory / ".gitignore").read_text(encoding="utf-8") == versions.GITIGNORE

    # the author edits the paper in place: the store alone gives OLD
    (root / "main.tex").write_text(EDITED_SETTING, encoding="utf-8")
    code, dry, seen = _main(
        ["update", "--json", str(root), "--host", "opencode", "--dry-run", "--json"],
        capsys,
        seen,
        directory,
    )
    assert code == 0, dry
    old_dry = [
        "update",
        "--json",
        str(root),
        "--host",
        "opencode",
        "--dry-run",
        "--json",
        "--store",
        str(directory),
    ]
    old_dry += ["--from", str(tmp_path / "old"), "--cut", str(tmp_path / "old.json")]
    code, given, seen = _main(old_dry, capsys, seen, directory)
    assert code == 0 and json.loads(dry) == json.loads(given)

    code, out, seen = _main(
        ["update", "--json", str(root), "--host", "none"], capsys, seen, directory
    )
    assert code == 5  # the identity answer kept, its sessions unanswered
    # from OLD's directory, on a copy of the store: the same identity answer
    other = tmp_path / "other-store"
    shutil.copytree(directory, other, ignore=shutil.ignore_patterns(".lock", ".run"))
    other_store = RecordStore(other)
    before = {a.key for a in other_store.authoritative().values()}
    given = ["update", "--json", str(root), "--store", str(other), "--host", "none"]
    given += ["--from", str(tmp_path / "old"), "--cut", str(tmp_path / "old.json")]
    assert main(given) == 5
    assert {a.key for a in other_store.authoritative().values()} == before
    new = read_paper(root)
    settings = UpdateSettings(
        files=FILES,
        review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
        paper=root.resolve(),
    )
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    held = store.held_identity(key)
    assert held is not None
    answered = run_update(
        new,
        store,
        settings,
        Runner(
            stepping(answering({"we-fix-a-field": ["Δ reworded — a clause added"]})),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )
    assert answered.complete, answered.unanswered
    seen = _grown(directory, seen)

    output = tmp_path / "new.json"
    code, out, seen = _main(
        ["update", "--json", str(root), "--host", "none", "-o", str(output)],
        capsys,
        seen,
        directory,
    )
    assert code == 0, out
    first, second = versions.states(store)
    assert second.parent == first.id and second.identity == key
    assert second.command is Command.UPDATE

    # the update's step: its identity answer less what the states give,
    # given back whole but for the carried lines' authors; the payload
    # held until it was kept is dropped
    state_id, step = store.kept_step(key)
    assert state_id == second.id and "links" not in step
    assert store.held_identity(key) is None
    raw = versions.identity_raw(store, store.authoritative()[key])
    assert "origins" not in raw["carried"] and raw == versions.step_raw(store, key)
    # given back as the run held it, the carried sheet from its lines alone
    held["carried"].pop("origins", None)
    assert raw == held
    assert set(step["raw"]["carried"]["sheet"]) == {"lines"}
    # and what later reads take from its session's answer, filed with it
    assert answered.answer is not None
    assert versions.session_facts(store, key) == json.loads(
        json.dumps(answered.session_facts(new))
    )

    # a re-run of the same update keeps no new state
    code, out, seen = _main(
        ["update", "--json", str(root), "--host", "none"], capsys, seen, directory
    )
    assert code == 0, out
    assert len(versions.states(store)) == 2

    # every block keeps its node; the edited one alone has a new text
    assert {n.block: n.node for n in second.nodes} == {
        n.block: n.node for n in first.nodes
    }
    (edited,) = [n for n in second.nodes if n not in first.nodes]
    assert "edited" in "".join(versions.texts(store)[edited.text])
    steps = versions.history(store, edited.node)
    assert [s.change for s in steps] == ["built", "changed"]
    assert {
        "delta": "reworded",
        "entry": None,
        "note": "a clause added",
        "by": "writer",
    } in steps[1].causes

    # each node's text is its block's owned text in the kept files
    payload = json.loads(output.read_text(encoding="utf-8"))
    texts = versions.texts(store)
    text = (root / "main.tex").read_text(encoding="utf-8")
    lines = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    for block in payload["blocks"]:
        runs = tuple(
            text[
                lines[r["start"][0] - 1] + r["start"][1] - 1 : lines[r["end"][0] - 1]
                + r["end"][1]
                - 1
            ]
            for r in block["owned"]
            if r["file"] == "main.tex"
        )
        if len(runs) == len(block["owned"]):
            assert (
                texts[next(n.text for n in second.nodes if n.block == block["id"])]
                == runs
            )

    # the graph reads back as written, and the history through the command
    code, out, seen = _main(["graph", str(root)], capsys, seen, directory)
    assert code == 0 and out == output.read_text(encoding="utf-8")
    code, out, seen = _main(
        ["graph", str(root), "--state", first.id[:8]], capsys, seen, directory
    )
    assert out == (tmp_path / "old.json").read_text(encoding="utf-8")
    code, out, seen = _main(
        ["history", str(root), edited.block], capsys, seen, directory
    )
    assert code == 0 and [s["change"] for s in json.loads(out)] == ["built", "changed"]
    code, out, seen = _main(["states", str(root)], capsys, seen, directory)
    assert code == 0 and out.splitlines()[1].startswith(f"* {second.id}")

    # the update from a named state, the first: as from OLD's directory
    code, out, seen = _main(
        [
            "update",
            "--json",
            str(root),
            "--state",
            first.id,
            "--host",
            "opencode",
            "--dry-run",
            "--json",
        ],
        capsys,
        seen,
        directory,
    )
    code_given, given, seen = _main(old_dry, capsys, seen, directory)
    assert code == code_given == 0 and json.loads(out) == json.loads(given)
    assert json.loads(out)["answered"] == 1


def test_an_update_of_the_built_version_is_refused(tmp_path, capsys) -> None:
    root, directory, seen = built(tmp_path, capsys)
    code, out, _ = _main(
        ["update", "--json", str(root), "--host", "none"], capsys, seen, directory
    )
    assert code == 1 and "nothing to update" in out


def test_another_store_on_a_paper_with_its_own_is_named(tmp_path, capsys) -> None:
    root, directory, _ = built(tmp_path, capsys)
    other = tmp_path / "other"
    shutil.copytree(directory, other)
    code = main(["build", "--json", str(root), "--store", str(other), "--host", "none"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["state"]["paper_store_not_used"] == str(directory)


def test_the_reader_never_reads_the_store(tmp_path) -> None:
    paper = make_paper(tmp_path / "paper")
    (paper.root / STORE_DIRECTORY).mkdir()
    (paper.root / STORE_DIRECTORY / "notes.md").write_text("a note", encoding="utf-8")
    (paper.root / "draft.tex").write_text("unused", encoding="utf-8")

    assert read_paper(paper.root).never_included == ("draft.tex",)


def test_every_command_promising_the_paper_s_own_store_defaults_to_it() -> None:
    """A `--store` whose help names the paper's own as its default is set
    to it when left out."""
    subcommands = next(
        a for a in parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    promising = {
        name
        for name, command in subcommands.choices.items()
        for action in command._actions
        if "--store" in action.option_strings and "(default: PAPER/" in action.help
    }

    assert promising and promising <= set(KEPT_COMMANDS)


def test_the_default_store_keeps_its_runs_files_out_of_git(tmp_path, capsys) -> None:
    root, directory, _ = built(tmp_path, capsys)
    (directory / "transcripts").mkdir(exist_ok=True)
    (directory / "transcripts" / "a-1.jsonl").write_text("{}\n", encoding="utf-8")

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    listed = git("status", "--porcelain", "--untracked-files=all").split("\n")
    names = {line[3:] for line in listed if line}

    assert f"{STORE_DIRECTORY}/{RECORD}" in names
    assert f"{STORE_DIRECTORY}/.gitignore" in names
    assert f"{STORE_DIRECTORY}/.gitattributes" in names
    assert not any(n.endswith((".lock", ".run")) or "/transcripts/" in n for n in names)


# --- the review's findings ------------------------------------------


def test_a_kept_path_outside_the_paper_or_a_bad_blob_name_is_refused(
    tmp_path,
) -> None:
    """`record.jsonl` comes with the paper's git: it never names a file
    outside the directory it is materialized into."""
    store = RecordStore(tmp_path / "store")
    text = "One."
    kept = keep(store, V1, text, export(text, [("a", "One.")]))
    (blob_name,) = kept.files.values()
    for path in ("../escaped.md", str(tmp_path / "abs.md")):
        crafted = versions.State(**{**_fields(kept), "files": {path: blob_name}})
        with pytest.raises(VersionError, match="outside its paper"):
            versions.materialize(store, crafted, tmp_path / "into")
    assert not (tmp_path / "escaped.md").exists() and not (tmp_path / "abs.md").exists()
    with pytest.raises(VersionError, match="64 hex digits"):
        versions.blob(store, "../../etc/passwd")


def _fields(state: versions.State) -> dict[str, Any]:
    return {name: getattr(state, name) for name in versions.State.__slots__}


def test_a_node_merged_away_takes_none_of_the_lines_on_the_id_it_had(
    tmp_path,
) -> None:
    """Ids are positions: the merged block may take the second old block's
    id. The node that ended there, the second's, keeps none of the merged
    block's causes; the first block's node takes them."""
    old_text, new_text = "Alpha one. Beta two.", "Alpha one, beta two."
    delta = {
        "block": "b",
        "entry": None,
        "class": "narrowed",
        "note": "n",
        "by": "writer",
    }
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", "Alpha one."), ("b", "Beta two.")]),
        new_text,
        export(new_text, [("b", "Alpha one, beta two.")], update={"deltas": [delta]}),
        [(("a", "b"), ("b",), "merged", "b")],
    )

    assert node(second, "b") == node(first, "a")
    *_, ended = versions.history(store, node(first, "b"))
    assert ended.change == "merged" and ended.causes == ()
    assert ended.joined == (node(second, "b"),)
    *_, merged = versions.history(store, node(first, "a"))
    assert merged.causes[0]["delta"] == "narrowed"


def test_a_split_s_pieces_are_joined_by_the_ids_the_run_ended_on(tmp_path) -> None:
    """The run re-cut the region: the rebased cut's ids are not the final
    cut's, and `joined` names the final blocks' nodes."""
    old_text, new_text = "Ee ff.", "Ee. Ff."
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, old_text, export(old_text, [("e", "Ee ff.")]))
    rebased = export(new_text, [("r1", "Ee."), ("r2", "Ff.")])
    key = identity(store, V1, V2, rebased, [(("e",), ("r1", "r2"), "split", "r1")])
    final = export(new_text, [("e1", "Ee."), ("e2", "Ff.")])
    second = keep(store, V2, new_text, final, Command.UPDATE, first, key)

    assert node(second, "e1") == node(first, "e")
    (split,) = versions.history(store, "e2")
    assert split.change == "split" and split.joined == (node(second, "e1"),)


def test_an_update_onto_a_version_already_built_continues_its_old_state(
    tmp_path,
) -> None:
    old_text, new_text = "Aa. Bb.", "Aa. Bb!"
    store = RecordStore(tmp_path / "store")
    new = export(new_text, [("a", "Aa."), ("b", "Bb!")])
    first = keep(store, V1, old_text, export(old_text, [("a", "Aa."), ("b", "Bb.")]))
    built_new = keep(store, V2, new_text, new)  # a fresh build of NEW
    key = identity(
        store,
        V1,
        V2,
        new,
        [(("a",), ("a",), "same", None), (("b",), ("b",), "changed", None)],
    )
    update = export(new_text, [("a", "Aa."), ("b", "Bb!")], update={"deltas": []})
    updated = keep(store, V2, new_text, update, Command.UPDATE, first, key)

    assert updated.parent == first.id
    assert node(updated, "b") == node(first, "b") != node(built_new, "b")


def test_two_standings_on_one_block_are_named_by_their_words(tmp_path) -> None:
    """Each standing is its block's node and its claim's words, whitespace
    normalised."""
    text = "One claim. Two claim."
    store = RecordStore(tmp_path / "store")
    standings = [("b", "One claim."), ("b", "Two  claim.")]
    kept = keep(store, V1, text, export(text, [("b", text)], standings=standings))
    payload = json.loads(versions.kept_export(store, kept))

    assert versions.standing_ids(kept, payload) == [
        (node(kept, "b"), "One claim."),
        (node(kept, "b"), "Two claim."),
    ]


def test_a_reworded_claim_continues_the_one_it_shares_most_characters_with() -> None:
    """Case 9's shape: a block of two claims, the second reworded; each
    continues its own, each earlier claim once, the largest share first."""
    older = [
        ("n1", "the estimate is consistent"),
        ("n1", "the bias vanishes as n grows"),
    ]
    newer = [
        ("n1", "the estimate is consistent"),
        ("n1", "the bias goes to zero as n grows large"),
    ]

    assert versions.continued_parts(older, newer) == {0: 0, 1: 1}
    assert versions.continued_parts(older, list(reversed(newer))) == {0: 1, 1: 0}
    assert versions.continued_parts(older, [("n1", "xyz")]) == {}


def test_a_claim_continues_into_a_piece_of_its_split_after_its_own_node() -> None:
    """Within its node first; else in a node begun from it (a split's
    piece) or taking it in (a merge's result), as `names` says."""
    older = [("n1", "first claim here"), ("n1", "second claim there")]
    newer = [("n1", "first claim here"), ("n9", "second claim there!")]
    names = versions.naming({"n1": ("n1",), "n9": ("n1",)}, ["n1"], ["n1", "n9"])

    assert versions.continued_parts(older, newer, names) == {0: 0, 1: 1}
    assert versions.continued_parts(older, newer) == {0: 0}


# --- origins ----------------------------------------------------------------


def test_origins_name_a_split_s_pieces_and_a_merge_s_blocks_within_a_version(
    tmp_path,
) -> None:
    """A cut edit: `a` split, `b` taking in `c`, the boundary between `d`
    and `e` moved. A piece names the block it came from; the merged block,
    both; a moved boundary joins neither in a history."""
    text = "Aa one. Aa two. Bb. Cc. Dd dd. Ee."
    store = RecordStore(tmp_path / "store")
    first = keep(
        store,
        V1,
        text,
        export(
            text,
            [
                ("a", "Aa one. Aa two."),
                ("b", "Bb."),
                ("c", "Cc."),
                ("d", "Dd dd."),
                ("e", "Ee."),
            ],
        ),
    )
    second = keep(
        store,
        V1,
        text,
        export(
            text,
            [
                ("a", "Aa one."),
                ("a2", "Aa two."),
                ("b", "Bb. Cc."),
                ("d", "Dd"),
                ("e2", "dd. Ee."),
            ],
        ),
        Command.EDIT,
        started_from=first,
    )

    a, b, c, d, e = (node(first, x) for x in "abcde")
    a2 = node(second, "a2")
    assert versions.origins(store, first, second) == {
        a: (a,),
        a2: (a,),
        b: (b, c),
        d: (d,),
        e: (e, d),
    }
    assert versions.begun_from(versions.origins(store, first, second), first) == {a}

    def joined(name: str) -> tuple[str, ...]:
        return versions.history(store, name)[-1].joined

    assert (joined(a2), joined(a), joined(b), joined(c)) == ((a,), (a2,), (c,), (b,))
    assert (joined(d), joined(e)) == ((), ())


def test_origins_compose_across_versions_and_states(tmp_path) -> None:
    """An update splits `e` (its heir by label the second piece); an edit
    then joins the pieces again. Each state's nodes name the build's."""
    old_text, new_text = "Aa. Ee ff.", "Aa. Ee. Ff."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", "Aa."), ("e", "Ee ff.")]),
        new_text,
        export(new_text, [("a", "Aa."), ("e1", "Ee."), ("e2", "Ff.")]),
        [(("a",), ("a",), "same", None), (("e",), ("e1", "e2"), "split", "e2")],
    )
    third = keep(
        store,
        V2,
        new_text,
        export(new_text, [("a", "Aa."), ("e1", "Ee. Ff.")]),
        Command.EDIT,
        started_from=second,
    )

    a, e = node(first, "a"), node(first, "e")
    e1, e2 = node(second, "e1"), node(second, "e2")
    assert e1 == e
    assert versions.origins(store, first, second) == {a: (a,), e1: (e,), e2: (e,)}
    assert versions.origins(store, second, third) == {a: (a,), e1: (e1, e2)}
    assert versions.origins(store, first, third) == {a: (a,), e1: (e,)}
    with pytest.raises(VersionError, match="is not before"):
        versions.origins(store, third, first)


def test_a_piece_the_run_s_own_cut_split_off_names_its_origin(tmp_path) -> None:
    """A one-session update: identity reads `e` changed, one to one; the run's
    own cut then splits it. The piece is added from `e`, not from nowhere."""
    old_text, new_text = "Ee ff.", "Ee. Ff gg."
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, old_text, export(old_text, [("e", "Ee ff.")]))
    rebased = export(new_text, [("e", "Ee. Ff gg.")])
    key = identity(store, V1, V2, rebased, [(("e",), ("e",), "changed", None)])
    final = export(new_text, [("e", "Ee."), ("f", "Ff gg.")])
    second = keep(store, V2, new_text, final, Command.UPDATE, first, key)

    e, f = node(first, "e"), node(second, "f")
    assert node(second, "e") == e and versions.origins(store, first, second)[f] == (e,)
    (began,) = versions.history(store, "f")
    assert (began.change, began.joined) == ("added", (e,))
    assert versions.history(store, e)[-1].joined == (f,)


# --- handles across states ------------------------------------------------------------


def test_a_block_whose_opening_words_change_keeps_its_handle(tmp_path) -> None:
    """A block's opening words reworded: the node keeps the handle it was first given,
    and the slug its new words give is an alias."""
    old_text, new_text = "When a reference measure.", "When a finite reference."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", old_text)], handles={"a": "when-a-reference"}),
        new_text,
        export(new_text, [("a", new_text)], handles={"a": "when-a-finite"}),
        [(("a",), ("a",), "changed", None)],
    )

    found = versions.handles(store)
    n = node(first, "a")
    assert found.kept[n] == "when-a-reference" and found.holder["when-a-reference"] == n
    assert found.aliases_of(n) == ("when-a-finite",)
    assert found.at(second) == {"a": "when-a-reference"}


def test_a_handle_never_passes_to_the_piece_split_off(tmp_path) -> None:
    """A table's heading and body swapped: the piece split off takes the
    old block's slug on its sheet, but the node that kept the block keeps
    the handle, and the new piece is minted past it."""
    old_text = "Heading words. Table body."
    new_text = "Table body. Heading words."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", old_text)], handles={"a": "heading-words"}),
        new_text,
        export(
            new_text,
            [("a", "Table body."), ("h", "Heading words.")],
            handles={"a": "table-body", "h": "heading-words"},
        ),
        [(("a",), ("a", "h"), "split", "a")],
    )

    found = versions.handles(store)
    table, heading = node(first, "a"), node(second, "h")
    assert node(second, "a") == table
    assert found.at(second) == {"a": "heading-words", "h": "heading-words-2"}
    assert found.holder["heading-words"] == table
    assert found.dropped[heading] == ("heading-words",)
    assert "heading-words" not in found.aliases


def test_a_merge_keeps_its_first_block_s_handle(tmp_path) -> None:
    old_text, new_text = "Alpha one. Beta two.", "Alpha one, beta two."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("a", "Alpha one."), ("b", "Beta two.")],
            handles={"a": "alpha-one", "b": "beta-two"},
        ),
        new_text,
        export(new_text, [("a", new_text)], handles={"a": "alpha-one-beta-two"}),
        [(("a", "b"), ("a",), "merged", "a")],
    )

    found = versions.handles(store)
    assert found.at(second) == {"a": "alpha-one"}
    assert found.kept[node(first, "b")] == "beta-two"
    assert found.aliases_of(node(first, "a")) == ("alpha-one-beta-two",)


def test_a_new_node_is_never_given_an_ended_node_s_handle(tmp_path) -> None:
    """Identity reads the block rewritten, so
    the node ended and a new one begins with the same words."""
    old_text, new_text = "A countable sum of.", "A countable sum of measures."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(old_text, [("a", old_text)], handles={"a": "a-countable-sum-of"}),
        new_text,
        export(new_text, [("c", new_text)], handles={"c": "a-countable-sum-of"}),
        [(("a",), (), "deleted", None), ((), ("c",), "added", None)],
    )

    found = versions.handles(store)
    ended, began = node(first, "a"), node(second, "c")
    assert ended != began
    assert found.holder["a-countable-sum-of"] == ended
    assert found.at(second) == {"c": "a-countable-sum-of-2"}


def test_an_alias_two_nodes_bear_is_both_s(tmp_path) -> None:
    """Two blocks whose slugs became the same name, each on its own state:
    the name is neither's kept handle, and is an alias of both."""
    text = "Pp. Qq."
    blocks = [("a", "Pp."), ("b", "Qq.")]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, export(text, blocks, handles={"a": "p", "b": "q"}))
    second = keep(
        store,
        V1,
        text,
        export(text, blocks, handles={"a": "x", "b": "q"}),
        Command.EDIT,
        first,
    )
    keep(
        store,
        V1,
        text,
        export(text, blocks, handles={"a": "p", "b": "x"}),
        Command.EDIT,
        second,
    )

    found = versions.handles(store)
    assert found.aliases["x"] == (node(first, "a"), node(first, "b"))


def test_a_group_keeps_its_handle_as_a_block_does(tmp_path) -> None:
    old_text, new_text = "Aa. Bb.", "Aaa. Bb."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("a", "Aa."), ("b", "Bb.")],
            [("g", ["a", "b"])],
            handles={"a": "aa", "b": "bb", "g": "group:aa"},
        ),
        new_text,
        export(
            new_text,
            [("a", "Aaa."), ("b", "Bb.")],
            [("g", ["a", "b"])],
            handles={"a": "aaa", "b": "bb", "g": "group:aaa"},
        ),
        [(("a",), ("a",), "changed", None), (("b",), ("b",), "same", None)],
    )

    assert versions.handles(store).at(second) == {"a": "aa", "b": "bb", "g": "group:aa"}


def test_a_node_id_is_shown_by_its_shortest_unique_prefix() -> None:
    """Five characters at least, more where another id shares them."""
    found = versions.short_ids(["nabcdefghij", "nabcdzzzzzz", "nqqqqqqqqqq"])
    assert found == {
        "nabcdefghij": "nabcde",
        "nabcdzzzzzz": "nabcdz",
        "nqqqqqqqqqq": "nqqqq",
    }


def test_new_node_ids_are_drawn_none_the_store_holds(tmp_path, monkeypatch) -> None:
    """A tail drawn again where the store holds its id already."""
    text = "We fix a field. The output is bounded."
    blocks = [("k", "We fix a field."), ("t", "The output is bounded.")]
    draws = iter("a" * 10 + "a" * 10 + "b" * 10)
    monkeypatch.setattr(versions, "_draw", lambda _: next(draws))
    kept = keep(RecordStore(tmp_path / "store"), V1, text, export(text, blocks))
    assert sorted(n.node for n in kept.nodes) == ["n" + "a" * 10, "n" + "b" * 10]


def test_new_group_ids_are_drawn_none_the_store_holds(tmp_path, monkeypatch) -> None:
    """A group's tail drawn again where an earlier state holds its id."""
    text = "We fix a field. The output is bounded."
    blocks = [("k", "We fix a field."), ("t", "The output is bounded.")]
    grouped = export(text, blocks, groups=[("G", ["k", "t"])])
    store = RecordStore(tmp_path / "store")
    draws = iter(
        "a" * 10 + "b" * 10 + "c" * 10 + "d" * 10 + "e" * 10 + "c" * 10 + "f" * 10
    )
    monkeypatch.setattr(versions, "_draw", lambda _: next(draws))
    first = keep(store, V1, text, grouped)
    second = keep(store, V2, text, grouped)
    assert list(first.groups.values()) == ["g" + "c" * 10]
    assert list(second.groups.values()) == ["g" + "f" * 10]


def test_a_re_run_under_a_later_state_keeps_nothing(tmp_path) -> None:
    """An update kept, then another on it: the first's re-run, the same
    start and the same export, gives the first's state back and keeps
    none (docs/model.md § 7)."""
    store = RecordStore(tmp_path / "store")
    base = keep(store, V1, "Aa.", export("Aa.", [("a", "Aa.")]))
    payload = export("Aa!", [("a", "Aa!")])
    first = keep(store, V2, "Aa!", payload, started_from=base)
    keep(store, "3" * 64, "Aa?", export("Aa?", [("a", "Aa?")]), started_from=first)
    count = len(versions.states(store))

    assert keep(store, V2, "Aa!", payload, started_from=base).id == first.id
    assert len(versions.states(store)) == count
