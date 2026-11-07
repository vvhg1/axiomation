"""A stale mark is a doubt about one line (`docs/model.md` § 5, "Stale
marks"): an update's record asks lines, and the marks keep those that can
be wrong; one mark per line at its latest `since`; a `verified` reading
that holds the line as its own closes it, and `review` asks it;
a negative reading carries the marks on its lines; an `about` line no
standing rests on is a description, in no gate.

The synthetic paper of `test_established` (an assumption `a` qualifying a
definition `d`; a lemma `l` justified by `q`, which uses `d`; a theorem `t`
justified by `p`, which uses `l` and `d`; a restating remark `r` about
`t`; an unsupported remark `c`), with a recalling remark `x` that uses `l`
and holds no claim, and a heading `h` about `t`. Each update's record is
written by hand, as an update of one session writes it. No model is
called.
"""

from __future__ import annotations

from typing import Any

from axiomation import check, versions
from axiomation.established import Missing
from axiomation.judgments import Author, judge, seen, subject
from axiomation.marks import MarkState, open_marks, reread
from axiomation.queries import View, open_report, show
from axiomation.questions.review_file import Session
from axiomation.records import Aspect, Depth, RecordStore, Verdict
from axiomation.review import Selection, marked_subjects, subjects
from axiomation.versions import Command
from test_established import BLOCKS, EDGES, STANDINGS, payload
from test_judgments import edge, same
from test_versions import V1, V2, identity, keep, node

V3 = "3" * 64
AGENT = Author("agent", "model-a", "s1")

LINES_BLOCKS = [*BLOCKS, ("x", "Recall that f is small."), ("h", "Bounds.")]
LINES_EDGES = [*EDGES, edge("x", "uses", "l"), edge("h", "about", "t")]
TEXT = " ".join(w for _, w in LINES_BLOCKS)
EDITED = TEXT.replace("Then f is small.", "Then f is quite small.")
EDITED_BLOCKS = [
    (b, w.replace("Then f is small.", "Then f is quite small."))
    for b, w in LINES_BLOCKS
]
BLOCK_IDS = [b for b, _ in LINES_BLOCKS]


def cause(owner: str, kind: str, far: str, why: str = "asked by the rule") -> dict:
    """A record's cause: `owner`'s line onto `far`, asked from its change."""
    return {
        "why": f"{why}, from {far}'s change",
        "from": far,
        "via": f"{owner} → {kind} {far}",
        "root": None,
    }


def support(target: str, j: str) -> dict:
    """A record's support cause: what justifies `target` changed."""
    return {
        "why": f"what justifies it changed: {j}",
        "from": j,
        "via": f"{j} → justifies {target}",
        "root": None,
    }


def record(
    stale: dict[str, list[dict]],
    deltas: tuple[str, ...] = (),
    moved: tuple[str, ...] = (),
    root: str | None = None,
) -> dict[str, Any]:
    """`review.update` of one session: the owners it asked with their
    causes, the writer's Δ lines (a carrying class) and what the rule moved
    unread, from the edit `root` where one is named."""
    return {
        "shape": "single-session",
        "deltas": [
            {
                "block": b,
                "handle": b,
                "entry": None,
                "class": "narrowed",
                "note": "",
                "by": "writer",
            }
            for b in deltas
        ],
        "reopened": [],
        "moved_by_rule": [
            {"handle": b, "causes": [] if root is None else [moved_from(root)]}
            for b in moved
        ],
        "stale": [
            {"handle": h, "causes": causes, "cleared_by": None}
            for h, causes in stale.items()
        ],
    }


def moved_from(root: str) -> dict:
    return {
        "why": f"its meaning moved by rule, from {root}'s change",
        "from": root,
        "via": f"{root} → qualifies {root}",
        "root": root,
    }


def paper(
    update: dict[str, Any] | None = None,
    blocks=LINES_BLOCKS,
    edges=LINES_EDGES,
    standings=None,
    text: str = TEXT,
) -> dict[str, Any]:
    found = payload(
        text,
        blocks,
        edges,
        STANDINGS if standings is None else standings,
        update,
    )
    for b in found["blocks"]:
        if b["id"] == "h":
            b["form"] = "heading"
    return found


def built(tmp_path) -> tuple[RecordStore, versions.State]:
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, paper())


