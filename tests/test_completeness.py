"""A justification's completeness: the claim's
completeness computed from the partials on the lines that cover it, an
`unsupported` claim's declaration named by `established`, the open view's
`partial` kind, the lapse of an analogy's `valid` through the argument it
carries over, and the lines `show` and `changes` print.

Synthetic kept states of a one-line paper (`test_versions`' helpers). No
model is called.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from axiomation.established import Completeness, Missing, completeness, established
from axiomation.judgments import StatusKind, judge, readings, seen, subject
from axiomation.marks import open_marks
from axiomation.queries import OpenKind, View, changes_report, open_report, show
from axiomation.records import Aspect, Depth, RecordStore, Verdict
from test_judgments import AGENT, edge, graph, naming, same, status, updated
from test_versions import V1, keep

TEXT = (
    "Then f is bounded. By the gain, f is bounded. "
    "The other case is similar. Then g is bounded. Surely f is finite."
)
BLOCKS = [
    ("t", "Then f is bounded."),
    ("p", "By the gain, f is bounded."),
    ("s", "The other case is similar."),
    ("u", "Then g is bounded."),
    ("c", "Surely f is finite."),
]


def kept_state(
    tmp_path,
    edges: Sequence[dict[str, Any]],
    standings: dict[str, tuple[str, str]],
    selfs: Sequence[dict[str, Any]] = (),
    entries: Sequence[dict[str, Any]] = (),
    declared: dict[str, str] | None = None,
    text: str = TEXT,
    blocks=BLOCKS,
):
    data = payload(edges, standings, selfs, entries, declared, text, blocks)
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, text, data)


def payload(
    edges,
    standings,
    selfs=(),
    entries=(),
    declared=None,
    text: str = TEXT,
    blocks=BLOCKS,
) -> dict[str, Any]:
    data = graph(
        text, blocks, edges, [(b, words) for b, (words, _) in standings.items()]
    )
    declared = declared or {}
    for s in data["standings"]:
        s["status"] = standings[s["block"]][1]
        if s["block"] in declared:
            s["declared"] = declared[s["block"]]
    data["self"] = list(selfs)
    data["entries"] = list(entries)
    return data


def selfmark(block: str, words: str, partial: str | None = None) -> dict[str, Any]:
    return {
        "block": block,
        "evidence": [{"text": words}],
        **({"partial": partial} if partial else {}),
    }


def flagged(e: dict[str, Any], partial: str) -> dict[str, Any]:
    return {**e, "partial": partial}


def levels(store, kept) -> dict[str, Completeness | None]:
    by_block = {n.node: n.block for n in kept.nodes}
    found = completeness(readings(store, kept))
    claims = [s["block"] for s in View(store, kept).standings]
    assert [by_block[c.claim.node] for c in found if c is not None] == [
        b for b, c in zip(claims, found, strict=True) if c is not None
    ]
    return {
        b: None if c is None else c.completeness
        for b, c in zip(claims, found, strict=True)
    }


# --- completeness ------------------------------------------------------------------


def test_the_fullest_line_gives_a_claim_s_completeness(tmp_path) -> None:
    """A full proof and a sketch into one claim: `given`; a sketch alone:
    `sketch`; a self mark `sketch` on a `self` claim: `sketch`."""
    edges = [
        edge("p", "justifies", "t"),
        flagged(edge("s", "justifies", "t"), "sketch"),
        flagged(edge("s", "justifies", "u"), "sketch"),
    ]
    standings = {
        "t": ("Then f is bounded.", "justified"),
        "u": ("Then g is bounded.", "justified"),
        "c": ("Surely f is finite.", "self"),
    }
    store, kept = kept_state(
        tmp_path, edges, standings, [selfmark("c", "Surely", "sketch")]
    )
    assert levels(store, kept) == {
        "t": Completeness.GIVEN,
        "u": Completeness.SKETCH,
        "c": Completeness.SKETCH,
    }


def test_two_cases_one_given_and_one_by_analogy(tmp_path) -> None:
    """One block, two claims; the proof
    names the first case's claim, a sentence in it the other's, `analogy`;
    neither covers the other's claim."""
    text = "f is bounded and g is bounded. By the gain, f is bounded. So for g."
    blocks = [
        ("t", "f is bounded and g is bounded."),
        ("p", "By the gain, f is bounded."),
        ("s", "So for g."),
    ]
    edges = [
        naming(edge("p", "justifies", "t"), "f is bounded"),
        naming(flagged(edge("s", "justifies", "t"), "analogy"), "g is bounded"),
        edge("s", "uses", "p"),
    ]
    data = graph(text, blocks, edges, [("t", "f is bounded"), ("t", "g is bounded")])
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, data)
    found = completeness(readings(store, kept))
    assert [(c.claim.words, c.completeness) for c in found] == [
        ("f is bounded", Completeness.GIVEN),
        ("g is bounded", Completeness.ANALOGY),
    ]
    assert [kept.node_of("s")] == list(found[1].by)


