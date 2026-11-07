"""`established`, reaffirmation and a person's name: a claim established by its
standing's route and by what it rests on, computed as a least fixed point over the
graph, the judgments and the open stale marks; a lapsed judgment reaffirmed at the
weaker depth; persons by name, who differ, disputed.

A synthetic one-line paper: an assumption `a` qualifying a definition `d`;
a lemma `l` justified by `q`, which uses `d`; a theorem `t` justified by
`p`, which uses `l` and `d`; a restating remark `r` about `t`; an
unsupported remark `c`. No model is called.
"""

from __future__ import annotations

from typing import Any

import pytest

from axiomation import judgments, queries
from axiomation.closure import from_export, reach
from axiomation.established import Established, Missing, established, undeclared
from axiomation.judgments import (
    Author,
    JudgmentError,
    StatusKind,
    anchored,
    answer_finding,
    find,
    judge,
    offered,
    readings,
    reaffirm,
    seen,
    subject,
)
from axiomation.marks import Mark, open_marks
from axiomation.records import (
    AnchorKind,
    Aspect,
    Depth,
    FindingStatus,
    RecordStore,
    Verdict,
)
from test_judgments import _run, edge, graph, naming, same, updated
from test_versions import V1, keep

BLOCKS = [
    ("a", "Assume f is positive."),
    ("d", "Let f be a gain."),
    ("l", "Then f is small."),
    ("q", "Since f is a gain, f is small."),
    ("t", "Then f is bounded."),
    ("p", "By the lemma, f is bounded."),
    ("r", "So f is bounded, as shown."),
    ("c", "Surely f is finite."),
]
TEXT = " ".join(w for _, w in BLOCKS)
EDGES = [
    edge("a", "qualifies", "d"),
    edge("q", "uses", "d"),
    edge("q", "justifies", "l"),
    edge("p", "uses", "l"),
    edge("p", "uses", "d"),
    edge("p", "justifies", "t"),
    {**edge("r", "about", "t"), "evidence": [{"text": "f is bounded"}]},
]
STANDINGS = {
    "l": ("Then f is small.", "justified"),
    "t": ("Then f is bounded.", "justified"),
    "r": ("So f is bounded, as shown.", "restated"),
    "c": ("Surely f is finite.", "unsupported"),
}
AGENT = Author("agent", "model-a", "s1")
PERSON = Author()


def payload(
    text: str = TEXT,
    blocks=BLOCKS,
    edges=EDGES,
    standings=STANDINGS,
    update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    found = graph(
        text,
        blocks,
        edges,
        [(b, words) for b, (words, _) in standings.items()],
        update,
    )
    for s in found["standings"]:
        s["status"] = standings[s["block"]][1]
    for b in found["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    return found


def built(tmp_path, data: dict[str, Any] | None = None, text: str = TEXT):
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, text, data or payload())


def valid(store, kept, j: str, author=AGENT, verdict=Verdict.VERIFIED, depth=None):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.VALID,
        subject(at, Aspect.VALID, j),
        verdict,
        depth or Depth.INFORMAL,
        "the proof holds",
        author,
    ).records.judgments[0]


def true(store, kept, on: str, author=AGENT, verdict=Verdict.VERIFIED):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        verdict,
        Depth.INFORMAL,
        "it holds",
        author,
    ).records.judgments[0]


def verdicts(store, kept) -> dict[str, Established]:
    found = established(readings(store, kept), open_marks(store, kept))
    by_block = {n.node: n.block for n in kept.nodes}
    return {by_block[e.claim.node]: e for e in found}


def handles(kept, nodes) -> list[str]:
    by_id = {n.node: n.block for n in kept.nodes}
    by_id |= {node: g for g, node in kept.groups.items()}
    return [by_id[n] for n in nodes]


# --- established ---------------------------------------------------------------------


def test_a_fresh_build_establishes_nothing(tmp_path) -> None:
    store, kept = built(tmp_path)
    found = verdicts(store, kept)
    assert not any(e.established for e in found.values())
    assert found["t"].reason.kind is Missing.UNJUDGED
    assert "`valid` of p → t" in found["t"].reason.why
    assert found["c"].reason.kind is Missing.UNSUPPORTED


def test_a_justified_claim_rests_on_what_its_j_uses(tmp_path) -> None:
    """t's J holds `verified`, but t rests on l, which is unjudged: the
    reason names l's missing judgment. Once l's J holds, t and the remark
    restating it are established, under the assumption that qualifies d."""
    store, kept = built(tmp_path)
    valid(store, kept, "p")
    found = verdicts(store, kept)
    assert not found["t"].established
    assert found["t"].reason.kind is Missing.UNJUDGED
    assert found["t"].reason.why.startswith(
        'rests on l "Then f is small.": `valid` of q'
    )
    assert found["r"].reason.why.startswith('rests on t "Then f is bounded."')

    valid(store, kept, "q")
    found = verdicts(store, kept)
    assert found["l"].established and found["t"].established
    assert handles(kept, [found["t"].via]) == ["p"]
    assert handles(kept, found["t"].assumptions) == ["a"]
    assert found["r"].established and handles(kept, found["r"].assumptions) == ["a"]