def updated(
    store: RecordStore,
    parent: versions.State,
    stale: dict[str, list[dict]],
    *,
    changed: tuple[str, ...] = (),
    deltas: tuple[str, ...] = (),
    moved: tuple[str, ...] = (),
    root: str | None = None,
    old: str = V1,
    new: str = V2,
    **kwargs: Any,
) -> versions.State:
    """`parent` updated to version `new`, the blocks `changed` edited (`l`'s
    words, where it is among them), its record asking `stale`."""
    text, blocks = (EDITED, EDITED_BLOCKS) if "l" in changed else (TEXT, LINES_BLOCKS)
    data = paper(record(stale, deltas, moved, root), blocks=blocks, text=text, **kwargs)
    links = same(*BLOCK_IDS, but=dict.fromkeys(changed, "changed"))
    key = identity(store, old, new, {"blocks": data["blocks"]}, links)
    return keep(store, new, text, data, Command.UPDATE, parent, key)


def read(store, kept, aspect: Aspect, on: str, verdict=Verdict.VERIFIED):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        aspect,
        subject(at, aspect, on),
        verdict,
        Depth.INFORMAL,
        "read it",
        AGENT,
    ).records.judgments[0]


def lines(store, kept) -> list[str]:
    return [m.edge for m in open_marks(store, kept)]


# --- rule 2: which lines a record's question keeps ----------------------------------


def test_a_line_carrying_no_standing_is_asked_where_its_far_end_s_words_changed(
    tmp_path,
) -> None:
    store, first = built(tmp_path)
    asked = {"p": [cause("p", "uses", "l")], "x": [cause("x", "uses", "l")]}
    edited = updated(store, first, asked, changed=("l",), deltas=("l",))
    assert lines(store, edited) == ["p → uses l", "x → uses l"]


def test_a_line_onto_unchanged_words_is_not_asked(tmp_path) -> None:
    """l's meaning moved behind its words (a ring Δ): p's line onto it asks
    nothing, as p's `valid` lapses in its place."""
    store, first = built(tmp_path)
    edited = updated(
        store, first, {"p": [cause("p", "uses", "l")]}, deltas=("l",), moved=("l",)
    )
    assert lines(store, edited) == []


def test_a_standing_s_line_is_asked_behind_a_ring_delta(tmp_path) -> None:
    """p's own Δ (a ring Δ: its words are the same) asks the justifies line
    t's standing rests on; moved by rule with no Δ, it asks nothing."""
    store, first = built(tmp_path)
    ring = updated(store, first, {"t": [support("t", "p")]}, deltas=("p",))
    assert lines(store, ring) == ["p → justifies t"]

    store = RecordStore(tmp_path / "moved")
    first = keep(store, V1, TEXT, paper())
    moved = updated(store, first, {"t": [support("t", "p")]}, moved=("p",))
    assert lines(store, moved) == []


def test_a_uses_line_no_reading_s_hop_holds_is_asked_onto_a_moved_claim(
    tmp_path,
) -> None:
    """x holds no claim and lies in no proof: no reading can lapse in its
    place, so its line onto l, moved by rule, is asked; p's line onto l
    lies in p's `valid` hop, and is not."""
    store, first = built(tmp_path)
    asked = {"p": [cause("p", "uses", "l")], "x": [cause("x", "uses", "l")]}
    edited = updated(store, first, asked, moved=("l",))
    assert lines(store, edited) == ["x → uses l"]


def test_a_line_the_state_no_longer_holds_is_asked(tmp_path) -> None:
    """A justification the session retracted: the line itself changed."""
    store, first = built(tmp_path)
    gone = [e for e in LINES_EDGES if e["id"] != "p>justifies>t>None"]
    edited = updated(
        store,
        first,
        {
            "t": [
                {
                    "why": "its justification was retracted",
                    "from": "p",
                    "via": "p → justifies t",
                    "root": None,
                }
            ]
        },
        edges=gone,
    )
    assert lines(store, edited) == ["p → justifies t"]


# --- rule 1: one mark per line -------------------------------------------------------


def test_a_line_marked_twice_is_one_mark_at_the_latest_since(tmp_path) -> None:
    """A reading between the two marks answered the first; the second, a
    later update's question, stands until a reading after it."""
    store, first = built(tmp_path)
    asked = {"x": [cause("x", "uses", "l")]}
    second = updated(store, first, asked, changed=("l",), deltas=("l",))
    third = updated(
        store,
        second,
        asked,
        changed=("l",),
        deltas=("l",),
        old=V2,
        new=V3,
    )
    (mark,) = open_marks(store, third)
    assert (mark.edge, mark.since) == ("x → uses l", third.id)

    shown = show(View(store, third), "x").sections["marks"]
    assert [i.data["since"] for i in shown] == [third.id]