def test_one_block_s_two_lines_give_each_claim_its_own(tmp_path) -> None:
    """One block writes the first case and declares the second similar: two
    justifies lines from it onto one target, one per claim, each claim
    read from its own line; each line's `valid` its own subject."""
    text = "f is bounded and g is bounded. By the gain, f is bounded; so for g."
    blocks = [
        ("t", "f is bounded and g is bounded."),
        ("p", "By the gain, f is bounded; so for g."),
    ]
    written = naming(edge("p", "justifies", "t"), "f is bounded")
    carried = naming(flagged(edge("p", "justifies", "t"), "analogy"), "g is bounded")
    edges = [written, {**carried, "id": "p>justifies>t>analogy"}]
    data = graph(text, blocks, edges, [("t", "f is bounded"), ("t", "g is bounded")])
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, data)
    found = completeness(readings(store, kept))
    assert [(c.claim.words, c.completeness) for c in found] == [
        ("f is bounded", Completeness.GIVEN),
        ("g is bounded", Completeness.ANALOGY),
    ]
    at = seen(store, kept)
    assert subject(at, Aspect.VALID, "p", "t", "g is bounded").words == "g is bounded"


def test_an_old_claim_part_names_no_claim(tmp_path) -> None:
    """A line names a claim by its handle, which the export writes as the
    claim's words; an old export's `claim` part names
    none, though `[I]` equals the first claim's words: equality does not
    read as covering. Both lines cover both claims, which read the least."""
    text = "f is bounded on [0, 1]; f is bounded. By the gain, it holds. As before."
    blocks = [
        ("t", "f is bounded on [0, 1]; f is bounded."),
        ("p", "By the gain, it holds."),
        ("s", "As before."),
    ]
    entries = [
        {"id": "t [I]", "anchor": "f is bounded on [0, 1]", "role": "claim"},
        {"id": "t [B]", "anchor": "f is bounded", "role": "claim"},
    ]
    edges = [
        edge("p", "justifies", "t", "t [B]"),
        flagged(edge("s", "justifies", "t", "t [I]"), "analogy"),
    ]
    data = graph(
        text, blocks, edges, [("t", "f is bounded on [0, 1]"), ("t", "f is bounded.")]
    )
    data["entries"] = entries
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, data)
    found = completeness(readings(store, kept))
    assert [c.completeness for c in found] == [
        Completeness.ANALOGY,
        Completeness.ANALOGY,
    ]


TWO_TEXT = "f is bounded and g is bounded. By the gain, it holds. So for g. Clearly."
TWO_BLOCKS = [
    ("t", "f is bounded and g is bounded."),
    ("p", "By the gain, it holds."),
    ("s", "So for g."),
    ("q", "Clearly."),
]


def two_claims(
    tmp_path,
    edges,
    entries=(),
    status: str = "justified",
    selfs: Sequence[dict[str, Any]] = (),
) -> list[Completeness | None]:
    """The completeness of `t`'s two claims, "f is bounded" and "g is
    bounded", both of `status`."""
    data = graph(
        TWO_TEXT, TWO_BLOCKS, edges, [("t", "f is bounded"), ("t", "g is bounded")]
    )
    for s in data["standings"]:
        s["status"] = status
    data["entries"] = list(entries)
    data["self"] = list(selfs)
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, TWO_TEXT, data)
    return [
        None if c is None else c.completeness
        for c in completeness(readings(store, kept))
    ]