def test_a_held_negative_or_critical_finding_on_what_it_rests_on_blocks_it(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    valid(store, kept, "p")
    valid(store, kept, "q")
    true(store, kept, "l", verdict=Verdict.CONTRADICTED)
    found = verdicts(store, kept)
    assert found["l"].reason.kind is Missing.CONTRADICTED
    assert found["t"].reason.kind is Missing.CONTRADICTED
    assert found["t"].reason.why.startswith('rests on l "Then f is small.": true on l')

    true(store, kept, "l", PERSON, Verdict.VERIFIED)
    assert verdicts(store, kept)["t"].established

    at = seen(store, kept)
    f = find(
        store, kept, Aspect.WELL_POSED, [anchored(at, AnchorKind.BLOCK, "d")], 3, "f?"
    ).records.findings[0]
    found = verdicts(store, kept)
    assert found["l"].reason.kind is Missing.FINDING
    assert found["t"].reason.kind is Missing.FINDING
    answer_finding(store, f.id, FindingStatus.FIXED)
    assert verdicts(store, kept)["t"].established


@pytest.mark.parametrize(
    "aspect", [Aspect.PRESENTATION, Aspect.SIGNIFICANCE, Aspect.NOVELTY]
)
def test_a_finding_on_how_it_reads_or_what_it_is_worth_never_blocks(
    tmp_path, aspect
) -> None:
    """A trivial or badly written result is still established:
    an open severity-3 finding of these aspects leaves the claim as it was."""
    store, kept = built(tmp_path)
    valid(store, kept, "p")
    valid(store, kept, "q")
    assert verdicts(store, kept)["t"].established
    at = seen(store, kept)
    find(store, kept, aspect, [anchored(at, AnchorKind.BLOCK, "d")], 3, "says nothing")
    assert verdicts(store, kept)["t"].established


def test_an_unsupported_or_conjectured_claim_takes_a_held_verified_true(
    tmp_path,
) -> None:
    standings = {**STANDINGS, "l": ("Then f is small.", "conjectured")}
    store, kept = built(tmp_path, payload(standings=standings))
    found = verdicts(store, kept)
    assert found["c"].reason.kind is Missing.UNSUPPORTED
    assert found["l"].reason.kind is Missing.UNSUPPORTED
    true(store, kept, "c")
    true(store, kept, "l", verdict=Verdict.INCONCLUSIVE)
    found = verdicts(store, kept)
    assert found["c"].established
    assert found["l"].reason.kind is Missing.INCONCLUSIVE


def test_circular_support_establishes_nothing(tmp_path) -> None:
    """l's J uses t and t's J uses l: each `valid` verified, neither is
    established (the least fixed point)."""
    edges = [*EDGES, edge("q", "uses", "t")]
    store, kept = built(tmp_path, payload(edges=edges))
    valid(store, kept, "p")
    valid(store, kept, "q")
    found = verdicts(store, kept)
    assert not found["l"].established and not found["t"].established
    assert found["t"].reason.kind is Missing.CIRCULAR


def test_an_edge_onto_a_part_with_no_claim_asks_no_claim(tmp_path) -> None:
    """p uses only a definition part of l: l's claim is not asked, its
    block is walked through, and a negative `true` on that claim, which t
    does not rest on, does not count against t."""
    edges = [e for e in EDGES if e["id"] != "p>uses>l>None"]
    edges.append(edge("p", "uses", "l", "l [f]"))
    data = payload(edges=edges)
    data["entries"] = [{"id": "l [f]", "anchor": "f", "role": "definition"}]
    store, kept = built(tmp_path, data)
    valid(store, kept, "p")
    true(store, kept, "l", verdict=Verdict.CONTRADICTED)
    found = verdicts(store, kept)
    assert found["t"].established
    assert found["l"].reason.kind is Missing.UNJUDGED


def test_a_group_j_rests_on_its_members_uses(tmp_path) -> None:
    edges = [e for e in EDGES if e["source"] != "p"]
    edges += [
        edge("p", "uses", "l"),
        {**edge("proof@p", "justifies", "t"), "source": "proof@p"},
    ]
    data = payload(edges=edges)
    data["groups"] = [{"id": "proof@p", "kind": "proof", "blocks": ["p", "r"]}]
    store, kept = built(tmp_path, data)
    valid(store, kept, "proof@p")
    found = verdicts(store, kept)
    assert found["t"].reason.why.startswith('rests on l "Then f is small."')
    valid(store, kept, "q")
    found = verdicts(store, kept)
    assert found["t"].established and handles(kept, [found["t"].via]) == ["proof@p"]


def test_an_open_stale_mark_on_what_it_rests_on_blocks_it(tmp_path) -> None:
    store, first = built(tmp_path)
    valid(store, first, "p")
    valid(store, first, "q")
    stale = [
        {
            "handle": "l",
            "causes": [
                {"why": "what it uses changed", "from": "d", "via": "uses", "root": "d"}
            ],
            "cleared_by": None,
        }
    ]
    second = updated(
        store,
        first,
        TEXT,
        payload(update={"deltas": [], "moved_by_rule": [], "stale": stale}),
        same(*(b for b, _ in BLOCKS)),
    )
    found = verdicts(store, second)
    assert found["l"].reason.kind is Missing.STALE
    assert found["t"].reason.why.startswith(
        'rests on l "Then f is small.": its line is unread since'
    )


# --- a person's name ------------------------------------------------------------------


def test_named_persons_who_differ_stay_disputed(tmp_path) -> None:
    store, kept = built(tmp_path)
    true(store, kept, "c", AGENT, Verdict.CONTRADICTED)
    true(store, kept, "c", Author("person", "ada"), Verdict.VERIFIED)
    found = judgments_status(store, kept, "c")
    assert found.status is StatusKind.HELD and found.held.author.name == "person ada"

    true(store, kept, "c", Author("person", "bo"), Verdict.NOT_SHOWN)
    assert judgments_status(store, kept, "c").status is StatusKind.DISPUTED

    true(store, kept, "c", Author("person", "bo"), Verdict.VERIFIED)
    found = judgments_status(store, kept, "c")
    assert found.status is StatusKind.HELD and found.held.author.name == "person bo"


def judgments_status(store, kept, block: str) -> judgments.SubjectStatus:
    node = kept.node_of(block)
    (one,) = [
        s
        for s in readings(store, kept).statuses
        if s.aspect is Aspect.TRUE and s.subject.node == node
    ]
    return one


# --- reaffirmation -------------------------------------------------------------------


def edited(store, first):
    """p's text changed: its `valid` lapses; a writer's Δ on it."""
    text = TEXT.replace("By the lemma", "By the lemma above")
    blocks = [(b, w.replace("By the lemma", "By the lemma above")) for b, w in BLOCKS]
    deltas = [
        {
            "block": "p",
            "entry": None,
            "class": "reworded",
            "note": "the lemma's name",
            "by": "writer",
        }
    ]
    data = payload(text, blocks, update={"deltas": deltas, "moved_by_rule": []})
    links = same(*(b for b, _ in BLOCKS), but={"p": "changed"})
    return updated(store, first, text, data, links)


def test_a_lapsed_judgment_reaffirmed_at_the_weaker_depth(tmp_path) -> None:
    store, first = built(tmp_path)
    old = valid(store, first, "p", PERSON, depth=Depth.FORMAL)
    with pytest.raises(JudgmentError, match="nothing to reaffirm"):
        reaffirm(store, first, old.id, Depth.INFORMAL, "still holds", AGENT)

    second = edited(store, first)
    new = reaffirm(store, second, old.id, Depth.INFORMAL, "the change is a word", AGENT)
    (record,) = new.records.judgments
    assert record.reaffirms == old.id and record.state == second.id
    assert record.verdict is Verdict.VERIFIED and record.depth is Depth.INFORMAL
    assert record.subject.node == second.node_of("p")
    p_valid = [
        s
        for s in readings(store, second).statuses
        if s.aspect is Aspect.VALID and s.subject.node == second.node_of("p")
    ]
    assert p_valid[0].status is StatusKind.HELD
    assert p_valid[0].held.author.name == "agent model-a"


def test_a_reaffirmation_of_a_deleted_subject_is_refused(tmp_path) -> None:
    store, first = built(tmp_path)
    old = true(store, first, "c")
    blocks = [x for x in BLOCKS if x[0] != "c"]
    text = " ".join(w for _, w in blocks)
    standings = {k: v for k, v in STANDINGS.items() if k != "c"}
    data = payload(text, blocks, standings=standings)
    links = [*same(*(b for b, _ in blocks)), (["c"], [], "deleted", None)]
    second = updated(store, first, text, data, links)
    with pytest.raises(JudgmentError, match="deleted"):
        reaffirm(store, second, old.id, Depth.INFORMAL, "x", AGENT)


def test_the_commands_reaffirm_show_established_and_ask_the_meaning(
    tmp_path, capsys
) -> None:
    store, first = built(tmp_path)
    paper, where = str(tmp_path), ["--store", str(tmp_path / "store")]
    old = valid(store, first, "p")
    valid(store, first, "q")
    code, out, _ = _run(capsys, "blocks", paper, *where, "--established", "yes")
    assert out.startswith("3 blocks of 8")
    code, out, _ = _run(capsys, "show", paper, *where, "t")
    assert '"Then f is bounded." justified  established by p, under a' in out

    edited(store, first)
    code, out, _ = _run(capsys, "show", paper, *where, "t")
    assert '"Then f is bounded." justified  not established: lapsed' in out
    code, out, err = _run(capsys, "reask", paper, *where, old.id)
    assert code == 0, err
    assert f"`axiomation reaffirm PAPER {old.id} --depth D" in out
    assert "does p's meaning move beyond `Δ reworded — the lemma's name`?" in out
    assert (
        "`axiomation change PAPER --delta p 'Δ reworded — the lemma'\"'\"'s name'`"
        in out
    )

    code, _, err = _run(
        capsys,
        "reaffirm",
        paper,
        *where,
        old.id[:14],
        "--depth",
        "informal",
        "--evidence",
        "a word added",
        "--agent",
        "m",
        "--agent-session",
        "s",
        "--person",
        "ada",
    )
    assert code == 1 and "one author" in err
    code, out, err = _run(
        capsys,
        "reaffirm",
        paper,
        *where,
        old.id[:14],
        "--depth",
        "formal",
        "--evidence",
        "a word added",
        "--person",
        "ada",
    )
    assert code == 0, err
    assert f"reaffirms {old.id}, verified (informal)" in out
    code, out, _ = _run(capsys, "show", paper, *where, "p")
    assert "by person ada" in out and f"reaffirms {old.id} at {first.id}" in out
    code, out, _ = _run(capsys, "blocks", paper, *where, "--established", "no")
    assert out.startswith("1 blocks of 8") and "  c  c" in out


# --- the review's cases -------------------------------------------------------------


def test_a_person_decides_over_an_agent_s_critical_finding_for_established(
    tmp_path,
) -> None:
    """An agent's severity-3 finding on l's `true`, then a person's
    `verified` on it: `show` holds it, and `established` agrees."""
    store, kept = built(tmp_path)
    valid(store, kept, "p")
    valid(store, kept, "q")
    at = seen(store, kept)
    find(
        store,
        kept,
        Aspect.TRUE,
        [anchored(at, AnchorKind.BLOCK, "l")],
        3,
        "f is not small",
        author=AGENT,
    )
    assert verdicts(store, kept)["t"].reason.kind is Missing.FINDING
    true(store, kept, "l", PERSON)
    found = verdicts(store, kept)
    assert found["l"].established and found["t"].established


def test_a_person_who_could_not_tell_decides_nothing(tmp_path) -> None:
    store, kept = built(tmp_path)
    true(store, kept, "c", Author("person", "ada"), Verdict.VERIFIED)
    true(store, kept, "c", AGENT, Verdict.CONTRADICTED)
    true(store, kept, "c", Author("person", "bo"), Verdict.INCONCLUSIVE)
    assert judgments_status(store, kept, "c").status is StatusKind.DISPUTED


def test_a_self_claim_takes_a_held_verified_true(tmp_path) -> None:
    standings = {**STANDINGS, "l": ("Then f is small.", "self")}
    store, kept = built(tmp_path, payload(standings=standings))
    found = verdicts(store, kept)
    assert found["l"].reason.kind is Missing.UNJUDGED
    assert found["l"].reason.why == '`true` of l "Then f is small." is unjudged'
    true(store, kept, "l")
    assert verdicts(store, kept)["l"].established


def test_a_restated_claim_restates_every_about_edge_of_its_block(tmp_path) -> None:
    """An about edge's evidence never chooses which claim a restated claim
    restates: r's second about edge, onto c, quotes nothing
    of r's words, yet c, unsupported and unjudged, holds r back."""
    edges = [*EDGES, {**edge("r", "about", "c"), "evidence": [{"text": "finite"}]}]
    store, kept = built(tmp_path, payload(edges=edges))
    valid(store, kept, "p")
    valid(store, kept, "q")
    found = verdicts(store, kept)
    assert not found["r"].established and not found["c"].established
    assert found["r"].reason is not None
    assert found["r"].reason.node == kept.node_of("c")


def test_a_claim_part_relies_on_every_claim_a_content_part_on_none(
    tmp_path,
) -> None:
    """p's uses edge onto c names a part with c's claim's words. Of role
    `claim` (an old export's), it names no claim and relies on every claim
    of c, here one, which p's proof then asks: unsupported, it holds t
    back. Of role `definition` (a part naming steps), the edge relies
    on content and goes on: c's claim is not asked."""
    for role, held_back in (("claim", True), ("definition", False)):
        edges = [*EDGES, edge("p", "uses", "c", "c [C]")]
        data = payload(edges=edges)
        data["entries"] = [
            {"id": "c [C]", "anchor": "Surely f is finite.", "role": role}
        ]
        store, kept = built(tmp_path / role, data)
        valid(store, kept, "p")
        valid(store, kept, "q")
        found = verdicts(store, kept)["t"]
        assert found.established is not held_back, role
        if held_back:
            assert found.reason is not None
            assert found.reason.node == kept.node_of("c")


TWO = [
    ("d", "Let f be a gain."),
    ("t", "Then f is bounded. Also f is even."),
    ("p", "By the theorem's hypotheses, f is bounded."),
    ("q", "By symmetry, f is even."),
]
TWO_TEXT = " ".join(w for _, w in TWO)


def test_two_claims_each_by_the_j_that_names_it_and_no_sibling_circle(
    tmp_path,
) -> None:
    """t holds two claims, each justified by the J naming it; p also
    relies on t's block whole (its hypotheses). Neither claim asks the
    other, and a J that argues one claim is never offered for the other."""
    edges = [
        edge("p", "uses", "t"),
        edge("p", "uses", "d"),
        naming(edge("p", "justifies", "t"), "f is bounded"),
        naming(edge("q", "justifies", "t"), "f is even"),
    ]
    data = graph(TWO_TEXT, TWO, edges, [("t", "f is bounded"), ("t", "f is even")])
    for b in data["blocks"]:
        b.setdefault("roles", [])
    store, kept = built(tmp_path, data, TWO_TEXT)
    at = seen(store, kept)
    judge(
        store,
        kept,
        Aspect.VALID,
        subject(at, Aspect.VALID, "p", claim="f is bounded"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "holds",
        AGENT,
    )
    found = established(readings(store, kept), open_marks(store, kept))
    bounded, even = found
    assert bounded.established and handles(kept, [bounded.via]) == ["p"]
    assert even.reason.kind is Missing.UNJUDGED
    assert "`valid` of q → t" in even.reason.why


def test_a_reaffirmation_where_j_justifies_t_no_more_is_refused(tmp_path) -> None:
    store, first = built(tmp_path)
    old = valid(store, first, "p")
    edges = [e for e in EDGES if e["id"] != "p>justifies>t>None"]
    second = updated(
        store, first, TEXT, payload(edges=edges), same(*(b for b, _ in BLOCKS))
    )
    with pytest.raises(JudgmentError, match="no longer offers"):
        reaffirm(store, second, old.id, Depth.INFORMAL, "x", AGENT)


def test_a_reaffirmation_of_an_ended_claim_is_refused(tmp_path) -> None:
    store, first = built(tmp_path)
    old = true(store, first, "c")
    standings = {k: v for k, v in STANDINGS.items() if k != "c"}
    second = updated(
        store,
        first,
        TEXT,
        payload(standings=standings),
        same(*(b for b, _ in BLOCKS)),
    )
    with pytest.raises(JudgmentError, match="ended"):
        reaffirm(store, second, old.id, Depth.INFORMAL, "x", AGENT)


def test_a_second_reading_that_agrees_never_lifts_a_doubt(tmp_path) -> None:
    """An agent's `contradicted` on t's own `true` blocks t, justified by a
    J held `verified`; another agent's `verified` makes it disputed, which
    still blocks: only a person's later reading decides."""
    store, kept = built(tmp_path)
    valid(store, kept, "p")
    valid(store, kept, "q")
    true(store, kept, "t", AGENT, Verdict.CONTRADICTED)
    assert verdicts(store, kept)["t"].reason.kind is Missing.CONTRADICTED
    true(store, kept, "t", Author("agent", "model-b", "s2"), Verdict.VERIFIED)
    assert verdicts(store, kept)["t"].reason.kind is Missing.DISPUTED
    true(store, kept, "t", PERSON, Verdict.VERIFIED)
    assert verdicts(store, kept)["t"].established


def test_a_justifies_edge_is_offered_for_the_claims_it_covers(tmp_path) -> None:
    """`valid` is offered once per claim J's line covers,
    the claims `established` reads it for: those it names, else every
    `justified` claim of T. p names "f is bounded"; q names none."""
    edges = [
        naming(edge("p", "justifies", "t"), "f is bounded"),
        edge("q", "justifies", "t"),
    ]
    data = graph(TWO_TEXT, TWO, edges, [("t", "f is bounded"), ("t", "f is even")])
    for b in data["blocks"]:
        b.setdefault("roles", [])
    store, kept = built(tmp_path, data, TWO_TEXT)
    found = [
        (handles(kept, [on.node])[0], on.words)
        for aspect, on in offered(seen(store, kept))
        if aspect is Aspect.VALID
    ]
    assert found == [
        ("p", "f is bounded"),
        ("q", "f is bounded"),
        ("q", "f is even"),
    ]


def test_an_about_edge_of_a_block_of_several_claims_lends_none_support(
    tmp_path,
) -> None:
    """r holds a restated claim and an unsupported one; its about edge onto
    t names neither at its source end, so it may be the other sentence's:
    it lends the restated claim no support, toward the alarm, and the
    reason says what clears it. Naming the claim at the source end, it
    restates t and stands with it."""
    blocks = [*BLOCKS[:6], ("r", "So f is bounded. It is new."), BLOCKS[7]]
    text = " ".join(w for _, w in blocks)
    about = {**edge("r", "about", "t"), "evidence": [{"text": "So f is bounded."}]}
    standings = [
        ("l", "Then f is small.", "justified"),
        ("t", "Then f is bounded.", "justified"),
        ("r", "So f is bounded.", "restated"),
        ("r", "It is new.", "unsupported"),
        ("c", "Surely f is finite.", "unsupported"),
    ]

    def restated(name, line):
        data = graph(text, blocks, [*EDGES[:-1], line], [s[:2] for s in standings])
        for s, (_, _, status) in zip(data["standings"], standings, strict=True):
            s["status"] = status
        for b in data["blocks"]:
            if b["id"] == "a":
                b["roles"] = ["assumption"]
        store, kept = built(tmp_path / name, data, text)
        valid(store, kept, "p")
        valid(store, kept, "q")
        found = established(readings(store, kept), open_marks(store, kept))
        (claim,) = [e for e in found if e.claim.words == "So f is bounded."]
        return claim

    unowned = restated("unowned", about)
    assert not unowned.established and unowned.reason is not None
    assert "no about edge of it names this one at its source end" in (
        unowned.reason.why
    )
    assert restated(
        "owned", {**about, "source_claims": ["So f is bounded."]}
    ).established


def test_a_narrowing_keeps_the_valid_filed_on_the_claim_it_keeps(tmp_path) -> None:
    """A review narrows p's line, which named no claim of t, to the claim
    it argues: the `valid` it filed on that claim continues, the line's
    named claims being no part of the edge's name across states."""
    lines = [
        edge("p", "uses", "t"),
        edge("p", "uses", "d"),
        edge("p", "justifies", "t"),
    ]
    claims = [("t", "f is bounded"), ("t", "f is even")]

    def data(edges):
        found = graph(TWO_TEXT, TWO, edges, claims)
        for b in found["blocks"]:
            b.setdefault("roles", [])
        return found

    store, first = built(tmp_path, data(lines), TWO_TEXT)
    at = seen(store, first)
    on = subject(at, Aspect.VALID, "p", claim="f is bounded")
    judge(
        store, first, Aspect.VALID, on, Verdict.VERIFIED, Depth.INFORMAL, "holds", AGENT
    )
    narrowed = [*lines[:2], naming(lines[2], "f is bounded")]
    second = updated(store, first, TWO_TEXT, data(narrowed), same(*(b for b, _ in TWO)))
    found = established(readings(store, second), open_marks(store, second))
    (bounded,) = [e for e in found if e.claim.words == "f is bounded"]
    assert bounded.established, bounded.reason


def test_an_unowned_about_edge_is_still_asked_beside_an_owned_one(tmp_path) -> None:
    """r's restated claim names t at its source end, and an about edge of
    r naming no claim goes onto c, unsupported: it may be this claim's, so
    it is asked, and r is held back."""
    blocks = [*BLOCKS[:6], ("r", "So f is bounded. It is new."), BLOCKS[7]]
    text = " ".join(w for _, w in blocks)
    owned = {
        **edge("r", "about", "t"),
        "evidence": [{"text": "So f is bounded."}],
        "source_claims": ["So f is bounded."],
    }
    unowned = {**edge("r", "about", "c"), "evidence": [{"text": "It is new."}]}
    standings = [
        ("l", "Then f is small.", "justified"),
        ("t", "Then f is bounded.", "justified"),
        ("r", "So f is bounded.", "restated"),
        ("r", "It is new.", "unsupported"),
        ("c", "Surely f is finite.", "unsupported"),
    ]
    data = graph(
        text, blocks, [*EDGES[:-1], owned, unowned], [s[:2] for s in standings]
    )
    for s, (_, _, status) in zip(data["standings"], standings, strict=True):
        s["status"] = status
    for b in data["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    store, kept = built(tmp_path, data, text)
    valid(store, kept, "p")
    valid(store, kept, "q")
    found = established(readings(store, kept), open_marks(store, kept))
    (claim,) = [e for e in found if e.claim.words == "So f is bounded."]
    assert not claim.established and claim.reason is not None
    assert claim.reason.node == kept.node_of("c")


def test_an_edge_line_names_the_claims_at_both_its_ends(tmp_path) -> None:
    """The reads print an edge as the edges sheet holds it: the source
    end's claims after its kind, the target's among its parts, each by
    the handle the sheet gives it, so a line that names a claim reads
    apart from one that names none."""
    claims = [("t", "f is bounded"), ("t", "f is even"), ("p", "So f is small.")]
    lines = [
        edge("p", "uses", "d"),
        naming(edge("p", "justifies", "t"), "f is bounded"),
        {**edge("p", "about", "t"), "source_claims": ["So f is small."]},
    ]
    data = graph(TWO_TEXT, TWO, lines, claims)
    for b in data["blocks"]:
        b.setdefault("roles", [])
    store, kept = built(tmp_path, data, TWO_TEXT)
    view = queries.View(store, kept)
    t = view.handle("t")
    assert [view.edge_line(e).split(" · ")[0] for e in view.edges[1:]] == [
        f"→ justifies {t} [f-is-bounded]",
        f"→ about [so-f-is-small] {t}",
    ]


# --- context: a proof's reliance on its own claim ----------------------------


def _context(tmp_path, blocks, edges, standings):
    """A state of `justified` claims, `standings` (block, words) in order."""
    text = " ".join(w for _, w in blocks)
    data = graph(text, blocks, edges, standings)
    for s in data["standings"]:
        s["status"] = "justified"
    return built(tmp_path, data, text)


#: `w` proves lemma `l`, relying on `l`'s hypotheses; `v` proves `m` by
#: reusing `w` ("as above").
REUSED = [
    ("l", "Then f is small."),
    ("w", "Since f is finite, f is small."),
    ("m", "Then f is tiny."),
    ("v", "As above, f is tiny."),
]
REUSING = [
    edge("w", "uses", "l"),
    edge("w", "justifies", "l"),
    edge("v", "uses", "w"),
    edge("v", "justifies", "m"),
]
REUSED_CLAIMS = [("l", "Then f is small."), ("m", "Then f is tiny.")]


def test_a_reused_step_does_not_ask_the_lemma_it_proves(tmp_path) -> None:
    """`m` rests on `w`'s reasoning, not on `l`'s result, so `m` is
    established by its own proof's reading while `l` is unjudged."""
    store, kept = _context(tmp_path, REUSED, REUSING, REUSED_CLAIMS)
    valid(store, kept, "v")
    found = verdicts(store, kept)
    assert found["m"].established
    assert not found["l"].established


def test_a_proof_naming_a_sibling_claim_asks_it(tmp_path) -> None:
    """`p` proves (b) of `t` and names (a), which no proof argues: (b) rests
    on (a)'s result and is not established while (a) is unsupported; its
    reliance on `t`'s hypotheses alone would ask nothing."""
    blocks = [
        ("t", "First f is small. Second f is tiny."),
        ("p", "By the first, f is tiny."),
    ]
    edges = [
        naming(edge("p", "uses", "t"), "First f is small."),
        naming(edge("p", "justifies", "t"), "Second f is tiny."),
    ]
    store, kept = _context(
        tmp_path,
        blocks,
        edges,
        [("t", "First f is small."), ("t", "Second f is tiny.")],
    )
    judge(
        store,
        kept,
        Aspect.VALID,
        subject(seen(store, kept), Aspect.VALID, "p", "t", "Second f is tiny."),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "the proof holds",
        AGENT,
    )
    second = [
        e
        for e in established(readings(store, kept), ())
        if e.claim.words == "Second f is tiny."
    ]
    assert not second[0].established
    assert "First f is small." in second[0].reason.why


def test_the_context_rule_changes_what_is_asked_never_what_an_edit_reaches(
    tmp_path,
) -> None:
    """The reuse stays an edge: an edit to `l`'s statement reaches the
    reused step `w`, and an edit to `w` reaches the proof `v` reusing it;
    an open stale mark on `w` counts against `m`."""
    text = " ".join(w for _, w in REUSED)
    export = graph(text, REUSED, REUSING, REUSED_CLAIMS)
    edges = from_export(export)
    assert "w" in [r.handle for r in reach(edges, "l").reached]
    assert "v" in [r.handle for r in reach(edges, "w").reached]
    store, kept = _context(tmp_path, REUSED, REUSING, REUSED_CLAIMS)
    valid(store, kept, "v")
    node = {n.block: n.node for n in kept.nodes}
    changed = {"why": "l changed", "from": "l", "via": None, "root": None}
    mark = Mark(node["w"], "w", "s0", (changed,))
    (m,) = [
        e
        for e in established(readings(store, kept), [mark])
        if e.claim.node == node["m"]
    ]
    assert not m.established
    assert m.reason.kind is Missing.STALE


def test_a_reused_step_s_definition_part_of_its_lemma_is_walked_through(
    tmp_path,
) -> None:
    """`w` uses the definition part `g` of `l`'s statement, and `l` uses
    `k`, an unsupported claim: reusing `w`, `m` rests on that content, so
    it is not established while `k` is not."""
    blocks = [
        ("k", "Surely f is finite."),
        ("l", "Let g be f squared. Then g is small."),
        ("w", "Since g is f squared, g is small."),
        ("m", "Then f is tiny."),
        ("v", "As above, f is tiny."),
    ]
    edges = [
        edge("l", "uses", "k"),
        edge("w", "uses", "l", "l [g]"),
        edge("w", "justifies", "l"),
        edge("v", "uses", "w"),
        edge("v", "justifies", "m"),
    ]
    text = " ".join(w for _, w in blocks)
    data = graph(
        text,
        blocks,
        edges,
        [
            ("k", "Surely f is finite."),
            ("l", "Then g is small."),
            ("m", "Then f is tiny."),
        ],
    )
    data["entries"] = [
        {"id": "l [g]", "anchor": "Let g be f squared.", "role": "definition"}
    ]
    for s in data["standings"]:
        s["status"] = "unsupported" if s["block"] == "k" else "justified"
    store, kept = built(tmp_path, data, text)
    valid(store, kept, "v")
    found = verdicts(store, kept)
    assert not found["m"].established
    assert "Surely f is finite." in found["m"].reason.why


def test_a_proof_of_two_claims_relies_on_the_one_it_uses_for_the_other(
    tmp_path,
) -> None:
    """`p` argues `t` and `c`, and uses `t` to argue `c`: within `c`'s own
    argument only `c`'s hypotheses are bound, so `t`'s result is asked and
    `c` waits for `t`'s reading."""
    blocks = [
        ("t", "Then f is small."),
        ("c", "Then f is tiny."),
        ("p", "First f is small, so f is tiny."),
    ]
    edges = [
        edge("p", "uses", "t"),
        edge("p", "justifies", "t"),
        edge("p", "justifies", "c"),
    ]
    store, kept = _context(
        tmp_path, blocks, edges, [("t", "Then f is small."), ("c", "Then f is tiny.")]
    )
    judge(
        store,
        kept,
        Aspect.VALID,
        subject(seen(store, kept), Aspect.VALID, "p", "c"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "the proof holds",
        AGENT,
    )
    found = verdicts(store, kept)
    assert not found["c"].established
    assert "Then f is small." in found["c"].reason.why


def _siblings(tmp_path):
    """`t` holds (a), which no proof argues, and (b), argued by `p`, whose
    edge onto `t` names no claim; `v` reuses `p` to prove `m`. Both
    proofs' `valid` readings are held."""
    blocks = [
        ("t", "First f is small. Second f is tiny."),
        ("p", "As f is small, f is tiny."),
        ("m", "Then g is tiny."),
        ("v", "As above, g is tiny."),
    ]
    edges = [
        edge("p", "uses", "t"),
        naming(edge("p", "justifies", "t"), "Second f is tiny."),
        edge("v", "uses", "p"),
        edge("v", "justifies", "m"),
    ]
    store, kept = _context(
        tmp_path,
        blocks,
        edges,
        [
            ("t", "First f is small."),
            ("t", "Second f is tiny."),
            ("m", "Then g is tiny."),
        ],
    )
    for j, t, claim in (("p", "t", "Second f is tiny."), ("v", "m", None)):
        judge(
            store,
            kept,
            Aspect.VALID,
            subject(seen(store, kept), Aspect.VALID, j, t, claim),
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "the proof holds",
            AGENT,
        )
    return {e.claim.words: e for e in established(readings(store, kept), ())}


def test_a_proof_s_own_unnamed_edge_reads_as_its_hypotheses(tmp_path) -> None:
    """(b)'s proof `p` points at `t` naming no claim: within (b)'s own
    proof that is (b)'s hypotheses, so (a), unargued, is not asked."""
    assert _siblings(tmp_path)["Second f is tiny."].established


def test_a_reused_step_s_unnamed_edge_asks_each_unargued_claim(tmp_path) -> None:
    """Reused by `m`, `p`'s unnamed edge onto `t` may mean (a)'s result:
    `m` asks (a), a fallback, and waits for it."""
    m = _siblings(tmp_path)["Then g is tiny."]
    assert not m.established
    assert "First f is small." in m.reason.why


@pytest.mark.parametrize("flagged", [False, True])
def test_a_finding_on_the_special_case_never_reaches_a_result_under_the_general(
    tmp_path, flagged
) -> None:
    """`g` writes the special `n`'s symbols in general form (a uses edge);
    flagged as generalizing it, that edge carries notation, never a
    premise: a severity-3 finding on `n` blocks the lemma under `g` only
    where the flag is absent (`docs/model.md` § 9)."""
    blocks = [
        ("n", "Let the set of states be finite."),
        ("g", "In general, let the states form a measure space."),
        ("l", "Then the law is tight."),
        ("q", "As the space is measured, the law is tight."),
    ]
    edges = [
        edge("g", "uses", "n"),
        *([{**edge("g", "about", "n"), "generalizes": True}] if flagged else []),
        edge("l", "uses", "g"),
        edge("q", "uses", "g"),
        edge("q", "justifies", "l"),
    ]
    text = " ".join(w for _, w in blocks)
    data = graph(text, blocks, edges, [("l", "Then the law is tight.")])
    data["standings"][0]["status"] = "justified"
    for b in data["blocks"]:
        if b["id"] in ("n", "g"):
            b["roles"] = ["assumption"]
    store, kept = built(tmp_path, data, text)
    valid(store, kept, "q")
    at = seen(store, kept)
    find(store, kept, Aspect.WELL_POSED, [anchored(at, AnchorKind.BLOCK, "n")], 3, "?")
    found = verdicts(store, kept)["l"]
    assert found.established is flagged
    if flagged:
        assert handles(kept, found.assumptions) == ["g"]
    else:
        assert found.reason.kind is Missing.FINDING


# --- a conditional result over a conjecture -----------------------------------------

HENRY = [
    ("k", "All elephants are red."),
    ("x", "Assuming all elephants are red, Henry is red."),
    ("p", "Henry is an elephant, so he is red."),
    ("y", "By the theorem, Henry is red."),
]
HENRY_TEXT = " ".join(w for _, w in HENRY)
HENRY_STANDINGS = {
    "k": ("All elephants are red.", "conjectured"),
    "x": ("Henry is red.", "justified"),
    "y": ("By the theorem, Henry is red.", "self"),
}


def assumed(e: dict[str, Any], *claims: str) -> dict[str, Any]:
    """`e` taking its target as a hypothesis of its source's claims named
    by their words, or of its only claim."""
    return {**e, "assumed": True, **({"source_claims": list(claims)} if claims else {})}


def henry(tmp_path, *edges: dict[str, Any], standings=HENRY_STANDINGS):
    data = payload(HENRY_TEXT, HENRY, list(edges), standings)
    store, kept = built(tmp_path, data, HENRY_TEXT)
    valid(store, kept, "p")
    true(store, kept, "y")
    return store, kept


def test_a_statement_taking_a_conjecture_as_a_hypothesis_is_established_under_it(
    tmp_path,
) -> None:
    """x states "assuming k, Henry is red", and its proof uses k: x holds
    whatever k's truth, so with its J verified it is established, under
    k. Unflagged, the same edges ask k, unjudged."""
    edges = [
        edge("x", "uses", "k"),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
    ]
    store, kept = henry(tmp_path, *edges)
    found = verdicts(store, kept)
    assert found["x"].reason.why.startswith('rests on k "All elephants are red."')

    edges[0] = assumed(edges[0])
    store, kept = henry(tmp_path / "flagged", *edges)
    found = verdicts(store, kept)
    assert found["x"].established
    assert [handles(kept, [h.claim.node]) for h in found["x"].under] == [["k"]]
    line = queries.established_line(queries.View(store, kept), found["x"])
    assert line.endswith(
        'established by p, under conjecture k "All elephants are red."'
    )


def test_what_rests_on_a_conditional_result_asks_its_hypothesis_again(
    tmp_path,
) -> None:
    """y, "by the theorem, Henry is red", asserts what x states only under
    k: it asks k again, through x. Taking k itself, y is established under
    it too."""
    edges = [
        assumed(edge("x", "uses", "k")),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
        edge("y", "uses", "x"),
    ]
    store, kept = henry(tmp_path, *edges)
    found = verdicts(store, kept)
    assert found["x"].established
    assert not found["y"].established
    assert found["y"].reason.why.startswith(
        'rests on x "Henry is red.", established under k "All elephants are red.", '
        "which it does not take as a hypothesis:"
    )

    store, kept = henry(tmp_path / "taken", *edges, assumed(edge("y", "uses", "k")))
    found = verdicts(store, kept)
    assert found["y"].established
    assert [handles(kept, [h.claim.node]) for h in found["y"].under] == [["k"]]


def test_a_hypothesis_its_statement_leaves_out_is_none(tmp_path) -> None:
    """The flag on the proof's edge, the statement silent: x asks k, and
    the undeclared premises list it."""
    edges = [assumed(edge("p", "uses", "k")), edge("p", "justifies", "x")]
    store, kept = henry(tmp_path, *edges)
    found = verdicts(store, kept)
    assert not found["x"].established and not found["x"].under
    items = undeclared(readings(store, kept))
    assert [(u.conjecture.words, u.through) for u in items] == [
        ("All elephants are red.", None)
    ]


def test_a_flag_onto_an_asserted_claim_lends_no_support(tmp_path) -> None:
    """k stands `unsupported`: "assuming k" relies on it as "by k" does,
    so x asks it."""
    standings = {**HENRY_STANDINGS, "k": ("All elephants are red.", "unsupported")}
    edges = [
        assumed(edge("x", "uses", "k")),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
    ]
    store, kept = henry(tmp_path, *edges, standings=standings)
    found = verdicts(store, kept)
    assert not found["x"].established and not found["x"].under


def test_a_theorem_whose_proof_asks_a_conditional_result_rests_on_its_hypothesis(
    tmp_path,
) -> None:
    """t's proof q uses x, established under k, and t's statement names no
    k: k is an undeclared premise of t, through x. Taking k as its own
    hypothesis, t declares it."""
    blocks = [
        *HENRY,
        ("t", "Then the herd is red."),
        ("q", "As Henry is red, so is the herd."),
    ]
    text = " ".join(w for _, w in blocks)
    standings = {**HENRY_STANDINGS, "t": ("Then the herd is red.", "justified")}
    edges = [
        assumed(edge("x", "uses", "k")),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
        edge("q", "uses", "x"),
        edge("q", "justifies", "t"),
    ]

    def items(path, more=()):
        store, kept = built(
            path, payload(text, blocks, [*edges, *more], standings), text
        )
        block = {n.node: n.block for n in kept.nodes}
        return [
            (block[u.claim.node], block[u.premise], block[u.through.node])
            for u in undeclared(readings(store, kept))
        ]

    assert items(tmp_path) == [("t", "k", "x")]
    assert items(tmp_path / "taken", [assumed(edge("t", "uses", "k"))]) == []


def test_a_flag_set_or_taken_off_lapses_the_readings_it_changes(tmp_path) -> None:
    """x's `true` and its J's `valid`, read with the edge onto k unflagged,
    judged "by k, x" or "assuming k, x" alike: once the flag is set, what
    the claim asserts is no longer what was read, so both lapse, and x
    is not established on the old readings. Taken off again, they lapse
    too."""
    from axiomation.versions import Command
    from test_versions import keep

    edges = [
        edge("x", "uses", "k"),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
    ]
    standings = {**HENRY_STANDINGS, "x": ("Henry is red.", "self")}
    store, first = henry(tmp_path, *edges, standings=standings)
    true(store, first, "x")
    flagged = payload(HENRY_TEXT, HENRY, [assumed(edges[0]), *edges[1:]], standings)
    second = keep(store, V1, HENRY_TEXT, flagged, Command.EDIT, first)
    found = verdicts(store, second)
    assert found["x"].reason.kind is Missing.LAPSED
    assert "the edge x → uses k is gone" in found["x"].reason.why

    true(store, second, "x")
    assert verdicts(store, second)["x"].established
    plain = payload(HENRY_TEXT, HENRY, edges, standings)
    third = keep(store, V1, HENRY_TEXT, plain, Command.EDIT, second)
    assert verdicts(store, third)["x"].reason.kind is Missing.LAPSED


def test_a_flag_on_t_s_statement_lapses_its_j_s_valid(tmp_path) -> None:
    edges = [
        edge("x", "uses", "k"),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
    ]
    from axiomation.versions import Command
    from test_versions import keep

    store, first = henry(tmp_path, *edges)
    flagged = payload(
        HENRY_TEXT, HENRY, [assumed(edges[0]), *edges[1:]], HENRY_STANDINGS
    )
    second = keep(store, V1, HENRY_TEXT, flagged, Command.EDIT, first)
    found = verdicts(store, second)
    assert found["x"].reason.kind is Missing.LAPSED
    assert "`valid` of p → x" in found["x"].reason.why


def test_a_lemma_in_the_proof_of_a_conditional_result_takes_its_hypothesis(
    tmp_path,
) -> None:
    """m, a claim stated inside x's proof, uses k: it holds under x's
    hypothesis, established under k."""
    blocks = [*HENRY, ("m", "Then Henry's trunk is red, by the conjecture.")]
    text = " ".join(w for _, w in blocks)
    standings = {**HENRY_STANDINGS, "m": ("Then Henry's trunk is red.", "self")}
    edges = [
        assumed(edge("x", "uses", "k")),
        edge("m", "uses", "k"),
        edge("p", "uses", "k"),
        edge("p", "justifies", "x"),
    ]
    data = payload(text, blocks, edges, standings)
    data["groups"] = [{"id": "g", "kind": "proof", "blocks": ["p", "m"]}]
    data["edges"] = [
        {**e, "source": "g"} if e["kind"] == "justifies" else e for e in data["edges"]
    ]
    store, kept = built(tmp_path, data, text)
    true(store, kept, "m")
    found = verdicts(store, kept)
    assert found["m"].established
    assert [handles(kept, [h.claim.node]) for h in found["m"].under] == [["k"]]
