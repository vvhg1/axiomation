"""Judgments beside the graph: filings recorded by
the resolver, each reading's basis its subject's one hop, its lapse walked
along the kept states, and a subject's status per aspect, disputes and
findings included.

Synthetic kept states of a one-line paper (`test_versions`' helpers): a
definition `d`, a theorem `t` that uses it, a proof `p` that uses it and
justifies `t`, and a remark `r` about `t`. Each worked case in its shape. No model is
called.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from axiomation import judgments, versions
from axiomation.judgments import (
    Author,
    CauseKind,
    JudgmentError,
    Reader,
    StatusKind,
    anchored,
    answer_finding,
    find,
    judge,
    readings,
    seen,
    subject,
    walk,
)
from axiomation.questions import review_file as rf
from axiomation.records import (
    JUDGMENT_KINDS,
    AnchorKind,
    AnswerRecord,
    Aspect,
    BasisKind,
    Depth,
    FindingStatus,
    JudgmentRecords,
    RecordStore,
    TemplateRef,
    Verdict,
)
from axiomation.versions import Command
from test_versions import PROVENANCE, V1, V2, export, identity, keep

TEXT = (
    "Let f be a gain. Then f is bounded. By the gain, f is bounded. "
    "The theorem is sharp."
)
BLOCKS = [
    ("d", "Let f be a gain."),
    ("t", "Then f is bounded."),
    ("p", "By the gain, f is bounded."),
    ("r", "The theorem is sharp."),
]
AGENT = Author("agent", "model-a", "s1")
OTHER_AGENT = Author("agent", "model-b", "s2")
PERSON = Author()


def edge(source: str, kind: str, target: str, part: str | None = None) -> dict:
    target_data: dict[str, Any] = {"block": target}
    if part is not None:
        target_data["entries"] = [part]
    return {
        "id": f"{source}>{kind}>{target}>{part}",
        "source": source,
        "kind": kind,
        "target": target_data,
        "evidence": [{"text": source}],
        "provenance": PROVENANCE,
    }


def naming(e: dict[str, Any], *claims: str) -> dict[str, Any]:
    """`e` naming its target's claims by handle, as the export writes it:
    their words."""
    return {**e, "target": {**e["target"], "claims": list(claims)}}


EDGES = [
    edge("t", "uses", "d"),
    edge("p", "uses", "d"),
    edge("p", "justifies", "t"),
    edge("r", "about", "t"),
]
STANDINGS = [("t", "Then f is bounded.")]


def graph(
    text: str = TEXT,
    blocks=BLOCKS,
    edges=EDGES,
    standings=STANDINGS,
    update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = export(
        text,
        blocks,
        edges=edges,
        standings=standings,
        update=update,
        handles={b: b for b, _ in blocks},
    )
    for b in payload["blocks"]:
        b.setdefault("segments", [])
        b.setdefault("roles", [])
        b.setdefault("form", "prose")
    return payload


def built(tmp_path: Path, payload: dict[str, Any] | None = None):
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, payload or graph())


def updated(
    store: RecordStore,
    first: versions.State,
    new_text: str,
    payload: dict[str, Any],
    links,
) -> versions.State:
    """`first` updated to `new_text` by identity's `links` (each `(old,
    new, class, heir)`), its export `payload`."""
    new_cut = {"blocks": payload["blocks"]}
    key = identity(store, V1, V2, new_cut, links)
    return keep(store, V2, new_text, payload, Command.UPDATE, first, key)


def same(*blocks: str, but: dict[str, str] | None = None):
    """Identity's links, each block itself, `same` but where `but` says."""
    but = but or {}
    return [([b], [b], but.get(b, "same"), None) for b in blocks]


def filed_true(store, kept, author=AGENT, verdict=Verdict.VERIFIED, on="t"):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        verdict,
        Depth.INFORMAL,
        "read it",
        author,
    ).records.judgments[0]


def filed_valid(store, kept, author=AGENT, verdict=Verdict.VERIFIED):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.VALID,
        subject(at, Aspect.VALID, "p"),
        verdict,
        Depth.INFORMAL,
        "the proof holds",
        author,
    ).records.judgments[0]


def status(store, kept, aspect: Aspect, node_block: str) -> judgments.SubjectStatus:
    found = readings(store, kept)
    node = kept.node_of(node_block)
    (one,) = [
        s for s in found.statuses if s.aspect is aspect and s.subject.node == node
    ]
    return one


# --- records ------------------------------------------------------------------------


def test_a_judgment_is_recorded_by_the_resolver_and_never_pending(tmp_path) -> None:
    store, kept = built(tmp_path)
    j = filed_true(store, kept)

    (held,) = store.judgment_records().judgments
    assert held == j and held.state == kept.id and held.aspect is Aspect.TRUE
    assert held.subject.words == "Then f is bounded."
    answer = store.authoritative()[held.answer]
    assert (answer.host, answer.model, answer.session) == ("agent", "model-a", "s1")
    assert answer.kind == "judgment" and answer.version is None and answer.cost == 0
    assert versions.pending_answers(store, kept.version) == ()


def test_an_author_may_file_again_what_it_filed_before(tmp_path) -> None:
    """A, then B, then A: each filing its own answer, the latest speaking,
    for a judgment and for a finding's answer."""
    store, kept = built(tmp_path)
    for verdict in (Verdict.VERIFIED, Verdict.CONTRADICTED, Verdict.VERIFIED):
        filed_true(store, kept, PERSON, verdict)
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.HELD
    assert found.held.judgment.verdict is Verdict.VERIFIED
    assert len(store.judgment_records().judgments) == 3

    at = seen(store, kept)
    f = find(
        store, kept, Aspect.TRUE, [anchored(at, AnchorKind.BLOCK, "t")], 3, "f = 0"
    ).records.findings[0]
    for answer in (
        FindingStatus.POSTPONED,
        FindingStatus.FIXED,
        FindingStatus.POSTPONED,
    ):
        answer_finding(store, f.id, answer)
    (read,) = readings(store, kept).findings
    assert read.open and read.answer.status is FindingStatus.POSTPONED


def test_each_aspect_s_basis_is_its_subject_s_one_hop(tmp_path) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)
    n = kept.node_of

    def named(aspect, nodes):
        return {(e.kind, e.name, e.edge) for e in judgments.one_hop(at, aspect, nodes)}

    assert named(Aspect.TRUE, [n("t")]) == {
        (BasisKind.NODE, n("t"), None),
        (BasisKind.EDGE, n("t"), ("uses", n("d"))),
        (BasisKind.NODE, n("d"), None),
    }
    assert named(Aspect.VALID, [n("p"), n("t")]) == {
        (BasisKind.NODE, n("p"), None),
        (BasisKind.NODE, n("t"), None),
        (BasisKind.EDGE, n("p"), ("justifies", n("t"))),
        (BasisKind.EDGE, n("p"), ("uses", n("d"))),
        (BasisKind.NODE, n("d"), None),
    }
    assert named(Aspect.ACCURATE, [n("r")]) == {
        (BasisKind.NODE, n("r"), None),
        (BasisKind.EDGE, n("r"), ("about", n("t"))),
        (BasisKind.NODE, n("t"), None),
    }
    assert named(Aspect.CITED, [n("r")]) == {(BasisKind.NODE, n("r"), None)}
    assert all(
        e.text
        for e in judgments.one_hop(at, Aspect.TRUE, [n("t")])
        if e.kind is BasisKind.NODE
    )


def test_a_subject_the_state_does_not_offer_is_refused(tmp_path) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)

    with pytest.raises(JudgmentError, match="no claim"):
        subject(at, Aspect.TRUE, "d")
    with pytest.raises(JudgmentError, match="justifies nothing"):
        subject(at, Aspect.VALID, "d")
    with pytest.raises(JudgmentError, match="describes nothing"):
        subject(at, Aspect.ACCURATE, "d")
    with pytest.raises(JudgmentError, match="found 0 times"):
        subject(at, Aspect.CITED, "r", span="Able 1998", text="The theorem is sharp.")
    with pytest.raises(JudgmentError, match="finding's aspect"):
        subject(at, Aspect.WELL_POSED, "d")
    # a cited judgment names its work, one its block cites
    with pytest.raises(JudgmentError, match="r cites no work"):
        subject(at, Aspect.CITED, "r", span="is sharp", text="The theorem is sharp.")


# --- the lapse ------------------------------------------


def test_a_proof_s_text_edited_lapses_its_valid_and_not_the_theorem_s_true(
    tmp_path,
) -> None:
    """Case 1: the proof's `valid` lapses by its own text; the theorem's
    `true` holds, no proof in its basis."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    filed_true(store, first)
    text = TEXT.replace("By the gain", "By the gain alone")
    blocks = [*BLOCKS[:2], ("p", "By the gain alone, f is bounded."), BLOCKS[3]]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )

    valid = status(store, second, Aspect.VALID, "p")
    assert valid.status is StatusKind.LAPSED
    assert (
        valid.cause.kind is CauseKind.TEXT
        and valid.cause.element.name == second.node_of("p")
    )
    assert status(store, second, Aspect.TRUE, "t").status is StatusKind.HELD


@pytest.mark.parametrize(
    ("old", "new", "cls", "lapses"),
    [
        ("By the gain", "By the\ngain", "same", False),
        ("By the gain", "By the *gain*", "markup", False),
        ("By the gain", "By the gain", "macro", True),
        ("By the gain", "By the gain", "math", True),
        ("By the gain", "By a gain", "changed", True),
    ],
    ids=["rewrapped", "markup", "macro", "identity-says-math", "changed"],
)
def test_text_changes_by_words_markup_aside_a_macro_counted(
    tmp_path, old: str, new: str, cls: str, lapses: bool
) -> None:
    """A re-wrap is no change of words; identity's `markup` lapses nothing;
    a class identity gives a change (`macro`, `math`) counts, whatever the
    words compare as."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    text = TEXT.replace(old, new)
    words = BLOCKS[2][1].replace(old, new)
    blocks = [*BLOCKS[:2], ("p", words), BLOCKS[3]]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"p": cls}),
    )

    found = status(store, second, Aspect.VALID, "p")
    assert (found.status is StatusKind.LAPSED) is lapses


def test_a_definition_reworded_lapses_its_users_and_no_further(tmp_path) -> None:
    """Case 3: `reworded` closes the rule, but one hop is by text: the
    users' judgments lapse, a judgment two hops away holds."""
    store, first = built(tmp_path)
    filed_true(store, first)
    filed_true(store, first, on="t", author=OTHER_AGENT)
    text = TEXT.replace("Let f be a gain.", "Let f be a gain, a ratio.")
    blocks = [("d", "Let f be a gain, a ratio."), *BLOCKS[1:]]
    deltas = [
        {"block": "d", "entry": None, "class": "reworded", "note": "", "by": "writer"}
    ]
    payload = graph(text, blocks, update={"deltas": deltas, "moved_by_rule": []})
    second = updated(
        store, first, text, payload, same("d", "t", "p", "r", but={"d": "changed"})
    )

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED
    assert [c.kind for r in found.reads for c in r.walked.causes] == [
        CauseKind.TEXT
    ] * 2
    assert status(store, second, Aspect.ACCURATE, "r").status is StatusKind.UNJUDGED


def test_a_change_two_definitions_down_lapses_by_the_rule_s_moved_blocks(
    tmp_path,
) -> None:
    """Case 2: nothing judged on the way; the judged claim's block is moved
    by the rule (`moved_by_rule`), and its `true` lapses by meaning."""
    store, first = built(tmp_path)
    filed_true(store, first)
    filed_valid(store, first)
    update = {
        "deltas": [],
        "moved_by_rule": [
            {
                "handle": "t",
                "causes": [
                    {
                        "why": "its definition moved",
                        "from": None,
                        "via": None,
                        "root": None,
                    }
                ],
            }
        ],
    }
    second = updated(store, first, TEXT, graph(update=update), same("d", "t", "p", "r"))

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.MEANING
    assert status(store, second, Aspect.VALID, "p").status is StatusKind.LAPSED


def test_a_carrying_delta_on_a_part_lapses_only_through_an_edge_naming_it(
    tmp_path,
) -> None:
    """A Δ that carries on part [K] of `d`: the theorem's `true` lapses by
    meaning through its uses edge onto [K]; a judgment through an edge onto
    the other part holds where that part's Δ closes it (a part with no Δ of
    its own counts as changed, `closure.carries`; case 11)."""
    edges = [
        edge("t", "uses", "d", "d [K]"),
        edge("p", "uses", "d", "d [L]"),
        edge("p", "justifies", "t"),
        edge("r", "about", "t"),
    ]
    payload = graph(edges=edges)
    payload["entries"] = [
        {"id": "d [K]", "anchor": "Let"},
        {"id": "d [L]", "anchor": "gain"},
    ]
    store, first = built(tmp_path, payload)
    filed_true(store, first)
    filed_valid(store, first)
    deltas = [
        {"block": "d", "entry": "K", "class": "narrowed", "note": "", "by": "writer"},
        {"block": "d", "entry": "L", "class": "unchanged", "note": "", "by": "writer"},
    ]
    after = graph(edges=edges, update={"deltas": deltas, "moved_by_rule": []})
    after["entries"] = payload["entries"]
    second = updated(store, first, TEXT, after, same("d", "t", "p", "r"))

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.MEANING
    assert status(store, second, Aspect.VALID, "p").status is StatusKind.HELD


def edited(store, first, edges) -> versions.State:
    """An edges edit of `first`: the same text, `edges` its edges."""
    return keep(store, V1, TEXT, graph(edges=edges), Command.EDIT, first)


def added_causes(found: judgments.SubjectStatus) -> list[str]:
    return [
        c.data["line"]
        for r in found.reads
        for c in r.walked.causes
        if c.kind is CauseKind.EDGE and c.data.get("added")
    ]


def test_an_edge_added_lapses_the_readings_whose_hop_holds_it(tmp_path) -> None:
    """Case 5, added: the theorem's `true` and the proof's `valid` were
    never shown the new uses edges; each lapses at once, by `edge`. A later
    change to its target is a further cause."""
    store, first = built(tmp_path)
    filed_true(store, first)
    filed_valid(store, first)
    added = edited(
        store, first, [*EDGES, edge("t", "uses", "r"), edge("p", "uses", "r")]
    )

    found = status(store, added, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.EDGE
    assert added_causes(found) == ["t → uses r"]
    assert added_causes(status(store, added, Aspect.VALID, "p")) == ["p → uses r"]

    text = TEXT.replace("is sharp", "is tight")
    blocks = [*BLOCKS[:3], ("r", "The theorem is tight.")]
    payload = graph(text, blocks, edges=[*EDGES, edge("t", "uses", "r")])
    key = identity(
        store,
        V1,
        V2,
        {"blocks": payload["blocks"]},
        same("d", "t", "p", "r", but={"r": "changed"}),
    )
    later = keep(store, V2, text, payload, Command.UPDATE, added, key)
    found = status(store, later, Aspect.TRUE, "t")
    names = [c.element.name for r in found.reads for c in r.walked.causes]
    assert later.node_of("r") in names


def test_an_edge_added_outside_a_hop_lapses_nothing(tmp_path) -> None:
    """A justifies edge added onto the theorem is in neither reading's hop:
    the `true` holds; so does the proof's `valid`, which reads its own
    justifies edge alone. Nor is a standing changed."""
    store, first = built(tmp_path)
    filed_true(store, first)
    filed_valid(store, first)
    added = edited(store, first, [*EDGES, edge("r", "justifies", "t")])

    assert status(store, added, Aspect.TRUE, "t").status is StatusKind.HELD
    assert status(store, added, Aspect.VALID, "p").status is StatusKind.HELD

    # a standing changed is in no basis: `established` reads the new route
    payload = graph(edges=[*EDGES, edge("r", "justifies", "t")])
    payload["standings"][0]["status"] = "self"
    restood = keep(store, V1, TEXT, payload, Command.EDIT, added)
    assert status(store, restood, Aspect.TRUE, "t").status is StatusKind.HELD


def test_an_about_edge_added_lapses_the_description(tmp_path) -> None:
    """`accurate` on the remark: it now describes the definition too, which
    its reading never checked."""
    store, first = built(tmp_path)
    at = seen(store, first)
    judge(
        store,
        first,
        Aspect.ACCURATE,
        subject(at, Aspect.ACCURATE, "r"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "it is sharp",
        AGENT,
    )
    added = edited(store, first, [*EDGES, edge("r", "about", "d")])

    found = status(store, added, Aspect.ACCURATE, "r")
    assert found.status is StatusKind.LAPSED
    assert added_causes(found) == ["r → about d"]


def test_a_use_added_onto_a_where_used_target_lapses_its_description(
    tmp_path,
) -> None:
    """The remark says where the definition is used: a new use of it
    changes the list it states, as a use retracted does."""
    where = {**edge("r", "about", "d"), "where_used": True}
    edges = [e for e in EDGES if e["id"] not in ("t>uses>d>None", "r>about>t>None")]
    store, first = built(tmp_path, graph(edges=[*edges, where]))
    at = seen(store, first)
    judge(
        store,
        first,
        Aspect.ACCURATE,
        subject(at, Aspect.ACCURATE, "r"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "used by the proof alone",
        AGENT,
    )
    added = edited(store, first, [*edges, where, edge("t", "uses", "d")])

    found = status(store, added, Aspect.ACCURATE, "r")
    assert found.status is StatusKind.LAPSED
    assert added_causes(found) == ["t → uses d"]


@pytest.mark.parametrize("change", ["retracted", "added"])
def test_a_qualifier_changed_on_a_hop_node_lapses_the_reading(tmp_path, change) -> None:
    """The theorem uses the definition, which the remark qualifies: the
    qualifier is a premise of the theorem (`docs/model.md` § 3), so the
    theorem's `true` is read with it. Retracted, the reading used a premise
    the graph no longer gives; added, it was never shown one."""
    qualifier = edge("r", "qualifies", "d")
    before = [*EDGES, qualifier] if change == "retracted" else EDGES
    after = EDGES if change == "retracted" else [*EDGES, qualifier]
    store, first = built(tmp_path, graph(edges=before))
    j = filed_true(store, first)
    if change == "retracted":
        assert "r → qualifies d" in _lines(store, first, j.basis)
    second = edited(store, first, after)

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.EDGE
    assert "r → qualifies d" in found.cause.why


def _lines(store, kept, basis) -> list[str]:
    at = seen(store, kept)
    return [judgments._edge_line(at, e) for e in basis if e.kind is BasisKind.EDGE]


def test_a_basis_recorded_under_an_earlier_hop_rule_does_not_lapse_by_it(
    tmp_path,
) -> None:
    """A reading recorded before its hop named qualifiers: at its own state
    its basis is today's one hop there, so the rule's change lapses
    nothing; the qualifier retracted later still does."""
    qualifier = edge("r", "qualifies", "d")
    store, first = built(tmp_path, graph(edges=[*EDGES, qualifier]))
    j = filed_true(store, first)
    older = tuple(
        e
        for e in j.basis
        if not (e.kind is BasisKind.EDGE and e.edge[0] == "qualifies")
    )
    assert older != j.basis
    same_again = edited(store, first, [*EDGES, qualifier, edge("p", "uses", "r")])
    hop = judgments.judged_hop(Aspect.TRUE)
    node = first.node_of("t")
    reader = Reader(store)
    walked = walk(reader, first.id, hop, [node], older, same_again, j.subject.words)
    assert walked is not None and walked.causes == ()

    retracted = edited(store, same_again, [*EDGES, edge("p", "uses", "r")])
    walked = walk(reader, first.id, hop, [node], older, retracted, j.subject.words)
    assert walked is not None
    assert [c.why for c in walked.causes] == ["the edge r → qualifies d is gone"]


def test_a_retarget_is_a_gone_edge_and_an_added_one(tmp_path) -> None:
    """The proof's use moved from the definition onto the remark: both
    causes, each listed."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    edges = [e for e in EDGES if e["id"] != "p>uses>d>None"]
    second = edited(store, first, [*edges, edge("p", "uses", "r")])

    found = status(store, second, Aspect.VALID, "p")
    whys = [c.why for r in found.reads for c in r.walked.causes]
    assert any("p → uses d is gone" in w for w in whys)
    assert added_causes(found) == ["p → uses r"]


def test_an_edit_pointing_an_edge_at_other_words_of_its_target_lapses(
    tmp_path,
) -> None:
    """Within a version the text is the same, so a part continues only by
    its words: the theorem's use moved from part [K] of the definition to
    part [L] was never shown to its reading."""
    entries = [{"id": "d [K]", "anchor": "Let"}, {"id": "d [L]", "anchor": "gain"}]
    payload = graph(edges=[edge("t", "uses", "d", "d [K]"), *EDGES[1:]])
    payload["entries"] = entries
    store, first = built(tmp_path, payload)
    filed_true(store, first)
    moved = graph(edges=[edge("t", "uses", "d", "d [L]"), *EDGES[1:]])
    moved["entries"] = entries
    second = keep(store, V1, TEXT, moved, Command.EDIT, first)

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED
    assert added_causes(found) == ["t → uses d [gain]"]


def test_a_reading_s_own_retracted_line_lapses_nothing_and_is_kept(tmp_path) -> None:
    """A reading recorded on the state as its own lines correct it (here,
    its theorem's use of the remark taken away): the edit that lands those
    lines lapses nothing, and the record keeps what was taken away."""
    store, first = built(tmp_path, graph(edges=[*EDGES, edge("t", "uses", "r")]))
    at = seen(store, first)
    shown = seen(store, first, export=graph(edges=EDGES))
    record = judgments.judgment_record(
        at,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, "t"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it, not using the remark",
        shown=shown,
    )
    judgments.file_records(store, "judgment", judgments.judged(record), AGENT)
    (held,) = store.judgment_records().judgments
    # the edge, and its target, which left the hop with it
    assert [
        judgments._edge_line(at, e) if e.kind is BasisKind.EDGE else at.handle(e.name)
        for e in held.retracted
    ] == ["t → uses r", "r"]
    assert held.basis == record.basis

    second = edited(store, first, EDGES)
    assert status(store, second, Aspect.TRUE, "t").status is StatusKind.HELD
    # the same lines never landing: the graph never held its basis
    other = edited(
        store, first, [*EDGES, edge("t", "uses", "r"), edge("p", "uses", "r")]
    )
    assert status(store, other, Aspect.TRUE, "t").status is StatusKind.LAPSED


def test_a_finding_lapses_on_an_added_edge_and_a_text_alone_one_on_no_qualifier(
    tmp_path,
) -> None:
    """A finding's hop is its anchors' under its aspect: a `true` finding on
    the theorem lapses when it gains a use; a `presentation` finding, whose
    basis names no edge, holds when a qualifier joins its block."""
    store, first = built(tmp_path)
    at = seen(store, first)
    on_t = [anchored(at, AnchorKind.BLOCK, "t")]
    find(store, first, Aspect.TRUE, on_t, 2, "overstated")
    find(store, first, Aspect.PRESENTATION, on_t, 1, "hard to read")
    second = edited(
        store, first, [*EDGES, edge("t", "uses", "r"), edge("r", "qualifies", "t")]
    )

    found = {f.finding.aspect: f for f in readings(store, second).findings}
    assert found[Aspect.TRUE].walked.lapsed
    assert not found[Aspect.PRESENTATION].walked.lapsed


def test_an_edge_retracted_lapses_the_judgment_resting_on_it(tmp_path) -> None:
    """Case 5, retracted: the proof's `valid` lapses when its uses edge is
    gone; the theorem's `true`, whose basis it is not in, holds."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    filed_true(store, first)
    edges = [e for e in EDGES if e["id"] != "p>uses>d>None"]
    second = keep(store, V1, TEXT, graph(edges=edges), Command.EDIT, first)

    found = status(store, second, Aspect.VALID, "p")
    assert found.status is StatusKind.LAPSED and found.cause.kind is CauseKind.EDGE
    assert "p → uses d" in found.cause.why
    assert status(store, second, Aspect.TRUE, "t").status is StatusKind.HELD


def test_a_deleted_subject_lapses_as_deleted(tmp_path) -> None:
    """Case 7's proof deleted: its `valid` lapses, the subject deleted."""
    store, first = built(tmp_path)
    j = filed_valid(store, first)
    text = TEXT.replace(" By the gain, f is bounded.", "")
    blocks = [b for b in BLOCKS if b[0] != "p"]
    edges = [e for e in EDGES if e["source"] != "p"]
    links = [*same("d", "t", "r"), (["p"], [], "deleted", None)]
    second = updated(store, first, text, graph(text, blocks, edges), links)

    walked = readings(store, second)
    (read,) = walked.reads
    assert read.judgment == j and not read.live
    assert judgments.first(read.walked.causes).kind is CauseKind.DELETED


def test_a_merged_basis_node_lapses_by_text_and_names_its_heir(tmp_path) -> None:
    """Case 6's merge: `d` merged into `t` (its node ended, `t` took it in);
    the theorem's `true` lapses by text."""
    store, first = built(tmp_path)
    filed_true(store, first)
    text = TEXT.replace("Let f be a gain. Then", "Let f be a gain; then")
    blocks = [("t", "Let f be a gain; then f is bounded."), BLOCKS[2], BLOCKS[3]]
    edges = [edge("p", "justifies", "t"), edge("r", "about", "t")]
    links = [(["d", "t"], ["t"], "merged", "t"), *same("p", "r")]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks, edges, [("t", "Let f be a gain; then f is bounded.")]),
        links,
    )

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.LAPSED
    kinds = {c.kind for r in found.reads for c in r.walked.causes}
    assert CauseKind.TEXT in kinds


def test_a_reworded_claim_of_two_continues_and_both_lapse(tmp_path) -> None:
    """Case 9: both claims' judgments lapse (the block's text); the second
    continues to its reworded words, not shown unjudged."""
    text = "One holds. Two holds well."
    blocks = [("b", text)]
    claims = [("b", "One holds."), ("b", "Two holds well.")]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, graph(text, blocks, [], claims))
    at = seen(store, first)
    for claim in ("One holds.", "Two holds"):
        judge(
            store,
            first,
            Aspect.TRUE,
            subject(at, Aspect.TRUE, "b", claim=claim),
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "read",
            AGENT,
        )
    new_text = "One holds. Two holds."
    claims_after = [("b", "One holds."), ("b", "Two holds.")]
    second = updated(
        store,
        first,
        new_text,
        graph(new_text, [("b", new_text)], [], claims_after),
        same("b", but={"b": "changed"}),
    )

    found = {s.subject.words: s.status for s in readings(store, second).statuses}
    assert found == {"One holds.": StatusKind.LAPSED, "Two holds.": StatusKind.LAPSED}


def test_a_judgment_on_another_branch_does_not_bear_on_a_state(tmp_path) -> None:
    store, first = built(tmp_path)
    one = keep(store, V1, TEXT, graph(edges=EDGES[:3]), Command.EDIT, first)
    filed_true(store, one)

    assert readings(store, first).reads == ()
    assert (
        walk(Reader(store), one.id, judgments.judged_hop(Aspect.TRUE), [], (), first)
        is None
    )


# --- status: disputes and findings -------------------------------------------------


def test_differing_live_judgments_are_disputed_and_a_person_decides(tmp_path) -> None:
    store, kept = built(tmp_path)
    filed_true(store, kept, AGENT, Verdict.VERIFIED)
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.HELD

    filed_true(store, kept, OTHER_AGENT, Verdict.INCONCLUSIVE)
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.HELD

    filed_true(store, kept, OTHER_AGENT, Verdict.CONTRADICTED)
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.DISPUTED and len(found.reads) == 2

    filed_true(store, kept, PERSON, Verdict.VERIFIED)
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.HELD and found.held.author.name == "person"

    filed_true(store, kept, AGENT, Verdict.NOT_SHOWN)
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.DISPUTED


def test_an_author_s_later_judgment_replaces_its_own(tmp_path) -> None:
    """Case 4: an agent's own later `verified` replaces its `contradicted`."""
    store, kept = built(tmp_path)
    filed_true(store, kept, AGENT, Verdict.CONTRADICTED)
    filed_true(store, kept, AGENT, Verdict.VERIFIED)

    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.HELD
    assert found.held.judgment.verdict is Verdict.VERIFIED and len(found.reads) == 1


def test_an_open_critical_finding_disputes_a_verified_judgment(tmp_path) -> None:
    store, kept = built(tmp_path)
    filed_true(store, kept, AGENT, Verdict.VERIFIED)
    at = seen(store, kept)
    minor = find(
        store, kept, Aspect.TRUE, [anchored(at, AnchorKind.BLOCK, "t")], 2, "a typo"
    ).records.findings[0]
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.HELD

    critical = find(
        store,
        kept,
        Aspect.TRUE,
        [anchored(at, AnchorKind.BLOCK, "t")],
        3,
        "false for f = 0",
        "assume f nonzero",
        OTHER_AGENT,
    ).records.findings[0]
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.DISPUTED and found.findings[0].finding == critical

    answer_finding(store, critical.id[:14], FindingStatus.DISMISSED, "f is a gain")
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.HELD
    read = readings(store, kept)
    assert {f.finding.id: f.open for f in read.findings} == {
        minor.id: True,
        critical.id: False,
    }
    assert [f.finding.id for f in read.findings_on("t")] == [minor.id, critical.id]


def test_a_finding_lapses_by_its_anchors_basis(tmp_path) -> None:
    """A `well-posed` finding on the definition rests on its text: an edit
    to it lapses the finding, shown lapsed, never fixed."""
    store, first = built(tmp_path)
    at = seen(store, first)
    find(
        store,
        first,
        Aspect.WELL_POSED,
        [anchored(at, AnchorKind.BLOCK, "d")],
        3,
        "f undefined",
    )
    text = TEXT.replace("Let f be a gain.", "Let f be a positive gain.")
    blocks = [("d", "Let f be a positive gain."), *BLOCKS[1:]]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"d": "changed"}),
    )

    (found,) = readings(store, second).findings
    assert not found.live and found.open and found.answer is None


def test_a_group_s_valid_shows_on_its_first_block(tmp_path) -> None:
    text = TEXT + " Hence it holds."
    blocks = [*BLOCKS, ("q", "Hence it holds.")]
    edges = [
        *EDGES[:2],
        edge("t", "uses", "d"),
        {**edge("proof@p", "justifies", "t"), "target": {"block": "t"}},
    ]
    edges[3]["source"] = "proof@p"
    payload = graph(text, blocks, edges)
    payload["groups"] = [{"id": "proof@p", "kind": "proof", "blocks": ["p", "q"]}]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    at = seen(store, kept)
    on = subject(at, Aspect.VALID, "proof@p")
    judge(store, kept, Aspect.VALID, on, Verdict.VERIFIED, Depth.FORMAL, "lean", PERSON)

    read = readings(store, kept)
    (shown,) = [s for s in read.statuses_on("p") if s.aspect is Aspect.VALID]
    assert shown.status is StatusKind.HELD and shown.held.judgment.depth is Depth.FORMAL
    assert read.statuses_on("q") == []


# --- the commands ------------------------------------------------------------------


def _run(capsys, *args: str) -> tuple[int, str, str]:
    from axiomation.cli import main

    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_commands_file_show_filter_and_re_ask(tmp_path, capsys) -> None:
    """`judge` and `find` file on the latest state, `show` and `blocks`
    read the statuses, `reask` gives a lapsed judgment's brief: its cause,
    its subject and basis now, the text then beside now."""
    store, first = built(tmp_path)
    paper, where = str(tmp_path), ["--store", str(tmp_path / "store")]
    code, out, err = _run(
        capsys,
        "judge",
        paper,
        *where,
        "--aspect",
        "valid",
        "--on",
        "p",
        "--verdict",
        "verified",
        "--depth",
        "informal",
        "--evidence",
        "the gain bounds it",
    )
    assert code == 0, err
    judged = out.split()[1]
    assert "basis (5):" in out and "p → uses d" in out
    code, out, err = _run(
        capsys,
        "find",
        paper,
        *where,
        "--aspect",
        "well-posed",
        "--on",
        "d",
        "--severity",
        "2",
        "--problem",
        "f's domain unstated",
        "--agent",
        "model-a",
        "--agent-session",
        "s1",
    )
    assert code == 0, err
    finding = out.split()[1]

    code, out, _ = _run(capsys, "show", paper, *where, "p")
    assert (
        'valid p → t · "Then f is bounded."  held: verified (informal) by person, '
        f"{judged}" in out
    )
    # the reason a person checks, under the verdict
    assert f"\n    evidence {judged}: the gain bounds it\n" in out
    code, out, _ = _run(capsys, "show", paper, *where, "d")
    assert f"finding {finding}  severity 2  well-posed  open" in out

    text = TEXT.replace("By the gain", "By the gain alone")
    blocks = [*BLOCKS[:2], ("p", "By the gain alone, f is bounded."), BLOCKS[3]]
    updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )

    code, out, _ = _run(capsys, "blocks", paper, *where, "--judgment", "lapsed")
    assert out.startswith("1 blocks of 4") and "  p  p" in out
    code, out, _ = _run(
        capsys, "blocks", paper, *where, "--judgment", "unjudged", "--aspect", "true"
    )
    assert out.startswith("1 blocks of 4") and "  t  t" in out
    code, out, _ = _run(capsys, "blocks", paper, *where, "--open-findings")
    assert out.startswith("1 blocks of 4")
    code, out, err = _run(capsys, "reask", paper, *where, judged[:14])
    assert code == 0, err
    assert (
        "evidence: the gain bounds it" in out
        and "text  p's text changed (changed)" in out
    )
    assert "now: By the gain alone, f is bounded." in out
    assert "then: By the gain, f is bounded." in out

    code, _, err = _run(
        capsys, "answer-finding", paper, *where, finding, "--as", "duplicate"
    )
    assert code != 0 and "names the finding it duplicates with --of" in err
    code, out, err = _run(
        capsys,
        "find",
        paper,
        *where,
        "--aspect",
        "well-posed",
        "--on",
        "d",
        "--severity",
        "1",
        "--problem",
        "the domain of f is not given",
    )
    assert code == 0, err
    again = out.split()[1]
    code, out, err = _run(
        capsys,
        "answer-finding",
        paper,
        *where,
        again,
        "--as",
        "duplicate",
        "--of",
        finding[:14],
    )
    assert code == 0, err
    assert f"finding {again} answered duplicate of {finding}: " in out
    code, out, err = _run(
        capsys, "answer-finding", paper, *where, finding, "--as", "fixed"
    )
    assert code == 0 and "answered fixed" in out
    code, out, _ = _run(capsys, "blocks", paper, *where, "--open-findings")
    assert out.startswith("0 blocks of 4")
    code, _, err = _run(
        capsys,
        "judge",
        paper,
        *where,
        "--aspect",
        "true",
        "--on",
        "d",
        "--verdict",
        "verified",
        "--depth",
        "formal",
        "--evidence",
        "x",
    )
    assert code == 1 and "holds no claim" in err


def test_show_and_history_take_a_finding_s_or_a_judgment_s_id(tmp_path, capsys) -> None:
    """The ids every list prints name what they filed: `show` prints a
    finding whole (its block's line, anchors, answers, earlier filings) or
    a judgment whole, by its id or a unique prefix; `history` the node it
    is on; a refiled finding by the id the author knew. An agent's answer
    by hand is a proposal, and says what came of it."""
    from axiomation.judgments import reaffirm_finding

    store, first = built(tmp_path)
    paper, where = str(tmp_path), ["--store", str(tmp_path / "store")]
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.PART, "d", "a gain", text="Let f be a gain.")]
    (finding,) = find(
        store, first, Aspect.WELL_POSED, on_d, 2, "which gain?", "say which", AGENT
    ).records.findings
    (lapsing,) = find(
        store, first, Aspect.WELL_POSED, on_d, 1, "gain of what?"
    ).records.findings
    judged = filed_true(store, first)
    (proposed,) = judge(
        store,
        first,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, "t"),
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "the bound needs f defined",
        PERSON,
        propose=True,
    ).records.judgments

    code, out, err = _run(capsys, "show", paper, *where, finding.id[:14])
    assert code == 0, err
    assert out.startswith(
        f'finding {finding.id}  severity 2  well-posed  open  on d · "a gain"  '
        '"which gain?" — fix: say which  (by agent model-a)\n'
    )
    assert "\nat " + first.nodes[0].node[:5] + "  d  d  " in out
    assert 'anchors (1):\n  part d "a gain"\n' in out
    assert "answers (0):" in out and "filings (0):" in out
    code, _, err = _run(capsys, "show", paper, *where, finding.id, "--kind", "uses")
    assert code != 0 and "--kind takes a block" in err
    code, out, err = _run(capsys, "show", paper, *where, finding.id, "--history")
    assert code == 0 and "history (1):" in out
    code, out, err = _run(capsys, "show", paper, *where, proposed.id)
    assert code == 0 and ", proposed\n" in out.split("evidence:")[0]
    code, out, err = _run(capsys, "show", paper, *where, judged.id, "--json")
    assert code == 0, err
    shown = json.loads(out)
    assert shown["judgment"]["id"] == judged.id and shown["block"]["block"] == "t"
    assert shown["status"]["items"][0]["held"] == judged.id
    code, out, err = _run(capsys, "history", paper, *where, finding.id)
    assert code == 0, err
    assert [s["block"] for s in json.loads(out)] == ["d"]
    code, _, err = _run(capsys, "show", paper, *where, "ffffffff")
    assert code != 0 and "or a finding's or a judgment's id" in err

    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the gain is defined", AGENT
    )
    second = _reworded_d(store, first)
    (refiled,) = reaffirm_finding(
        store, second, finding.id, "still says no which", words="positive gain"
    ).records.findings
    judgments.answer_finding(store, finding.id, FindingStatus.FIXED, "named it")
    code, out, err = _run(capsys, "show", paper, *where, finding.id)
    assert code == 0, err
    assert out.startswith(f"finding {finding.id} (refiled as {refiled.id})")
    assert f"filings (1):\n  {finding.id}  at state {first.id}  by agent" in out
    assert 'fixed  by person: "named it"  (speaks)' in out
    assert (
        f'dismissed  by agent model-a  on {finding.id}: "the gain is defined"  '
        "(proposed, not taken: refiled)"
    ) in out
    code, out, err = _run(capsys, "show", paper, *where, lapsing.id, "--json")
    shown = json.loads(out)
    assert shown["finding"]["state"] == "lapsed" and shown["causes"]["total"] == 1
    assert shown["anchors"]["items"][0]["block"] == "d"
    # at the state it was filed on, the finding is as it was then, and its
    # refiling is not there
    code, out, err = _run(
        capsys, "show", paper, *where, "--state", first.id, finding.id
    )
    assert code == 0, err
    assert out.startswith(f"finding {finding.id}  severity 2  well-posed  open")
    assert "filings (0):" in out and "(proposed, not settled)" in out
    code, out, err = _run(
        capsys, "show", paper, *where, "--state", first.id, refiled.id
    )
    assert code != 0 and "not along state" in err


def test_a_person_s_answer_is_withdrawn_and_the_finding_reads_as_before(
    tmp_path, capsys
) -> None:
    """A person who meant to propose and answered: the answer says so where
    a review refiled the finding against their proposal. Withdrawn, by its
    id or by the finding's, it speaks no more, the finding is open, and a
    proposal can follow; `show` keeps it, withdrawn, with why. A withdrawal
    is a person's, gives its reason, and is filed once."""
    from axiomation.judgments import reaffirm_finding, withdraw_answer

    store, first = built(tmp_path)
    paper, where = str(tmp_path), ["--store", str(tmp_path / "store")]
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.PART, "d", "a gain", text="Let f be a gain.")]
    review = Author("claude-code", "model-r", "r1")
    (finding,) = find(
        store, first, Aspect.WELL_POSED, on_d, 3, "which gain?", "say which", review
    ).records.findings
    answer_finding(
        store,
        finding.id,
        FindingStatus.DISMISSED,
        "the gain is defined",
        PERSON,
        propose=True,
    )
    second = _reworded_d(store, first)
    (refiled,) = reaffirm_finding(
        store, second, finding.id, "still says no which", review, words="positive gain"
    ).records.findings

    code, out, err = _run(
        capsys,
        "answer-finding",
        paper,
        *where,
        refiled.id,
        "--as",
        "dismissed",
        "--note",
        "the gain is the one of §1",
    )
    assert code == 0, err
    answer = store.judgment_records().answers[-1]
    assert out.splitlines()[1] == (
        "  answered, closing it; --propose would have asked the review to weigh "
        f"the note. To take it back: axiomation answer-finding {paper} {answer.id} "
        '--withdraw --reason "…"'
    )
    assert judgments.speaking_answer(store, refiled.id) == answer

    with pytest.raises(JudgmentError, match="a withdrawal is a person's"):
        withdraw_answer(store, answer.id, "wrong", AGENT)
    with pytest.raises(JudgmentError, match="gives its reason"):
        withdraw_answer(store, answer.id, " ")
    code, _, err = _run(
        capsys,
        "answer-finding",
        paper,
        *where,
        answer.id,
        "--withdraw",
        "--as",
        "fixed",
        "--reason",
        "x",
    )
    assert code != 0 and "no --as, --note, --of or --propose" in err
    code, _, err = _run(
        capsys, "answer-finding", paper, *where, answer.id, "--withdraw"
    )
    assert code != 0 and "gives its --reason" in err
    code, _, err = _run(capsys, "answer-finding", paper, *where, refiled.id)
    assert code != 0 and "names its --as" in err
    code, _, err = _run(
        capsys,
        "answer-finding",
        paper,
        *where,
        refiled.id,
        "--as",
        "fixed",
        "--reason",
        "x",
    )
    assert code != 0 and "--reason is a withdrawal's" in err

    # the finding's first id names the answer that speaks for its latest filing
    code, out, err = _run(
        capsys,
        "answer-finding",
        paper,
        *where,
        finding.id,
        "--withdraw",
        "--reason",
        "meant to propose",
    )
    assert code == 0, err
    (withdrawal,) = store.judgment_records().answer_withdrawals
    assert out == (
        f"answer {answer.id} (dismissed) on finding {refiled.id} withdrawn by "
        f"person: {withdrawal.id}\n"
        f"  finding {refiled.id}: open: answer it, or propose (--propose --note)\n"
    )
    assert judgments.speaking_answer(store, refiled.id) is None
    with pytest.raises(JudgmentError, match="withdrawn already"):
        withdraw_answer(store, answer.id, "again")
    with pytest.raises(JudgmentError, match="no answer to withdraw"):
        withdraw_answer(store, refiled.id, "again")

    (proposal,) = answer_finding(
        store,
        refiled.id,
        FindingStatus.DISMISSED,
        "the gain is the one of §1",
        PERSON,
        propose=True,
    ).records.answers
    (read,) = [
        f for f in readings(store, second).findings if f.finding.id == refiled.id
    ]
    assert read.open and read.proposal is not None
    assert read.proposal.answer == proposal
    code, out, err = _run(capsys, "show", paper, *where, refiled.id)
    assert code == 0, err
    assert (
        f'{answer.id}  dismissed  by person: "the gain is the one of §1"  '
        '(withdrawn: "meant to propose")'
    ) in out
    assert f"{proposal.id}  dismissed  by person: " in out
    code, out, err = _run(capsys, "show", paper, *where, refiled.id, "--json")
    items = {i["id"]: i for i in json.loads(out)["answers"]["items"]}
    assert items[answer.id]["withdrawn"] == {
        "id": withdrawal.id,
        "reason": "meant to propose",
    }
    assert items[proposal.id]["withdrawn"] is None


def test_a_withdrawal_takes_a_whole_id_before_another_s_prefix(tmp_path) -> None:
    """One review's filing numbers its findings, then its answers, under one
    key: its finding `K-1-1` is a prefix of its answer `K-1-10`. The whole
    id names the finding; a prefix of both is refused."""
    from axiomation.judgments import file_records, withdraw_answer
    from axiomation.records import FINDING, FindingAnswer

    store, first = built(tmp_path)
    on = [anchored(seen(store, first), AnchorKind.BLOCK, "d")]
    (earlier,) = find(store, first, Aspect.WELL_POSED, on, 2, "which?").records.findings
    review = Author("claude-code", "model-r", "r1")
    filed = file_records(
        store,
        FINDING,
        JudgmentRecords(
            findings=(replace(earlier, id="", answer=None, problem="which gain?"),),
            answers=tuple(
                FindingAnswer("", None, earlier.id, FindingStatus.POSTPONED)
                for _ in range(10)
            ),
        ),
        review,
    ).records
    (finding,) = filed.findings
    assert filed.answers[-2].id == finding.id + "0"
    (fixed,) = answer_finding(store, finding.id, FindingStatus.FIXED).records.answers
    (withdrawal,) = withdraw_answer(
        store, finding.id, "not fixed"
    ).records.answer_withdrawals
    assert withdrawal.finding_answer == fixed.id
    assert judgments.speaking_answer(store, finding.id) is None
    with pytest.raises(JudgmentError, match="names an answer and a finding"):
        withdraw_answer(store, finding.id[:-1], "which one?")


def test_an_edge_onto_a_part_whose_words_changed_is_not_gone(tmp_path) -> None:
    """A part continues by shared characters within its node, so the
    edge onto it continues: under identity's `markup`, nothing lapses."""
    text = TEXT.replace("a gain", "a *gain*")
    blocks = [("d", "Let f be a *gain*."), *BLOCKS[1:]]
    edges = [edge("t", "uses", "d", "d [K]"), *EDGES[1:]]
    payload = graph(text, blocks, edges)
    payload["entries"] = [{"id": "d [K]", "block": "d", "anchor": "a *gain*"}]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, payload)
    filed_true(store, first)
    new_text = TEXT.replace("a gain", "a _gain_")
    after = graph(new_text, [("d", "Let f be a _gain_."), *BLOCKS[1:]], edges)
    after["entries"] = [{"id": "d [K]", "block": "d", "anchor": "a _gain_"}]
    second = updated(
        store, first, new_text, after, same("d", "t", "p", "r", but={"d": "markup"})
    )

    found = status(store, second, Aspect.TRUE, "t")
    assert found.status is StatusKind.HELD, found.reads[0].walked.causes


def test_a_person_decides_over_an_agent_s_critical_finding_filed_before(
    tmp_path,
) -> None:
    """Judgments and findings on one timeline: a person's `verified` after an
    agent's open severity-3 finding holds; the agent's later finding
    disputes it."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    t = [anchored(at, AnchorKind.BLOCK, "t")]
    find(store, kept, Aspect.TRUE, t, 3, "false for f = 0", author=AGENT)
    filed_true(store, kept, PERSON, Verdict.VERIFIED)
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.HELD

    find(store, kept, Aspect.TRUE, t, 3, "false for f < 0", author=AGENT)
    assert status(store, kept, Aspect.TRUE, "t").status is StatusKind.DISPUTED


def test_a_claim_that_ends_with_its_text_kept_lapses(tmp_path) -> None:
    """A part continues, else it lapses: a standing taken off in an edit
    of the same text lapses the judgment on it, shown as its claim ended."""
    text = "One holds. Two holds well."
    claims = [("b", "One holds."), ("b", "Two holds well.")]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, graph(text, [("b", text)], [], claims))
    judge(
        store,
        first,
        Aspect.TRUE,
        subject(seen(store, first), Aspect.TRUE, "b", claim="Two holds"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read",
        AGENT,
    )
    second = keep(
        store, V1, text, graph(text, [("b", text)], [], claims[:1]), Command.EDIT, first
    )

    (read,) = readings(store, second).reads
    assert not read.live
    cause = judgments.first(read.walked.causes)
    assert cause.kind is CauseKind.DELETED and "Two holds well." in cause.why


def test_a_moved_block_s_judgment_holds_and_an_edit_state_counts_no_delta_twice(
    tmp_path,
) -> None:
    """Case 6's move: identity's `moved` (words equal) lapses nothing. An
    edit state of the update carries the update's record; its Δs and the
    rule's moved blocks were counted on the step across versions, never
    again."""
    store, first = built(tmp_path)
    filed_true(store, first, on="t")
    j = filed_valid(store, first)
    text = "The theorem is sharp. " + TEXT.replace(" The theorem is sharp.", "")
    blocks = [BLOCKS[3], *BLOCKS[:3]]
    update = {
        "deltas": [],
        "moved_by_rule": [
            {
                "handle": "p",
                "causes": [{"why": "moved", "from": None, "via": None, "root": None}],
            }
        ],
    }
    links = same("d", "t", "p", "r", but={"r": "moved"})
    second = updated(store, first, text, graph(text, blocks, update=update), links)
    assert status(store, second, Aspect.TRUE, "t").status is StatusKind.HELD

    edited = keep(
        store, V2, text, graph(text, blocks, update=update), Command.EDIT, second
    )
    (read,) = [r for r in readings(store, edited).reads if r.judgment == j]
    assert [c.kind for c in read.walked.causes] == [CauseKind.MEANING]
    assert status(store, edited, Aspect.TRUE, "t").status is StatusKind.HELD


def test_a_description_of_the_paper_lapses_when_a_block_is_added(tmp_path) -> None:
    """`accurate` on a `scope:paper` about edge rests on the paper: an added
    block lapses it."""
    edges = [
        *EDGES,
        {**edge("r", "about", "t"), "id": "r>paper", "target": {"scope": "paper"}},
    ]
    store, first = built(tmp_path, graph(edges=edges))
    on = subject(seen(store, first), Aspect.ACCURATE, "r")
    judge(store, first, Aspect.ACCURATE, on, Verdict.VERIFIED, Depth.INFORMAL, "ok")
    text = TEXT + " A new remark."
    blocks = [*BLOCKS, ("n", "A new remark.")]
    links = [*same("d", "t", "p", "r"), ([], ["n"], "added", None)]
    second = updated(store, first, text, graph(text, blocks, edges), links)

    found = status(store, second, Aspect.ACCURATE, "r")
    assert found.status is StatusKind.LAPSED
    assert "1 added" in found.cause.why


# --- what lapsed, as the reads give it ----------------------------------


def _lapsed(view) -> list[dict[str, Any]]:
    from axiomation.queries import OpenKind, open_report

    return [i.data for i in open_report(view).sections[OpenKind.LAPSED.section]]


def _changed(store, old, new) -> list[dict[str, Any]]:
    from axiomation.queries import View, changes_report

    report = changes_report(store, View(store, old), View(store, new))
    return [i.data for i in report.sections["lapsed"]]


def _reworded_d(store, first):
    text = TEXT.replace("Let f be a gain.", "Let f be a positive gain.")
    blocks = [("d", "Let f be a positive gain."), *BLOCKS[1:]]
    return updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"d": "changed"}),
    )


def test_an_open_finding_that_lapsed_is_open_s_lapsed_and_the_change_s(
    tmp_path,
) -> None:
    """A rewording of the definition lapses the theorem's `true` and every
    finding on the definition: an open or postponed one is listed lapsed,
    with its cause and how to clear it, never re-asked; a fixed one is in
    neither list; a finding elsewhere stays open."""
    from axiomation.queries import OpenKind, View, open_report

    store, first = built(tmp_path)
    filed_true(store, first)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.BLOCK, "d")]
    opened, fixed, postponed = (
        find(store, first, Aspect.WELL_POSED, on_d, n, f"problem {n}")
        .records.findings[0]
        .id
        for n in (2, 1, 3)
    )
    on_r = find(
        store,
        first,
        Aspect.PRESENTATION,
        [anchored(at, AnchorKind.BLOCK, "r")],
        1,
        "vague",
    ).records.findings[0]
    answer_finding(store, fixed, FindingStatus.FIXED, "reworded")
    answer_finding(store, postponed, FindingStatus.POSTPONED, "later")
    second = _reworded_d(store, first)

    view = View(store, second)
    lapsed = _lapsed(view)
    subjects = [i for i in lapsed if "finding" not in i]
    found = [i for i in lapsed if "finding" in i]
    assert [(i["status"], i["handle"]) for i in subjects] == [("lapsed", "t")]
    assert [i["finding"] for i in found] == [postponed, opened]
    for item in found:
        assert item["cause"]["kind"] == "text" and item["cause"]["state"] == second.id
        assert item["cause"]["element"] == {"kind": "node", "name": second.node_of("d")}
        answer = f"`axiomation answer-finding` {item['finding']}"
        assert item["clears"].startswith(answer)
        assert "reask" not in item["clears"]
        assert item["line"].startswith(f"finding {item['finding']}")
        # a lapsed finding's line says the answer it holds, if any
        state = "lapsed, postponed" if item["finding"] == postponed else "lapsed"
        assert f"  {state}  " in item["line"] and item["block"] == "d"
        assert item["anchors"] == [item["anchor"]] and item["severity"] in (2, 3)
    still = [i.data for i in open_report(view).sections[OpenKind.FINDING.section]]
    assert [i["finding"] for i in still] == [on_r.id]

    read = {f.finding.id: f for f in readings(store, second).findings}
    assert read[opened].cause == judgments.first(read[opened].walked.causes)
    assert read[on_r.id].cause is None

    # what the update lapsed: each once, and nothing between it and an edit
    # that lapses nothing
    between = _changed(store, first, second)
    assert sorted(i.get("finding") or "t" for i in between) == sorted(
        [opened, postponed, "t"]
    )
    assert _changed(store, first, first) == []
    text = TEXT.replace("Let f be a gain.", "Let f be a positive gain.")
    blocks = [("d", "Let f be a positive gain."), *BLOCKS[1:]]
    third = keep(
        store,
        V2,
        text,
        graph(text, blocks, edges=[*EDGES, edge("r", "uses", "d")]),
        Command.EDIT,
        second,
    )
    assert len(_lapsed(View(store, third))) == 3
    assert _changed(store, second, third) == []


def test_what_lapsed_between_two_states_is_by_their_statuses(tmp_path) -> None:
    """The proof's `valid` lapses by its edge retracted, then its text
    changes: the second change lapses nothing new, though its cause would
    name the later state."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    edges = [e for e in EDGES if e["id"] != "p>uses>d>None"]
    second = keep(store, V1, TEXT, graph(edges=edges), Command.EDIT, first)
    text = TEXT.replace("By the gain", "By its gain")
    blocks = [*BLOCKS[:2], ("p", "By its gain, f is bounded."), BLOCKS[3]]
    third = updated(
        store,
        second,
        text,
        graph(text, blocks, edges),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )
    assert status(store, third, Aspect.VALID, "p").cause.state == third.id

    (valid,) = _changed(store, first, second)
    assert valid["cause"]["kind"] == "edge" and valid["handle"] == "p"
    assert _changed(store, second, third) == []
    assert [i["handle"] for i in _changed(store, first, third)] == ["p"]


def test_a_lapsed_subject_names_the_cause_of_the_reading_it_offers_to_reask(
    tmp_path,
) -> None:
    """One agent's `valid` lapses by the proof's text; another's, filed
    after it, by an edge retracted: the status names the second's cause,
    though a text cause comes before an edge's by kind."""
    from axiomation.queries import View, status_line

    store, first = built(tmp_path)
    filed_valid(store, first)
    text = TEXT.replace("By the gain", "By its gain")
    blocks = [*BLOCKS[:2], ("p", "By its gain, f is bounded."), BLOCKS[3]]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )
    latest = filed_valid(store, second, OTHER_AGENT)
    edges = [e for e in EDGES if e["id"] != "p>uses>d>None"]
    third = keep(store, V2, text, graph(text, blocks, edges), Command.EDIT, second)

    valid = status(store, third, Aspect.VALID, "p")
    assert valid.status is StatusKind.LAPSED and len(valid.reads) == 2
    assert valid.cause.kind is CauseKind.EDGE and valid.cause.state == third.id
    line = status_line(View(store, third), valid)
    assert f"lapsed at {third.id}: edge, " in line
    assert line.endswith(f"(`axiomation reask` {latest.id})")


def test_a_finding_whose_every_anchor_is_deleted_lapses_at_the_paper(
    tmp_path,
) -> None:
    from axiomation.queries import View

    store, first = built(tmp_path)
    at = seen(store, first)
    words = "By the gain"
    span = anchored(at, AnchorKind.SPAN, "p", words=words, text=BLOCKS[2][1])
    filed = find(
        store, first, Aspect.PRESENTATION, [span], 1, "which gain?"
    ).records.findings[0]
    text = TEXT.replace(" By the gain, f is bounded.", "")
    blocks = [b for b in BLOCKS if b[0] != "p"]
    edges = [e for e in EDGES if e["source"] != "p"]
    links = [*same("d", "t", "r"), (["p"], [], "deleted", None)]
    second = updated(store, first, text, graph(text, blocks, edges), links)

    (item,) = [i for i in _lapsed(View(store, second)) if "finding" in i]
    assert item["finding"] == filed.id and item["block"] is None
    assert item["cause"]["kind"] == "deleted"
    assert item["anchor"]["words"] == words


def test_a_subject_re_read_in_range_and_lapsed_again_is_listed_whoever_re_read_it(
    tmp_path,
) -> None:
    """The proof's `valid` lapses (an edge retracted), its author re-reads
    it on that state, and a text change lapses it again: from the first
    state to the third it lapsed anew, as it does with another author's
    re-reading; from the second to the third too; never by the earlier
    reading alone."""
    store, first = built(tmp_path)
    filed_valid(store, first)
    edges = [e for e in EDGES if e["id"] != "p>uses>d>None"]
    second = keep(store, V1, TEXT, graph(edges=edges), Command.EDIT, first)
    filed_valid(store, second)
    text = TEXT.replace("By the gain", "By its gain")
    blocks = [*BLOCKS[:2], ("p", "By its gain, f is bounded."), BLOCKS[3]]
    third = updated(
        store,
        second,
        text,
        graph(text, blocks, edges),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )
    assert status(store, third, Aspect.VALID, "p").status is StatusKind.LAPSED

    assert [i["handle"] for i in _changed(store, first, third)] == ["p"]
    assert [i["handle"] for i in _changed(store, second, third)] == ["p"]

    filed_valid(store, second, OTHER_AGENT)
    assert [i["handle"] for i in _changed(store, first, third)] == ["p"]


def test_a_lapsed_finding_names_an_anchor_that_holds_and_its_block(tmp_path) -> None:
    """A finding on the proof's words and on the remark: the proof deleted,
    it lapses, and its item names the remark's anchor and block, not the
    deleted proof's."""
    from axiomation.queries import View

    store, first = built(tmp_path)
    at = seen(store, first)
    words = "By the gain"
    filed = find(
        store,
        first,
        Aspect.PRESENTATION,
        [
            anchored(at, AnchorKind.SPAN, "p", words=words, text=BLOCKS[2][1]),
            anchored(at, AnchorKind.BLOCK, "r"),
        ],
        1,
        "which gain?",
    ).records.findings[0]
    text = TEXT.replace(" By the gain, f is bounded.", "")
    blocks = [b for b in BLOCKS if b[0] != "p"]
    edges = [e for e in EDGES if e["source"] != "p"]
    links = [*same("d", "t", "r"), (["p"], [], "deleted", None)]
    second = updated(store, first, text, graph(text, blocks, edges), links)

    (item,) = [i for i in _lapsed(View(store, second)) if "finding" in i]
    assert item["finding"] == filed.id and item["cause"]["kind"] == "deleted"
    assert item["anchor"]["node"] == second.node_of("r") and item["block"] == "r"
    assert item["anchors"][0]["words"] == words, "every anchor kept as filed"


def test_a_lapsed_finding_reaffirmed_is_refiled_live_and_closes_the_lapsed(
    tmp_path,
) -> None:
    """A re-reading that finds the problem standing refiles it at the new
    state, its anchors walked and their words found again; the lapsed one is
    no longer open. A live, an answered or an already reaffirmed finding is
    refused, as is a quote no longer found once."""
    from axiomation.judgments import reaffirm_finding

    store, first = built(tmp_path)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.PART, "d", "a gain", text="Let f be a gain.")]
    lapsing, answered = (
        find(store, first, Aspect.WELL_POSED, on_d, 3, problem, "say which")
        .records.findings[0]
        .id
        for problem in ("which gain?", "a gain of what?")
    )
    on_r = [anchored(at, AnchorKind.BLOCK, "r")]
    live = find(store, first, Aspect.PRESENTATION, on_r, 2, "vague")
    live = live.records.findings[0].id
    second = _reworded_d(store, first)
    answer_finding(store, answered, FindingStatus.DISMISSED)

    with pytest.raises(JudgmentError, match="holds at state"):
        reaffirm_finding(store, second, live, "still vague")
    with pytest.raises(JudgmentError, match="answered dismissed"):
        reaffirm_finding(store, second, answered, "still")
    with pytest.raises(JudgmentError, match="says why"):
        reaffirm_finding(store, second, lapsing, " ")
    with pytest.raises(JudgmentError, match="quote its words as they read now"):
        reaffirm_finding(store, second, lapsing, "still", words="a negative gain")

    filed = reaffirm_finding(
        store, second, lapsing, "positive says nothing of which", words="positive gain"
    )
    (refiled,) = filed.records.findings
    assert (refiled.state, refiled.reaffirms, refiled.evidence) == (
        second.id,
        lapsing,
        "positive says nothing of which",
    )
    assert (refiled.problem, refiled.fix, refiled.severity) == (
        "which gain?",
        "say which",
        3,
    )
    assert [(a.kind, a.words) for a in refiled.anchors] == [
        (AnchorKind.PART, "positive gain")
    ]
    read = {f.finding.id: f for f in readings(store, second).findings}
    assert read[lapsing].reaffirmed == refiled.id and not read[lapsing].open
    assert read[refiled.id].live and read[refiled.id].open
    # the old id names its latest filing, live: nothing to reaffirm
    with pytest.raises(JudgmentError, match=f"{refiled.id} holds"):
        reaffirm_finding(store, second, lapsing, "again")
    # and an answer by the id the author knew answers the filing it became
    (answer,) = answer_finding(
        store, lapsing, FindingStatus.DISMISSED, "not a gap"
    ).records.answers
    assert answer.finding == refiled.id
    read = {f.finding.id: f for f in readings(store, second).findings}
    assert not read[refiled.id].open


def test_a_finding_that_quotes_nothing_is_not_quoted_anew(tmp_path) -> None:
    from axiomation.judgments import reaffirm_finding

    store, first = built(tmp_path)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.BLOCK, "d")]
    lapsing = find(store, first, Aspect.WELL_POSED, on_d, 3, "which gain?")
    second = _reworded_d(store, first)
    ref = lapsing.records.findings[0].id
    with pytest.raises(JudgmentError, match="quotes no words"):
        reaffirm_finding(store, second, ref, "still", words="positive gain")
    (refiled,) = reaffirm_finding(store, second, ref, "still").records.findings
    assert [a.kind for a in refiled.anchors] == [AnchorKind.BLOCK]


# --- the question a review answered ------------------------------------------------

#: Before the ruling question's version 2 took effect: a review's answer
#: filed then, its template recording none, answered version 1.
BEFORE = "2026-10-21T19:50:00+00:00"
AFTER = "2026-10-24T09:00:00+00:00"


def reviewed(
    store,
    records: JudgmentRecords,
    model: str = "model-a",
    time: str = AFTER,
    asks: tuple[tuple[str, int], ...] | None = None,
) -> JudgmentRecords:
    """`records` filed as an argument review's answer by `model` at `time`,
    its template recording `asks` (by default what the brief asks now)."""
    asks = rf.asks(rf.Session.ARGUMENT) if asks is None else asks
    key = hashlib.sha256(f"{model} {time} {len(store.answers())}".encode()).hexdigest()
    ids = (f"{key[:12]}-1-{n}" for n in itertools.count(1))
    numbered = JudgmentRecords(
        **{
            kind.field: tuple(
                replace(r, id=next(ids), answer=key)
                for r in getattr(records, kind.field)
            )
            for kind in JUDGMENT_KINDS
        }
    )
    store.append(
        AnswerRecord(
            key=key,
            kind="review-argument",
            stage="review",
            template=TemplateRef("review-argument", "0123456789ab", asks),
            attempt=1,
            host="claude-code 2.1.310",
            model=model,
            time=time,
            seconds=0.0,
            cost=0.0,
            valid=True,
            records=numbered.ids,
        ),
        judgments=numbered,
    )
    return numbered


def reviewed_true(store, kept, verdict: Verdict, **given) -> None:
    at = seen(store, kept)
    record = judgments.judgment_record(
        at, Aspect.TRUE, subject(at, Aspect.TRUE, "t"), verdict, Depth.INFORMAL, "read"
    )
    reviewed(store, JudgmentRecords(judgments=(record,)), **given)


def test_a_reading_under_an_older_question_is_asked_again_until_one_concludes(
    tmp_path, monkeypatch
) -> None:
    """With `true`'s question at version 2, model-a's `contradicted` from a
    review under version 1 speaks and is asked again; model-b's
    `inconclusive` under version 2 takes no debit away, so it stands, and is
    not asked again; model-b's `verified` replaces it, no dispute. A reading
    filed by hand answered no brief, and is never asked again."""
    monkeypatch.setitem(rf.QUESTION_VERSIONS, Aspect.TRUE.value, 2)
    store, kept = built(tmp_path)
    reviewed_true(store, kept, Verdict.CONTRADICTED, time=BEFORE, asks=())
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.outdated and found.held.outdated and found.held.question == 1
    old = found.held.judgment.id

    reviewed_true(store, kept, Verdict.INCONCLUSIVE, model="model-b")
    found = status(store, kept, Aspect.TRUE, "t")
    assert not found.outdated
    assert old in {r.judgment.id for r in found.reads}

    reviewed_true(store, kept, Verdict.VERIFIED, model="model-b")
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.status is StatusKind.HELD and not found.outdated
    assert found.held.judgment.verdict is Verdict.VERIFIED
    assert old not in {r.judgment.id for r in found.reads}

    filed_true(store, kept, OTHER_AGENT, Verdict.VERIFIED)
    assert all(r.question is None for r in readings(store, kept).reads[-1:])


def test_a_reading_a_person_decided_over_is_not_asked_again(
    tmp_path, monkeypatch
) -> None:
    """model-a's `contradicted` under `true`'s version 1, then a person's
    `verified`: the person decides, and no review settles it."""
    monkeypatch.setitem(rf.QUESTION_VERSIONS, Aspect.TRUE.value, 2)
    store, kept = built(tmp_path)
    reviewed_true(store, kept, Verdict.CONTRADICTED, time=BEFORE, asks=())
    filed_true(store, kept, PERSON, Verdict.VERIFIED)
    found = status(store, kept, Aspect.TRUE, "t")
    assert found.held.author.person and not found.outdated


def test_a_block_no_review_judges_says_its_text_changed_since_it_was_read(
    tmp_path,
) -> None:
    """The definition `d` the proof uses: an argument review's `valid` of
    the proof read it; once `d` is reworded, its last read says the text
    changed since. A findings session at the new state that read the paper
    whole is then its last read; a scoped one names no block it read."""
    from axiomation.queries import View, show

    payload = graph()
    for b in payload["blocks"]:
        if b["id"] == "d":
            b["roles"] = ["definition"]
    store, first = built(tmp_path, payload)
    at = seen(store, first)
    record = judgments.judgment_record(
        at,
        Aspect.VALID,
        subject(at, Aspect.VALID, "p"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "d bounds it",
        (),
    )
    (valid,) = reviewed(store, judgments.judged(record)).judgments

    def read_at(kept) -> dict[str, Any]:
        return show(View(store, kept), "d").data["no_claim"]["last_read"]

    read = read_at(first)
    assert (read["readings"], read["changed_since"]) == ([valid.id], False)
    second = _reworded_d(store, first)
    read = read_at(second)
    assert (read["state"], read["changed_since"]) == (first.id, True)
    assert read["words"].endswith("; its text changed since")

    for n, name in enumerate(("review-findings+scope", "review-findings")):
        store.append(
            AnswerRecord(
                key=f"findings-{n}",
                kind="review-findings",
                stage="review",
                template=TemplateRef(name, "0123456789ab"),
                attempt=1,
                host="claude-code 2.1.310",
                model="model-a",
                time=f"2026-10-2{5 + n}T09:00:00+00:00",
                seconds=0.0,
                cost=0.0,
                valid=True,
                state=second.id,
            )
        )
        read = read_at(second)
        assert read["answer"] == (valid.answer if n == 0 else "findings-1")
    assert (read["session"], read["changed_since"]) == ("findings", False)