def test_a_later_update_s_mark_on_a_reread_owner_stands(tmp_path) -> None:
    """A reread clears what it was shown: the line marked again after it
    is a question of its own."""
    store, first = built(tmp_path)
    asked = {"x": [cause("x", "uses", "l")]}
    second = updated(store, first, asked, changed=("l",), deltas=("l",))
    reread(store, second, [node(second, "x")], "the line stands")
    assert open_marks(store, second) == ()
    third = updated(store, second, asked, changed=("l",), deltas=("l",), old=V2, new=V3)
    (mark,) = open_marks(store, third)
    assert (mark.edge, mark.since) == ("x → uses l", third.id)


# --- rule 3: a reading closes a mark on its own line --------------------------------


def test_a_verified_reading_of_its_own_line_closes_a_mark(tmp_path) -> None:
    store, first = built(tmp_path)
    edited = updated(
        store, first, {"p": [cause("p", "uses", "l")]}, changed=("l",), deltas=("l",)
    )
    assert lines(store, edited) == ["p → uses l"]

    read(store, edited, Aspect.VALID, "p", Verdict.INCONCLUSIVE)
    assert lines(store, edited) == ["p → uses l"]

    judgment = read(store, edited, Aspect.VALID, "p")
    view = View(store, edited)
    assert view.marks == ()
    (cleared,) = view.cleared_marks
    assert cleared.reading is not None and cleared.by == judgment.id
    assert not cleared.by_person
    (closed,) = show(view, "p").sections["marks"]
    assert closed.line.startswith("closed: p → uses l") and judgment.id in closed.line
    assert closed.data["closed_by"] == judgment.id
    assert closed.data["read_by"] == "judgment"


def test_a_reading_before_the_mark_closes_nothing(tmp_path) -> None:
    store, first = built(tmp_path)
    read(store, first, Aspect.VALID, "p")
    edited = updated(
        store, first, {"p": [cause("p", "uses", "l")]}, changed=("l",), deltas=("l",)
    )
    assert lines(store, edited) == ["p → uses l"]


def test_a_qualifier_s_line_in_a_hop_is_not_the_reading_s_own(tmp_path) -> None:
    """q's `valid` reads d with its qualifier a, so a's line onto d is in
    its basis; it is a third block's line, and stays marked."""
    store, first = built(tmp_path)
    edited = updated(
        store,
        first,
        {"a": [cause("a", "qualifies", "d")]},
        changed=("d",),
        deltas=("d",),
    )
    assert lines(store, edited) == ["a → qualifies d"]
    judgment = read(store, edited, Aspect.VALID, "q")
    assert any(e.edge == ("qualifies", node(edited, "d")) for e in judgment.basis)
    assert lines(store, edited) == ["a → qualifies d"]


def test_a_claim_freed_of_its_mark_still_rests_on_a_lapsed_reading(tmp_path) -> None:
    """Nothing lapses by a mark: t's `valid` lapsed with l's words, and
    t stays not established once p's mark is read."""
    store, first = built(tmp_path)
    read(store, first, Aspect.VALID, "q")
    read(store, first, Aspect.VALID, "p")
    edited = updated(
        store, first, {"p": [cause("p", "uses", "l")]}, changed=("l",), deltas=("l",)
    )
    reread(store, edited, [node(edited, "p")], "the line stands")
    view = View(store, edited)
    assert view.marks == ()
    (t,) = [e for e in view.established if e.claim.node == node(edited, "t")]
    assert t.reason is not None
    assert t.reason.kind is not Missing.STALE


# --- rule 4: a negative reading carries the marks on its lines ----------------------


def test_a_negative_reading_over_a_marked_line_is_one_defect(tmp_path) -> None:
    store, first = built(tmp_path)
    edited = updated(
        store, first, {"p": [cause("p", "uses", "l")]}, changed=("l",), deltas=("l",)
    )
    judgment = read(store, edited, Aspect.VALID, "p", Verdict.NOT_SHOWN)
    verdict = check.gates(View(store, edited))
    gates = {r.gate: r for r in verdict.results}
    (defect,) = gates[check.Gate.DEFECTS].items
    assert defect.data["cause"]["judgment"] == judgment.id
    assert defect.data["marked"] == [{"edge": "p → uses l", "since": edited.id}]
    assert f"line p → uses l marked at {edited.id}" in defect.line
    assert not [
        i for i in gates[check.Gate.UNREVIEWED].items if i.data["kind"] == "stale"
    ]