def test_a_claim_part_inside_a_claim_s_words_falls_back_to_the_least(
    tmp_path,
) -> None:
    """A `claim` part names no claim: `[F]` "f is" and `[G]`
    "g" lie inside the two claims' words, so both lines cover both claims,
    and each claim reads the least of them."""
    entries = [
        {"id": "t [F]", "anchor": "f is", "role": "claim"},
        {"id": "t [G]", "anchor": "g", "role": "claim"},
    ]
    edges = [
        edge("p", "justifies", "t", "t [F]"),
        flagged(edge("s", "justifies", "t", "t [G]"), "analogy"),
    ]
    assert two_claims(tmp_path, edges, entries) == [
        Completeness.ANALOGY,
        Completeness.ANALOGY,
    ]


def test_two_lines_naming_no_claim_give_both_claims_the_least(tmp_path) -> None:
    """A full proof and a sketch onto a block of two `justified` claims,
    neither naming one: which claim each argues is unknown, so both read
    `sketch`. A line naming a claim's handle names it."""
    edges = [
        edge("p", "justifies", "t"),
        flagged(edge("s", "justifies", "t"), "sketch"),
    ]
    assert two_claims(tmp_path / "none", edges) == [
        Completeness.SKETCH,
        Completeness.SKETCH,
    ]
    named = [naming(edges[0], "f is bounded"), edges[1]]
    assert two_claims(tmp_path / "named", named) == [
        Completeness.GIVEN,
        Completeness.SKETCH,
    ]


def test_a_partial_taken_from_an_unnamed_line_says_what_clears_it(
    tmp_path,
) -> None:
    """A full proof and an
    analogy onto a block of two `justified` claims, neither naming one.
    Each claim reads `analogy`, the least; `show` and `open` say that a
    fuller line names no claim, and `open` that naming it clears it, not
    only a reading of the analogy."""
    edges = [
        edge("p", "justifies", "t"),
        flagged(edge("s", "justifies", "t"), "analogy"),
    ]
    data = graph(
        TWO_TEXT, TWO_BLOCKS, edges, [("t", "f is bounded"), ("t", "g is bounded")]
    )
    for st in data["standings"]:
        st["status"] = "justified"
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, TWO_TEXT, data)
    found = completeness(readings(store, kept))
    assert [(c.completeness, c.unnamed) for c in found] == [
        (Completeness.ANALOGY, True),
        (Completeness.ANALOGY, True),
    ]
    view = View(store, kept)
    first = show(view, "t").sections["standings"][0]
    assert first.line.splitlines()[0].endswith(
        "(analogy; a fuller line names no claim of its block)"
    )
    assert first.data["unnamed"] is True
    items = open_report(view, kinds=[OpenKind.PARTIAL.value]).sections["partial"]
    assert [i.data["cause"]["unnamed"] for i in items] == [True, True]
    assert (
        items[0]
        .data["clears"]
        .startswith("the fuller line that argues it naming the claim")
    )


def test_one_claim_s_fullest_line_counts_though_no_line_names_it(
    tmp_path,
) -> None:
    """One `justified` claim: a line naming no part covers it alone, so a
    full proof beside a sketch reads `given`."""
    edges = [
        edge("p", "justifies", "t"),
        flagged(edge("s", "justifies", "t"), "sketch"),
    ]
    standings = {"t": ("Then f is bounded.", "justified")}
    store, kept = kept_state(tmp_path, edges, standings)
    assert levels(store, kept)["t"] is Completeness.GIVEN


def test_a_self_mark_on_two_self_claims_gives_both_the_least(tmp_path) -> None:
    """A self mark names no claim, so on a block of two `self` claims a
    `given` mark beside a `sketch` mark reads `sketch` for both."""
    marks = [selfmark("t", "f is bounded"), selfmark("t", "g is bounded", "sketch")]
    assert two_claims(tmp_path, [], status="self", selfs=marks) == [
        Completeness.SKETCH,
        Completeness.SKETCH,
    ]