# --- rule 5: an about line no standing rests on is a description -------------------


def test_an_about_line_no_standing_rests_on_is_in_no_gate(tmp_path) -> None:
    """h's line onto t describes it: listed by `open` after the gate's
    marks and on its block, in no gate and off the established walk; r's,
    under its `restated` standing, is the gate's."""
    store, first = built(tmp_path)
    asked = {"h": [cause("h", "about", "t")], "r": [cause("r", "about", "t")]}
    edited = updated(store, first, asked, changed=("t",), deltas=("t",))
    view = View(store, edited)
    states = {m.edge: m.state for m in view.marks}
    assert states == {
        "h → about t": MarkState.DESCRIPTION,
        "r → about t": MarkState.GATE,
    }
    assert [m.edge for m in view.gate_marks] == ["r → about t"]

    for kinds in ((), ["stale"]):
        listed = open_report(view, kinds=kinds).sections["stale"]
        assert [i.data["edge"] for i in listed] == ["r → about t", "h → about t"]
        assert "a description" in listed[1].line
        assert "a description" not in listed[0].line

    verdict = check.gates(view)
    (unreviewed,) = [r for r in verdict.results if r.gate is check.Gate.UNREVIEWED]
    assert [
        i.data.get("edge") for i in unreviewed.items if i.data["kind"] == "stale"
    ] == ["r → about t"]


def test_a_generalizing_about_line_is_the_gate_s(tmp_path) -> None:
    store, first = built(tmp_path)
    flagged = [
        {**e, "generalizes": True} if e["id"] == "h>about>t>None" else e
        for e in LINES_EDGES
    ]
    edited = updated(
        store,
        first,
        {"h": [cause("h", "about", "t")]},
        changed=("t",),
        deltas=("t",),
        edges=flagged,
    )
    (mark,) = View(store, edited).marks
    assert mark.state is MarkState.GATE


# --- rule 9: the lever ---------------------------------------------------------------


def test_the_marks_are_ranked_by_the_claims_they_hold_back(tmp_path) -> None:
    """a's line onto d holds back l and t, both resting on d, which a
    qualifies, their proofs read since, and r, which restates t; x's holds
    back none."""
    store, first = built(tmp_path)
    asked = {"x": [cause("x", "uses", "l")], "a": [cause("a", "qualifies", "d")]}
    edited = updated(store, first, asked, changed=("l", "d"), deltas=("l", "d"))
    read(store, edited, Aspect.VALID, "q")
    read(store, edited, Aspect.VALID, "p")
    view = View(store, edited)
    items = open_report(view, kinds=["stale"]).sections["stale"]
    assert [(i.data["edge"], i.data["holds_back"]) for i in items] == [
        ("a → qualifies d", 3),
        ("x → uses l", 0),
    ]
    (lemma,) = [e for e in view.established if e.claim.node == node(edited, "l")]
    assert lemma.reason is not None
    assert (
        lemma.reason.why == f"a's line is unread since {edited.id} (`a → qualifies d`)"
    )
    verdict = check.gates(view)
    assert verdict.lever is not None
    assert verdict.lever.data["edge"] == "a → qualifies d"
    assert "holds back 3 claims" in "\n".join(check.report(verdict).head)


# --- rule 8: review asks the marked lines -----------------------------------


def asks(store, kept) -> list[tuple[Aspect, str, str | None]]:
    """What `review` asks the argument session, by block."""
    by_node = {node(kept, b): b for b in BLOCK_IDS}
    return [
        (s.aspect, by_node[s.subject.node], by_node.get(s.subject.target))
        for s in subjects(View(store, kept), Session.ARGUMENT, Selection.OPEN)
    ]


def test_review_asks_a_held_subject_whose_own_line_is_marked(tmp_path) -> None:
    """p's ring Δ marks the justifies line t's standing rests on; p's held
    `valid` reading of t does not lapse, its words unchanged, so only the
    mark asks it again; a later reading of it closes the mark, and it is
    not asked."""
    store, first = built(tmp_path)
    read(store, first, Aspect.VALID, "p")
    p_t = (Aspect.VALID, "p", "t")
    assert p_t not in asks(store, first)
    ring = updated(store, first, {"t": [support("t", "p")]}, deltas=("p",))
    assert lines(store, ring) == ["p → justifies t"]
    assert p_t in asks(store, ring)

    read(store, ring, Aspect.VALID, "p")
    assert lines(store, ring) == []
    assert p_t not in asks(store, ring)


def test_review_asks_no_subject_for_a_third_block_s_line(tmp_path) -> None:
    """a's line onto d enters q's hop only as a qualifier: no reading
    closes it, so no subject is asked for it; a `reread` answers it."""
    store, first = built(tmp_path)
    edited = updated(
        store,
        first,
        {"a": [cause("a", "qualifies", "d")]},
        changed=("d",),
        deltas=("d",),
    )
    assert lines(store, edited) == ["a → qualifies d"]
    view = View(store, edited)
    assert marked_subjects(view, (Aspect.TRUE, Aspect.VALID)) == frozenset()


# --- rule 6: what an update carried unread ------------------------------------------


def test_what_an_update_carried_unread_is_one_item_per_edit(tmp_path) -> None:
    """d's edit moved q, p and t unread: one item, in `open`, in `check`'s
    head and in no gate; a later update that reads none of them leaves it,
    and each Δ of their own a later update gives takes its block out."""
    store, first = built(tmp_path)
    second = updated(
        store, first, {}, changed=("d",), deltas=("d",), moved=("q", "p", "t"), root="d"
    )
    view = View(store, second)
    (item,) = view.unread
    assert (item.edit, item.since, item.classes) == ("d", second.id, "narrowed")
    assert len(item.nodes) == 3
    (listed,) = open_report(view).sections["carried_unread"]
    assert listed.line == (
        f"d's edit (Δ narrowed), in update {second.id} carried 3 blocks unread: "
        "moved by the rule, read by no Δ or reading since"
    )
    assert listed.data["cause"]["blocks"] == ["q", "p", "t"]
    verdict = check.gates(view)
    assert f"carried unread: {listed.line}" in check.report(verdict).head
    assert not [
        i
        for r in verdict.results
        for i in r.items
        if i.data["kind"] == "carried-unread"
    ]

    third = updated(store, second, {}, old=V2, new=V3)
    assert [len(u.nodes) for u in View(store, third).unread] == [3]

    fourth = updated(store, third, {}, deltas=("q", "p"), old=V3, new="4" * 64)
    (left,) = View(store, fourth).unread
    assert (left.since, left.nodes) == (second.id, (node(fourth, "t"),))


def test_a_verified_reading_since_the_update_reads_a_carried_block(
    tmp_path,
) -> None:
    """A reading of p's `valid` holds p and t as its own: made before the
    update it reads neither; made since, it takes both off the item and
    leaves q, which no reading holds."""
    store, first = built(tmp_path)
    read(store, first, Aspect.VALID, "p")
    second = updated(
        store, first, {}, changed=("d",), deltas=("d",), moved=("q", "p", "t"), root="d"
    )
    assert [len(u.nodes) for u in View(store, second).unread] == [3]

    read(store, second, Aspect.VALID, "p", Verdict.INCONCLUSIVE)
    assert [len(u.nodes) for u in View(store, second).unread] == [3]

    read(store, second, Aspect.VALID, "p")
    (left,) = View(store, second).unread
    assert left.nodes == (node(second, "q"),)


def test_a_record_naming_no_edit_gives_one_item_for_the_update(tmp_path) -> None:
    store, first = built(tmp_path)
    second = updated(store, first, {}, changed=("d",), deltas=("d",), moved=("q",))
    (item,) = View(store, second).unread
    assert item.edit is None
    assert (
        open_report(View(store, second))
        .sections["carried_unread"][0]
        .line.startswith(f"update {second.id} carried 1 block unread")
    )


def test_the_update_s_summary_counts_per_edit(tmp_path) -> None:
    from axiomation.commands.update import _reach_by_edit

    store, first = built(tmp_path)
    rooted = {**cause("x", "uses", "l"), "root": "l"}
    second = updated(
        store,
        first,
        {"x": [rooted]},
        changed=("l",),
        deltas=("l",),
        moved=("q",),
        root="l",
    )
    assert _reach_by_edit(View(store, second)) == [
        {"edit": "l", "unread": 1, "marks": 1, "descriptions": 0}
    ]