def test_an_unflagged_self_mark_beside_an_analogy_reads_given(tmp_path) -> None:
    """The rule the keys and the brief follow: a citation that
    names what an analogy carries over is the analogy's own line, flagged;
    a second, unflagged self mark beside it would read `given`."""
    standings = {"c": ("Surely f is finite.", "self")}
    marks = [selfmark("c", "Surely", "analogy"), selfmark("c", "finite")]
    store, kept = kept_state(tmp_path, [], standings, marks)
    assert levels(store, kept)["c"] is Completeness.GIVEN
    store, kept = kept_state(tmp_path / "one", [], standings, marks[:1])
    assert levels(store, kept)["c"] is Completeness.ANALOGY


def test_an_unsupported_claim_has_none_and_its_reason_names_its_declaration(
    tmp_path,
) -> None:
    standings = {"c": ("Surely f is finite.", "unsupported")}
    store, kept = kept_state(tmp_path, [], standings, declared={"c": "omitted"})
    assert levels(store, kept) == {"c": None}
    (verdict,) = established(readings(store, kept), open_marks(store, kept))
    assert verdict.reason is not None
    assert verdict.reason.kind is Missing.UNSUPPORTED
    assert "(omitted by the paper)" in verdict.reason.why


# --- the lapse through an analogy ---------------------------------------------------


def test_an_analogy_s_valid_lapses_when_a_member_of_the_group_it_uses_changes(
    tmp_path,
) -> None:
    """J (`analogy`) uses the proof group it carries over; a member block of
    that group changes, not the group's own first block, and J's `valid`
    lapses (one hop, through the group)."""
    text = "Then f is bounded. By the gain, f is bounded. Hence it holds. As before."
    blocks = [
        ("t", "Then f is bounded."),
        ("p", "By the gain, f is bounded."),
        ("q", "Hence it holds."),
        ("s", "As before."),
    ]
    edges = [
        {**edge("s", "uses", "proof@p"), "target": {"group": "proof@p"}},
        flagged(edge("s", "justifies", "t"), "analogy"),
    ]
    first_payload = graph(text, blocks, edges)
    first_payload["groups"] = [{"id": "proof@p", "kind": "proof", "blocks": ["p", "q"]}]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, first_payload)
    at = seen(store, first)
    judge(
        store,
        first,
        Aspect.VALID,
        subject(at, Aspect.VALID, "s"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "carried over",
        AGENT,
    )
    new_text = text.replace("Hence it holds.", "Hence it holds for all f.")
    new_blocks = [*blocks[:2], ("q", "Hence it holds for all f."), blocks[3]]
    second_payload = graph(new_text, new_blocks, edges)
    second_payload["groups"] = first_payload["groups"]
    second = updated(
        store,
        first,
        new_text,
        second_payload,
        same("t", "p", "q", "s", but={"q": "changed"}),
    )
    assert status(store, first, Aspect.VALID, "s").status is StatusKind.HELD
    assert status(store, second, Aspect.VALID, "s").status is StatusKind.LAPSED


# --- the open view -----------------------------------------------------------------


ANALOGY_EDGES = [
    edge("p", "justifies", "t"),
    flagged(edge("s", "justifies", "u"), "analogy"),
    edge("s", "uses", "p"),
]
ANALOGY_STANDINGS = {
    "t": ("Then f is bounded.", "justified"),
    "u": ("Then g is bounded.", "justified"),
}


def partials(store, kept) -> list[str]:
    report = open_report(View(store, kept), kinds=[OpenKind.PARTIAL.value])
    return [i.data["handle"] for i in report.sections[OpenKind.PARTIAL.section]]


def test_a_claim_argued_by_analogy_stays_open_until_its_j_is_read(tmp_path) -> None:
    """An `analogy` claim leaves `unsupported` for `justified`, so the open
    view lists it `partial` until a held `verified` `valid`
    of its J on it."""
    store, kept = kept_state(tmp_path, ANALOGY_EDGES, ANALOGY_STANDINGS)
    assert partials(store, kept) == ["u"]
    (item,) = open_report(View(store, kept)).sections["partial"]
    assert item.data["cause"] == {
        "completeness": "analogy",
        "by": ["s"],
        "unnamed": False,
    }
    assert item.line == 'u  ⊢ justified · "Then g is bounded." (analogy)'
    at = seen(store, kept)
    judge(
        store,
        kept,
        Aspect.VALID,
        subject(at, Aspect.VALID, "s"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "carried the proof over",
        AGENT,
    )
    assert partials(store, kept) == []


def test_a_fuller_j_coming_in_clears_the_partial(tmp_path) -> None:
    edges = [*ANALOGY_EDGES, edge("c", "justifies", "u")]
    store, kept = kept_state(tmp_path, edges, ANALOGY_STANDINGS)
    assert partials(store, kept) == []


def test_a_self_claim_s_partial_clears_on_a_held_true(tmp_path) -> None:
    standings = {"c": ("Surely f is finite.", "self")}
    store, kept = kept_state(
        tmp_path, [], standings, [selfmark("c", "Surely", "analogy")]
    )
    assert partials(store, kept) == ["c"]
    at = seen(store, kept)
    judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, "c"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "it holds",
        AGENT,
    )
    assert partials(store, kept) == []


# --- the lines ---------------------------------------------------------------------


def test_show_prints_the_flags_the_declaration_and_the_completeness(tmp_path) -> None:
    standings = {**ANALOGY_STANDINGS, "c": ("Surely f is finite.", "unsupported")}
    store, kept = kept_state(
        tmp_path,
        ANALOGY_EDGES,
        standings,
        declared={"c": "promised"},
    )
    view = View(store, kept)
    shown = show(view, "u")
    assert [i.line.splitlines()[0] for i in shown.sections["standings"]] == [
        '⊢ justified · "Then g is bounded." (analogy)'
    ]
    assert shown.sections["standings"][0].data["completeness"] == "analogy"
    assert [i.line for i in shown.sections["rests_on"]] == [
        's → justifies u analogy · "s"'
    ]
    (given,) = show(view, "t").sections["standings"]
    assert given.line.splitlines()[0] == '⊢ justified · "Then f is bounded."'
    assert given.data["completeness"] == "given"
    (declared,) = show(view, "c").sections["standings"]
    assert (
        declared.line.splitlines()[0]
        == '⊢ unsupported promised · "Surely f is finite."'
    )
    assert declared.data["completeness"] is None


def test_changes_lists_a_declaration_added_and_no_edge_for_a_re_flag(
    tmp_path,
) -> None:
    """Between two states of one version: a declaration added is a standing
    changed; a partial changed alone on an edge is not listed, as
    `where-used` is not."""
    standings = {**ANALOGY_STANDINGS, "c": ("Surely f is finite.", "unsupported")}
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, TEXT, payload(ANALOGY_EDGES, standings))
    edges = [
        ANALOGY_EDGES[0],
        flagged(edge("s", "justifies", "u"), "sketch"),
        ANALOGY_EDGES[2],
    ]
    second = keep(
        store,
        V1,
        TEXT,
        payload(edges, standings, declared={"c": "omitted"}),
        started_from=first,
    )
    report = changes_report(store, View(store, first), View(store, second))
    lines = [i.line for items in report.sections.values() for i in items]
    assert any(
        '⊢ unsupported omitted · "Surely f is finite."' in line for line in lines
    )
    assert not any("justifies u" in line for line in lines)


def test_a_line_naming_both_claims_argues_each(tmp_path) -> None:
    """A full proof and a sketch, each naming both claims of t: neither is
    ambiguous, so each claim reads the fullest, `given`, and no word says
    a fuller line names no claim."""
    both = ("f is bounded", "g is bounded")
    edges = [
        naming(edge("p", "justifies", "t"), *both),
        naming(flagged(edge("s", "justifies", "t"), "sketch"), *both),
    ]
    data = graph(TWO_TEXT, TWO_BLOCKS, edges, [("t", w) for w in both])
    for st in data["standings"]:
        st["status"] = "justified"
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, TWO_TEXT, data)
    found = completeness(readings(store, kept))
    assert [(c.completeness, c.unnamed) for c in found] == [
        (Completeness.GIVEN, False),
        (Completeness.GIVEN, False),
    ]
